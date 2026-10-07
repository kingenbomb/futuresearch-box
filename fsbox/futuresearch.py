"""FutureSearch / Supabase 上游接口封装（同步，只用 requests）。

整条链路：
    signup ──► build_session_cookie ──► [浏览器过 Turnstile] ──► activate
                                                            └──► create_api_key ──► sk-cho-...
    sk-cho-... ──► run_research(prompt)  （异步任务：submit → poll → result）


白嫖站 · https://baipiao.org/  —— 免费 API / 公益站 / 羊毛资源
"""
import base64
import json
import time

import requests

from . import config

# OpenAI messages 里我们只关心文本
_SESSION_KEYS = ("access_token", "token_type", "expires_in", "expires_at",
                 "refresh_token", "user")

# 轮询节奏对齐官方 SDK：2s 起，×1.5 退避，封顶 15s
POLL_EVERY = 2.0
POLL_BACKOFF = 1.5
POLL_MAX = 15.0


class UpstreamError(RuntimeError):
    def __init__(self, msg, status=None, code=None):
        self.status = status
        self.code = code
        super().__init__(msg)


def _err(resp) -> tuple[str, str]:
    """从上游 JSON 错误里取 (error_code, message)。"""
    try:
        d = resp.json()
    except Exception:
        return "?", (resp.text or "")[:140]
    code = str(d.get("error_code") or d.get("code") or d.get("error") or "?")
    msg = d.get("msg") or d.get("message") or d.get("error_description") \
        or d.get("detail") or json.dumps(d, ensure_ascii=False)[:140]
    return code, str(msg)[:160]


