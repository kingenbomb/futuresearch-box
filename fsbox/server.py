"""OpenAI 兼容网关（stdlib http.server，无需额外依赖）。

    GET  /health                探活 + 号池概览
    GET  /v1/models             模型清单
    POST /v1/chat/completions   对话（支持 stream=true）
    GET  /panel                 号池面板（见 panel.py）

鉴权：Authorization: Bearer <config.api_key>。上游 FutureSearch 的 sk-cho- key
永远不会暴露给调用方。

💡 想找更多免费 API、公益站、羊毛资源？→ https://baipiao.org/
"""
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ad, config, models, panel, toolcall
from .futuresearch import FutureSearchClient, messages_to_task
from .pool import Pool, log


class Gateway:
    """服务端共享状态：配置 + 号池 + 上游客户端。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.pool = Pool(cfg)
        self.client = FutureSearchClient(proxy=cfg.get("proxy") or "")
        self.model = cfg.get("model_name") or "futuresearch-deep"

    def wait_account(self, timeout: float = 120.0):
        """要一个可用号；池子空了就补号并等一会儿。"""
        acct = self.pool.acquire()
        if acct:
            return acct
        deadline = time.time() + timeout
        if self.cfg.get("auto_register", True) and not self.pool._registering:
            threading.Thread(target=self.pool.ensure, daemon=True).start()
        while time.time() < deadline:
            time.sleep(1.0)
            acct = self.pool.acquire()
            if acct:
                return acct
        return None

    def chat(self, messages: list, model: str, effort: str | None = None,
             tools: list | None = None, tool_choice=None) -> dict:
        """跑一次研究任务，返回 {content, tool_calls, usage}。

        model 决定底层用哪个模型。请求带 tools 时走「提示词协议 + 解析器」：
        函数清单拼进 task，上游答案若命中调用则转成标准 tool_calls。
        """
        acct = self.wait_account()
        if not acct:
            raise RuntimeError("号池里没有可用号，且补号失败/超时（看启动日志）")

        use_tools = bool(tools) and not toolcall.choice_disabled(tool_choice)
        task = messages_to_task(messages)
        if use_tools:
            task = toolcall.build_task(task, tools, tool_choice)
        else:
            task = ad.augment_task(task, self.cfg)   # 提示词广告（工具模式跳过，免得污染 JSON）
        if not task:
            raise ValueError("messages 里没有可用文本")

        llm = models.resolve(model)   # None = 用上游系统默认
        key = acct["api_key"]
        try:
            answer = self.client.run_research(
                key, task,
                effort=effort or self.cfg.get("effort_level", "high"),
                timeout=int(self.cfg.get("task_timeout", 600)),
                llm=llm,
                budget=int(self.cfg.get("iteration_budget") or 0) or None,
                include_reasoning=bool(self.cfg.get("include_reasoning")))
        except Exception as e:
            self.pool.mark_fail(acct, f"{type(e).__name__}: {e}")
            raise
        self.pool.mark_ok(acct)

        # 用量按字符粗估（上游不返回 token，这里只为了客户端能显示点什么）
        usage = {"prompt_tokens": max(1, len(task) // 4),
                 "completion_tokens": max(1, len(answer) // 4)}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

        if use_tools:
            calls = toolcall.parse(answer, tools)
            if calls:
                return {"content": None, "tool_calls": toolcall.to_openai(calls),
                        "usage": usage}

        answer = ad.decorate(answer, self.cfg)                        # 结果广告
        usage["completion_tokens"] = max(1, len(answer) // 4)
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return {"content": answer, "tool_calls": None, "usage": usage}


def make_handler(gw: Gateway):
    model = gw.model
    api_key = gw.cfg["api_key"]

    class Handler(BaseHTTPRequestHandler):
        server_version = "fsbox/1.0"
        protocol_version = "HTTP/1.1"

        # ---- 工具 ----

        def _send(self, code, obj, ctype="application/json"):
            body = obj if isinstance(obj, bytes) else \
                json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _download(self, filename, obj):
            """以附件形式返回 JSON（导出用，浏览器会弹下载）。"""
            body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _err(self, code, msg, etype="invalid_request_error"):
            self._send(code, {"error": {"message": msg, "type": etype}})

        def _body(self) -> dict:
            """读并解析请求体 JSON（面板 API 用，读不到就当空对象）。"""
            try:
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return {}

        def _authed(self) -> bool:
            h = self.headers.get("Authorization") or ""
            tok = h.replace("Bearer ", "").strip()
            return bool(tok) and tok == api_key

        def log_message(self, fmt, *args):   # 收掉默认的逐请求噪声
            pass

        # ---- 路由 ----

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization,Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/")
            if path == "/health":
                st = gw.pool.status()
                return self._send(200, {"status": "ok", "model": model,
                                        "pool": st})
            if path == "/panel":          # 页面本身免鉴权，数据接口才要 key
                return self._send(200, panel.page(), "text/html; charset=utf-8")
            if not self._authed():
                return self._err(401, "Invalid API key", "authentication_error")
            if path == "/v1/models":
                return self._send(200, {"object": "list",
                                        "data": models.list_models()})
            if path == "/panel/api/state":
                # 分页/筛选/排序参数走查询串，交给 panel.api_state 在服务端处理
                import urllib.parse as _up
                qs = _up.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                params = {k: v[0] for k, v in qs.items()}
                return self._send(200, panel.api_state(gw, params))
            if path == "/panel/api/models":
                return self._send(200, {"object": "list",
                                        "data": models.list_models()})
            if path == "/panel/api/export":
                import urllib.parse as _up
                qs = _up.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                what = (qs.get("what") or ["accounts"])[0]
                from .invites import InviteTree, seed_tokens
                from .referral import ReferralTree
                if what == "invites":
                    payload = {
                        "type": "fsbox-invites", "version": 1,
                        "exported_at": int(time.time()),
                        "invites": InviteTree(seed_tokens(gw.cfg)).export(),
                        "referrals": ReferralTree(
                            gw.cfg.get("referral_seed_code", ""),
                            gw.cfg.get("referral_fanout", 5)).export(),
                    }
                    return self._download("fsbox-invites.json", payload)
                payload = {
                    "type": "fsbox-accounts", "version": 1,
                    "exported_at": int(time.time()),
                    "count": len(gw.pool.accounts),
                    "accounts": gw.pool.export_accounts(),
                }
                return self._download("fsbox-accounts.json", payload)
            return self._err(404, f"Not found: {self.path}")

        def do_POST(self):
            path = self.path.split("?")[0].rstrip("/")
            if not path.startswith("/panel/api/"):
                return self._chat(path)
            if not self._authed():
                return self._err(401, "Invalid API key", "authentication_error")
            return self._panel_post(path)

        def _panel_post(self, path):
            # /panel/api/accounts/{email}/{action}  |  /panel/api/replenish
            import urllib.parse as _up
            if path == "/panel/api/config":
                # 面板改运行时配置（档位 / 迭代预算 / 是否带推理），持久化到 config.json。
                # 注意：effort_level 只在「用默认模型」时生效；选具体底层模型时看 iteration_budget。
                body = self._body()
                changed = {}
                if "effort_level" in body:
                    v = str(body["effort_level"]).strip().lower()
                    if v in ("low", "medium", "high"):
                        gw.cfg["effort_level"] = v
                        changed["effort_level"] = v
                if "iteration_budget" in body:
                    try:
                        v = max(0, min(100, int(body["iteration_budget"])))
                        gw.cfg["iteration_budget"] = v
                        changed["iteration_budget"] = v
                    except (TypeError, ValueError):
                        pass
                if "include_reasoning" in body:
                    v = bool(body["include_reasoning"])
                    gw.cfg["include_reasoning"] = v
                    changed["include_reasoning"] = v
                if changed:
                    try:
                        config.save(gw.cfg)
                    except Exception as e:  # noqa: BLE001
                        log(f"配置持久化失败: {e}")
                return self._send(200, {"ok": True, "changed": changed})
            if path == "/panel/api/rescue":
                # 后台跑：用邀请 token 把 pending(waitlist) 号激活 + 补 key（可耗时）
                threading.Thread(target=gw.pool.rescue_pending, daemon=True).start()
                pend = sum(1 for a in gw.pool.accounts if a.get("status") == "pending")
                return self._send(200, {"ok": True, "pending": pend,
                                        "status": gw.pool.status()})
            if path == "/panel/api/import":
                # 导入账号信息 / 邀请码（content 是上传文件的文本）
                body = self._body()
                what = (body.get("what") or "accounts").strip()
                content = body.get("content") or ""
                if what == "invites":
                    from .invites import InviteTree, seed_tokens
                    from .referral import ReferralTree
                    toks, ref = _parse_invites(content)
                    a1, s1 = InviteTree(seed_tokens(gw.cfg)).import_tokens(toks)
                    a2 = s2 = 0
                    if ref:
                        rt = ReferralTree(gw.cfg.get("referral_seed_code", ""),
                                          gw.cfg.get("referral_fanout", 5))
                        a2, s2 = rt.import_state(ref)
                    return self._send(200, {
                        "ok": True, "added": a1 + a2, "skipped": s1 + s2,
                        "detail": {"tokens_added": a1, "tokens_skipped": s1,
                                   "referrals_added": a2, "referrals_skipped": s2}})
                items = _parse_accounts(content)
                added, skipped = gw.pool.import_accounts(items)
                return self._send(200, {"ok": True, "added": added, "skipped": skipped,
                                        "status": gw.pool.status()})
            if path == "/panel/api/replenish":
                if not gw.cfg.get("auto_register", True):
                    gw.cfg["auto_register"] = True    # 手动补号时临时允许
                # 面板可以带 {"count": N} 指定这次补多少个（加到当前可用数之上）
                try:
                    n = int(self._body().get("count") or 0)
                except Exception:
                    n = 0
                base = len(gw.pool.usable())
                target = (base + n) if n > 0 else None
                threading.Thread(target=gw.pool.replenish,
                                 kwargs={"target": target}, daemon=True).start()
                return self._send(200, {"ok": True, "count": n or "default",
                                        "status": gw.pool.status()})
            if path == "/panel/api/models":
                return self._send(200, {"object": "list",
                                        "data": models.list_models()})
            if path == "/panel/api/check-all":
                return self._send(200, gw.pool.check_all())
            parts = path.split("/")
            if len(parts) == 6 and parts[3] == "accounts":
                email = _up.unquote(parts[4])
                action = parts[5]
                if action == "check":
                    return self._send(200, gw.pool.check(email))
                if action == "pause":
                    a = gw.pool.find(email)
                    if not a:
                        return self._err(404, "no such account")
                    nxt = "active" if a.get("status") == "paused" else "paused"
                    gw.pool.set_status(email, nxt)
                    return self._send(200, {"ok": True, "status": nxt})
                if action == "delete":
                    ok = gw.pool.remove(email)
                    return self._send(200 if ok else 404, {"ok": ok})
            return self._err(404, f"Not found: {path}")

        def _chat(self, path):
            if path != "/v1/chat/completions":
                return self._err(404, f"Not found: {self.path}")
            if not self._authed():
                return self._err(401, "Invalid API key", "authentication_error")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._err(400, f"bad json: {e}")

            req_model = body.get("model") or model
            if not models.is_known(req_model):
                return self._err(
                    404,
                    f"model '{req_model}' not found. 用 '{model}' 走默认，"
                    f"或用 /v1/models 里的任一底层模型（如 claude-opus-5.5）",
                    "invalid_request_error")
            messages = body.get("messages") or []
            stream = bool(body.get("stream"))
            # tools 透传：新的 tools/tool_choice，兼容旧的 functions
            tools = body.get("tools") or body.get("functions") or None
            tool_choice = body.get("tool_choice")

            try:
                res = gw.chat(messages, req_model, tools=tools, tool_choice=tool_choice)
            except Exception as e:
                return self._err(500, f"{type(e).__name__}: {e}", "api_error")

            content = res["content"]
            tool_calls = res["tool_calls"]
            usage = res["usage"]
            finish = "tool_calls" if tool_calls else "stop"

            cid = "chatcmpl-" + str(int(time.time() * 1000))
            created = int(time.time())
            if not stream:
                msg = {"role": "assistant", "content": content}
                if tool_calls:
                    msg["content"] = None
                    msg["tool_calls"] = tool_calls
                return self._send(200, {
                    "id": cid, "object": "chat.completion", "created": created,
                    "model": req_model,
                    "choices": [{"index": 0, "finish_reason": finish,
                                 "message": msg}],
                    "usage": usage})

            # 上游不是流式的，这里把结果一次性当成 delta 发出去（很多客户端
            # 只认 stream，所以必须给 SSE 外壳）
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()

            def chunk(delta, finish=None):
                return "data: " + json.dumps({
                    "id": cid, "object": "chat.completion.chunk",
                    "created": created, "model": req_model,
                    "choices": [{"index": 0, "delta": delta,
                                 "finish_reason": finish}]}, ensure_ascii=False) + "\n\n"

            try:
                if tool_calls:
                    # 标准 tool_calls 流式分片：每个调用一个 delta，带 index
                    pieces = [{"role": "assistant", "content": None,
                               "tool_calls": [{"index": i, "id": tc["id"],
                                               "type": "function",
                                               "function": tc["function"]}]}
                              for i, tc in enumerate(tool_calls)]
                    for p in pieces:
                        self.wfile.write(chunk(p).encode())
                    self.wfile.write(chunk({}, "tool_calls").encode())
                else:
                    self.wfile.write(chunk({"role": "assistant",
                                            "content": content}).encode())
                    self.wfile.write(chunk({}, "stop").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


class _SingleBindServer(ThreadingHTTPServer):
    """禁止端口复用：HTTPserver 默认 allow_reuse_address=1(SO_REUSEADDR)，
    在 Windows 上这会让**第二个实例也能绑上同一端口** → 两个网关抢一个 8000，
    请求随机落到其中一个（表现为偶发连接中断）。这里显式独占，第二个实例
    启动时直接报「端口被占用」而不是悄悄顶掉对方。"""

    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt":
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        super().server_bind()


def serve(cfg: dict, on_ready=None) -> None:
    """启动服务（阻塞）。on_ready(base_url) 会在监听后回调。"""
    gw = Gateway(cfg)
    host, port = cfg["host"], int(cfg["port"])
    try:
        httpd = _SingleBindServer((host, port), make_handler(gw))
    except OSError as e:
        log(f"启动失败：{host}:{port} 已被占用（是不是已经有一个实例在跑？）: {e}")
        raise SystemExit(1)

    base = f"http://{host}:{port}/v1"
    if on_ready:
        on_ready(base)

    # 后台补号：服务先起来，别让用户等注册
    if cfg.get("auto_register", True):
        threading.Thread(target=gw.pool.ensure, daemon=True).start()
        threading.Thread(target=_replenish_loop, args=(gw,), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("收到 Ctrl+C，关闭中…")
    finally:
        httpd.shutdown()


def _replenish_loop(gw: Gateway, every: int = 120):
    """空闲巡检：号池低于下限就补。"""
    while True:
        time.sleep(every)
        try:
            gw.pool.ensure()
        except Exception as e:  # noqa: BLE001
            log(f"巡检补号异常: {type(e).__name__}: {str(e)[:120]}")


def _parse_accounts(content: str) -> list:
    """宽容解析导入的账号：接受 {accounts:[...]} / 裸数组 / 单对象 / JSONL。"""
    text = (content or "").strip()
    if not text:
        return []
    try:
        d = json.loads(text)
        if isinstance(d, dict) and isinstance(d.get("accounts"), list):
            return d["accounts"]
        if isinstance(d, list):
            return d
        if isinstance(d, dict):
            return [d]
    except Exception:
        pass
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _parse_invites(content: str):
    """宽容解析邀请码：接受 {invites:[...], referrals:{...}} / 裸数组 / JSONL。"""
    text = (content or "").strip()
    if not text:
        return [], None
    try:
        d = json.loads(text)
        if isinstance(d, list):
            return [str(x) for x in d], None
        if isinstance(d, dict):
            toks = [str(x) for x in (d.get("invites") or [])]
            return toks, d.get("referrals")
    except Exception:
        pass
    toks = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return toks, None
