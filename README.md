# xhs-scraper

一个面向 Agent 的小红书关键词采集 Skill。它编排兼容的 `xiaohongshu-cli`，按指定筛选条件
取得每个关键词前 N 个帖子，抓取帖子信息、顶层评论和子评论，并在数据完整后同步到业务 API。

> 本仓库只包含 Skill，不包含抓取器。实际执行依赖带有 `scrape_and_sync.py` 的
> `xiaohongshu-cli` 项目。

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

## 完成标准

Agent 应分别报告：筛选条件、每词目标数、选中数量、全局唯一帖子数、`uploaded`、`partial`、
`failed`、`unavailable`、运行目录和风控状态。只有上传成功且评论 checkpoint 完整，才能称为
完整同步。

详细的 Agent 执行规则见 [SKILL.md](SKILL.md)。
