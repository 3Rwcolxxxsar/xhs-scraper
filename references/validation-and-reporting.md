# 验证与完成报告

## Skill 校验

```bash
python3 "$SKILL_CREATOR_DIR/scripts/quick_validate.py" /path/to/xhs-scraper
```

## CLI 单元测试

在 `XHS_CLI_DIR` 执行：

```bash
uv run ruff check xhs_cli tests scrape_and_sync.py sync_xhs.py
uv run python -m pytest -q
```

## 最小真实测试

先用一个关键词、1 个帖子验证登录、搜索、详情、评论 checkpoint 和 dry-run：

```bash
uv run python scrape_and_sync.py \
  --cookie-source saved \
  --keyword "测试关键词" --limit 1 \
  --sort general --time all --scope all \
  --max-notes-per-run 1 \
  --max-comment-pages-per-run 8 \
  --dry-run
```

真实上传测试必须得到用户授权并设置 `XHS_SYNC_API_URL`。不得把生产 API 写入测试文件。
API 地址只从当前环境或用户本轮输入读取，不从 Agent 记忆恢复，也不在报告中无必要地回显。

## 完成判定

分别核对：

1. 每个关键词选中数是否达到目标 N；
2. 全局唯一帖子数；
3. `uploaded/ready/partial/failed/unavailable/unstarted` 数量；
4. 每个 uploaded 帖子的评论 checkpoint 是否 complete；
5. 是否触发验证码、429、登录失效、IP 拦截或风险暂停；
6. API 响应是否成功。

向用户报告关键词、排序/时间/范围、每词目标、选中数、全局唯一数、各状态数量、run-dir、同步
目标和风险状态。不得把 `partial` 描述成成功同步，不得把 `unavailable` 计入 uploaded，也不得
把大评论帖描述成自动跳过。

dry-run 报告必须写“未向业务 API 上传；已向小红书 API 发出真实采集请求”，不得写成“未向
任何 API 发送”。
