---
name: xhs-scraper
description: 按关键词自然排序抓取前 N 个小红书帖子，采集正文、图片、顶层评论和全部子评论，并断点续传到业务 API。涉及小红书搜索、评论导出或 API 同步时使用。
---

# XHS Scraper

本 skill 依赖 `xiaohongshu-cli` 项目中的唯一生产入口：

```text
$XHS_CLI_DIR/scrape_and_sync.py
```

开始前解析配置：

- `XHS_CLI_DIR` 必须指向包含 `scrape_and_sync.py`、`pyproject.toml` 和 `xhs_cli/` 的项目目录。若未设置，先从当前工作目录定位；仍无法确定时向用户询问，不猜测绝对路径。
- 上传任务必须设置 `XHS_SYNC_API_URL`。不得在 skill 中硬编码组织内部 API；`--dry-run` 不需要该变量。

目标流程：

```text
关键词列表 + 每词数量 N
→ 小红书按指定排序、发布时间和搜索范围搜索
→ 每个关键词保留前 N 个唯一帖子
→ 读取正文、作者、图片、点赞和评论总数
→ 顶层评论逐页
→ 每条主评论的子评论逐页
→ 完整后上传 API
```

## 前置检查：登录会话

所有真实浏览器操作统一使用 Kimi WebBridge。禁止使用 Playwright、Camoufox、Selenium、CLI 自启浏览器、`browser_cookie3` 或直接读取 Chrome/Safari Cookie 数据库。

WebBridge 控制用户已经打开且安装扩展的真实 Chrome；帖子、搜索和评论抓取仍由 CLI 的签名 API 完成，不通过 DOM 点击翻页。运行前先导入当前 WebBridge Profile 的会话并确认非 guest：

```bash
cd "$XHS_CLI_DIR"
uv run python -m xhs_cli --account worker-a --cookie-source webbridge login --json
uv run python -m xhs_cli --account worker-a --cookie-source saved status --json
```

如果显示 `guest: true` 或 Session expired，使用 WebBridge 打开真实浏览器登录页并等待用户扫码：

```bash
uv run python -m xhs_cli --account worker-a --cookie-source webbridge login --qrcode --json
```

Kimi WebBridge daemon 当前只接受一个扩展连接。多个 Chrome Profile 必须逐个切换：让目标 Profile 成为当前连接的扩展，再执行对应 `--account ... login` 导入会话。不得声称 WebBridge 能同时操控多个 Profile。

## 标准命令

用户提供关键词和每词数量后，直接执行：

```bash
cd "$XHS_CLI_DIR"
test -n "$XHS_SYNC_API_URL" || { echo "XHS_SYNC_API_URL is required" >&2; exit 2; }
uv run python scrape_and_sync.py \
  --cookie-source saved \
  --keyword "关键词一" \
  --keyword "关键词二" \
  --limit 5 \
  --sort general \
  --time all \
  --scope all \
  --api-url "$XHS_SYNC_API_URL" \
  --max-notes-per-run 2 \
  --max-comment-pages-per-run 8
```

> 注意：在 Conda 与 uv 混合的环境中，若运行 uv 出现 `TypeError: expected str, bytes or os.PathLike object, not NoneType`，说明 Conda 的 shell 插件干扰了解析器。此时在命令前加 `CONDA_NO_PLUGINS=true`：
>
> ```bash
> CONDA_NO_PLUGINS=true uv run python scrape_and_sync.py ...
> ```

测试而不上传：

```bash
uv run python scrape_and_sync.py --keyword "关键词" --limit 1 --dry-run
```

相同参数再次运行时自动恢复。只有用户明确要求丢弃历史进度时才添加 `--restart`。

## 多账号并行

多账号模式用于把明确分开的 API 抓取任务交给不同已导入账号，不用于触发风控后的自动换号。每个账号的 Cookie、token/index cache、风险状态、RunLock 和默认 run-dir 都独立：

```text
~/.xiaohongshu-cli/accounts/<account>/
.xhs-scrape-runs/accounts/<account>/<run-key>/
```

可在两个终端运行互不重叠的任务：

```bash
CONDA_NO_PLUGINS=true uv run python scrape_and_sync.py \
  --account worker-a --cookie-source saved \
  --keyword "加拿大线货代" --limit 20 \
  --api-url "$XHS_SYNC_API_URL"

CONDA_NO_PLUGINS=true uv run python scrape_and_sync.py \
  --account worker-b --cookie-source saved \
  --keyword "美线货代" --limit 20 \
  --api-url "$XHS_SYNC_API_URL"
```

必须遵守：

