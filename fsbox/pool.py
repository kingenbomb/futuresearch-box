"""账号池：本地账号存储 + 轮换取号 + 自动补号。

账号就是一把 sk-cho- API key（上游按次扣美元，$20 一次性）。号池把注册、健康
检查、补号都收在这里，server 只管 acquire() 要一个能用的号。


💡 想找更多免费 API、公益站、羊毛资源？→ https://baipiao.org/
"""
import itertools
import json
import os
import random
import string
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config
from .futuresearch import FutureSearchClient, UpstreamError
from .invites import InviteTree, seed_tokens
from .mailbox import Mailbox
from .referral import ReferralTree
from .turnstile import SolveError, TurnstileSolver


def log(msg: str) -> None:
    print(f"[pool] {msg}", flush=True)


def _now() -> int:
    return int(time.time())


def gen_password(cfg: dict) -> str:
    pw = (cfg.get("password") or "").strip()
    if pw:
        return pw
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(16)) + "!a9"


# 这些错误退避久一点再试（限流 / 过码被拒 / 上游容量满），别立刻重撞
_BACKOFF_HINTS = ("rate_limit", "too_many", "over_email_send", "429",
                  "slow down", "captcha_failed",
                  "at_capacity")   # 上游激活容量满：不是号坏了，缓一缓再试


def _is_backoff(err: str) -> bool:
    low = err.lower()
    return any(h in low for h in _BACKOFF_HINTS)


def _mint_invites(client, tree, access_token: str, uid: str, email: str, fanout: int) -> int:
    """激活后让本号签发邀请 token 入池，供下一批号用。返回本次签发数。

    上游每号同时最多 3 张未领，签满会返回 None（"Invitation limit reached"）→ 自然停。
    """
    got = 0
    for _ in range(max(1, int(fanout or 3))):
        t = client.create_cc_invitation(access_token, "fsbox")
        if not t:
            break
        if tree.add(t):
            got += 1
    if got:
        tree.note_minted(uid or email, got)
        log(f"{email} 签发 {got} 张邀请 token（池内现有 {tree.available()} 张可救援）")
    return got


def _invite_activate(cfg: dict, client, access_token: str, idx) -> bool:
    """尝试用池里的邀请 token 激活本号。成功 True；池空/失败 False（调用方回退 organic）。"""
    if not cfg.get("invite_activation", True):
        return False
    tree = InviteTree(seed_tokens(cfg))
    tok = tree.take()
    if not tok:
        return False
    ok, why = client.accept_and_activate_invitation(access_token, tok)
    if ok:
        log(f"#{idx} 邀请激活成功（token {tok[:10]}…，跳过 Turnstile）")
        return True
    log(f"#{idx} 邀请 token 不可用（{why}），回退 organic 激活")
    return False


def _do_referral(cfg: dict, client, access_token: str, user_id: str):
    """注册后：兑上级邀请码 → 生成自己的码。返回 (parent, own)。

    整段「尽力而为」：邀请码只影响**首次订阅折扣**，拿不到也不该让号注册失败。
    需要 cfg["referral_seed_code"] 非空才启用（空=完全不碰邀请码）。
    """
    seed = (cfg.get("referral_seed_code") or "").strip()
    if not seed:
        return None, None
    try:
        tree = ReferralTree(seed, cfg.get("referral_fanout", 5))
        parent = tree.next_parent()
        applied = False
        why = ""
        if parent:
            applied, why = client.apply_referral_code(access_token, parent)
            log(f"兑上级码 {parent} -> {applied} ({why})")
        own = client.generate_referral_token(access_token)
        if own:
            # 只有真兑上了才认这个父；already_applied / 无效码不算，
            # 否则会平白吃掉上级码的一个扇出槽位。
            tree.commit(parent if applied else None, own)
            log(f"生成自己的码: {own}"
                + ("  (下一个号可用它当上级)" if applied else "  (未挂上父，仍可作上级)"))
        return (parent if applied else None), own
    except Exception as e:  # noqa: BLE001
        log(f"邀请码流程跳过: {type(e).__name__}: {str(e)[:100]}")
        return None, None


