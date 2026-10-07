"""让 OpenAI 兼容网关支持 function calling（工具调用）。

上游 FutureSearch 是「异步研究任务」——只吐一段纯文本，没有原生 tools。
所以这里用「提示词协议 + 确定性解析器」把它包成标准 OpenAI tool_calls：

    请求带 tools ──► build_task(): 把函数清单 + 输出格式要求拼进 task
                        │
    上游答案 ──────────► parse(): 命中工具调用 JSON → tool_calls
                                   否则 → 当普通文本，原样返回

不依赖二次 LLM：解析是确定性的，只有 name 命中请求里声明过的函数才算调用，
认不出就当普通回答 —— 不会把正常答案误判成调用。


💡 想找更多免费 API、公益站、羊毛资源？→ https://baipiao.org/
"""
import json
import re
import time

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _schema(tool):
    """OpenAI tool 定义 → 精简 {name, description, parameters}。

    兼容两种写法：新的 {"type":"function","function":{...}} 和旧的裸 {name,...}。
    """
    if not isinstance(tool, dict):
        return None
    fn = tool.get("function") if tool.get("type") == "function" else tool
    if not isinstance(fn, dict) or not fn.get("name"):
        return None
    return {
        "name": str(fn["name"]),
        "description": str(fn.get("description") or ""),
        "parameters": fn.get("parameters") or fn.get("input_schema")
                      or {"type": "object", "properties": {}},
    }


def specs_of(tools):
    return [s for s in (_schema(t) for t in (tools or [])) if s]


def names_of(tools):
    return [s["name"] for s in specs_of(tools)]


def choice_disabled(tool_choice) -> bool:
    """tool_choice == 'none' → 完全不提供工具，按纯文本走。"""
    return isinstance(tool_choice, str) and tool_choice.strip().lower() == "none"


def build_task(task, tools, tool_choice=None):
    """把工具协议拼进 task。没有可用函数 或 tool_choice='none' 时原样返回。"""
    specs = specs_of(tools)
    if not specs or choice_disabled(tool_choice):
        return task

    forced = None
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) \
            else tool_choice
        forced = fn.get("name")
    required = (tool_choice == "required") or bool(forced)

    lines = ["\n\n[工具调用协议] 你可以调用以下函数：",
             "```json",
             json.dumps(specs, ensure_ascii=False, indent=2),
             "```"]
    if forced:
        lines.append(f"本次**必须**调用函数 `{forced}`（不要只给文字）。")
    elif required:
        lines.append("本次**必须**调用某一个函数（不要只给文字）。")
    lines.append("需要调用函数时，**只输出**下面这一行 JSON，不要任何多余文字：")
    lines.append('{"tool_calls":[{"name":"函数名","arguments":{参数对象}}]}')
    lines.append("不需要调用函数时，直接正常回答即可。"
                 "若需同时调用多个函数，放进 tool_calls 数组。")
    return (task or "") + "\n".join(lines)


def _iter_json_blobs(text):
    """从文本里挖可能的 JSON：整段、``` 围栏内容、以及每个 { 起的平衡子串。"""
    if not text:
        return
    t = text.strip()
    yield t                                    # 1) 整段
    for m in _FENCE.finditer(t):               # 2) 代码围栏
        yield m.group(1).strip()
    n = len(t)                                 # 3) 平衡花括号
    i = 0
    while i < n:
        if t[i] == "{":
            depth = 0
            for j in range(i, n):
                if t[j] == "{":
                    depth += 1
                elif t[j] == "}":
                    depth -= 1
                    if depth == 0:
                        yield t[i:j + 1]
                        i = j
                        break
        i += 1


def _norm_calls(obj, allowed):
    """把解析出的对象规整成 [{name, arguments}]；不合法/名字不在允许集 返回 None。"""
    if isinstance(obj, dict) and "tool_calls" in obj:
        raw = obj["tool_calls"]
    elif isinstance(obj, dict) and ("name" in obj or "function" in obj):
        raw = [obj]
    elif isinstance(obj, list):
        raw = obj
    else:
        return None
    if not isinstance(raw, list) or not raw:
        return None
    out = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        fn = item.get("function") if isinstance(item.get("function"), dict) else item
        name = fn.get("name")
        if not name or name not in allowed:
            return None
        args = fn.get("arguments")
        if args is None:
            args = fn.get("input") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except Exception:
                pass
        out.append({"name": name, "arguments": args})
    return out


def parse(answer, tools):
    """解析上游答案。命中工具调用 → [{"name","arguments"}]；否则 None（当普通文本）。"""
    allowed = set(names_of(tools))
    if not allowed:
        return None
    seen = set()
    for blob in _iter_json_blobs(answer):
        if not blob or blob in seen:
            continue
        seen.add(blob)
        try:
            obj = json.loads(blob)
        except Exception:
            continue
        calls = _norm_calls(obj, allowed)
        if calls:
            return calls
    return None


def to_openai(calls, call_id_prefix="call"):
    """规整后的调用 → OpenAI 标准 tool_calls 字段。"""
    out = []
    for i, c in enumerate(calls):
        args = c["arguments"]
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        out.append({
            "id": f"{call_id_prefix}_{int(time.time() * 1000)}_{i}",
            "type": "function",
            "function": {"name": c["name"], "arguments": args},
        })
    return out
