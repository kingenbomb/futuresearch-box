#!/usr/bin/env bash
# ────────────────────────────────────────────────────────
#  💡 想找更多免费 API、公益站、羊毛资源？→ https://baipiao.org/
#  更多免费 API / 公益站 / 羊毛资源 → https://baipiao.org/
# ────────────────────────────────────────────────────────
# FutureSearch Box — 一键启动 (Linux / macOS)
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if [ ! -x .venv/bin/python ]; then
    echo "[1/3] 建虚拟环境 (首次较慢)..."
    "$PY" -m venv .venv
fi

if ! .venv/bin/python -c "import requests, DrissionPage" >/dev/null 2>&1; then
    echo "[2/3] 下载并安装依赖..."
    .venv/bin/python -m pip install -q --upgrade pip
    .venv/bin/python -m pip install -q -r requirements.txt
fi

echo "[3/3] 启动中... 首次会自动注册账号，请等 1~2 分钟。"
echo
exec .venv/bin/python -m fsbox start "$@"