class FutureSearchClient:
    """一个实例持有一条 requests.Session（连接复用 + 统一代理/UA）。"""

    def __init__(self, proxy: str = "", timeout: int = 40):
        self.proxy = proxy or None
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": config.UA,
                               "Accept": "application/json"})
        if self.proxy:
            self.s.proxies.update({"http": self.proxy, "https": self.proxy})

    # ---------- 注册 ----------

    def signup(self, email: str, password: str) -> dict:
        """Supabase 邮箱注册。服务端 mailer_autoconfirm=true → 直接返回会话，
        不需要真收信（UI 上那句 "existing accounts only" 只是前端摆设）。"""
        r = self.s.post(
            f"{config.SUPABASE_URL}/auth/v1/signup",
            headers={"apikey": config.SUPABASE_ANON_KEY,
                     "Content-Type": "application/json"},
            json={"email": email, "password": password},
            timeout=self.timeout)
        d = r.json() if r.content else {}
        if r.status_code == 200 and d.get("access_token"):
            return d
        code, msg = _err(r)
        # 重试/续跑常见：号已建过 → 用密码登录取回会话，让流程幂等可重入
        if code == "user_already_exists":
            return self.password_login(email, password)
        raise UpstreamError(f"signup HTTP {r.status_code} [{code}]: {msg}",
                            r.status_code, code)

    def password_login(self, email: str, password: str) -> dict:
        r = self.s.post(
            f"{config.SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={"apikey": config.SUPABASE_ANON_KEY,
                     "Content-Type": "application/json"},
            json={"email": email, "password": password},
            timeout=self.timeout)
        d = r.json() if r.content else {}
        if r.status_code == 200 and d.get("access_token"):
            return d
        code, msg = _err(r)
        raise UpstreamError(f"login HTTP {r.status_code} [{code}]: {msg}",
                            r.status_code, code)

    @staticmethod
    def build_session_cookie(session: dict) -> str:
        """@supabase/ssr 的会话 cookie 值：base64- + base64url(JSON(会话))。
        把它注入浏览器，SPA 才会以登录态渲染出 Turnstile widget。"""
        payload = {k: session[k] for k in _SESSION_KEYS if k in session}
        raw = json.dumps(payload, separators=(",", ":"))
        return "base64-" + base64.urlsafe_b64encode(raw.encode()).decode()

    def activate(self, access_token: str, turnstile_token: str) -> str:
        """POST /app/api/activation/organic —— 认 Bearer，但必须带过验证的 token。
        没过激活的号是 waitlist 状态，造 key 会 403。"""
        r = self.s.post(
            f"{config.APP_BASE}/app/api/activation/organic",
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json"},
            json={"turnstileToken": turnstile_token},
            timeout=self.timeout)
        d = r.json() if r.content else {}
        status = d.get("status") or ""
        if r.status_code != 200 or status != "activated":
            code, msg = _err(r)
            raise UpstreamError(f"activate HTTP {r.status_code} [{code}]: {msg}",
                                r.status_code, code)
        return status

    def create_api_key(self, access_token: str, account_id: str, name: str) -> str:
        r = self.s.post(
            f"{config.APP_BASE}/app/api/api-keys/create",
            headers={"Authorization": f"Bearer {access_token}",
                     "x-cohort-account-id": account_id,
                     "Content-Type": "application/json",
                     "Origin": config.APP_BASE,
                     "Referer": f"{config.APP_BASE}/app/api-key"},
            json={"name": name}, timeout=self.timeout)
        d = r.json() if r.content else {}
        if r.status_code != 200 or not d.get("key"):
            code, msg = _err(r)
            raise UpstreamError(f"create-key HTTP {r.status_code} [{code}]: {msg}",
                                r.status_code, code)
        return d["key"]

    # ---------- 号池维护 ----------

    def billing(self, api_key: str):
        """实时余额（美元）。只有上游明说 key 不认(401/403)才返回 None(判死)。"""
        try:
            r = self.s.get(f"{config.API_BASE}/billing",
                           headers={"Authorization": f"Bearer {api_key}"},
                           timeout=20)
        except requests.RequestException:
            raise
        if r.status_code in (401, 403):
            return None
        if r.status_code != 200:
            return None
        v = (r.json() or {}).get("current_balance_dollars")
        return float(v) if isinstance(v, (int, float)) else None

    def whoami(self, api_key: str) -> dict:
        r = self.s.get(f"{config.API_BASE}/whoami",
                       headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
        return r.json() if r.content else {}

    # ---------- 研究任务（异步：submit → poll → result） ----------

    def run_research(self, api_key: str, task: str,
                     effort: str = "high", timeout: int = 600,
                     llm: str | None = None,
                     budget: int | None = None,
                     include_reasoning: bool = False) -> str:
        """把一段文字丢给 single-agent，阻塞到出结果，返回 answer 文本。

        llm: 上游底层模型枚举（见 models.py）。None = 用 effort_level 预设。
             两者**互斥**：用 llm 时上游还要求补齐 iteration_budget 和
             include_reasoning（effort_level 平时就是这两个的预设），
             这里按 llm 的档位后缀自动推预算。
        """
        h = {"Authorization": f"Bearer {api_key}",
             "Content-Type": "application/json"}
        if llm:
            from . import models
            body = {"input": {}, "task": task, "llm": llm,
                    "iteration_budget": models.budget_for(llm, budget),
                    "include_reasoning": bool(include_reasoning)}
        else:
            body = {"input": {}, "task": task, "effort_level": effort}
        r = self.s.post(f"{config.API_BASE}/operations/single-agent", headers=h,
                        json=body, timeout=self.timeout)
        d = r.json() if r.content else {}
        task_id = d.get("task_id")
        if not task_id:
            code, msg = _err(r)
            raise UpstreamError(f"submit HTTP {r.status_code} [{code}]: {msg}",
                                r.status_code, code)

        deadline = time.time() + timeout
        interval = POLL_EVERY
        while time.time() < deadline:
            time.sleep(interval)
            interval = min(interval * POLL_BACKOFF, POLL_MAX)
            try:
                s = self.s.get(f"{config.API_BASE}/tasks/{task_id}/status",
                               headers=h, timeout=30).json()
            except Exception:
                continue  # 轮询掉线不当作任务失败
            st = (s or {}).get("status")
            if st == "completed":
                break
            if st in ("failed", "revoked"):
                raise UpstreamError(f"task {task_id} {st}: {(s or {}).get('error')}")
        else:
            raise UpstreamError(f"task {task_id} 超过 {timeout}s 仍未完成")

        res = self.s.get(f"{config.API_BASE}/tasks/{task_id}/result",
                         headers=h, timeout=40).json()
        return _extract_answer(res)


def _extract_answer(res: dict) -> str:
    """结果形状是 data:[{"answer": "..."}]（list of dict），也兼容 dict。"""
    data = (res or {}).get("data")
    if isinstance(data, dict) and data.get("answer") is not None:
        data = data["answer"]
    elif isinstance(data, list) and data and isinstance(data[0], dict) \
            and data[0].get("answer") is not None:
        data = data[0]["answer"]
    elif data is None:
        return ""
    else:
        return json.dumps(data, ensure_ascii=False)
    return data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)


def messages_to_task(messages) -> str:
    """OpenAI messages → 一段研究任务文本。system 当背景/要求。"""
    parts = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, list):  # 多模态分片，只取文本
            c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
        c = (c or "").strip()
        if not c:
            continue
        role = m.get("role")
        if role == "system":
            parts.append("[背景/要求]\n" + c)
        elif role == "assistant":
            parts.append("[已有回答]\n" + c)
        else:
            parts.append(c)
    return "\n\n".join(parts)
