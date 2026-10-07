#!/usr/bin/env bash
# ────────────────────────────────────────────────────────
#  白嫖站 · https://baipiao.org/  —— 免费 API / 公益站 / 羊毛资源
#  本程序由「白嫖站」免费开源。
# ────────────────────────────────────────────────────────
# 只装依赖，不启动服务
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
echo "装好了。跑 ./start.sh 启动。"
