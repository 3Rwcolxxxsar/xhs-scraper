# 小红书抓取流程部署包清单

## 用途

把 `xhs-scraper` skill 与其依赖的 `xiaohongshu-cli` 项目迁移到另一台电脑。Skill 本身不包含 CLI 源码、浏览器会话或业务 API 凭据。

## 应包含

```text
xhs-scraper/
  SKILL.md
  agents/openai.yaml
  references/

xiaohongshu-cli/
  pyproject.toml
  uv.lock
  scrape_and_sync.py
  xhs_cli/
  tests/
```

## 必须排除

| 目录/文件 | 原因 |
|---|---|
| `.venv/`、`node_modules/` | 可重建依赖 |
| `.xhs-scrape-runs/` | 含运行结果和断点 |
| `.git/`、缓存、日志 | 与运行无关 |
| Cookie、浏览器 Profile、token cache | 账号与设备敏感数据 |
| `.env` 和任何密钥文件 | 安全数据 |
| 无关嵌套项目 | 不属于生产入口 |

## 目标电脑配置

1. 安装 Python 3.10+ 与 `uv`。
2. 安装并连接 Kimi WebBridge；不得改用 Playwright、Camoufox、Selenium 或直接读取浏览器 Cookie 数据库。
3. 设置 CLI 路径：`export XHS_CLI_DIR=/path/to/xiaohongshu-cli`。
4. 上传任务设置：`export XHS_SYNC_API_URL=https://example.com/xhs/sync`。
5. 每台设备和每个账号必须重新登录，不复制 Cookie。

## 验证

```bash
cd "$XHS_CLI_DIR"
uv sync
uv run python -m xhs_cli --cookie-source webbridge login --json
uv run python -m xhs_cli --cookie-source saved status --json
uv run python scrape_and_sync.py \
  --cookie-source saved \
  --keyword "测试关键词" \
  --limit 1 \
  --dry-run
```

验证通过后，再设置 `XHS_SYNC_API_URL` 执行真实上传。Cookie 或 API 配置缺失时停止，不承诺“复制即运行”。
