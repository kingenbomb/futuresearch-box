"""底层模型目录 + 选择器。

FutureSearch 的 single-agent 支持一个 `llm` 参数挑底层模型（上游 OpenAPI 的
`LLMEnumPublic`，共 143 个）。本模块把这份清单固化下来，并提供一个宽容的
model 名 → llm 枚举 的解析器，让客户端用亲民的名字就能选中。

  "claude-opus-5.5"        -> CLAUDE_5_5_OPUS_HIGH
  "claude-5-5-opus-high"   -> CLAUDE_5_5_OPUS_HIGH   (规范形式)
  "CLAUDE_5_5_OPUS_MAX"    -> CLAUDE_5_5_OPUS_MAX    (原样)
  "gpt-5.5"                -> GPT_5_5_HIGH

清单来自上游 spec，可能随上游更新。想刷新就跑 `python -m fsbox.models --refresh`
（需要号池里有一个可用 key）。


💡 想找更多免费 API、公益站、羊毛资源？→ https://baipiao.org/
"""
import json
import re

DEFAULT_MODEL = "futuresearch-deep"   # 不传 llm，用上游系统默认

# 全部可用底层模型（上游 LLMEnumPublic，143 个）
LLM_ENUM = [
    "GPT_5", "GPT_5_MINIMAL", "GPT_5_LOW", "GPT_5_HIGH",
    "GPT_5_MINI", "GPT_5_NANO", "GPT_5_1_NT", "GPT_5_1_LOW",
    "GPT_5_2_NT", "GPT_5_2_LOW", "GPT_5_2_MEDIUM", "GPT_5_2_HIGH",
    "GPT_5_4_NT", "GPT_5_4_LOW", "GPT_5_4_MEDIUM", "GPT_5_4_HIGH",
    "GPT_5_4_XHIGH", "GPT_5_4_PRO_MEDIUM", "GPT_5_4_PRO_HIGH", "GPT_5_4_PRO_XHIGH",
    "GPT_5_4_MINI_NT", "GPT_5_4_MINI_LOW", "GPT_5_4_MINI_MEDIUM", "GPT_5_4_MINI_HIGH",
    "GPT_5_4_NANO_NT", "GPT_5_4_NANO_LOW", "GPT_5_4_NANO_MEDIUM", "GPT_5_4_NANO_HIGH",
    "GPT_5_5_NT", "GPT_5_5_LOW", "GPT_5_5_MEDIUM", "GPT_5_5_HIGH",
    "GPT_5_5_XHIGH", "GPT_5_6_SOL_NT", "GPT_5_6_SOL_LOW", "GPT_5_6_SOL_MEDIUM",
    "GPT_5_6_SOL_HIGH", "GPT_5_6_SOL_XHIGH", "GPT_5_6_SOL_MAX", "GPT_5_6_TERRA_NT",
    "GPT_5_6_TERRA_LOW", "GPT_5_6_TERRA_MEDIUM", "GPT_5_6_TERRA_HIGH", "GPT_5_6_TERRA_XHIGH",
    "GPT_5_6_TERRA_MAX", "GPT_5_6_LUNA_NT", "GPT_5_6_LUNA_LOW", "GPT_5_6_LUNA_MEDIUM",
    "GPT_5_6_LUNA_HIGH", "GPT_5_6_LUNA_XHIGH", "GPT_5_6_LUNA_MAX", "GPT_6_ASTRA_LOW",
    "GPT_6_ASTRA_MEDIUM", "GPT_6_ASTRA_HIGH", "GPT_6_ASTRA_XHIGH", "GPT_6_ASTRA_MAX",
    "GPT_6_1_SOL_LOW", "GPT_6_1_SOL_MEDIUM", "GPT_6_1_SOL_HIGH", "GPT_6_1_SOL_XHIGH",
    "GPT_6_1_SOL_MAX", "CLAUDE_4_5_SONNET", "CLAUDE_4_5_SONNET_THINKING", "CLAUDE_4_5_SONNET_HIGH",
    "CLAUDE_4_5_SONNET_MINIMAL", "CLAUDE_4_5_HAIKU", "CLAUDE_4_5_HAIKU_THINKING", "CLAUDE_4_5_HAIKU_MINIMAL",
    "CLAUDE_4_5_OPUS", "CLAUDE_4_5_OPUS_THINKING", "CLAUDE_4_5_OPUS_LOW", "CLAUDE_4_5_OPUS_HIGH",
    "CLAUDE_4_6_OPUS_NT", "CLAUDE_4_6_OPUS_LOW", "CLAUDE_4_6_OPUS_MEDIUM", "CLAUDE_4_6_OPUS_HIGH",
    "CLAUDE_4_6_OPUS_MAX", "CLAUDE_4_6_SONNET_NT", "CLAUDE_4_6_SONNET_LOW", "CLAUDE_4_6_SONNET_MEDIUM",
    "CLAUDE_4_6_SONNET_HIGH", "CLAUDE_4_6_SONNET_MAX", "CLAUDE_4_7_OPUS_NT", "CLAUDE_4_7_OPUS_LOW",
    "CLAUDE_4_7_OPUS_MEDIUM", "CLAUDE_4_7_OPUS_HIGH", "CLAUDE_4_7_OPUS_MAX", "CLAUDE_4_8_OPUS_NT",
    "CLAUDE_4_8_OPUS_LOW", "CLAUDE_4_8_OPUS_MEDIUM", "CLAUDE_4_8_OPUS_HIGH", "CLAUDE_4_8_OPUS_XHIGH",
    "CLAUDE_4_8_OPUS_MAX", "CLAUDE_FABLE_5_NT", "CLAUDE_FABLE_5_LOW", "CLAUDE_FABLE_5_MEDIUM",
    "CLAUDE_FABLE_5_HIGH", "CLAUDE_FABLE_5_XHIGH", "CLAUDE_FABLE_5_MAX", "CLAUDE_5_SONNET_NT",
    "CLAUDE_5_SONNET_LOW", "CLAUDE_5_SONNET_MEDIUM", "CLAUDE_5_SONNET_HIGH", "CLAUDE_5_SONNET_XHIGH",
    "CLAUDE_5_SONNET_MAX", "CLAUDE_5_OPUS_NT", "CLAUDE_5_OPUS_LOW", "CLAUDE_5_OPUS_MEDIUM",
    "CLAUDE_5_OPUS_HIGH", "CLAUDE_5_OPUS_XHIGH", "CLAUDE_5_OPUS_MAX", "CLAUDE_5_5_OPUS_LOW",
    "CLAUDE_5_5_OPUS_MEDIUM", "CLAUDE_5_5_OPUS_HIGH", "CLAUDE_5_5_OPUS_XHIGH", "CLAUDE_5_5_OPUS_MAX",
    "GEMINI_3_1_PRO_LOW", "GEMINI_3_1_PRO_MEDIUM", "GEMINI_3_1_PRO_HIGH", "GEMINI_3_FLASH_MINIMAL",
    "GEMINI_3_FLASH_LOW", "GEMINI_3_FLASH_MEDIUM", "GEMINI_3_FLASH_HIGH", "GEMINI_3_5_FLASH_MINIMAL",
    "GEMINI_3_5_FLASH_LOW", "GEMINI_3_5_FLASH_MEDIUM", "GEMINI_3_5_FLASH_HIGH", "GEMINI_3_8_FLASH_MINIMAL",
    "GEMINI_3_8_FLASH_LOW", "GEMINI_3_8_FLASH_MEDIUM", "GEMINI_3_8_FLASH_HIGH", "GEMINI_3_5_FLASH_LITE",
    "GEMINI_3_5_FLASH_LITE_LOW", "GEMINI_3_5_FLASH_LITE_MEDIUM", "GEMINI_3_5_FLASH_LITE_HIGH", "MUSE_SPARK_MINIMAL",
    "MUSE_SPARK_LOW", "MUSE_SPARK_MEDIUM", "MUSE_SPARK_HIGH", "MUSE_SPARK_XHIGH",
    "GROK_4_5", "GLM_5_3_FLASH", "GLM_5_3",
]

