"""CLI 入口：python -m fsbox [bootstrap|start|register|status]

    bootstrap  start.bat 用：交互问注册数量 → 带日志注册 → 自动起网关
    start      启动 OpenAI 兼容服务，打印 Base URL + API Key
    register   先手动补 N 个号（不启动服务）
    status     看号池现状

💡 想找更多免费 API、公益站、羊毛资源？→ https://baipiao.org/
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
    from . import ad, models, server

    def ready(base):
        panel_url = base.replace("/v1", "/panel")
        n_models = len(models.list_models())
        _banner(cfg)
        print(ad.banner())            # ← 广告位
        print("-" * 62)
        print(f"  号池面板 : {panel_url}")
        print(f"  默认模型 : {cfg['model_name']}   (可选 {n_models} 个，见下)")
        print(f"  Base URL : {base}")
        print(f"  API Key  : {cfg['api_key']}")
        print(f"  号池     : 下限 {cfg['min_accounts']} / 目标 {cfg['target_accounts']}"
              f"  (自动补号: {'开' if cfg.get('auto_register') else '关'})")
        print(f"  上游档位 : effort={cfg['effort_level']}  (high 约 $0.40/次)")
        print("-" * 62)
        print("  用任意 OpenAI 客户端连：")
        print(f"    base_url = \"{base}\"")
        print(f"    api_key  = \"{cfg['api_key']}\"")
        print(f"    model    = \"{cfg['model_name']}\"      # 默认：上游研究 agent")
        print("               也可以点名底层模型，例如：")
        print("                 \"claude-opus-5.5\"  \"claude-opus-5.5-max\"  \"gpt-5.5\"")
        print("                 \"gemini-3.1-pro\"    \"claude-sonnet-5\"      \"grok-4.5\" …")
        print("-" * 62)
        print(f"  列全部 {n_models} 个可选模型 : python main.py models [关键词]")
        print(f"  浏览器看号池         : {panel_url}  (首次让你填上面的 API Key)")
        print("  Ctrl+C 退出。")
        print()
        sys.stdout.flush()      # 重定向到文件时 stdout 是块缓冲，不刷会看不到

    server.serve(cfg, on_ready=ready)


def cmd_bootstrap(args, cfg):
    """start.bat 的入口：交互问注册数量 → 带日志注册 → 自动起网关。"""
    from . import ad
    _banner(cfg)
    print(ad.banner())
    print("-" * 62)

    pool = Pool(cfg)
    have = len(pool.usable())
    print(f"  当前号池: 可用 {have} 个 / 共 {len(pool.accounts)} 个")
    print(f"  每个号自带 $20 额度（effort=high 约 $0.40/次，一号约 50 次）")
    print()

    raw = ""
    try:
        raw = input("  要注册几个账号？[回车=默认 {}，输 0 = 跳过直接启动] > "
                    .format(cfg.get("target_accounts", 5))).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消。")
        return 1

    if raw == "":
        want = int(cfg.get("target_accounts", 5))
    else:
        try:
            want = max(0, int(raw))
        except ValueError:
            print("  不是数字，按跳过处理。")
            want = 0

    if want > 0:
        cap = int(cfg.get("max_accounts", 50))
        if len(pool.accounts) + want > cap:
            want = max(0, cap - len(pool.accounts))
            print(f"  超出号池上限 {cap}，本次只注册 {want} 个。")
        print()
        print(f"  开始注册 {want} 个（每个约 30~60 秒，过 Turnstile 是唯一慢步骤）")
        print(f"  日志前缀 [pool] 是单个号的过程，失败会自动退避重试。")
        print("-" * 62)
        try:
            added = pool.replenish(target=have + want)
        except KeyboardInterrupt:
            print("\n  注册被中断，已成功的号都保留在号池里。")
            added = 0
        print("-" * 62)
        print(f"  本次新增 {added} 个")

    st = Pool(cfg).status()      # 重新读盘，拿最新状态
    print(f"  号池现状: 可用 {st['usable']} / 共 {st['total']}"
          f"  · 余额合计 ${st['balance_usd']}")
    print()

    # 网关自动启动（不阻塞在这里，直接进 serve）
    return cmd_start(args, cfg)


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


def cmd_referrals(args, cfg):
    """看邀请码树：多少码、铺到第几层、哪些还能当上级。"""
    from .referral import ReferralTree
    seed = (cfg.get("referral_seed_code") or "").strip()
    _banner(cfg)
    if not seed:
        print("  未启用：config.json 里 referral_seed_code 还是空的。")
        print("  填上顶层种子码（比如 N55DhsZUV2Mo）后，注册的号会自动")
        print("  兑上级码 → 生成自己的码 → 成为下批号的上级。")
        return 0
    tree = ReferralTree(seed, cfg.get("referral_fanout", 5))
    st = tree.stats()
    print(f"  种子码   : {seed}")
    print(f"  扇出上限 : {tree.fanout} 次/码")
    print(f"  码总数   : {st['codes']}  可用作上级: {st['available']}")
    print(f"  已用槽位 : {st['used_slots']}")
    print(f"  层级分布 : {st['by_level']}")
    print()
    print("  ── 树 ──")
    for code in tree.order:
        n = tree.nodes[code]
        parent = n.get("parent") or "(种子)"
        mark = "★" if n.get("seed") else " "
        print(f"   {mark} {code:<16} ← {parent:<16} 已带 {n['used']}/{tree.fanout}")
    print()
    print("  ★ = 种子码。下一批号的上级按 BFS 选：先用满上层，再铺下层。")
    return 0


def main(argv=None):
    # Windows 控制台默认 GBK，打印中文/emoji 会 UnicodeEncodeError。
    # 统一重编码成 UTF-8（中文 Windows + chcp 936 下直接跑 main.py 也不会崩）。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

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
    p_bs = sub.add_parser("bootstrap", help="交互式：问注册数量 → 带日志注册 → 自动起网关")
    p_bs.set_defaults(fn=cmd_bootstrap)
    p_rf = sub.add_parser("referrals", help="看邀请码树")
    p_rf.set_defaults(fn=cmd_referrals)
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