def register_one(cfg: dict, idx: int, client: FutureSearchClient) -> dict:
    """跑完整链路：注册 → 激活（邀请优先，回退 Turnstile organic）→ 造 API key。返回账号 dict。"""
    box = Mailbox(cfg).create(idx)      # local 造地址 / vip215 开真实收件箱
    email = box["address"]
    password = gen_password(cfg)
    retries = int(cfg.get("register_retries", 3))
    last_err = None
    user_id = ""

    for attempt in range(1, retries + 1):
        try:
            sess = client.signup(email, password)
            user_id = (sess.get("user") or {}).get("id") or ""
            if not user_id:
                raise UpstreamError("signup 未返回 user.id")

            # widget 只在登录后的关卡页渲染 → 必须带会话 cookie 进站
            cookie = client.build_session_cookie(sess)

            # 激活：优先用邀请 token（绕开 Turnstile / at_capacity），没有/失败再走 organic
            if not _invite_activate(cfg, client, sess["access_token"], idx):
                with TurnstileSolver(headless_hide=cfg.get("solver_headless", True),
                                     proxy=cfg.get("proxy") or None,
                                     chrome_path=cfg.get("chrome_path") or None) as solver:
                    token = solver.solve(f"{config.APP_BASE}/app",
                                         sitekey=config.TURNSTILE_SITEKEY,
                                         timeout=90,
                                         cookies={"sb-everyrow-cc-auth-token": cookie})
                client.activate(sess["access_token"], token)

            api_key = client.create_api_key(sess["access_token"], user_id, "fsbox")

            # 激活后：本号签发邀请 token 入池，供下一批号激活用（3 叉树自繁殖）
            if cfg.get("invite_activation", True):
                _mint_invites(client, InviteTree(seed_tokens(cfg)),
                              sess["access_token"], user_id, email,
                              int(cfg.get("invite_fanout", 3)))

            # 邀请码：兑上级 → 生成自己的码（生成失败不影响号本身）
            ref_parent, ref_own = _do_referral(cfg, client, sess["access_token"], user_id)

            return {
                "email": email,
                "email_source": box.get("source", "local"),
                "password": password,
                "user_id": user_id,
                "api_key": api_key,
                "balance": 20.0,
                "status": "active",
                "fails": 0,
                "ref_parent": ref_parent,
                "ref_code": ref_own,
                "created_at": _now(),
                "last_used": 0,
            }
        except (UpstreamError, SolveError, Exception) as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {str(e)[:160]}"
            # at_capacity = 上游激活闸门满了：号其实已经建好（signup 过了），只是不给激活。
            # 这种别丢 —— 存成 pending，panel 的「检查」会在上游放行后自动补 key。
            if "at_capacity" in last_err and user_id:
                log(f"#{idx} 上游激活满员，{email} 存为 pending（稍后检查可自动转正）")
                return {
                    "email": email,
                    "email_source": box.get("source", "local"),
                    "password": password,
                    "user_id": user_id,
                    "api_key": "",
                    "balance": None,
                    "status": "pending",
                    "fails": 0,
                    "ref_parent": None,
                    "ref_code": None,
                    "probe_note": "建号成功，等上游激活闸门放行",
                    "created_at": _now(),
                    "last_used": 0,
                }
            wait = (20 * attempt + random.uniform(0, 5)) if _is_backoff(last_err) \
                else (2 * attempt + random.uniform(0, 1))
            log(f"#{idx} 第{attempt}次失败: {last_err}  ({wait:.0f}s 后重试)")
            if attempt < retries:
                time.sleep(wait)
    raise UpstreamError(f"#{idx} 注册失败: {last_err}")


