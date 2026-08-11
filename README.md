# xhs-scraper

一个面向 Agent 的小红书关键词采集 Skill。它编排兼容的 `xiaohongshu-cli`，按指定筛选条件
取得每个关键词前 N 个帖子，抓取帖子信息、顶层评论和子评论，并在数据完整后同步到业务 API。

> 本仓库只包含 Skill，不包含抓取器。实际执行依赖带有 `scrape_and_sync.py` 的
> `xiaohongshu-cli` 项目。

## 运行时组件

- `scrape_and_sync.py`：关键词搜索、全局去重、帖子读取、分轮恢复和完整性判定的唯一生产入口。
- `sync_xhs.py`：被入口导入，提供两层评论 checkpoint、payload 构造和业务 API 上传适配；其内置
  的示例帖子批处理仅为历史兼容，不要直接运行。
- `fetch_comments.py`、`fetch_sub_comments.py`：本地遗留的手动恢复脚本，不是生产入口。前者只
  翻顶层评论页，后者虽尝试两层翻页但没有 checkpoint、去重和完整性判定。不要在未确认没有外部
  调用前直接删除；新任务一律使用 `scrape_and_sync.py`。

## 能做什么

- 多关键词搜索，并保持小红书 API 返回顺序
- 每个关键词取得前 N 个唯一帖子；跨关键词相同帖子只抓取、上传一次
- 采集 `note_id`、标题、作者、正文、图片、发布时间、点赞数、评论总数
- 分页采集全部可访问的顶层评论与子评论
- 按 comment ID 去重，检测重复 cursor
- 中断后从 manifest 和评论 cursor 继续
- 评论完整后才上传，避免部分数据覆盖完整数据
- 对验证码、429、登录失效和 IP 拦截采取停止策略

“全部评论”指持续分页直到平台响应表示完成。帖子删除、权限限制或真实风控信号仍可能导致
`unavailable` 或 `partial`，Skill 不会把这些状态伪装成完成。

## 工作方式

```text
关键词 + 每词数量 + 筛选条件
  → 签名 Web API 搜索
  → 帖子详情
  → 顶层评论分页
  → 子评论分页
  → 完整性检查
  → 业务 API
```

正常抓取不操作网页 DOM，也不使用 Playwright、Selenium 或其他自动化浏览器。
Kimi WebBridge 仅用于首次导入真实 Chrome 中的 Cookie、会话失效后的重新登录，以及用户人工
处理登录/验证码；它不是降风控手段，有有效 saved 会话时不需要在每轮运行前调用。

## 安装

```bash
git clone https://github.com/3Rwcolxxxsar/xhs-scraper.git \
  ~/.hermes/skills/xhs-scraper
```

准备兼容的 `xiaohongshu-cli` 后设置：

```bash
export XHS_CLI_DIR="/path/to/xiaohongshu-cli"
export XHS_SYNC_API_URL="https://your-api.example/xhs/sync"
```

`XHS_CLI_DIR` 中必须存在 `scrape_and_sync.py`、`pyproject.toml` 和 `xhs_cli/`。
API 地址不保存在本仓库中。

## 告诉 Agent 怎么抓

示例提示词：

```text
使用 xhs-scraper：
关键词：美国货代、加拿大货代
每个关键词：5 个帖子
排序：综合
发布时间：不限
搜索范围：不限
完整抓取顶层评论和子评论，完成后同步到已配置 API。
```

筛选值：

| 维度 | 可用值 |
| --- | --- |
| 排序 | `general`、`latest`、`most-liked`、`most-commented`、`most-collected` |
| 发布时间 | `all`、`day`、`week`、`half-year` |
| 搜索范围 | `all`、`seen`、`unseen`、`followed` |

Agent 最终会执行类似命令：

```bash
cd "$XHS_CLI_DIR"
uv run python scrape_and_sync.py \
  --cookie-source saved \
  --keyword "美国货代" \
  --keyword "加拿大货代" \
  --limit 5 \
  --sort general --time all --scope all \
  --api-url "$XHS_SYNC_API_URL" \
  --max-notes-per-run 2 \
  --max-comment-pages-per-run 8
```

相同参数再次运行会恢复同一任务。`partial` 表示可恢复的未完成进度，不表示跳过；只有评论
完整的帖子才会进入 `uploaded`。运行数据保存在 CLI 项目的 `.xhs-scrape-runs/`，文件权限为
`0600`。

`--dry-run` 仅表示不向业务 API 上传；搜索、详情和评论测试仍会向小红书发出真实请求并受相同
风控规则约束。