_ENUM_SET = set(LLM_ENUM)

# 亲民别名 -> 枚举。别名都带默认档位（一般取 HIGH）。
ALIASES = {
    # ---- Claude ----
    "claude-opus-5.5": "CLAUDE_5_5_OPUS_HIGH",
    "claude-opus-5.5-max": "CLAUDE_5_5_OPUS_MAX",
    "claude-opus-5.5-xhigh": "CLAUDE_5_5_OPUS_XHIGH",
    "claude-sonnet-5.5": "CLAUDE_5_5_SONNET_HIGH",
    "claude-opus-5": "CLAUDE_5_OPUS_HIGH",
    "claude-sonnet-5": "CLAUDE_5_SONNET_HIGH",
    "claude-fable-5": "CLAUDE_FABLE_5_HIGH",
    "claude-opus-4.8": "CLAUDE_4_8_OPUS_HIGH",
    "claude-opus-4.7": "CLAUDE_4_7_OPUS_HIGH",
    "claude-opus-4.6": "CLAUDE_4_6_OPUS_HIGH",
    "claude-opus-4.5": "CLAUDE_4_5_OPUS_HIGH",
    "claude-sonnet-4.6": "CLAUDE_4_6_SONNET_HIGH",
    "claude-sonnet-4.5": "CLAUDE_4_5_SONNET_HIGH",
    "claude-haiku-4.5": "CLAUDE_4_5_HAIKU",
    # ---- GPT ----
    "gpt-5": "GPT_5_HIGH",
    "gpt-5.2": "GPT_5_2_HIGH",
    "gpt-5.4": "GPT_5_4_HIGH",
    "gpt-5.4-pro": "GPT_5_4_PRO_HIGH",
    "gpt-5.4-mini": "GPT_5_4_MINI_HIGH",
    "gpt-5.4-nano": "GPT_5_4_NANO_HIGH",
    "gpt-5.5": "GPT_5_5_HIGH",
    "gpt-5.6-sol": "GPT_5_6_SOL_HIGH",
    "gpt-5.6-terra": "GPT_5_6_TERRA_HIGH",
    "gpt-5.6-luna": "GPT_5_6_LUNA_HIGH",
    "gpt-6-astra": "GPT_6_ASTRA_HIGH",
    "gpt-6.1-sol": "GPT_6_1_SOL_HIGH",
    # ---- Gemini ----
    "gemini-3.1-pro": "GEMINI_3_1_PRO_HIGH",
    "gemini-3-flash": "GEMINI_3_FLASH_HIGH",
    "gemini-3.5-flash": "GEMINI_3_5_FLASH_HIGH",
    "gemini-3.8-flash": "GEMINI_3_8_FLASH_HIGH",
    "gemini-3.5-flash-lite": "GEMINI_3_5_FLASH_LITE_HIGH",
    # ---- 其它 ----
    "glm-5.3": "GLM_5_3",
    "glm-5.3-flash": "GLM_5_3_FLASH",
    "grok-4.5": "GROK_4_5",
    "muse-spark": "MUSE_SPARK_HIGH",
}