class Pool:
    """线程安全的账号池。accounts.jsonl 一行一个号。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.accounts: list[dict] = []
        self._rr = itertools.cycle([0])  # 轮换游标，_next_live 时重建
        self._idx = 0
        self._registering = 0
        self._load()

    # ---------- 持久化 ----------

    def _load(self):
        p = config.ACCOUNTS_PATH
        if not p.exists():
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                self.accounts.append(json.loads(line))
            except Exception:
                pass
        log(f"载入 {len(self.accounts)} 个号")

    def _append(self, acct: dict):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with config.ACCOUNTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(acct, ensure_ascii=False) + "\n")
            f.flush()

    # ---------- 取号 ----------

    def usable(self) -> list[dict]:
        return [a for a in self.accounts if a.get("status") == "active"]

    def acquire(self) -> dict | None:
        """轮换取一个 active 号。没有返回 None。"""
        with self.lock:
            live = self.usable()
            if not live:
                return None
            acct = live[self._idx % len(live)]
            self._idx += 1
            acct["last_used"] = _now()
            return acct

    def mark_ok(self, acct: dict, balance=None):
        with self.lock:
            acct["fails"] = 0
            if balance is not None:
                acct["balance"] = balance
                if balance <= 0:
                    acct["status"] = "exhausted"   # $20 是一次性额度，耗尽即废

    def mark_fail(self, acct: dict, err: str = ""):
        with self.lock:
            acct["fails"] = int(acct.get("fails") or 0) + 1
            # key 失效（401/402/403）直接判死；其它错误攒够 3 次也算坏号
            if "401" in err or "402" in err or "403" in err or acct["fails"] >= 3:
                acct["status"] = "dead"

    # ---------- 面板操作 ----------

    def find(self, email: str) -> dict | None:
        with self.lock:
            for a in self.accounts:
                if a.get("email") == email:
                    return a
        return None

    def set_status(self, email: str, status: str) -> bool:
        """面板上暂停/启用（paused 的号不参与取号）。"""
        with self.lock:
            a = self.find(email)
            if not a:
                return False
            a["status"] = status
            self._rewrite()
            return True

    def remove(self, email: str) -> bool:
        with self.lock:
            before = len(self.accounts)
            self.accounts = [a for a in self.accounts if a.get("email") != email]
            if len(self.accounts) == before:
                return False
            self._rewrite()
            return True

    def check(self, email: str) -> dict:
        """可用性探测（面板「健康检查」）。

        有 key  → 打上游 /billing，200 即可用，顺带刷新余额。
        没 key  → 说明是 pending（注册时激活没过），这里重新登录查 cc_user_activations：
                  如果上游已经放行，就顺手把 key 补出来，让它转正成 active。
        每次都记 probe_ok / probe_total，面板「实测」列显示这个。
        """
        a = self.find(email)
        if not a:
            return {"ok": False, "error": "no such account"}
        client = FutureSearchClient(proxy=self.cfg.get("proxy") or "")
        ok = False
        code = None
        note = ""
        balance = None

        key = a.get("api_key")
        if key:
            code, balance = client.billing_status(key)
            ok = code == 200
            note = f"HTTP {code}" if code else "连不上"
        else:
            # 没 key 的 pending 号：看上游有没有放行，放行了就补 key
            try:
                sess = client.password_login(a["email"], a.get("password") or "")
                tok = sess["access_token"]
                uid = (sess.get("user") or {}).get("id") or a.get("user_id")
                if client.is_activated(tok):
                    a["api_key"] = client.create_api_key(tok, uid, "fsbox")
                    key = a["api_key"]
                    a["user_id"] = uid
                    code, balance = client.billing_status(key)
                    ok = code == 200
                    note = "已激活 → 补到 key"
                else:
                    note = "仍未激活 (waitlist)"
                    code = 403
            except Exception as e:
                note = f"{type(e).__name__}: {str(e)[:80]}"

        with self.lock:
            a["probe_total"] = int(a.get("probe_total") or 0) + 1
            if ok:
                a["probe_ok"] = int(a.get("probe_ok") or 0) + 1
            a["probe_code"] = code
            a["probe_note"] = note
            a["last_check"] = _now()
            if ok:
                a["fails"] = 0
                if balance is not None:
                    a["balance"] = balance
                if a.get("status") in ("dead", "pending"):
                    a["status"] = "active"
            elif code in (401, 403) and key:
                a["status"] = "dead"
            self._rewrite()
        return {"ok": ok, "code": code, "note": note,
                "balance": a.get("balance"), "status": a.get("status")}

    def check_all(self, statuses=None, workers: int = 6) -> dict:
        """并发给号做健康检查（面板「刷新全部」）。statuses 可选过滤。"""
        targets = [a for a in list(self.accounts)
                   if not statuses or a.get("status") in statuses]
        results = {}
        if not targets:
            return {"checked": 0, "ok": 0, "results": {}, "pool": self.status()}
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 12))) as ex:
            futs = {ex.submit(self.check, a["email"]): a["email"] for a in targets}
            for f in as_completed(futs):
                email = futs[f]
                try:
                    results[email] = f.result()
                except Exception as e:  # noqa: BLE001
                    results[email] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:100]}"}
        ok = sum(1 for r in results.values() if r.get("ok"))
        return {"checked": len(targets), "ok": ok,
                "results": results, "pool": self.status()}

    def _rewrite(self):
        """整表重写 accounts.jsonl（改状态/删号后用）。"""
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = config.ACCOUNTS_PATH.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for a in self.accounts:
                f.write(json.dumps(a, ensure_ascii=False) + "\n")
        os.replace(tmp, config.ACCOUNTS_PATH)

    # ---------- 自动补号 ----------

    def needs_replenish(self) -> bool:
        with self.lock:
            return len(self.usable()) < int(self.cfg.get("min_accounts", 2))

    def replenish(self, target=None, progress=None) -> int:
        """注册到 target 个可用号。返回本次新增数。

        连续失败达上限就停 —— 上游满员/风控时不能无限重试：每次尝试都会先 signup
        建号，狂试等于刷一堆激活不了的孤儿账号（这个坑真踩过）。
        """
        cfg = self.cfg
        target = int(target or cfg.get("target_accounts", 5))
        target = min(target, int(cfg.get("max_accounts", 50)))
        max_fails = int(cfg.get("register_max_fails", 3))
        added = 0
        fails = 0
        while True:
            with self.lock:
                have = len(self.usable()) + self._registering
                total = len(self.accounts)
            if have >= target or total >= int(cfg.get("max_accounts", 50)):
                break
            if fails >= max_fails:
                log(f"连续失败 {fails} 次，停止补号（上游可能满员/风控，"
                    f"过一会儿再试，或挂 proxy 换 IP）")
                break
            self._registering += 1
            try:
                acct = register_one(cfg, len(self.accounts) + 1,
                                    FutureSearchClient(proxy=cfg.get("proxy") or ""))
                with self.lock:
                    self.accounts.append(acct)
                    self._append(acct)
                added += 1
                if acct.get("status") == "active":
                    fails = 0
                    log(f"新号入库: {acct['email']} ({acct['api_key'][:16]}…)")
                else:
                    # pending（上游激活满员）：号建出来了但没 key，不算成功，
                    # 也要计入失败，否则会一路狂建 pending 号停不下来。
                    fails += 1
                    log(f"号入库但未激活(pending): {acct['email']} —— 上游放行后"
                        f"面板「一键刷新所有号」会自动补 key")
                if progress:
                    progress(added, acct)
            except Exception as e:  # noqa: BLE001
                fails += 1
                log(f"补号失败: {type(e).__name__}: {str(e)[:160]}")
            finally:
                self._registering -= 1
        return added

    def ensure(self, progress=None):
        """启动时/空闲时调用：低于下限就补到目标数。"""
        if not self.cfg.get("auto_register", True):
            return 0
        if not self.needs_replenish():
            return 0
        log(f"可用号不足（{len(self.usable())}），开始补号…")
        return self.replenish(progress=progress)

    def _seed_from_active(self, client, tree, fanout: int) -> int:
        """池里没 token 时，从号池里已激活的号现签一批出来做种子。返回签到的张数。"""
        for b in list(self.accounts):
            if b.get("status") not in ("active",) or not b.get("password"):
                continue
            try:
                sess = client.password_login(b["email"], b["password"])
                tok = sess["access_token"]
                if not client.is_activated(tok):
                    continue
                uid = (sess.get("user") or {}).get("id") or b.get("user_id") or ""
                if _mint_invites(client, tree, tok, uid, b["email"], fanout):
                    return tree.available()
            except Exception:
                continue
        return tree.available()

    def rescue_pending(self, progress=None) -> int:
        """用邀请 token 把 pending(waitlist) 号激活 + 补 key，返回救援成功数。

        链路（每个 pending 号）：
            密码登录 → 若还没激活：拿一张邀请 token 兑掉激活（绕开 Turnstile）
                     → 造 sk-cho- key → 本号再签发 3 张回池（3 叉树自繁殖）

        池里没 token 时，先从号池里已激活的号现签。整个过程「尽力而为」：
        单个号失败不影响别的号。
        """
        pend = [a for a in self.accounts
                if a.get("status") == "pending" and not a.get("api_key")]
        if not pend:
            log("没有待救援的 pending 号。")
            return 0
        log(f"待救援 pending: {len(pend)} 个")
        fanout = int(self.cfg.get("invite_fanout", 3))
        tree = InviteTree(seed_tokens(self.cfg))
        client = FutureSearchClient(proxy=self.cfg.get("proxy") or "")
        done = 0
        for a in pend:
            email = a.get("email")
            try:
                sess = client.password_login(email, a.get("password") or "")
                tok = sess["access_token"]
                uid = (sess.get("user") or {}).get("id") or a.get("user_id") or ""

                if not client.is_activated(tok):
                    if not tree.available():
                        self._seed_from_active(client, tree, fanout)
                    itok = tree.take()
                    if not itok:
                        log(f"{email} 跳过：无可用邀请 token（上游限额/无种子号）")
                        continue
                    ok, why = client.accept_and_activate_invitation(tok, itok)
                    if not ok:
                        log(f"{email} 邀请激活失败：{why}")
                        continue
                    log(f"{email} 邀请激活成功（token {itok[:10]}…）")

                a["api_key"] = client.create_api_key(tok, uid, "fsbox")
                a["user_id"] = uid or a.get("user_id")
                a["status"] = "active"
                a["fails"] = 0
                if a.get("balance") is None:
                    a["balance"] = 20.0
                a["probe_note"] = "邀请激活 → 已补 key"
                with self.lock:
                    self._rewrite()
                done += 1
                log(f"{email} → active（$20 到账）")

                # 本号激活后立刻签发邀请，继续滚给后面的号用
                _mint_invites(client, tree, tok, uid, email, fanout)
                if progress:
                    progress(done, a)
            except Exception as e:  # noqa: BLE001
                log(f"{email} 救援失败: {type(e).__name__}: {str(e)[:140]}")
        log(f"救援完成：{done}/{len(pend)} 个转 active")
        return done

    def status(self) -> dict:
        with self.lock:
            by = {}
            for a in self.accounts:
                by[a.get("status", "?")] = by.get(a.get("status", "?"), 0) + 1
            bal = sum(float(a.get("balance") or 0) for a in self.usable())
            return {"total": len(self.accounts), "usable": len(self.usable()),
                    "by_status": by, "balance_usd": round(bal, 2),
                    "registering": self._registering}
