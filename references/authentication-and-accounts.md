# 登录、WebBridge 与多账号

## 会话选择

优先检查账号隔离目录中的 saved Cookie：

```bash
cd "$XHS_CLI_DIR"
uv run python -m xhs_cli --cookie-source saved status --json
```

若返回已登录且 `guest=false`，直接以 `--cookie-source saved` 执行抓取。不要为了“模拟用户”
重复调用 WebBridge；后续请求仍是签名 API，浏览器前置动作不会改变这一点。

若 `--cookie-source chrome` 报 `No 'a1' cookie found`，尤其是在 Windows 的新版 Chrome 上，
不要把它当成“浏览器没有登录”。旧式 Cookie 读取器可能无法解密 Chrome 的 Cookie。改为连接目标
Chrome Profile 的 Kimi WebBridge 扩展，并只在 saved 会话缺失或过期时导入一次：

```bash
uv run python -m xhs_cli --cookie-source webbridge login --json
uv run python -m xhs_cli --cookie-source saved status --json
```

需要用户登录时：

```bash
uv run python -m xhs_cli --cookie-source webbridge login --qrcode --json
```

多账号时，先在 Chrome 中切换到目标 Google/Chrome Profile，再导入到稳定的本地别名：

```bash
# Profile A 已连接 WebBridge 且已登录小红书
uv run python -m xhs_cli --account account-a --cookie-source webbridge login --json
uv run python -m xhs_cli --account account-a --cookie-source saved status --json

# 切换到 Profile B 并连接后再执行；不要复用 account-a
uv run python -m xhs_cli --account account-b --cookie-source webbridge login --json
uv run python -m xhs_cli --account account-b --cookie-source saved status --json
```

导入成功后，所有抓取任务使用对应别名的 `--cookie-source saved`。`scrape_and_sync.py` 的别名参数
同样是 `--account account-a`；不要依赖当前 WebBridge Profile 来决定后台任务使用哪个账号。

验证码必须由用户在真实 Chrome 中人工处理。不得自动识别、绕过或更换账号继续探测。

## WebBridge 边界

- WebBridge 只读取当前真实 Chrome 的会话或打开登录页。
- 搜索、详情和评论分页不使用 DOM，也不由 WebBridge 发请求。
- 默认连接 `http://127.0.0.1:10086/command`。若用户已为不同 Profile 配置独立 daemon，可仅在
  导入时通过 `XHS_WEBBRIDGE_URL=http://127.0.0.1:<port>/command` 选择对应 daemon；CLI 不启动或管理
  第二个 daemon。每个 daemon 导入到不同 `--account` 后，后台抓取统一使用 `saved`。
- 不关闭用户已有标签页或 session，除非用户明确要求。

## 多账号

每个 `--account` 独立保存 Cookie、token/index cache、风险状态、RunLock 和 run-dir：

```text
~/.xiaohongshu-cli/accounts/<account>/
<xhs_cli_dir>/.xhs-scrape-runs/accounts/<account>/<run-key>/
```

规则：

1. 同一账号只运行一个进程；不同账号最多各一个进程，先用两个账号窄测。
2. 多账号任务必须互不重叠，或远端 API 已确认按 `note_id` 幂等。
3. 需要跨关键词统一首次出现顺序和全局去重的任务由一个账号、一个 run-dir 完成。
4. 不同账号不得共享显式 `--run-dir`。
5. 后台抓取统一用 `--cookie-source saved`，避免 WebBridge 当前 Profile 串号。
6. 任一账号触发验证码、429、登录失效或 IP 拦截时只停止，不自动把任务转给其他账号。
7. 多 Profile 可能共享公网 IP、设备和浏览器环境，账号隔离不等于风险隔离。
