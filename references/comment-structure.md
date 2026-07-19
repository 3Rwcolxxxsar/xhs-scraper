# XHS 评论数据完整字段参考

## 完整评论结构（两层翻页）

### 顶层评论字段
```json
{
  "id": "<root-comment-id>",
  "note_id": "<note-id>",
  "content": "澳大利亚求货代",
  "user_info": {
    "user_id": "<user-id>",
    "nickname": "🦄",
    "image": "https://sns-avatar-qc.xhscdn.com/...",
    "xsec_token": "<platform-token>",
    "ai_agent": false
  },
  "ip_location": "广东",
  "like_count": "2",
  "create_time": 1783411976000,
  "sub_comment_count": "1",           // 子评论总数（字符串！）
  "sub_comment_has_more": false,      // 子评论是否还有下一页
  "sub_comment_cursor": "<next-cursor>", // 子评论下一页游标
  "sub_comments": [],                // embedded 第一页（可能只有1条）
  "at_users": [],
  "show_tags": [],
  "liked": false,
  "status": 0
}
```

### 子评论字段
```json
{
  "id": "<reply-comment-id>",
  "note_id": "<note-id>",
  "content": "老板做什么产品的呀",
  "user_info": {
    "user_id": "<reply-user-id>",
    "nickname": "小高",
    "image": "https://sns-avatar-qc.xhscdn.com/...",
    "ai_agent": false,
    "xsec_token": "<platform-token>"
  },
  "ip_location": "江苏",
  "liked": false,
  "like_count": "0",
  "create_time": 1783474018000,
  "target_comment": {                 // 回复的目标（主评论）
    "id": "<root-comment-id>",
    "user_info": {
      "user_id": "<root-user-id>",
      "nickname": "🦄",
      "image": "https://sns-avatar-qc.xhscdn.com/...",
      "ai_agent": false,
      "xsec_token": "<platform-token>"
    }
  },
  "at_users": [],
  "show_tags": [],
  "status": 0
}
```

## 翻页判断逻辑

```
顶层 has_more=true  →  用 top_cursor 继续翻
子评论 has_more=true →  用 sub_comment_cursor 继续翻
```

## 完整性校验

- 平台 `comment_count` 应与去重后的顶层评论数加子评论数核对。
- `has_more=false` 才表示对应层分页完成；达到本轮预算只表示可恢复的 `partial`。
- 同一 comment ID 只能保留一次，父子关系通过 root comment 和 `target_comment.id` 保存。

## code=-1 子评论端点拒绝

子评论端点有时对特定 `root_comment_id` 返回：

```
code=-1, msg="", no platform message
```

先检查签名依赖版本、`xsec_token`、Cookie 和请求参数，不要直接归因于平台限制。统一 `code=-1` 曾由旧签名器导致；修复依赖后从 checkpoint 继续。只有验证码、429、登录失效或 IP 拦截等真实风险信号才触发风险暂停。