1. 同一账号始终只运行一个进程；不同账号才允许各一个进程并行。首次使用先以最多两个账号窄测。
2. 一个要求“跨关键词全局去重并保持统一排名”的逻辑任务仍由一个账号、一个 run-dir 执行。把关键词拆到不同账号后只能各自去重，不能保证跨进程首次出现顺序。
3. 只有任务天然不重叠，或远端 API 已人工确认按 `note_id` 幂等，才并行分片。未确认时不得承诺不会重复上传。
4. 不同账号不得共享显式 `--run-dir`；manifest 会记录账号并拒绝账号不匹配的恢复。
5. 任一账号遇到验证码、429、登录失效或 IP 拦截，停止该账号；不得自动把它未完成的任务转交另一个账号继续探测。
6. 多 Profile 仍可能共享公网 IP、设备和浏览器环境；账户隔离不能保证平台把它们视为互不相关，也不能替代每账号的随机请求间隔。
7. 多账号并行抓取统一使用 `--cookie-source saved`，禁止后台自动从当前 WebBridge Profile 刷新，避免串号。若会话失效，停止该账号，切换到正确 Profile 后再用 WebBridge 重新导入。
8. WebBridge 的标签页按一个任务一个 session 管理；除非用户明确要求，不关闭其 session 或标签组。

## 不可违反的采集规则

1. 搜索筛选必须显式记录在 manifest：排序支持 `general/latest/most-liked/most-commented/most-collected`，发布时间支持 `all/day/week/half-year`，搜索范围支持 `all/seen/unseen/followed`。每种组合都按 API 返回顺序取结果，采集后不得本地二次重排。
2. 每个关键词必须尝试取得前 N 个唯一 `note_id`；同一帖子命中多个关键词时，搜索名额分别计算，但全局只抓取和上传一次。
3. 禁止设置 `MAX_COMMENT_COUNT`，禁止因为评论数大于 300、500 或任何阈值而跳过帖子。
4. 大评论帖通过 checkpoint 分轮完成。单轮因风控、网络或页数预算停止时标记 `partial`，下次从 cursor 继续。
5. 禁止用一个长时间 `xhs comments --all` 子进程包住完整帖子。顶层评论和子评论必须两层逐页。
6. comment ID 优先去重；每层 cursor 都要检测重复，避免死循环。
7. 评论未完整时不得上传，防止部分数据覆盖远端完整数据。
8. 遇到验证码、429、登录失效、IP 拦截或 `risk_paused` 时停止本轮，不绕过验证，不继续探测。
9. 不把“50–100 篇帖子/轮”误写成“50 个分页请求/轮”。默认不按请求数触发 30 分钟冷却；搜索 3–10 秒、详情 8–24 秒、评论分页 5–12 秒随机间隔，30 分钟冷却只用于真实风险信号。
10. 大任务必须拆分多轮。用 `--max-notes-per-run 2` 限制每轮尝试的帖子数，同时用 `--max-comment-pages-per-run 8` 限制本轮所有一级+子评论请求总页数。不要只压低评论页预算却让整批帖子在一次调用中继续运行。
11. 若连续多轮只产生 `partial`、没有 `uploaded` 或 `ready`，说明 `max-notes-per-run` 相对 `max-comment-pages-per-run` 过高，导致每个帖子都拿不到足够页数完成评论。应立即降低 `max-notes-per-run` 到 1，或提高 `max-comment-pages-per-run` 到 12–16（该预算同时计算一级评论和子评论请求），不要继续用原参数反复空转。参考 `references/partial-deadlock.md`。
12. `max-comment-pages-per-run` 是断点预算，不是评论截断。达到预算出现 `partial` 属于可恢复进度；下一轮必须继续同一 run-dir，直到 comment checkpoint 为 `complete`。
13. 搜索响应会混入 `model_type=rec_query` 的“相关搜索”卡片，其 ID 形如 `UUID#时间戳`。生产入口会过滤并自动补足后续真实帖子；不得对这类 ID 调用详情接口。

## 断点与状态

运行目录默认位于：

```text
<xhs_cli_dir>/.xhs-scrape-runs/<keywords-limit-hash>/
<xhs_cli_dir>/.xhs-scrape-runs/accounts/<account>/<keywords-limit-hash>/  # 多账号模式
```

关键文件：

- `manifest.json`：关键词选择、帖子状态、命中关键词、payload 和错误。
- `comments/<note_id>.json`：顶层 cursor、每个父评论的 reply cursor、已抓评论和失败原因。
- `raw/search/<keyword-hash>/page-N.json`：搜索页完整原始响应，用于审计排名并在跨天恢复时重建 token 上下文。
- `raw/notes/<note_id>.json`：帖子详情完整原始响应。
- `payloads/<note_id>.json`：准备上传的完整业务 payload。

