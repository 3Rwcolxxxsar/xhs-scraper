# 登录、WebBridge 与多账号

## 会话选择

优先检查账号隔离目录中的 saved Cookie：

```bash
cd "$XHS_CLI_DIR"
uv run python -m xhs_cli --cookie-source saved status --json
```

若返回已登录且 `guest=false`，直接以 `--cookie-source saved` 执行抓取。不要为了“模拟用户”
重复调用 WebBridge；后续请求仍是签名 API，浏览器前置动作不会改变这一点。

仅在 saved Cookie 缺失或过期时，从当前连接的 WebBridge Profile 导入并验证：

```bash
uv run python -m xhs_cli --cookie-source webbridge login --json
uv run python -m xhs_cli --cookie-source saved status --json
```

需要用户登录时：

```bash
uv run python -m xhs_cli --cookie-source webbridge login --qrcode --json
```

上述命令使用默认账号目录。只有用户明确给出已配置别名时，才在命令中添加
`--account worker-a` 等参数；不要从示例中虚构账号。

验证码必须由用户在真实 Chrome 中人工处理。不得自动识别、绕过或更换账号继续探测。

## WebBridge 边界

- WebBridge 只读取当前真实 Chrome 的会话或打开登录页。
- 搜索、详情和评论分页不使用 DOM，也不由 WebBridge 发请求。
- 当前实现固定连接一个 daemon；不能声称可同时操控多个 Profile 或通过多个端口并行。
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