## 登录与账号

先使用 `--cookie-source saved` 检查本地保存会话。只有会话缺失或失效时，才连接当前
WebBridge Chrome Profile 导入 Cookie。当前 CLI 使用单个 WebBridge daemon；多 Profile
需要逐个切换并导入到不同 `--account`，不能通过配置多个 WebSocket 端口实现并行浏览器控制。

不同账号可各运行一个互不重叠的 API 任务，但不得在触发风控后自动换号继续。多个账号也可能
共享公网 IP 和设备环境，账号隔离不等于风险隔离。

## 风控与断点

默认请求间隔来自 CLI：搜索 3–10 秒、帖子详情 8–24 秒、评论分页 5–12 秒。默认不按固定请求
数强制休息；30 分钟只是在验证码、429、登录失效、IP 拦截或连续失败触发暂停时使用的默认
冷却值。

每轮默认最多尝试 2 个未完成帖子、最多请求 8 页评论。页预算不是评论截断；重新执行相同命令
会从保存的 cursor 继续。不得因为帖子评论数超过 300 或其他阈值而跳过。

## 调整风控参数

风控参数由运行环境读取；在启动命令前设置即可生效，不需要改脚本。默认值是保守起点，随机
等待只能降低请求密度，不能保证不会触发平台限制。触发验证码、429、登录失效、IP 拦截或连续
失败时，任务仍会停止。

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `XHS_RISK_ENABLED` | `1` | 是否启用限频、暂停和同账号单实例锁；仅本地自动化测试可设为 `0`。 |
| `XHS_RISK_SEARCH_MIN_SECONDS` / `XHS_RISK_SEARCH_MAX_SECONDS` | `3` / `10` | 两次搜索请求间的随机等待范围（秒）。 |
| `XHS_RISK_NOTE_MIN_SECONDS` / `XHS_RISK_NOTE_MAX_SECONDS` | `8` / `24` | 帖子详情及其他非搜索、非评论请求的随机等待范围（秒）。 |
| `XHS_RISK_COMMENT_MIN_SECONDS` / `XHS_RISK_COMMENT_MAX_SECONDS` | `5` / `12` | 顶层评论与子评论分页请求的随机等待范围（秒）。 |
| `XHS_RISK_MAX_REQUESTS_PER_RUN` | `0` | 单进程请求上限；`0` 表示不按固定请求数限制。设为正数时，到达上限仅结束本轮并保存断点，不会自动触发 30 分钟冷却。 |
| `XHS_RISK_COOLDOWN_SECONDS` | `1800` | 真实风险信号或连续失败触发暂停后的冷却秒数。 |
| `XHS_RISK_MAX_CONSECUTIVE_FAILURES` | `2` | 连续可恢复请求失败达到该次数后暂停。 |
| `XHS_RISK_STATE_FILE` | 账号目录下的 `risk_state.json` | 可选：指定该账号的风险状态文件位置；多账号不要共用同一个文件。 |

一次任务临时调整，例如将搜索间隔改为 4–8 秒、详情改为 10–20 秒：

```bash
cd "$XHS_CLI_DIR"
XHS_RISK_SEARCH_MIN_SECONDS=4 \
XHS_RISK_SEARCH_MAX_SECONDS=8 \
XHS_RISK_NOTE_MIN_SECONDS=10 \
XHS_RISK_NOTE_MAX_SECONDS=20 \
CONDA_NO_PLUGINS=true uv run python scrape_and_sync.py \
  --cookie-source saved \
  --keyword "美国货代" --limit 5 \
  --sort general --time all --scope all \
  --api-url "$XHS_SYNC_API_URL"
```

需要长期使用时，将变量写入你自己维护的环境文件，例如 `.xhs-scraper.env`，每次启动前显式加载：

```bash
source .xhs-scraper.env
CONDA_NO_PLUGINS=true uv run python scrape_and_sync.py --cookie-source saved ...
```

脚本不会自动读取 `.env` 文件；必须由 shell、任务调度器或部署环境注入这些变量。每轮工作量仍由
`--max-notes-per-run`、`--max-comment-pages-per-run` 和 `--max-reply-pages-per-root` 控制，它们不是
风控环境变量。

## 完成标准

Agent 应分别报告：筛选条件、每词目标数、选中数量、全局唯一帖子数、`uploaded`、`partial`、
`failed`、`unavailable`、运行目录和风控状态。只有上传成功且评论 checkpoint 完整，才能称为
完整同步。

详细的 Agent 执行规则见 [SKILL.md](SKILL.md)。
