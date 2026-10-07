"""Cloudflare Turnstile 打码 —— 接管本机真实 Chrome（DrissionPage）。

为什么不能省：FutureSearch 的邮箱注册虽然免收信，但号建出来是 waitlist 状态，
必须过一次 Turnstile 激活才能用。这一步是整条链路唯一的浏览器环节。

踩过的坑（都在代码里）：
  1. 必须真实 Chrome。Playwright 自带 chromium 指纹脏，会被判 failed。
  2. widget 的 iframe 在 closed shadow DOM，JS 看不见；但隐藏 input
     `input[name=cf-turnstile-response]` 在 light DOM，沿它父链上溯找约 300x65
     的祖先 = widget 容器。
  3. iframe 内 checkbox 是 100% 透明覆盖整个 widget，点容器中心屏幕坐标即命中。
     每 >=2.5s 点一次，别狂点。
  4. **widget 只在「已登录但未激活」的关卡页渲染** —— 干净浏览器打开首页永远
     定位不到。所以要先带上会话 cookie 再进站（cookies 参数）。
  5. 不能真 headless（CF 会检测导致超时）。用「有头 + 窗口挪到屏幕外 + 隐藏」。


白嫖站 · https://baipiao.org/  —— 免费 API / 公益站 / 羊毛资源
"""
import json
import time
from urllib.parse import urlparse

from DrissionPage import ChromiumOptions, ChromiumPage

# 沿 cf-turnstile-response 的父链上溯，返回 widget 容器可视矩形（屏幕坐标）。
_LOCATE_WIDGET_JS = """
var ci = document.querySelector('input[name="cf-turnstile-response"]')
      || document.querySelector('[id^=cf-chl-widget]');
if (!ci) return JSON.stringify({found: false, reason: 'no-input'});
var p = ci.parentElement;
for (var k = 0; k < 6 && p; k++) {
  var r = p.getBoundingClientRect();
  if (r.width >= 200 && r.width <= 400 && r.height >= 40 && r.height <= 110) {
    return JSON.stringify({found: true, x: r.x, y: r.y, w: r.width, h: r.height});
  }
  p = p.parentElement;
}
return JSON.stringify({found: false, reason: 'no-sized-ancestor'});
"""

# 过验证后 CF 把 token 写进这个 input。
_READ_TOKEN_JS = """
var el = document.querySelector('input[name="cf-turnstile-response"]');
return el ? el.value : '';
"""


class SolveError(RuntimeError):
    """过验证失败（超时、widget 未渲染、定位不到等）。"""


class TurnstileSolver:
    """一次任务一个独立浏览器实例。推荐用 with 语句。

        with TurnstileSolver() as s:
            token = s.solve("https://futuresearch.ai/app", cookies={"sb-...": "..."})
    """

    def __init__(self, headless_hide=True, proxy=None, chrome_path=None):
        self.headless_hide = headless_hide
        self.proxy = proxy
        self.chrome_path = chrome_path
        self.page = None

    def __enter__(self):
        self._launch()
        return self

    def __exit__(self, *exc):
        self.close()

    def _launch(self):
        co = ChromiumOptions()
        # auto_port：独立用户数据目录 + 随机端口，多个实例互不干扰
        co.auto_port()
        if self.chrome_path:
            co.set_browser_path(self.chrome_path)
        if self.proxy:
            co.set_proxy(self.proxy)
        co.set_argument("--no-first-run")
        co.set_argument("--no-default-browser-check")
        if self.headless_hide:
            # 挪到屏幕外（不是 headless，否则 CF 拒发 token）
            co.set_argument("--window-position=-32000,-32000")
        self.page = ChromiumPage(co)
        if self.headless_hide:
            try:
                self.page.set.window.hide()
            except Exception:
                pass  # 隐藏失败不影响功能，窗口已在屏幕外

    def close(self):
        if self.page is not None:
            try:
                self.page.quit()
            except Exception:
                pass
            self.page = None

    def _locate_widget(self):
        raw = self.page.run_js(_LOCATE_WIDGET_JS)
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {"found": False, "reason": "bad-json"}

    def _read_token(self):
        return self.page.run_js(_READ_TOKEN_JS) or ""

    def _human_click(self, cx, cy):
        try:
            self.page.actions.move_to((cx - 40, cy + 15)) \
                .move_to((cx, cy), duration=0.2).click()
        except Exception:
            self.page.actions.move_to((cx, cy)).click()

    def solve(self, url, sitekey=None, timeout=90, poll_interval=2.5, cookies=None):
        """打开 url 过掉 Turnstile，返回 token 字符串。

        cookies: 可选 {name: value}，导航前注入目标域 —— widget 只在登录后渲染的
                 站点必须传（FutureSearch 就是）。
        """
        if self.page is None:
            raise SolveError("浏览器未启动")

        if cookies:
            parts = urlparse(url)
            try:
                self.page.get(f"{parts.scheme}://{parts.netloc}/")
                self.page.set.cookies([
                    {"name": k, "value": v, "domain": parts.netloc, "path": "/"}
                    for k, v in cookies.items()
                ])
            except Exception as e:
                raise SolveError(f"注入 cookies 失败: {e}")

        self.page.get(url)
        try:
            self.page.wait.doc_loaded(timeout=15)
        except Exception:
            pass
        time.sleep(1.0)

        deadline = time.time() + timeout
        last_click = 0.0
        widget_seen = False
        while time.time() < deadline:
            token = self._read_token()
            if token:                       # 环境信誉够时 CF 直接发，不用点
                return token
            info = self._locate_widget()
            if info.get("found"):
                widget_seen = True
                now = time.time()
                if now - last_click >= poll_interval:
                    # checkbox 恒定靠左，距左缘约 30px、垂直居中
                    self._human_click(info["x"] + 30, info["y"] + info["h"] / 2)
                    last_click = now
            time.sleep(0.4)

        token = self._read_token()          # 超时兜底再读一次
        if token:
            return token
        reason = "token 超时未回填"
        if not widget_seen:
            reason += "（始终未定位到 widget —— 页面可能需先登录才渲染，"
            reason += "或本机 IP 已被风控）"
        raise SolveError(reason)
