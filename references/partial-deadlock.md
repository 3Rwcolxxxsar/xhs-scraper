# Partial 死锁/空转诊断

## 现象

断点续跑时出现这样的循环：

- 每一轮都处理 2 个帖子（`max-notes-per-run=2`）。
- 每一轮都因 `comment page budget reached (8)` 退出。
- 输出中 `uploaded: 0` 或极低，`partial` 数量不变或反复切换不同帖子。
- 多轮后 `unstarted` 没有明显下降，或几乎没新帖子变成 `uploaded`。

这说明每轮 8 页评论预算被 2 个帖子瓜分，每个帖子只能推进 4 页左右，评论永远跑不到 `complete`。

## 处理

立刻降低单轮并发或提高页预算：

- 推荐：把 `--max-notes-per-run` 从 2 降到 1，保留 `--max-comment-pages-per-run 8`。
- 若单帖子评论极多（>300 条或某条评论子评论非常多），可同时把 `--max-comment-pages-per-run` 提到 12–16。

**注意**：`max-comment-pages-per-run` 同时计算一级评论和子评论请求。子评论页会迅速消耗预算，因此高子评论帖需要更大预算或更小的 `max-notes-per-run`。

## 不应做的事

- 不要继续用导致空转的参数再跑更多轮。
- 不要手动删除 `comments/<note_id>.json` 或修改 manifest 状态来“跳过”评论。
- 不要向用户提议“只同步正文、放弃评论”。完整评论是默认验收标准。

## 验证

调整参数后再跑一轮，观察：

- 是否至少有一个帖子从 `partial` 进入 `uploaded`。
- 已有 `uploaded` 的帖子数量是否在增加。

若仍无进展，读取 manifest 检查是否有真实失败（如 `HTTP 502`、`The read operation timed out` 或 `RiskPausedError`），那不属于 partial 死锁。
