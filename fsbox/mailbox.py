"""邮箱来源。

两种模式：

  local   本地造地址（默认，`fs<时间戳><随机>@mailinator.com`）。
          上游 FutureSearch 是 `mailer_autoconfirm=true` —— 注册直接返回会话，
          **根本不发确认信**，所以地址只要格式合法就行，收不收信无所谓。

  vip215  走 vip.215.im（= yyds Mail，https://vip.215.im/docs）开真实收件箱，
          自己配 key 即可。好处：用他们的域名而不是一眼假的临时域，风控友好一些，
          而且真能收信 —— 将来上游要是加了验证/密码找回就用得上。

    契约：
      POST {base}/accounts            -> {address, token, id}
      GET  {base}/messages/next?address=&wait= -> {verificationCode, ...}
    鉴权：X-API-Key: AC-...


白嫖站 · https://baipiao.org/  —— 免费 API / 公益站 / 羊毛资源
"""
import json
import random
import time

import requests

from . import config


def log(msg: str) -> None:
    print(f"[mail] {msg}", flush=True)


class Mailbox:
    """地址工厂。create() 拿一个地址，fetch_code() 从那个地址取验证码。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.mode = (cfg.get("email_mode") or "local").strip().lower()
        self.base = (cfg.get("email_api_base")
                     or "https://maliapi.215.im/v1").rstrip("/")
        self.key = (cfg.get("email_api_key") or "").strip()
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": config.UA, "Accept": "application/json"})
        if cfg.get("proxy"):
            self.s.proxies.update({"http": cfg["proxy"], "https": cfg["proxy"]})
        # 配了 vip215 但没给 key → 退回本地造地址，别让注册直接崩
        if self.mode == "vip215" and not self.key:
            log("email_mode=vip215 但没配 email_api_key，退回本地造地址")
            self.mode = "local"

    # ---------- 造地址 ----------

    def create(self, idx: int) -> dict:
        """返回 {address, token, id, source}。token/id 只对 vip215 有值。"""
        if self.mode == "vip215":
            try:
                return self._create_vip215(idx)
            except Exception as e:
                log(f"vip215 开箱失败({type(e).__name__}: {str(e)[:100]})，本次退回本地地址")
        return {"address": self._local_address(idx), "token": "", "id": "",
                "source": "local"}

    def _local_address(self, idx: int) -> str:
        dom = self.cfg.get("email_domain") or "mailinator.com"
        return f"fs{int(time.time())}{random.randint(100000, 999999)}{idx}@{dom}"

    def _create_vip215(self, idx: int) -> dict:
        local = f"fs{int(time.time())}{random.randint(100000, 999999)}{idx}"
        body = {"localPart": local}
        # 幂等键：超时重试时复用同一个 key 和 body，避免开出多个箱
        headers = {"X-API-Key": self.key, "Content-Type": "application/json",
                   "Idempotency-Key": f"fsbox-{local}"}
        r = self.s.post(f"{self.base}/accounts", headers=headers, json=body, timeout=30)
        d = r.json() if r.content else {}
        data = d.get("data") or d
        addr = data.get("address")
        if not addr:
            raise RuntimeError(f"HTTP {r.status_code}: {json.dumps(d, ensure_ascii=False)[:160]}")
        return {"address": addr, "token": data.get("token") or "",
                "id": data.get("id") or "", "source": "vip215"}

    # ---------- 收信（当前注册流程用不到，留给将来的验证/找回密码） ----------

    def fetch_code(self, box: dict, wait: int = 30, timeout: int = 60) -> str | None:
        """从该地址取下一个未读邮件的验证码。没有就返回 None。

        注意 local 模式拿不到信（mailinator 是公共箱且我们也没去读），
        这里只对 vip215 有效。
        """
        if box.get("source") != "vip215" or not self.key:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = self.s.get(f"{self.base}/messages/next",
                               headers={"X-API-Key": self.key},
                               params={"address": box["address"], "wait": wait},
                               timeout=wait + 15)
                if r.status_code == 204 or not r.content:
                    continue
                d = r.json()
                data = d.get("data") or d
                code = data.get("verificationCode") or data.get("verification_code")
                if code:
                    return str(code)
            except Exception as e:
                log(f"取码失败({type(e).__name__}: {str(e)[:80]})，重试")
                time.sleep(2)
        return None