manifest、raw、comments 和 payload 文件均只允许当前用户读写（`0600`）。正常采集直接调用小红书签名 Web API，不操作浏览器页面；仅在 Cookie 缺失、过期或需要重新登录时读取/使用浏览器会话。

帖子状态：

- `partial`：可恢复，不是跳过。评论分页中断会保存阶段、父评论 ID 和 cursor，不阻塞其他帖子。
- `ready`：dry-run 已生成完整 payload。
- `uploaded`：API 上传成功；后续恢复不重复上传。
- `unavailable`：合法搜索帖子随后被删除、设为不可见或详情确实不存在；保留原因并在后续轮次跳过，避免永久占用处理预算。
- `failed`：非风控类失败，需要检查 manifest 中的 error。

退出码：本轮出现 `partial` 或 `failed` 时为 1；这不等于断点损坏。先读 manifest 的 error，页预算类 partial 直接进入下一轮，真实风控和登录错误才暂停。

## 分轮恢复执行模板

当任务规模大、单轮超时或风控被暂停时，使用如下循环：

```bash
cd "$XHS_CLI_DIR"
RUN_DIR=.xhs-scrape-runs/<existing-run-key>

CONDA_NO_PLUGINS=true uv run python scrape_and_sync.py \
  --keyword "加拿大线货代" \
  --keyword "美线货代" \
  --keyword "美加线货代" \
  --limit 20 \
  --sort general --time all --scope all \
  --api-url "$XHS_SYNC_API_URL" \
  --max-notes-per-run 2 \
  --max-comment-pages-per-run 8 \
  --max-reply-pages-per-root 20
```

每次 Hermes 工具调用只执行上面一轮，结束后读取一次 manifest，再发起下一轮。不要依赖系统 `timeout` 命令，不要把几十轮包进一个超过 300 秒的前台命令，也不要在状态不明时重复启动后台进程。

每轮结束后查看 manifest：

```bash
python3 -c "
import json
from collections import Counter
m = json.load(open('$RUN_DIR/manifest.json'))
statuses = Counter(s.get('status') for s in m['notes'].values())
print('selected', sum(len(v) for v in m['selections'].values()))
print('notes', len(m['notes']))
print('statuses', dict(statuses))
print('unstarted', len(set(x['note_id'] for v in m['selections'].values() for x in v) - set(m['notes'])))
print('partial/failed errors:')
for nid, s in m['notes'].items():
    if s.get('status') in ('partial', 'failed') and s.get('error'):
        print(' ', nid, s['error'][:80])
"
```

重复运行直到所有已选有效 note_id 都进入 `uploaded/ready/unavailable`，且 `partial/failed/unstarted` 均为 0。`unavailable` 必须单独汇报，不能算 uploaded。若出现 `RiskPausedError`，先确认没有活跃进程；只有确无进程且风险状态允许时才执行 `risk-resume --yes`。

## 单轮超时与环境异常

- 前台工具达到 300 秒不等于抓取失败。先用 `ps` 检查进程；仍在运行就等待，不能并发重启。
- 新版默认 `--max-notes-per-run 2 --max-comment-pages-per-run 8`，正常情况下应在前台超时前主动退出并保存断点。
- `max-comment-pages-per-run` 同时计算一级评论和子评论请求，防止一级完成后大量子评论让单轮失控。
- Conda 插件异常时仅给当前命令添加 `CONDA_NO_PLUGINS=true`，不要修改用户全局 Conda 配置。
- macOS 没有 GNU `timeout` 是正常情况；本流程不依赖该命令。

## 自主继续与请求用户帮助的边界

以下情况不询问用户，直接按规则继续下一轮：

- `comment page budget reached` 导致的 `partial`；
- 本轮达到 `max-notes-per-run`，输出存在 `deferred`；
- 前台 300 秒超时但进程仍存活；
- Conda 插件异常，可用单命令 `CONDA_NO_PLUGINS=true` 修复；
- 搜索辅助卡片、重复 note_id、已上传帖子和已标记 unavailable 的帖子。

只有以下情况才请求用户帮助：

- 当前 WebBridge Profile 无法取得 `guest=false` 的账号，需要用户在 WebBridge 打开的真实浏览器中登录或扫码；
- 页面明确出现验证码，需要用户人工处理；
- API 地址、认证方式或 payload 契约缺失/变化；
- 同一真实风险信号在停止并等待后仍重复出现。

