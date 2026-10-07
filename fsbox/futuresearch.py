"""FutureSearch / Supabase 上游接口封装（同步，只用 requests）。

整条链路：
    signup ──► build_session_cookie ──► [浏览器过 Turnstile] ──► activate
                                                            └──► create_api_key ──► sk-cho-...
    sk-cho-... ──► run_research(prompt)  （异步任务：submit → poll → result）


💡 想找更多免费 API、公益站、羊毛资源？→ https://baipiao.org/
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
    # 有 200 但业务失败的情况（如 activate 返回 {"status":"at_capacity"}），
    # status 就是「错误码」，别让它掉进 "?" 看不出来。
    code = str(d.get("error_code") or d.get("code") or d.get("error")
               or d.get("status") or "?")
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

    def is_activated(self, access_token: str) -> bool:
        """这个号激活了没。判据是 cc_user_activations 有没有行 —— 有行=已激活，
        空=waitlist（造 key 会 403）。"""
        try:
            r = self.s.get(
                f"{config.SUPABASE_URL}/rest/v1/cc_user_activations?select=user_id",
                headers={"apikey": config.SUPABASE_ANON_KEY,
                         "Authorization": f"Bearer {access_token}"},
                timeout=20)
            return r.status_code == 200 and bool(r.json())
        except Exception:
            return False

    def billing_status(self, api_key: str) -> tuple[int | None, float | None]:
        """打一次 /billing，返回 (HTTP 状态码, 余额)。可用性探测用。"""
        try:
            r = self.s.get(f"{config.API_BASE}/billing",
                           headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
            if r.status_code != 200:
                return r.status_code, None
            v = (r.json() or {}).get("current_balance_dollars")
            return 200, (float(v) if isinstance(v, (int, float)) else None)
        except Exception:
            return None, None

    # ---------- 邀请码（Supabase RPC） ----------

    def _rpc(self, name: str, access_token: str, payload: dict):
        r = self.s.post(f"{config.SUPABASE_URL}/rest/v1/rpc/{name}",
                        headers={"apikey": config.SUPABASE_ANON_KEY,
                                 "Authorization": f"Bearer {access_token}",
                                 "Content-Type": "application/json"},
                        json=payload, timeout=self.timeout)
        try:
            return r.status_code, (r.json() if r.content else None)
        except Exception:
            return r.status_code, {"_raw": (r.text or "")[:160]}

    def can_be_referrer(self, access_token: str, account_id: str) -> bool:
        st, d = self._rpc("account_can_be_referrer", access_token,
                          {"p_account_id": account_id})
        return st == 200 and d is True

    def generate_referral_token(self, access_token: str) -> str | None:
        """生成本号的邀请码。同一个号可以反复生成，每次返回一个新码。"""
        st, d = self._rpc("generate_referral_token", access_token, {"p_metadata": {}})
        if st == 200 and isinstance(d, dict):
            return d.get("token")
        return None

    def apply_referral_code(self, access_token: str, code: str) -> tuple[bool, str]:
        """用别人的邀请码。返回 (是否成功, 说明)。

        注意上游的判定顺序：**一旦兑过，任何码都返回 already_applied**（连无效码
        也是），所以「已兑过」会盖掉「码无效」。兑之前无效码才是 400 invalid。
        """
        st, d = self._rpc("apply_referral_code", access_token, {"p_token": code})
        if isinstance(d, dict) and d.get("applied") is True:
            return True, "applied"
        if isinstance(d, dict) and d.get("reason"):
            return False, str(d["reason"])
        if st == 400 and isinstance(d, dict):
            return False, str(d.get("message") or "invalid")[:80]
        return False, f"HTTP {st}"

    # ---------- 邀请激活（cc_invitations；注意不是上面的 referral 折扣） ----------
    #
    # 两套系统的区别见 invites.py 顶部注释：
    #   referral   = 折扣券，apply 到 waitlist 号不产生激活行
    #   invitation = 真正的激活：waitlist 号用别人的 token 一兑即激活 + $20

    def create_cc_invitation(self, access_token: str, label: str = "fsbox") -> str | None:
        """本号签发一张邀请 token 供别人激活。

        上游每号**同时最多 3 张未领**（invitation_limit=3），满了返回
        400 "Invitation limit reached"；被领走会腾出槽位。签发失败返回 None。
        """
        st, d = self._rpc("create_cc_invitation", access_token, {"p_label": label})
        if st == 200 and isinstance(d, dict):
            return d.get("token")
        return None

    def accept_and_activate_invitation(self, access_token: str, token: str) -> tuple[bool, str]:
        """用别人的邀请 token **激活**本号（waitlist → 激活，绕开 Turnstile）。

        成功返回 (True, "activated")；token 无效/已用返回 (False, 原因)。
        激活后 cc_user_activations 会多一行 activation_type="invited"。
        """
        st, d = self._rpc("accept_and_activate_invitation", access_token, {"p_token": token})
        if st == 200:
            return True, "activated"
        if isinstance(d, dict) and d.get("message"):
            return False, str(d["message"])[:80]
        return False, f"HTTP {st}"

    def activation_row(self, access_token: str, uid: str):
        """读本号的激活行（cc_user_activations）。没激活返回 None。

        行里 activation_type 是 organic(invite?) / invited，invitation_limit 是额度。
        """
        try:
            r = self.s.get(
                f"{config.SUPABASE_URL}/rest/v1/cc_user_activations"
                f"?select=*&user_id=eq.{uid}",
                headers={"apikey": config.SUPABASE_ANON_KEY,
                         "Authorization": f"Bearer {access_token}"},
                timeout=20)
            d = r.json() if r.content else []
            return d[0] if isinstance(d, list) and d else None
        except Exception:
            return None

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
    """OpenAI messages → 一段研究任务文本。system 当背景/要求。

    支持工具调用往返：assistant 的 tool_calls 渲染成 [已调用工具]，
    role=tool 的返回渲染成 [工具返回]，避免被当成普通用户输入。
    """
    parts = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, list):  # 多模态分片，只取文本
            c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
        c = (c or "").strip()
        role = m.get("role")

        if role == "tool":                       # 工具执行结果回灌
            who = m.get("name") or m.get("tool_call_id") or ""
            parts.append((f"[工具返回 {who}]" if who else "[工具返回]") + "\n" + c)
            continue

        if role == "assistant" and m.get("tool_calls"):   # 上一轮的调用记录
            calls = []
            for t in m["tool_calls"] or []:
                fn = (t or {}).get("function") or {}
                calls.append(f"{fn.get('name')}({fn.get('arguments')})")
            parts.append("[已调用工具]\n" + "; ".join(calls))

        if not c:
            continue
        if role == "system":
            parts.append("[背景/要求]\n" + c)
        elif role == "assistant":
            parts.append("[已有回答]\n" + c)
        else:
            parts.append(c)
    return "\n\n".join(parts)
