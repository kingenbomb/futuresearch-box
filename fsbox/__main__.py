"""CLI 入口：python -m fsbox [start|register|status]

    start     启动 OpenAI 兼容服务，打印 Base URL + API Key（默认）
    register  先手动补 N 个号（不启动服务）
    status    看号池现状
"""
import argparse
import sys

from . import config
from .pool import Pool, log


def _banner(cfg: dict):
    print("=" * 62)
    print("  FutureSearch Box")
    print("=" * 62)


def cmd_start(args, cfg):
    from . import ad, server

    def ready(base):
        _banner(cfg)
        print(ad.banner())            # ← 广告位
        print("-" * 62)
        print(f"  模型名   : {cfg['model_name']}")
        print(f"  Base URL : {base}")
        print(f"  API Key  : {cfg['api_key']}")
        print(f"  号池     : 下限 {cfg['min_accounts']} / 目标 {cfg['target_accounts']}"
              f"  (自动补号: {'开' if cfg.get('auto_register') else '关'})")
        print(f"  上游档位 : effort={cfg['effort_level']}  (high 约 $0.40/次)")
        print("-" * 62)
        print("  用任意 OpenAI 客户端连：")
        print(f"    base_url = \"{base}\"")
        print(f"    api_key  = \"{cfg['api_key']}\"")
        print(f"    model    = \"{cfg['model_name']}\"")
        print("-" * 62)
        print("  号池为空时会自动注册（第一次要等 1~2 分钟）。Ctrl+C 退出。")
        print()

    server.serve(cfg, on_ready=ready)


def cmd_register(args, cfg):
    _banner(cfg)
    pool = Pool(cfg)
    n = args.count if args.count else int(cfg.get("target_accounts", 5))
    have = len(pool.usable())
    want = max(0, n - have)
    print(f"  现有可用 {have} 个，本次补 {want} 个\n")
    if want == 0:
        print("  已达数量，无需补号。")
        return 0
    added = pool.replenish(target=n)
    print(f"\n  本次新增 {added} 个。号池现状: {pool.status()}")
    return 0


def cmd_status(args, cfg):
    pool = Pool(cfg)
    st = pool.status()
    _banner(cfg)
    print(f"  可用 {st['usable']} / 共 {st['total']} 个"
          f"  余额合计 ${st['balance_usd']}")
    print(f"  分状态: {st['by_status']}")
    if st["registering"]:
        print(f"  正在注册: {st['registering']} 个")
    for a in pool.accounts[-10:]:
        print(f"    {a.get('status'):<9} {a.get('email'):<44} "
              f"{a.get('api_key','')[:18]}…  ${a.get('balance')}")
    return 0


def cmd_models(args, cfg):
    from . import models as M
    if args.refresh:
        return M._refresh()

    q = (args.search or "").strip().lower()
    rows = []
    if not args.aliases_only:
        for v in M.LLM_ENUM:
            rows.append((M.CANONICAL[v], v, False))
    for a, e in M.ALIASES.items():
        rows.append((a, e, True))
    if q:
        rows = [r for r in rows if q in r[0].lower() or q in r[1].lower()]

    _banner(cfg)
    filt = f"  过滤 {q!r} → {len(rows)} 条" if q else ""
    print(f"  底层模型 {len(M.LLM_ENUM)} 个 · 别名 {len(M.ALIASES)} 个{filt}")
    print()

    # 按家族分组，读起来清楚
    groups = {}
    for cid, enum, is_alias in rows:
        groups.setdefault(enum.split("_")[0], []).append((cid, enum))
    for fam in sorted(groups):
        items = groups[fam]
        print(f"  ── {fam} ({len(items)}) ──")
        for cid, enum in items:
            star = "*" if enum in M.ALIASES.values() else " "
            print(f"   {star} {cid:<26} = {enum}")
        print()
    print("  * = 别名，直接填进 model 字段即可：")
    print('      client.chat.completions.create(model="claude-opus-5.5", messages=[...])')
    print(f"  默认（不选底层模型）: {M.DEFAULT_MODEL}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fsbox", description="FutureSearch Box")
    sub = ap.add_subparsers(dest="cmd")
    p_start = sub.add_parser("start", help="启动服务（默认）")
    p_start.set_defaults(fn=cmd_start)
    p_reg = sub.add_parser("register", help="手动补号")
    p_reg.add_argument("count", nargs="?", type=int, default=0,
                       help="目标可用号数量（默认 config.target_accounts）")
    p_reg.set_defaults(fn=cmd_register)
    p_st = sub.add_parser("status", help="号池现状")
    p_st.set_defaults(fn=cmd_status)
    p_md = sub.add_parser("models", help="列出/搜索可选的底层模型")
    p_md.add_argument("search", nargs="?", default="",
                      help="关键词过滤，如 opus / gpt / gemini")
    p_md.add_argument("--aliases-only", action="store_true", help="只看别名")
    p_md.add_argument("--refresh", action="store_true",
                      help="从上游 spec 重新拉清单并对比（需号池里有可用 key）")
    p_md.set_defaults(fn=cmd_models)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        args.fn = cmd_start
    cfg = config.load()
    return args.fn(args, cfg) or 0


if __name__ == "__main__":
    sys.exit(main())
