# 故障诊断

## 子评论接口 `code=-1`

不要把 `code=-1` 直接归因于评论太多或平台永久禁止。按顺序窄测：

1. `uv sync` 并确认项目满足 `xhshow>=0.2.0`；
2. 用一个已知 `sub_comment_has_more=true` 的父评论测试子评论接口；
3. 检查 `xsec_token` 是否来自当前搜索/详情上下文且未过期；
4. 检查 saved Cookie 是否仍为 `guest=false`；
5. 对照当前真实请求判断接口或签名契约是否变化。

这些都是诊断分支，不应在未验证前写成唯一根因。恢复同一 run-dir 可复用已完成的顶层评论。

## 搜索辅助卡片与不可见帖子

- `model_type=rec_query` 或 ID 含 `#` 的记录不是帖子，过滤后继续搜索补足 N。
- 合法 note ID 返回 `empty noteDetailMap` 时标记 `unavailable` 并保留原因，不无限重试或伪造正文。

## 工具超时

- 前台工具达到 300 秒不等于抓取进程失败；先检查进程是否仍在运行。
- 仍在运行就等待，不能并发启动相同账号任务。
- macOS 没有 GNU `timeout` 是正常情况，本流程不依赖它。

## Conda 与 uv

若 uv 因 Conda shell 插件报 `expected str, bytes or os.PathLike object, not NoneType`，只给当前
命令添加：

```bash
CONDA_NO_PLUGINS=true uv run python scrape_and_sync.py ...
```

不要修改用户全局 Conda 配置。

## 需要用户介入

仅在以下情况请求用户帮助：

- saved 会话缺失或失效，需要在 WebBridge Chrome 中登录；
- 页面出现验证码，需要人工处理；
- API 地址、认证方式或 payload 契约缺失/变化；
- 同一真实风险信号在停止等待后仍重复出现。

页预算 `partial`、deferred、重复 note ID、辅助卡片和 Conda 单命令修复不需要用户介入。
