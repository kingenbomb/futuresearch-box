"""邀请码树 —— 每个新号：兑换上级码 → 生成自己的码 → 成为下一批的上级。

形状（fanout=5 时正好是种子码扇出 5 个兄弟，再由它们各自扇出）：

    种子码 N55DhsZUV2Mo
      ├─ gen1 ─ 兑种子 → 生成 nxq8iwhumPKr
      │    ├─ gen2 ─ 兑 nxq8iwhumPKr → 生成新码 …
      │    └─ gen3 …
      └─ ref1..ref4 ─ 各兑种子 → 各自生成码

选上级用 **BFS**：优先挑「还用不满 fanout 次」的码，从一开始的种子往后逐层铺开。
所以种子会被前 fanout 个号用掉，然后才轮到 gen1 生成的码 —— 这就自然长出多层树，
而不是一条直线。

状态存 data/referrals.json，跨次运行累积。

⚠️ 兑现的是「首次订阅折扣券」，不是免费额度 —— 见 README。
"""
import json
import threading

from . import config


def log(msg: str) -> None:
    print(f"[ref] {msg}", flush=True)


class ReferralTree:
    """线程安全的邀请码树。"""

    def __init__(self, seed: str, fanout: int = 5):
        self.seed = (seed or "").strip()
        self.fanout = max(1, int(fanout or 5))
        self.lock = threading.RLock()
        self.nodes: dict[str, dict] = {}
        self.order: list[str] = []      # 保持插入顺序，BFS 取「第一个还没用满的」
        self._load()
        if self.seed and self.seed not in self.nodes:
            # 种子自己也是个节点（它不是本机生成的，used 从 0 起）
            self.nodes[self.seed] = {"code": self.seed, "used": 0,
                                     "parent": None, "children": 0, "seed": True}
            self.order.insert(0, self.seed)
            self._save()

    # ---------- 持久化 ----------

    def _path(self):
        return config.DATA_DIR / "referrals.json"

    def _load(self):
        p = self._path()
        if not p.exists():
            return
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            self.nodes = {n["code"]: n for n in d.get("nodes", []) if n.get("code")}
            self.order = [c for c in d.get("order", []) if c in self.nodes]
            for c in self.nodes:            # order 缺失时兜底
                if c not in self.order:
                    self.order.append(c)
            self.seed = self.seed or d.get("seed") or ""
        except Exception as e:
            log(f"referrals.json 读取失败: {e}")

    def _save(self):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self._path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(
            {"seed": self.seed, "fanout": self.fanout,
             "order": self.order, "nodes": list(self.nodes.values())},
            indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path())

    # ---------- 选上级 / 记账 ----------

    def next_parent(self) -> str | None:
        """BFS 挑一个还没用满 fanout 次的码。全用满了就复用最闲的那个。"""
        with self.lock:
            for code in self.order:
                if self.nodes[code]["used"] < self.fanout:
                    return code
            if not self.order:
                return None
            # 都满了：挑用得最少的，继续扩（树不封顶）
            return min(self.order, key=lambda c: self.nodes[c]["used"])

    def commit(self, parent: str, new_code: str):
        """登记一次成功兑换：parent 用量 +1，new_code 入树等待当上级。"""
        if not new_code:
            return
        with self.lock:
            if parent in self.nodes:
                self.nodes[parent]["used"] += 1
                self.nodes[parent]["children"] += 1
            if new_code not in self.nodes:
                self.nodes[new_code] = {"code": new_code, "used": 0,
                                        "parent": parent, "children": 0}
                self.order.append(new_code)
            self._save()

    def stats(self) -> dict:
        with self.lock:
            depth = {}
            for c in self.order:
                d, n = 0, c
                while self.nodes.get(n, {}).get("parent"):
                    n = self.nodes[n]["parent"]
                    d += 1
                    if d > 100:
                        break
                depth[d] = depth.get(d, 0) + 1
            return {"codes": len(self.nodes),
                    "used_slots": sum(n["used"] for n in self.nodes.values()),
                    "by_level": dict(sorted(depth.items())),
                    "available": sum(1 for n in self.nodes.values()
                                     if n["used"] < self.fanout)}
