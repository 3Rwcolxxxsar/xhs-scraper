# 搜索、评论与 API 契约

## 搜索筛选

| 维度 | CLI 值 | 平台请求值 |
| --- | --- | --- |
| 综合 | `general` | `general` |
| 最新 | `latest` | `time_descending` |
| 最多点赞 | `most-liked` | `popularity_descending` |
| 最多评价 | `most-commented` | `comment_descending` |
| 最多收藏 | `most-collected` | `collect_descending` |
| 发布时间 | `all/day/week/half-year` | 同名值 |
| 搜索范围 | `all/seen/unseen/followed` | 同名值 |

每个关键词按 API 返回顺序保留前 N 个唯一 `note_id`，不得本地二次重排。同一帖子命中多个
关键词时分别占用名额，但全局队列按首次出现顺序只抓取和上传一次。

过滤 `model_type=rec_query`、ID 含 `#` 的相关搜索卡片，并继续翻页补足真实帖子。

## 评论完整性

- 不设置评论数跳过阈值。
- 顶层评论和每条顶层评论的子评论分别逐页抓取。
- 以稳定 comment ID 去重；每层 cursor 都检测重复，防止死循环。
- 达到单轮页预算时保存 `partial`，下轮从 cursor 恢复。
- 只有顶层和全部可访问子评论都完成后才生成并上传 payload。

完整平台评论字段见 [comment-structure.md](comment-structure.md)。

## 当前上传结构

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
  "comments": []
}
```

评论对象保留平台原始结构，不压缩成少数字段；平台未返回的可选字段不伪造。原始搜索响应、
帖子响应、评论 checkpoint 和 payload 保存在运行目录供审计。若业务 API 契约变化，停止并向
用户确认，不自行猜字段。