# 枚举 -> 规范小写横杠形式，客户端也可以用这个
CANONICAL = {v: v.lower().replace("_", "-") for v in LLM_ENUM}

_TIER_SUFFIXES = ("_XHIGH", "_HIGH", "_MEDIUM", "_LOW", "_MAX", "_MINIMAL",
                  "_NT", "_THINKING")

# 用 llm 时必须同时给 iteration_budget（上游要求：effort_level 与 llm 互斥，
# 而 effort_level 平时就是 iteration_budget + include_reasoning 的预设）。
# llm 枚举自带档位后缀，就按档位推预算 —— 档位越高，迭代越多。
_TIER_BUDGET = {"MINIMAL": 8, "LOW": 12, "NT": 20, "THINKING": 20,
                "MEDIUM": 25, "HIGH": 35, "XHIGH": 50, "MAX": 60}
_DEFAULT_BUDGET = 30


def budget_for(llm: str, override=None) -> int:
    """按 llm 档位推 iteration_budget。override 优先，范围夹在 0~100。"""
    if override:
        return max(0, min(100, int(override)))
    for suf, n in _TIER_BUDGET.items():
        if llm.endswith("_" + suf):
            return n
    return _DEFAULT_BUDGET


def resolve(model: str):
    """model 名 → llm 枚举。认不出或不需选（默认）返回 None。"""
    if not model:
        return None
    m = model.strip()
    if m == DEFAULT_MODEL:
        return None                      # 用上游系统默认
    if m in _ENUM_SET:                   # 原样枚举
        return m
    low = m.lower()
    if low in ALIASES:
        return ALIASES[low]
    for enum, canon in CANONICAL.items():   # 规范横杠形式
        if low == canon:
            return enum
    # 宽松归一：点/横杠/斜杠都当分隔符，再补默认档位
    norm = re.sub(r"[.\-/\s]+", "_", low).upper()
    if norm in _ENUM_SET:
        return norm
    for suf in _TIER_SUFFIXES:
        if norm + suf in _ENUM_SET:
            return norm + suf
    return None


def is_known(model: str) -> bool:
    return model == DEFAULT_MODEL or resolve(model) is not None


def list_models():
    """给 /v1/models 的清单。默认模型 + 全部可选底层模型 + 别名。"""
    out = [{"id": DEFAULT_MODEL, "object": "model", "created": 0,
            "owned_by": "futuresearch", "description": "FutureSearch 默认研究 agent"}]
    for v in LLM_ENUM:
        out.append({"id": CANONICAL[v], "object": "model", "created": 0,
                    "owned_by": "futuresearch", "llm": v})
    for alias, enum in ALIASES.items():
        out.append({"id": alias, "object": "model", "created": 0,
                    "owned_by": "futuresearch", "llm": enum})
    return out


def _refresh():
    """从上游 spec 重新拉清单并改写本文件的 LLM_ENUM（需要可用 key）。"""
    import urllib.request
    from . import config, pool
    p = pool.Pool(config.load())
    acct = p.acquire()
    if not acct:
        print("号池里没有可用 key，先 python -m fsbox register 1")
        return 1
    req = urllib.request.Request(f"{config.API_BASE}/openapi.json",
                                 headers={"Authorization": "Bearer " + acct["api_key"]})
    spec = json.loads(urllib.request.urlopen(req, timeout=40).read())
    new = spec["components"]["schemas"]["LLMEnumPublic"]["enum"]
    print(f"上游现有 {len(new)} 个（本地 {len(LLM_ENUM)} 个）")
    for v in sorted(set(new) - set(LLM_ENUM)):
        print("  新增:", v)
    for v in sorted(set(LLM_ENUM) - set(new)):
        print("  消失:", v)
    return 0


if __name__ == "__main__":
    import sys
    if "--refresh" in sys.argv:
        sys.exit(_refresh())
    print(f"共 {len(LLM_ENUM)} 个底层模型 + {len(ALIASES)} 个别名")
    for a, e in ALIASES.items():
        print(f"  {a:<22} -> {e}")
