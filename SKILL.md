---
name: xhs-scraper
description: 按关键词和筛选条件抓取小红书自然排序前 N 个帖子，完整采集正文、图片、顶层评论和子评论，并通过断点续传同步到业务 API。用户要求小红书关键词搜索、批量帖子采集、完整评论导出、恢复中断任务或 API 同步时使用。
---

# XHS Scraper

先把 Skill 内置的 runtime overlay 安装到固定版本的上游 `xiaohongshu-cli` clone，再使用生产入口：

```text
$XHS_CLI_DIR/scrape_and_sync.py
```

运行 [install_runtime_overlay.py](scripts/install_runtime_overlay.py) 后，`XHS_CLI_DIR` 必须指向包含
`scrape_and_sync.py`、`pyproject.toml` 和 `xhs_cli/` 的项目目录。上传任务还必须设置
`XHS_SYNC_API_URL`；不得把内部 API 地址写入 Skill。

## 核心边界

- 搜索、帖子详情和评论分页由 CLI 调用签名 Web API，不通过 DOM 点击或浏览器翻页。
- 有效的已保存会话可直接用于抓取。WebBridge 只在首次导入 Cookie、会话失效或需要用户人工登录时使用；它不模拟浏览，也不降低 API 风控。
- `--dry-run` 只禁止业务 API 上传，仍会向小红书发出真实搜索、详情和评论请求。
- API 地址只读取当前环境变量或用户本轮明确提供的值；不得从记忆、历史任务或示例恢复内部地址，也不要在无必要时回显完整地址。
- 不因评论数大于任何阈值跳过帖子。评论不完整时保存 `partial`，不上传半成品。
- 遇到验证码、429、登录失效或 IP 拦截时停止，不绕过验证，也不自动换号继续探测。

## 执行流程

1. 若 `scrape_and_sync.py` 不存在，先按 [deployment-package.md](references/deployment-package.md)
   clone 固定上游版本并安装 runtime overlay；不要要求用户手动复制本机私有改动。
2. 优先检查已保存会话：

   ```bash
   cd "$XHS_CLI_DIR"
   uv run python -m xhs_cli --cookie-source saved status --json
   ```

   仅当会话缺失或无效时，按 [authentication-and-accounts.md](references/authentication-and-accounts.md)
   使用 WebBridge 导入或登录。只有用户明确指定已配置的账号别名时才添加
   `--account <alias>`，不要默认虚构 `worker-a`。
3. 使用显式筛选和 `saved` 会话执行一轮：

   ```bash
   test -n "$XHS_SYNC_API_URL" || { echo "XHS_SYNC_API_URL is required" >&2; exit 2; }
   CONDA_NO_PLUGINS=true uv run python scrape_and_sync.py \
     --cookie-source saved \
     --keyword "关键词一" --keyword "关键词二" \
     --limit 5 \
     --sort general --time all --scope all \
     --api-url "$XHS_SYNC_API_URL" \
     --max-notes-per-run 2 \
     --max-comment-pages-per-run 8
   ```

4. 读取输出中的 `run_dir` 和 `manifest.json`。页预算造成的 `partial` 应以完全相同参数再次
   执行；只有用户明确要求丢弃进度时才加 `--restart`。
5. 重复分轮，直到所有有效帖子进入 `uploaded`（或 dry-run 的 `ready`）以及可解释的
   `unavailable`，且没有 `partial/failed/unstarted`。
6. 按 [validation-and-reporting.md](references/validation-and-reporting.md) 验证并汇报；不得把
   “上传成功”与“评论抓取完整”合并成一个结论。

## 必须保持的数据语义

- 每个关键词按 API 返回顺序取得前 N 个唯一 `note_id`，不在本地重新排序。
- 同一帖子命中多个关键词时分别占用各关键词名额，但全局只抓取和上传一次。
- 顶层评论与每条顶层评论的子评论分别分页，以 comment ID 去重并检测重复 cursor。
- 原始搜索页、帖子详情、评论 checkpoint 和 payload 写入权限为 `0600` 的运行目录。
- API 仅接收评论完整的 payload；字段和完整性规则见
  [collection-contract.md](references/collection-contract.md) 与
  [comment-structure.md](references/comment-structure.md)。

## 按需读取

- 登录、Cookie、WebBridge 和多账号：
  [authentication-and-accounts.md](references/authentication-and-accounts.md)
- 搜索筛选、去重、字段和 API 契约：
  [collection-contract.md](references/collection-contract.md)
- 断点、状态、分轮恢复、风险间隔和 RunLock：
  [resume-and-risk.md](references/resume-and-risk.md)
- 连续 `partial` 空转：
  [partial-deadlock.md](references/partial-deadlock.md)
- `code=-1`、环境异常、辅助卡片和不可见帖子：
  [troubleshooting.md](references/troubleshooting.md)
- 测试命令和完成报告：
  [validation-and-reporting.md](references/validation-and-reporting.md)
- 迁移到其他电脑：
  [deployment-package.md](references/deployment-package.md)

## 兼容边界

关键词采集任务始终使用 `scrape_and_sync.py`；`sync_xhs.py` 是其内部的评论 checkpoint 与 API 上传
适配模块，不能作为独立生产入口。
