#!/usr/bin/env python3
# ruff: noqa: E501
import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from xhs_cli.client import XhsClient
from xhs_cli.cookies import get_cookies
from xhs_cli.risk_control import RunLock

API_URL = os.getenv("XHS_SYNC_API_URL", "")
DEFAULT_CHECKPOINT_DIR = Path(__file__).with_name(".xhs-sync-checkpoints")


def _comment_id(comment: dict[str, Any]) -> str:
    return str(comment.get("id") or comment.get("comment_id") or comment.get("commentId") or "")


def _load_checkpoint(path: Path, note_id: str) -> dict[str, Any]:
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if data.get("note_id") == note_id:
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "schema": "xhs-comment-sync-checkpoint.v1",
        "note_id": note_id,
        "status": "pending",
        "top_cursor": "",
        "top_complete": False,
        "top_pages_fetched": 0,
        "seen_top_cursors": [],
        "comments": [],
        "reply_state": {},
        "failure": {},
    }


def _save_checkpoint(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    temp_path.replace(path)
    path.chmod(0o600)


def _merge_comments(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {_comment_id(comment): comment for comment in existing if _comment_id(comment)}
    without_id = [comment for comment in existing if not _comment_id(comment)]
    for comment in incoming:
        comment_id = _comment_id(comment)
        if not comment_id:
            without_id.append(comment)
            continue
        previous = by_id.get(comment_id, {})
        preserved_replies = previous.get("sub_comments", [])
        by_id[comment_id] = {**previous, **comment}
        if preserved_replies and not by_id[comment_id].get("sub_comments"):
            by_id[comment_id]["sub_comments"] = preserved_replies
    return [*by_id.values(), *without_id]


def _merge_replies(comment: dict[str, Any], incoming: list[dict[str, Any]]) -> None:
    existing = comment.get("sub_comments", [])
    comment["sub_comments"] = _merge_comments(existing if isinstance(existing, list) else [], incoming)


def simplify_comment(comment: dict[str, Any]) -> dict[str, Any]:
    """Legacy compact mapping retained for callers that explicitly need it."""
    user = comment.get("user_info") or comment.get("user") or {}
    sub_comments = comment.get("sub_comments", []) or []

    def simplify_reply(reply: dict[str, Any]) -> dict[str, Any]:
        reply_user = reply.get("user_info") or reply.get("user") or {}
        return {
            "id": _comment_id(reply),
            "content": reply.get("content", ""),
            "user_nickname": reply_user.get("nickname", ""),
            "user_id": reply_user.get("user_id", reply_user.get("id", "")),
            "ip_location": reply.get("ip_location", ""),
            "liked_count": reply.get("liked_count", reply.get("like_count", 0)),
            "create_time": reply.get("create_time", ""),
        }

    return {
        "id": _comment_id(comment),
        "content": comment.get("content", ""),
        "user_nickname": user.get("nickname", ""),
        "user_id": user.get("user_id", user.get("id", "")),
        "ip_location": comment.get("ip_location", ""),
        "liked_count": comment.get("liked_count", comment.get("like_count", 0)),
        "create_time": comment.get("create_time", ""),
        "sub_comment_count": comment.get("sub_comment_count", 0),
        "sub_comments": [simplify_reply(reply) for reply in sub_comments if isinstance(reply, dict)],
    }


def build_sync_payload(note: dict[str, Any]) -> dict[str, Any]:
    """Build the API payload without dropping fields from raw comment results."""
    return {
        "note_id": note["note_id"],
        "title": note["title"],
        "author_name": note["author_name"],
        "text": note["text"],
        "images": note.get("images", []),
        "like_count": note.get("like_count", 0),
        "comment_count": note.get("comment_count", 0),
        "created_at": note.get("created_at", ""),
        "comments": note.get("comments", []),
    }


def fetch_comments_with_checkpoint(
    client: XhsClient,
    note_id: str,
    *,
    checkpoint_dir: Path = DEFAULT_CHECKPOINT_DIR,
    max_pages_per_run: int = 100,
    max_reply_pages_per_root: int = 20,
) -> dict[str, Any]:
    """Fetch all available comment pages, saving progress after every successful page."""
    checkpoint_path = checkpoint_dir / f"{note_id}.json"
    state = _load_checkpoint(checkpoint_path, note_id)
    state.update({"status": "running", "failure": {}})
    _save_checkpoint(checkpoint_path, state)
    failure_context: dict[str, Any] = {"stage": "initializing"}

    try:
        pages_this_run = 0
        while not state["top_complete"] and pages_this_run < max_pages_per_run:
            cursor = str(state.get("top_cursor", ""))
            failure_context = {"stage": "top_comments", "cursor": cursor}
            if cursor in state["seen_top_cursors"]:
                raise RuntimeError(f"top-level cursor repeated: {cursor!r}")
            data = client.get_comments(note_id, cursor=cursor)
            page_comments = data.get("comments", []) if isinstance(data, dict) else []
            state["comments"] = _merge_comments(state["comments"], page_comments)
            state["seen_top_cursors"].append(cursor)
            state["top_pages_fetched"] += 1
            pages_this_run += 1
            has_more = bool(data.get("has_more", False))
            next_cursor = str(data.get("cursor", ""))
            state["top_complete"] = not has_more
            state["top_cursor"] = next_cursor if has_more else ""
            if has_more and not next_cursor:
                raise RuntimeError("top-level response has_more=true but cursor is empty")
            _save_checkpoint(checkpoint_path, state)

        if not state["top_complete"]:
            raise RuntimeError(f"comment page budget reached ({max_pages_per_run}) during top_comments")

        comments_by_id = {_comment_id(comment): comment for comment in state["comments"] if _comment_id(comment)}
        for root_comment_id, comment in comments_by_id.items():
            reply_count = int(comment.get("sub_comment_count", 0) or 0)
            embedded = comment.get("sub_comments", [])
            embedded = embedded if isinstance(embedded, list) else []
            if reply_count <= len(embedded):
                state["reply_state"][root_comment_id] = {"complete": True, "cursor": "", "pages_fetched": 0}
                continue

            reply_state = state["reply_state"].setdefault(root_comment_id, {
                "complete": False,
                "cursor": str(comment.get("sub_comment_cursor", "")),
                "pages_fetched": 0,
                "seen_cursors": [],
            })
            reply_pages_this_run = 0
            while (
                not reply_state["complete"]
                and reply_pages_this_run < max_reply_pages_per_root
                and pages_this_run < max_pages_per_run
            ):
                cursor = str(reply_state.get("cursor", ""))
                failure_context = {
                    "stage": "sub_comments",
                    "root_comment_id": root_comment_id,
                    "cursor": cursor,
                }
                if cursor in reply_state["seen_cursors"]:
                    raise RuntimeError(f"reply cursor repeated for {root_comment_id}: {cursor!r}")
                data = client.get_sub_comments(note_id, root_comment_id, cursor=cursor)
                replies = data.get("comments", []) if isinstance(data, dict) else []
                _merge_replies(comment, replies)
                reply_state["seen_cursors"].append(cursor)
                reply_state["pages_fetched"] += 1
                reply_pages_this_run += 1
                pages_this_run += 1
                has_more = bool(data.get("has_more", False))
                next_cursor = str(data.get("cursor", ""))
                reply_state["complete"] = not has_more
                reply_state["cursor"] = next_cursor if has_more else ""
                if has_more and not next_cursor:
                    raise RuntimeError(f"reply response has_more=true but cursor is empty for {root_comment_id}")
                _save_checkpoint(checkpoint_path, state)

            if not reply_state["complete"]:
                if pages_this_run >= max_pages_per_run:
                    raise RuntimeError(
                        f"comment page budget reached ({max_pages_per_run}) during sub_comments "
                        f"for {root_comment_id}"
                    )
                raise RuntimeError(
                    f"reply page budget reached for {root_comment_id} ({max_reply_pages_per_root})"
                )

        state.update({"status": "complete", "failure": {}})
        _save_checkpoint(checkpoint_path, state)
        return {
            "complete": True,
            "comments": state["comments"],
            "checkpoint": str(checkpoint_path),
            "pages_fetched": state["top_pages_fetched"],
        }
    except Exception as exc:
        reason = str(exc)
        if failure_context.get("stage") == "sub_comments" and getattr(exc, "code", None) == -1:
            reason = (
                "Sub-comment endpoint rejected the request with code=-1 and no platform message "
                f"(root_comment_id={failure_context.get('root_comment_id', '')})"
            )
        state.update({
            "status": "partial",
            "failure": {
                "reason": reason,
                "top_cursor": state.get("top_cursor", ""),
                **failure_context,
            },
        })
        _save_checkpoint(checkpoint_path, state)
        return {
            "complete": False,
            "comments": state["comments"],
            "checkpoint": str(checkpoint_path),
            "pages_fetched": state["top_pages_fetched"],
            "error": reason,
        }

# 5个帖子数据
notes = [
    {
        "note_id": "6a5770f70000000006033b70",
        "title": "特种柜到美加目的港，先确认这4件事",
        "author_name": "Winnie",
        "text": "特种柜到美加目的港，真的不能按普通柜处理\n\n开顶柜、框架箱、冷冻柜、危险品、超限货……\n很多问题不是出在「有没有订到舱」，而是出在到港后：拖车接不接、仓库能不能卸、文件齐不齐、额外费用谁确认。\n\n国内货代接这类货，建议接单前先问清4件事\n\n✅ 货物类型：是不是冷冻/危险品/超限/特殊装卸？\n✅ 目的港条件：到哪个港？是否需要预约或特殊安排？\n✅ 拖车仓库：普通车架和普通仓库能不能接？\n✅ 文件责任：资料谁提供？异常和额外费用谁确认？\n\n特殊货物最怕「货到了再找资源」。\n前端多确认一句，后端可能少很多仓储费、改约费和客户扯皮。\n\n如果你手上有美加线开顶柜、框架箱、冷冻柜、危险品或其他特殊货物，可以私信「特殊货物」，先帮你判断这票目的港要排查哪些节点。\n\n具体操作以实际货物、港口、船司和当地要求为准。@Winnie\n\n#国际物流 #货代 #跨境物流 #美线货代 #目的港服务 #美国拖车 #美国仓储 #特种柜 #PrimeAgency #美加线",
        "images": [],
        "like_count": 1,
        "comment_count": 0,
        "created_at": "2026-06-19"
    },
    {
        "note_id": "6a30f754000000000f006a90",
        "title": "有美加线货代吗？是同行的货",
        "author_name": "蜡笔小旧",
        "text": "#货代[话题]# #国际物流[话题]# #外贸[话题]# #出口外贸[话题]#",
        "images": [],
        "like_count": 20,
        "comment_count": 101,
        "created_at": "2026-04-14"
    },
    {
        "note_id": "69c201290000000021007f35",
        "title": "04年新人闯货代",
        "author_name": "北美物流Felix",
        "text": "刚踏入货代圈没多久\n\n每天：查价、报价、盯船、被客户问懵\n\n一边崩溃一边自愈\n\n#货代[话题]# #美加线[话题]#",
        "images": [],
        "like_count": 18,
        "comment_count": 26,
        "created_at": "2025-12-22"
    },
    {
        "note_id": "6a54a6150000000007029928",
        "title": "求靠谱美加本土尾程卡派！",
        "author_name": "Winnie",
        "text": "做亚马逊大件家具，每月稳定 8-12 条柜，美西、美东、美中 FBA 都要铺货，最近尾程派送问题直接搞崩绩效 之前合作的尾程全是转包....\n\n1. 需求： ✅ 自营本土卡车车队，不层层转包，能书面列明所有尾程附加费\n\n2. ✅ 美西 / 美东 / 美中全境 FBA 锁仓预约，大件重货合规派送\n\n3. ✅ 加拿大本土尾程同步覆盖，不用二次中转\n\n4. ✅ 异常响应快，破损、延误有完整赔付方案\n\n广告勿扰！有真实稳定渠道的同行老板麻烦评论区留言，私发报价明细，长期稳定出货！\n\n#美加线[话题]#  #美线尾程[话题]#  #跨境[话题]#  #加拿大物流[话题]##货代[话题]#",
        "images": [],
        "like_count": 13,
        "comment_count": 40,
        "created_at": "2026-06-15"
    },
    {
        "note_id": "6a57532e000000002102056f",
        "title": "美加线基本港口，一篇搞懂中英文和代码",
        "author_name": "Winnie",
        "text": "货代新人做报价、订舱、目的港沟通时，港口英文名和代码很容易混。\n\n这篇先整理一版「基本港口表」\n✅ 美西/加西：LA、LB、Oakland、Seattle、Tacoma、Vancouver、Prince Rupert\n✅ 美东/东南：NY/NJ、Norfolk、Savannah、Charleston、Miami\n✅ 墨湾/加东：Houston、New Orleans、Montreal、Halifax\n\n小提醒：港口代码这里按 UN/LOCODE 常用口径整理，实际订舱、报关、船司系统可能会出现更细的码头/城市/口岸代码。下单前一定以船司系统为准。\n\n收藏这张地图，下次看美加线报价会快很多\n想要我继续整理「美加线内陆点/铁路坡道代码」，评论区打：内陆点。\n@Winnie @Winnie\n\n#货代 #国际物流 #跨境物流 #外贸干货 #海运 #港口代码 #美国港口 #加拿大港口 #PrimeAgency #美加线",
        "images": [],
        "like_count": 12,
        "comment_count": 0,
        "created_at": "2026-06-18"
    }
]

def sync_to_api(note, *, api_url: str | None = None):
    api_url = api_url or API_URL
    if not api_url:
        raise ValueError("An API destination is required via --api-url or XHS_SYNC_API_URL.")
    payload = build_sync_payload(note)

    try:
        req = urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"error": str(e)}

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resume-safe XHS note/comment synchronization")
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    parser.add_argument("--max-pages-per-run", type=int, default=100)
    parser.add_argument("--max-reply-pages-per-root", type=int, default=20)
    parser.add_argument("--restart", action="store_true", help="Discard existing comment checkpoints")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.restart and args.checkpoint_dir.exists():
        for checkpoint in args.checkpoint_dir.glob("*.json"):
            checkpoint.unlink()

    _browser, cookies = get_cookies()
    completed = 0
    partial = 0
    with RunLock(), XhsClient(cookies, timeout=60, max_retries=1) as client:
        for i, note in enumerate(notes):
            print(f"[{i + 1}/{len(notes)}] 处理: {note['title']}")
            if int(note.get("comment_count", 0) or 0) == 0:
                fetch = {"complete": True, "comments": [], "pages_fetched": 0, "checkpoint": ""}
            else:
                print(f"  获取评论中 (note_id: {note['note_id']})...")
                fetch = fetch_comments_with_checkpoint(
                    client,
                    note["note_id"],
                    checkpoint_dir=args.checkpoint_dir,
                    max_pages_per_run=args.max_pages_per_run,
                    max_reply_pages_per_root=args.max_reply_pages_per_root,
                )

            note["comments"] = fetch["comments"]
            if not fetch["complete"]:
                partial += 1
                print(f"  ⚠️ 评论未抓完整，已保存 {len(fetch['comments'])} 条顶层评论")
                print(f"  失败原因: {fetch['error']}")
                print(f"  下次运行将从检查点继续: {fetch['checkpoint']}")
                print("  跳过 API 同步，避免用部分评论覆盖完整数据")
                print()
                continue

            print(f"  评论抓取完成：{len(fetch['comments'])} 条顶层评论")
            print("  同步到 API...")
            result = sync_to_api(note)
            if "error" in result:
                print(f"  ❌ 同步失败: {result['error']}")
            else:
                completed += 1
                print("  ✅ 同步成功")
            print()

    print(f"全部完成：成功同步 {completed} 个，评论待续抓 {partial} 个")
    return 1 if partial else 0


if __name__ == "__main__":
    raise SystemExit(
        "sync_xhs.py is an internal module. Run scrape_and_sync.py with explicit keywords instead."
    )
