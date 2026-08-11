"""Stable, redacted capture records for notes, search results, and comments."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def captured_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def note_url(note_id: str) -> str:
    return f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""


def _first(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "")), "")


def _comment_id(comment: dict[str, Any]) -> str:
    return str(_first(comment.get("id"), comment.get("comment_id"), comment.get("commentId")))


def normalize_comment(
    comment: dict[str, Any],
    *,
    note_id: str,
    parent_comment_id: str = "",
    root_comment_id: str = "",
    source_url: str = "",
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Normalize an API comment without inventing missing source fields."""
    user = comment.get("user_info") or comment.get("user") or {}
    inferred_root = str(_first(root_comment_id, comment.get("root_comment_id"), comment.get("rootCommentId")))
    inferred_parent = str(_first(parent_comment_id, comment.get("target_comment_id"), comment.get("parent_comment_id")))
    return {
        "note_id": note_id,
        "comment_id": _comment_id(comment),
        "parent_comment_id": inferred_parent,
        "root_comment_id": inferred_root,
        "author_id": str(_first(user.get("user_id"), user.get("id"), user.get("userid"))),
        "author_name": str(_first(user.get("nickname"), user.get("nick_name"), user.get("name"))),
        "text": str(_first(comment.get("content"), comment.get("text"))),
        "created_at": _first(comment.get("create_time"), comment.get("created_at"), comment.get("time")),
        "like_count": _first(comment.get("like_count"), comment.get("liked_count"), 0),
        "reply_count": _first(comment.get("sub_comment_count"), comment.get("sub_comment_num"), 0),
        "source_url": source_url or note_url(note_id),
        "captured_at": timestamp or captured_at(),
    }


def capture_comments(
    data: dict[str, Any],
    *,
    note_id: str,
    root_comment_id: str = "",
    source_url: str = "",
) -> dict[str, Any]:
    """Create a machine-stable capture payload from one comment response."""
    timestamp = captured_at()
    comments = data.get("comments", []) if isinstance(data, dict) else []
    normalized = [
        normalize_comment(
            comment,
            note_id=note_id,
            parent_comment_id=root_comment_id,
            root_comment_id=root_comment_id,
            source_url=source_url,
            timestamp=timestamp,
        )
        for comment in comments
        if isinstance(comment, dict)
    ]
    replies = data.get("replies", []) if isinstance(data, dict) else []
    normalized_replies = [
        normalize_comment(
            reply,
            note_id=note_id,
            parent_comment_id=str(reply.get("_root_comment_id", root_comment_id)),
            root_comment_id=str(reply.get("_root_comment_id", root_comment_id)),
            source_url=source_url,
            timestamp=timestamp,
        )
        for reply in replies
        if isinstance(reply, dict)
    ]
    return {
        "schema": "xhs-comment-capture.v1",
        "meta": {
            "note_id": note_id,
            "root_comment_id": root_comment_id,
            "source_url": source_url or note_url(note_id),
            "captured_at": timestamp,
            "pages_fetched": data.get("pages_fetched", 1) if isinstance(data, dict) else 0,
            "total_fetched": data.get("total_fetched", len(normalized)) if isinstance(data, dict) else len(normalized),
            "duplicates_removed": data.get("duplicates_removed", 0) if isinstance(data, dict) else 0,
            "stopped_reason": (
                data.get("stopped_reason", "single_page") if isinstance(data, dict) else "invalid_response"
            ),
            "has_more": bool(data.get("has_more", False)) if isinstance(data, dict) else False,
            "next_cursor": data.get("cursor", "") if isinstance(data, dict) else "",
        },
        "comments": normalized,
        "replies": normalized_replies,
        "reply_failures": data.get("reply_failures", []) if isinstance(data, dict) else [],
    }


def capture_search(
    data: dict[str, Any],
    *,
    keyword: str,
    sort: str,
    note_type: str,
    note_time: str = "all",
    note_range: str = "all",
    page: int,
) -> dict[str, Any]:
    timestamp = captured_at()
    records = []
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        card = item.get("note_card") or {}
        user = card.get("user") or {}
        interact = card.get("interact_info") or {}
        note_id = str(_first(item.get("id"), card.get("note_id"), card.get("id")))
        records.append({
            "note_id": note_id,
            "title": str(_first(card.get("title"), card.get("display_title"))),
            "author_id": str(_first(user.get("user_id"), user.get("id"))),
            "author_name": str(_first(user.get("nickname"), user.get("nick_name"))),
            "note_type": str(card.get("type", "")),
            "like_count": _first(interact.get("liked_count"), 0),
            "comment_count": _first(interact.get("comment_count"), 0),
            "source_url": note_url(note_id),
            "captured_at": timestamp,
        })
    return {
        "schema": "xhs-search-capture.v1",
        "meta": {
            "keyword": keyword,
            "sort": sort,
            "note_type": note_type,
            "note_time": note_time,
            "note_range": note_range,
            "page": page,
            "captured_at": timestamp,
            "has_more": bool(data.get("has_more", False)) if isinstance(data, dict) else False,
        },
        "notes": records,
    }


def capture_note(data: dict[str, Any], *, note_id: str, source_url: str = "") -> dict[str, Any]:
    """Normalize either feed API or HTML note data into one record."""
    item = (data.get("items") or [{}])[0] if isinstance(data, dict) else {}
    note = item.get("note_card") if isinstance(item, dict) else None
    note = note or data if isinstance(data, dict) else {}
    user = note.get("user") or {}
    interact = note.get("interact_info") or {}
    timestamp = captured_at()
    return {
        "schema": "xhs-note-capture.v1",
        "meta": {"note_id": note_id, "source_url": source_url or note_url(note_id), "captured_at": timestamp},
        "note": {
            "note_id": str(_first(note.get("note_id"), note.get("id"), note_id)),
            "title": str(_first(note.get("title"), note.get("display_title"))),
            "text": str(_first(note.get("desc"), note.get("content"))),
            "author_id": str(_first(user.get("user_id"), user.get("id"))),
            "author_name": str(_first(user.get("nickname"), user.get("nick_name"))),
            "created_at": _first(note.get("time"), note.get("create_time"), note.get("created_at")),
            "like_count": _first(interact.get("liked_count"), note.get("liked_count"), 0),
            "comment_count": _first(interact.get("comment_count"), note.get("comment_count"), 0),
            "collect_count": _first(interact.get("collected_count"), note.get("collected_count"), 0),
            "source_url": source_url or note_url(note_id),
            "captured_at": timestamp,
        },
    }


def save_capture(capture: dict[str, Any], path: str | Path) -> dict[str, str]:
    """Write a capture plus a small redacted manifest suitable for audit/retry."""
    capture_path = Path(path).expanduser()
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(capture, ensure_ascii=False, indent=2) + "\n"
    capture_path.write_text(content)
    manifest_path = capture_path.with_name(f"{capture_path.stem}.manifest.json")
    manifest = {
        "schema": "xhs-capture-manifest.v1",
        "capture_file": capture_path.name,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "captured_at": capture.get("meta", {}).get("captured_at", ""),
        "record_count": len(capture.get("comments", capture.get("notes", [capture.get("note")]))) if capture else 0,
        "stopped_reason": capture.get("meta", {}).get("stopped_reason", ""),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {"capture": str(capture_path), "manifest": str(manifest_path)}