不要因为页预算 partial 向用户提供“放弃评论/只上传正文”等降级选项；完整评论仍是默认验收标准。

## 搜索辅助卡片与不可见帖子

- `model_type=rec_query`、ID 含 `#` 的记录不是帖子。恢复旧 manifest 时入口会删除这类 selection、清理对应失败状态并继续翻搜索页补足 N。
- 24 位真实 note_id 若返回 `empty noteDetailMap`，标记 `unavailable` 并保留原因；不要无限重试，也不要伪造正文上传。
- 每关键词的 N 是有效搜索帖子名额；跨关键词仍按 note_id 全局只抓取和上传一次。

## RunLock 冲突处理

同一账号同时只能有一个 CLI 实例运行；不同 `--account` 使用不同锁。遇到以下错误时：

```
RiskPausedError: Requests paused for account safety (another_cli_run_is_active)
```

先诊断是否真的有残留进程：

```bash
ps aux | grep -E "python.*scrape_and_sync|sync_xhs" | grep -v grep
```

如果看到正在运行的进程（如 `scrape_and_sync.py --keyword xxx`），等它结束再重试。
如果没有残留进程但仍报锁，可能是上一个进程异常退出未释放文件锁，此时执行：

```bash
uv run python -m xhs_cli risk-resume --yes
```

然后重新运行采集任务。风险状态本身（`risk-status`）显示 `paused: false` 不能说明没有 RunLock 持有者。

## code=-1 子评论拒绝

如果多个帖子在 `/api/sns/web/v2/comment/sub/page` 统一返回 `code=-1` 且无平台消息，先检查 `xhshow` 版本：

- 项目要求 `xhshow>=0.2.0`；0.1.9 的旧签名会导致该接口统一拒绝，但一级评论仍可成功。
- 运行 `uv sync` 后用一个已知 `sub_comment_has_more=true` 的父评论做窄回归。
- 升级后重新运行同一批任务会从 checkpoint 恢复，不重抓已完成的一级评论。
- 只有升级后仍失败，才继续检查新鲜 `xsec_token`、Cookie 和真实浏览器请求；不要把统一 `code=-1` 直接归因于平台限制。

## 评论字段与 API

完整平台字段参考：`references/comment-structure.md`。

业务 API 由环境变量提供：

```text
$XHS_SYNC_API_URL
```

上传结构：

```json
{
  "note_id": "帖子ID",
  "title": "标题",
  "author_name": "作者",
  "text": "正文",
  "images": [],
  "like_count": 0,
  "comment_count": 0,
  "created_at": "YYYY-MM-DD",
  "comments": [
    {
      "id": "主评论ID",
      "note_id": "帖子ID",
      "content": "内容",
      "user_info": {
        "user_id": "用户ID",
        "nickname": "用户名",
        "image": "头像URL",
        "xsec_token": "平台返回值",
        "ai_agent": false
      },
      "ip_location": "地区",
      "like_count": 0,
      "liked": false,
      "create_time": 0,
      "sub_comment_count": 2,
      "sub_comment_cursor": "",
      "sub_comment_has_more": false,
      "show_tags": [],
      "at_users": [],
      "pictures": [],
      "status": 0,
      "sub_comments": [
        {
          "id": "子评论ID",
          "target_comment": {
            "id": "被回复评论ID",
            "user_info": {}
          }
        }
      ]
    }
  ]
}
```

评论对象以平台原始评论结构为契约，不压缩成 `user_nickname/liked_count` 子集；平台实际未返回的可选字段不会伪造。字段说明见 `references/comment-structure.md`。

## 验证与汇报

改动后运行：

```bash
uv run ruff check xhs_cli tests scrape_and_sync.py sync_xhs.py
uv run python -m pytest -q
```

向用户汇报：关键词、排序/时间/范围筛选、每词目标数、选中数量、全局唯一帖子数、uploaded/partial/failed/unavailable 数量、run_dir、同步目标，以及是否触发风控。不要把 `partial` 描述成“成功同步”，也不要把大评论帖描述成“自动跳过”。`unavailable` 必须单独说明，不能算入 uploaded。

## 兼容脚本

`fetch_comments.py`、`fetch_sub_comments.py` 和 `sync_xhs.py` 仅保留为底层参考或历史兼容，不再作为关键词抓取任务的入口。

## 迁移与打包

若用户要求“让这套抓取流程能在别人电脑上运行”，按 `references/deployment-package.md` 生成打包目录并说明登录依赖，不要简单复制 `.xhs-scrape-runs` 或浏览器 Cookie 文件。
