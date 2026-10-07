# FutureSearch Box

> 💡 **想找更多免费 API、公益站、羊毛资源？去 [baipiao.org](https://baipiao.org/) 看看。**

把 [FutureSearch](https://futuresearch.ai/) 的深度研究能力，变成一个**本地开箱即用的 OpenAI 兼容接口**。

自动注册账号 → 自动过 Cloudflare Turnstile → 自动补号 → 对外提供一个 `base_url` + `api_key`，
任何 OpenAI 客户端填上就能用。

```
你 ──(OpenAI 协议)──► FutureSearch Box ──(账号池轮换)──► futuresearch.ai
```

## 一键使用

**Windows**：双击 `start.bat`
**Linux / macOS**：`./start.sh`

脚本自动建虚拟环境、装依赖、检查 Chrome，然后**交互问你注册几个账号**：

```
  当前号池: 可用 0 个 / 共 0 个
  每个号自带 $20 额度（effort=high 约 $0.40/次，一号约 50 次）

  要注册几个账号？[回车=默认 5，输 0 = 跳过直接启动] > 5

  开始注册 5 个（每个约 30~60 秒，过 Turnstile 是唯一慢步骤）
[pool] 新号入库: fs1791…@mailinator.com (sk-cho-…)
  ...
  本次新增 5 个
  号池现状: 可用 5 / 共 5  · 余额合计 $100.0
```

注册过程**实时打日志**（每个号的尝试/失败/退避都可见）。注册完**网关自动启动**：

```
  模型名   : futuresearch-deep
  Base URL : http://127.0.0.1:8000/v1
  API Key  : sk-fsbox-xxxxxxxx
```

拿这三样填进任意 OpenAI 客户端即可。之后不用再手动注册 —— 号池低于下限会自动补。

> 跳过交互直接起服务：`python main.py start`
> 只补号不起服务：`python main.py register 10`

## 前置条件

只有一个：**本机装了 Google Chrome**。

过 Turnstile 必须用真实 Chrome 的指纹（Playwright 自带的 chromium 会被判失败），
所以请装 Chrome，Edge / 其他浏览器不行。Chrome 装好后本项目会自动找到它。

## 调用示例

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="sk-fsbox-xxxx")
r = client.chat.completions.create(
    model="futuresearch-deep",
    messages=[{"role": "user", "content": "Anthropic 最新一轮融资的估值是多少？给出来源。"}],
)
print(r.choices[0].message.content)
```

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-fsbox-xxxx" -H "Content-Type: application/json" \
  -d '{"model":"futuresearch-deep","messages":[{"role":"user","content":"2+2=?"}]}'
```

## 这是什么 / 不是什么

**是**：一个把 FutureSearch 的**异步研究任务**包成同步对话的网关。它把用户的问题
丢给 FutureSearch 的 `single-agent`，等它检索+推理完（通常 20 秒~几分钟），把最终
答案作为一条 assistant 消息返回。

**不是**：不是 Claude/GPT 的 token 中转。上游按**次**计费（每个号 $20 一次性额度），
没有 token 概念。所以：

| | 说明 |
|---|---|
| 速度 | 慢，单次几十秒到几分钟（深度研究会真的去查资料） |
| 计费 | 上游按次扣美元；本服务对你的用量只是**估算** token 数给你看 |
| 并发 | 受号池大小限制，号用完了会自动补 |
| 流式 | 支持 SSE 外壳，但内容是一次性给的（上游不是流式） |

## 号池面板

服务起来后浏览器打开 **http://127.0.0.1:8000/panel** —— 卡片式看每个号的情况：

- 每个号一张卡：`FutureSearch` 徽标、邮箱、UID、**可用积分**、状态、邮箱来源
- 四个按钮：`✓` 健康检查（真打一次上游 `/billing`）、`↻` 刷新余额、`⏸` 暂停/启用、`✕` 删除
- 顶栏：`刷新` / **`一键刷新所有号`**（并发打上游，实测 2 个号 1 秒完）/ `补号`
- 筛选栏：**排序**（积分 ↓/↑、状态、邮箱、最近注册）+ **状态筛选** + **邮箱/UID 搜索**
- 单个号检查中会置灰；页面每 15 秒自动刷新（有任务在跑时暂停轮询，免得打断）

**积分怎么来的**：上游（REST API / 网页端 / MCP 三处都查过）**只有美元余额**，没有独立的
积分字段 —— MCP 的 `futuresearch_balance` 也返回 `"Current balance: $19.03"`。所以面板按
**1 积分 = 1 美分**（`余额 × 100`）换算，$20 → 2000 积分。倍率在 `panel.py` 的
`CREDITS_PER_DOLLAR`，要改改那一行。

首次打开会让你填 API Key（就是 `data/config.json` 里的 `api_key`），存在浏览器本地。
面板数据接口也吃这个 key，所以别把服务暴露到公网。

## 选底层模型

上游支持挑具体模型（OpenAPI 的 `LLMEnumPublic`，共 **143 个**：Claude 4.5~5.5 / Fable 5、
GPT 5~6.1 / Gemini 3.x / GLM / Grok / Muse）。`GET /v1/models` 会列出全部，客户端
把 `model` 换成想用的那个即可：

```python
# 走默认研究 agent（不指定底层模型）
client.chat.completions.create(model="futuresearch-deep", messages=[...])

# 指定用 Claude Opus 5.5 跑
client.chat.completions.create(model="claude-opus-5.5", messages=[...])

# 指定档位（max 迭代更多、更慢更贵）
client.chat.completions.create(model="claude-opus-5.5-max", messages=[...])
```

三种写法都认：

| 写法 | 例 |
|---|---|
| 亲民别名 | `claude-opus-5.5`、`gpt-5.5`、`gemini-3.1-pro`、`grok-4.5` |
| 规范形式（枚举小写） | `claude-5-5-opus-max`、`gpt-5-6-sol-high` |
| 原样枚举 | `CLAUDE_5_5_OPUS_MAX` |

**注意**：`effort_level` 和 `llm` 上游是**互斥**的 —— 用别名选模型时，服务会自动按模型
档位后缀推 `iteration_budget`（`_HIGH`→35、`_MAX`→60…），不再发 `effort_level`。
想手动定就设 `iteration_budget`（0=自动）。

## 邀请码树

填了 `referral_seed_code` 后，每个新号会**自动**跑：**兑换上级码 → 生成自己的码 →
自己的码成为下一批号的上级**，如此逐层铺开：

```
种子码 N55DhsZUV2Mo          ← referral_seed_code
  ├─ 号1  兑种子 → 生成 Grqv5Jyq8fJ8
  │    └─ 号6  兑 Grqv5Jyq8fJ8 → 生成 49zIBrbIY33l    ← 第 3 层
  ├─ 号2  兑种子 → 生成 T8C7Z7oXt3z
  ├─ 号3 …
  └─ 号5 …
```

选上级用 **BFS**：一个码最多当 5 次上级（`referral_fanout`），用满才轮到下一层 ——
所以种子的前 5 个号是兄弟，第 6 个才开始用 gen1 的码。

```bash
python main.py referrals     # 看整棵树：几层、每个码带过几个号
```

实测输出：

```
  码总数   : 7   可用作上级: 6
  层级分布 : {0: 1, 1: 5, 2: 1}

   ★ N55DhsZUV2Mo     ← (种子)         已带 5/5
     Grqv5Jyq8fJ8     ← N55DhsZUV2Mo   已带 1/5
     49zIBrbIY33l     ← Grqv5Jyq8fJ8   已带 0/5
```

树状态存在 `data/referrals.json`，跨次运行累积。

> ⚠️ **兑到的是「首次订阅折扣券」，不是免费额度** —— 兑完号的 `tier` 仍是 free、
> `$20` 余额不变，券只在真正订阅时抵扣。邀请方那份还要等对方**付费**才结算，且
> 官方写明「人工审核防刷、每月限量」。所以这条链**刷不出额度**，只能给将来的订阅省钱。
>
> 邀请码整段是「尽力而为」：拿不到不影响号本身注册成功。

## 邮箱来源

| `email_mode` | 行为 |
|---|---|
| `local`（默认） | 本地造地址 `fs<时间戳><随机>@mailinator.com`。因为上游 `autoconfirm` **不发确认信**，收不收信无所谓 |
| `vip215` | 走 [vip.215.im](https://vip.215.im/docs) 开真实收件箱，需自配 `email_api_key`（`X-API-Key: AC-...`）。好处：用真域名而非临时域，风控友好，且真能收信（将来做验证/找回密码用得上） |

`vip215` 开箱失败会自动退回 `local`，不会让注册崩掉。

## 命令

```bash
python main.py start              # 启动服务（默认）
python main.py register 10        # 手动补到 10 个号
python main.py status             # 看号池现状
```

## 配置

所有配置在 `data/config.json`（首次运行自动生成）。常改的几个：

| 键 | 默认 | 说明 |
|---|---|---|
| `port` | 8000 | 服务端口 |
| `api_key` | 随机生成 | **对外的 key**，可以改成你喜欢的 |
| `model_name` | futuresearch-deep | 对外模型名 |
| `min_accounts` | 2 | 可用号少于这个数自动补 |
| `target_accounts` | 5 | 补到这么多就停 |
| `auto_register` | true | 关掉就只手动 `register` |
| `effort_level` | high | 走默认模型时的档位：`high` 答案带引用（约 $0.40/次）；`low` 免费但答案质量差 |
| `iteration_budget` | 0 | 选具体模型时的迭代预算；0=按模型档位自动推 |
| `include_reasoning` | false | 选具体模型时是否把推理过程带进回答 |
| `email_mode` | local | `local`=本地造地址 / `vip215`=走 vip.215.im 开真实收件箱 |
| `email_api_key` | 空 | `vip215` 模式用的 key（`AC-...`） |
| `register_retries` | 3 | 单个号的重试次数（过码被拒会退避重试） |
| `register_max_fails` | 3 | 补号时**连续失败几次就停** —— 上游满员/风控时不至于狂刷孤儿号 |
| `referral_seed_code` | 空 | 顶层种子邀请码；**填了才启用**邀请码树（见上节） |
| `referral_fanout` | 5 | 一个邀请码最多当几次上级 |
| `proxy` | 空 | 如 `http://user:pass@host:port`。注册被风控时挂代理换 IP |
| `chrome_path` | 空 | 空 = 自动找 Chrome |

环境变量可覆盖任意键：`FSBOX_PORT=9000`、`FSBOX_PROXY=...` 等。

## 号池与成本

每个注册出来的号自带 **$20 一次性额度**，按 `effort_level=high`（约 $0.40/次）算，
**一个号大约能跑 50 次**。号池耗尽会自动补新号。

`python main.py status` 能看到每个号的余额和状态。

## 排错

| 现象 | 原因 / 处理 |
|---|---|
| 起不来，说没找到 Chrome | 装 Google Chrome（不是 Edge） |
| 一直补不到号 | 本机 IP 被风控了 —— 在 `config.json` 里挂 `proxy` 换 IP |
| 日志报 `captcha_failed` | 同上，换 IP；或等一会儿再试 |
| 日志报 `at_capacity` | 上游激活容量满，不是号坏了 —— 等一阵再补，或换 IP |
| 补号刷了几次就自己停了 | 正常：连续失败达 `register_max_fails`（默认 3）会停，防止刷孤儿号 |
| 请求很慢 | 正常，深度研究要几十秒到几分钟 |
| 返回 500 `号池里没有可用号` | 补号还没完成或全挂了，看日志；先 `python main.py register 3` |

## 目录结构

```
futuresearch-box/
├── start.bat / start.sh     # 一键启动（建环境+装依赖+跑）
├── setup.bat / setup.sh     # 只装依赖
├── main.py                  # 便捷入口
├── requirements.txt
├── fsbox/
│   ├── config.py            # 配置读写
│   ├── turnstile.py         # 自带打码（DrissionPage 接管真实 Chrome）
│   ├── futuresearch.py      # 上游接口：注册/激活/造 key/跑研究
│   ├── pool.py              # 号池 + 自动补号
│   ├── server.py            # OpenAI 兼容 HTTP 服务
│   └── __main__.py          # CLI
└── data/                    # 运行时生成（config.json / accounts.jsonl）
```

## 免责声明

- 本项目**仅用于技术研究与学习**，**无任何商业用途**。
- 本项目**不是** FutureSearch 官方产品，与 FutureSearch / Anthropic / OpenAI / Google 等**均无任何关联**，也未获得其授权或认可。
- 本项目通过公开接口访问上游服务。**批量注册账号可能违反上游服务条款**，账号可能被限制或封禁，由此产生的一切后果**由使用者自行承担**。
- 使用者应自行确保其使用行为符合所在地法律法规及上游服务条款。**因使用本项目产生的任何直接或间接损失，作者概不负责。**
- 请勿将本服务暴露到公网、勿用于商业转售；上游额度用尽/被封与作者无关。
- 本项目按「现状」提供，不附带任何明示或暗示的担保。
