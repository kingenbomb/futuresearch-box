"""邀请激活树 —— 用「邀请（invitation）」把 waitlist 号直接激活。

⚠️ 这和 referral.py 是**两套不同的东西**：

    referral（折扣 referral）  apply_referral_code / generate_referral_token
                               → 只给首次订阅折扣，**不激活**

    invitation（邀请 invitation）create_cc_invitation / accept_and_activate_invitation
                               → 直接把 waitlist 号激活、发 $20、并再给 3 张邀请

    本模块处理的是后者。实测（2026-10）：
      · 已激活号 create_cc_invitation(p_label) → 一张 token
        每号**同时最多 3 张未领**，满了报 400 "Invitation limit reached"
      · waitlist 号 accept_and_activate_invitation(p_token) → 激活
        激活行 activation_type="invited"，且$20 到账、能造 sk-cho- key
      · 被邀请激活的号**自己也能再发 3 张** → 天然一棵 3 叉树，指数铺开

所以只要有一个激活号当种子，后续全走邀请，就能绕开 Turnstile / at_capacity。

本模块只管「未用 token 池」的持久化与取用；签发与激活由 pool.py 用 client 完成。
状态存 data/invites.json，跨次运行累积。
"""
import json
import threading

from . import config


def log(msg: str) -> None:
    print(f"[invite] {msg}", flush=True)


class InviteTree:
    """线程安全的邀请 token 池（未领取的 token 队列）。"""

    def __init__(self, seed_tokens=None):
        self.lock = threading.RLock()
        self.tokens: list[str] = []        # 还没被用掉的邀请 token
        self.minted: dict[str, int] = {}   # email/uid -> 已签发张数（仅统计用）
        self.used: int = 0                 # 累计消耗张数
        self._load()
        if seed_tokens:
            self.add_many(seed_tokens)

    # ---------- 持久化 ----------

    def _path(self):
        return config.DATA_DIR / "invites.json"

    def _load(self):
        p = self._path()
        if not p.exists():
            return
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            self.tokens = [t for t in d.get("tokens", []) if t]
            self.minted = d.get("minted", {}) or {}
            self.used = int(d.get("used", 0))
        except Exception as e:
            log(f"invites.json 读取失败: {e}")

    def _save(self):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self._path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(
            {"tokens": self.tokens, "minted": self.minted, "used": self.used},
            indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path())

    # ---------- 取 / 放 ----------

    def add(self, token: str):
        token = (token or "").strip()
        if not token:
            return False
        with self.lock:
            if token in self.tokens:
                return False
            self.tokens.append(token)
            self._save()
            return True

    def add_many(self, toks) -> int:
        n = 0
        with self.lock:
            for t in toks or []:
                t = (t or "").strip()
                if t and t not in self.tokens:
                    self.tokens.append(t)
                    n += 1
            if n:
                self._save()
        return n

    def take(self) -> str | None:
        """取一张 token 用（先进先出）。没有返回 None。"""
        with self.lock:
            if not self.tokens:
                return None
            t = self.tokens.pop(0)
            self.used += 1
            self._save()
            return t

    def available(self) -> int:
        with self.lock:
            return len(self.tokens)

    def note_minted(self, key: str, n: int):
        if not key or not n:
            return
        with self.lock:
            self.minted[key] = int(self.minted.get(key, 0)) + int(n)
            self._save()

    # ---------- 导出 / 导入 ----------

    def export(self) -> list[str]:
        """导出未用邀请 token 池（备份/迁移用）。"""
        with self.lock:
            return list(self.tokens)

    def import_tokens(self, toks) -> tuple[int, int]:
        """并入外部邀请 token，去重。返回 (新增, 跳过)。"""
        added = skipped = 0
        with self.lock:
            have = set(self.tokens)
            for t in toks or []:
                t = str(t).strip() if t is not None else ""
                if not t or t in have:
                    skipped += 1
                    continue
                self.tokens.append(t)
                have.add(t)
                added += 1
            if added:
                self._save()
        return added, skipped

    def stats(self) -> dict:
        with self.lock:
            return {"available": len(self.tokens),
                    "used": self.used,
                    "accounts_minted": len(self.minted),
                    "minted_total": sum(self.minted.values())}


def seed_tokens(cfg: dict) -> list[str]:
    """从 config 读手动种子 token（逗号分隔）。启用邀请时用它做冷启动。"""
    raw = str(cfg.get("invite_seed_token") or "").strip()
    return [t.strip() for t in raw.split(",") if t.strip()]
