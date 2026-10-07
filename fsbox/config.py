"""配置：data/config.json 持久化，环境变量可覆盖。

首次运行自动生成一份带默认值的 config.json（含随机 API key），
用户改这个文件即可，不需要读源码。

💡 想找更多免费 API、公益站、羊毛资源？→ https://baipiao.org/
"""
import json
import os
import secrets
from pathlib import Path

# 项目根目录（本文件在 <root>/fsbox/config.py）
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.json"
ACCOUNTS_PATH = DATA_DIR / "accounts.jsonl"

# --- 上游端点（2026-10 实测） ---
SUPABASE_URL = "https://iliivszxpymuffgrwsws.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_utrt9HpcpA88JK1noRRBvw_1cNVA5GM"
APP_BASE = "https://futuresearch.ai"
API_BASE = f"{APP_BASE}/api/v0"
TURNSTILE_SITEKEY = "0x4AAAAAAEbuHQEs1muQzWNX"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")

DEFAULTS = {
    # ---- 对外服务 ----
    "host": "127.0.0.1",
    "port": 8000,
    # 用户拿这个 key 调本服务；首次运行随机生成
    "api_key": "",
    # 对外模型名（客户端 model 字段填这个）
    "model_name": "futuresearch-deep",

    # ---- 号池 / 自动补号 ----
    "min_accounts": 2,        # 可用号少于这个数就自动补
    "target_accounts": 5,     # 补到这么多就停
    "max_accounts": 50,       # 号池上限，防跑飞
    "auto_register": True,    # 关掉就只用手动 `register` 命令补号

    # ---- 注册参数 ----
    # 邮箱来源: "local" = 本地造地址(默认, 不真收信, 因为上游 autoconfirm 不发信)
    #           "vip215" = 走 vip.215.im 开真实收件箱(需 email_api_key)
    "email_mode": "local",
    "email_domain": "mailinator.com",   # local 模式用；不用收信，任意域都行
    "email_api_base": "https://maliapi.215.im/v1",   # vip215 模式
    "email_api_key": "",                # 你自己的 key（X-API-Key: AC-...）
    "password": "Test123456!",          # 统一密码；留空则每号随机
    "register_concurrency": 2,          # 过码服务容量有限，别调太高
    "register_retries": 3,              # 单个号的重试次数
    "register_max_fails": 3,            # 补号时连续失败几次就停（防上游满员时狂刷孤儿号）

    # ---- 上游调用 ----
    "effort_level": "high",   # 用默认模型时的档位；low 免费但答案过时
    "task_timeout": 600,      # 单次研究任务墙钟上限(秒)
    # 选了具体底层模型时上游要求补这两个（effort_level 与 llm 互斥）。
    # iteration_budget=0 表示按模型档位自动推（见 models.budget_for）。
    "iteration_budget": 0,
    "include_reasoning": False,

    # ---- 打码 / 网络 ----
    "proxy": "",              # 如 http://user:pass@host:port，空=直连
    "chrome_path": "",        # 空=自动找本机 Chrome
    "solver_headless": True,  # 隐形运行（有头但窗口挪到屏幕外）

    # ---- 广告 / 署名（文案在 fsbox/ad.py，这里只控制开关）----
    "ad_enabled": True,       # 总开关，false = 全部不投
    "ad_prompt": True,        # 提示词广告：让上游 agent 在回答里提一句来源
    "ad_footer": "",          # 结果署名用自定义文案；留空用 ad.py 里的默认
}


def _random_key() -> str:
    return "sk-fsbox-" + secrets.token_urlsafe(24)


def load() -> dict:
    """读配置；文件不存在就写一份默认的。环境变量可覆盖任意键（FSBOX_<大写>）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[config] {CONFIG_PATH} 解析失败，用默认值: {e}")
    if not cfg.get("api_key"):
        cfg["api_key"] = _random_key()
        save(cfg)
    elif not CONFIG_PATH.exists():
        save(cfg)

    # 环境变量覆盖（部署用）：FSBOX_PORT / FSBOX_API_KEY / FSBOX_PROXY ...
    for k in list(DEFAULTS):
        env = os.environ.get("FSBOX_" + k.upper())
        if env is None:
            continue
        cur = DEFAULTS[k]
        try:
            if isinstance(cur, bool):
                cfg[k] = env not in ("0", "false", "False", "")
            elif isinstance(cur, int):
                cfg[k] = int(env)
            else:
                cfg[k] = env
        except ValueError:
            pass
    return cfg


def save(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)
