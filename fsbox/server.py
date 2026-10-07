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
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ad, config, models, panel
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

    def chat(self, messages: list, model: str, effort: str | None = None) -> tuple[str, dict]:
        """跑一次研究任务，返回 (answer, 用量信息)。model 决定底层用哪个模型。"""
        acct = self.wait_account()
        if not acct:
            raise RuntimeError("号池里没有可用号，且补号失败/超时（看启动日志）")
        task = ad.augment_task(messages_to_task(messages), self.cfg)  # 提示词广告
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
        answer = ad.decorate(answer, self.cfg)                        # 结果广告
        # 用量按字符粗估（上游不返回 token，这里只为了客户端能显示点什么）
        usage = {"prompt_tokens": max(1, len(task) // 4),
                 "completion_tokens": max(1, len(answer) // 4)}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return answer, usage


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
                return self._send(200, panel.api_state(gw))
            if path == "/panel/api/models":
                return self._send(200, {"object": "list",
                                        "data": models.list_models()})
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

            try:
                answer, usage = gw.chat(messages, req_model)
            except Exception as e:
                return self._err(500, f"{type(e).__name__}: {e}", "api_error")

            cid = "chatcmpl-" + str(int(time.time() * 1000))
            created = int(time.time())
            if not stream:
                return self._send(200, {
                    "id": cid, "object": "chat.completion", "created": created,
                    "model": req_model,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": answer}}],
                    "usage": usage})

            # 上游不是流式的，这里把结果一次性当成一个 delta 发出去（很多客户端
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
                self.wfile.write(chunk({"role": "assistant", "content": answer}).encode())
                self.wfile.write(chunk({}, "stop").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def serve(cfg: dict, on_ready=None) -> None:
    """启动服务（阻塞）。on_ready(base_url) 会在监听后回调。"""
    gw = Gateway(cfg)
    host, port = cfg["host"], int(cfg["port"])
    httpd = ThreadingHTTPServer((host, port), make_handler(gw))
    httpd.daemon_threads = True

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
