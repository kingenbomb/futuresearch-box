"""便捷入口：python main.py [start|register|status]

等价于 python -m fsbox。双击 start.bat / ./start.sh 会自动建环境再跑这个。

────────────────────────────────────────────────────────────
 白嫖站 · https://baipiao.org/  —— 免费 API / 公益站 / 羊毛资源
 本程序由「白嫖站」免费开源，转载/分发请保留此行署名。
────────────────────────────────────────────────────────────
"""
import sys

from fsbox.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
