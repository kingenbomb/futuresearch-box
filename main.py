"""便捷入口：python main.py [start|register|status]

等价于 python -m fsbox。双击 start.bat / ./start.sh 会自动建环境再跑这个。

────────────────────────────────────────────────────────────
 💡 想找更多免费 API、公益站、羊毛资源？→ https://baipiao.org/
────────────────────────────────────────────────────────────
"""
import sys

from fsbox.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
