# 断点、分轮与风险控制

## 运行目录和状态

默认目录：

```text
<xhs_cli_dir>/.xhs-scrape-runs/<keywords-limit-hash>/
<xhs_cli_dir>/.xhs-scrape-runs/accounts/<account>/<keywords-limit-hash>/
```

- `manifest.json`：筛选、选中帖子、命中关键词、状态、payload 和错误。
- `comments/<note_id>.json`：顶层和各父评论的 cursor、去重集合及评论数据。
- `raw/search/...`、`raw/notes/...`：原始平台响应。
- `payloads/<note_id>.json`：完整后待上传的数据。

状态：

- `partial`：可恢复的未完成评论或风险中断。
- `ready`：dry-run 已生成完整 payload。
- `uploaded`：上传成功，恢复时不重复上传。
- `unavailable`：帖子删除、不可见或详情确实不存在。
- `failed`：非风控失败，需要检查 error。

退出码 1 可能只表示存在 `partial`，不代表断点损坏。

## 分轮恢复

默认每轮最多尝试 2 个未完成帖子，并给顶层加子评论共 8 页预算。每轮只执行一次 CLI，读取
manifest 后再决定下一轮；不要把几十轮包进长时间前台命令，也不要并发重启未知状态的进程。

相同关键词、数量、筛选、账号和 run-dir 生成同一任务并恢复。只有用户明确要求丢弃进度时才
使用 `--restart`。

持续运行直到：

- 有效选中帖子全部为 `uploaded/ready/unavailable`；
- `partial/failed/unstarted` 为 0；
- `unavailable` 单独列出，不计入 uploaded。

连续多轮只有 `partial`、没有 `uploaded/ready` 时，读取
[partial-deadlock.md](partial-deadlock.md)，降低 `max-notes-per-run` 或提高评论页预算。

## 请求间隔与暂停

CLI 默认按请求类型随机等待：

- 搜索：3–10 秒；
- 详情及其他非评论请求：8–24 秒；
- 顶层及子评论分页：5–12 秒。

默认 `XHS_RISK_MAX_REQUESTS_PER_RUN=0`，不会因为固定请求数自动冷却。默认 1800 秒冷却只在
验证码、429、登录失效、IP 拦截或连续失败达到阈值时触发，可由环境配置调整。随机间隔降低
请求密度，但不能保证不触发平台限制。

## RunLock

同一账号同时只能运行一个 CLI 实例。出现 `another_cli_run_is_active` 时先检查：

```bash
ps aux | grep -E "python.*scrape_and_sync|sync_xhs" | grep -v grep
```

有活跃进程就等待。确认无进程后再检查风险状态；只有锁确属异常残留且风险状态允许时，才执行：

```bash
uv run python -m xhs_cli risk-resume --yes
```

不得在真实风险暂停期间用该命令强行恢复。
