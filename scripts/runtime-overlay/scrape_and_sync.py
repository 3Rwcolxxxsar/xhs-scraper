#!/usr/bin/env python3
"""Keyword-driven, resume-safe Xiaohongshu collection and API sync pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sync_xhs import API_URL, fetch_comments_with_checkpoint, sync_to_api
from xhs_cli.client import XhsClient
from xhs_cli.cookies import cache_note_context, get_cookies, normalize_account_name
from xhs_cli.exceptions import (
    IpBlockedError,
    NeedVerifyError,
    RateLimitedError,
    RiskPausedError,
    RunLimitReachedError,
    SessionExpiredError,
)
from xhs_cli.risk_control import RunLock

DEFAULT_RUNS_DIR = Path(__file__).with_name(".xhs-scrape-runs")
SEARCH_SORTS = {
    "general": "general",
    "latest": "time_descending",
    "most-liked": "popularity_descending",
    "most-commented": "comment_descending",
    "most-collected": "collect_descending",
}
SEARCH_TIMES = ("all", "day", "week", "half-year")
SEARCH_RANGES = ("all", "seen", "unseen", "followed")


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)
    path.chmod(0o600)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _note_id(item: dict[str, Any]) -> str:
    card = item.get("note_card") or {}
    return str(item.get("id") or card.get("note_id") or card.get("id") or "")


def _is_note_search_item(item: dict[str, Any]) -> bool:
    """Reject related-search and other non-note cards mixed into search results."""
    model_type = str(item.get("model_type") or "")
    note_id = _note_id(item)
    return bool(note_id) and "#" not in note_id and (not model_type or model_type == "note")


def _run_key(
    keywords: list[str],
    limit: int,
    sort: str,
    note_time: str,
    note_range: str,
) -> str:
    raw = json.dumps(
        {
            "keywords": keywords,
            "limit": limit,
            "sort": sort,
            "note_time": note_time,
            "note_range": note_range,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()[:10]
    readable = "-".join(keyword.strip().replace("/", "_") for keyword in keywords)[:48] or "xhs"
    return f"{readable}-{limit}-{digest}"


def _keyword_key(keyword: str) -> str:
    digest = hashlib.sha256(keyword.encode()).hexdigest()[:10]
    return f"keyword-{digest}"


def _restore_search_contexts(run_dir: Path, keyword: str, note_ids: set[str]) -> None:
    """Rebuild expiring token cache from restricted raw search snapshots."""
    if not note_ids:
        return
    search_dir = run_dir / "raw" / "search" / _keyword_key(keyword)
    restored: set[str] = set()
    for path in sorted(search_dir.glob("page-*.json")):
        result = _load_json(path)
        for item in result.get("items", []):
            if not isinstance(item, dict):
                continue
            note_id = _note_id(item)
            if note_id not in note_ids or note_id in restored:
                continue
            card = item.get("note_card") or {}
            token = str(item.get("xsec_token") or card.get("xsec_token") or "")
            if token:
                cache_note_context(note_id, token, "pc_search", context=f"keyword:{keyword}")
                restored.add(note_id)


def _timestamp_to_date(value: Any) -> str:
    timestamp = _int(value)
    if not timestamp:
        return ""
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return datetime.fromtimestamp(timestamp, tz=UTC).date().isoformat()


def normalize_note_for_api(data: dict[str, Any], note_id: str) -> dict[str, Any]:
    """Normalize feed API or HTML note detail to the business API payload."""
    item = (data.get("items") or [{}])[0] if isinstance(data, dict) else {}
    card = item.get("note_card") if isinstance(item, dict) else None
    card = card or data
    user = card.get("user") or card.get("user_info") or {}
    interact = card.get("interact_info") or {}
    images = []
    for image in card.get("image_list", card.get("images", [])) or []:
        if isinstance(image, str):
            images.append(image)
            continue
        if not isinstance(image, dict):
            continue
        url = image.get("url_default") or image.get("url")
        if not url:
            info_list = image.get("info_list") or []
            if info_list and isinstance(info_list[0], dict):
                url = info_list[0].get("url", "")
        if url:
            images.append(url)

    return {
        "note_id": str(card.get("note_id") or card.get("id") or note_id),
        "title": str(card.get("title") or card.get("display_title") or ""),
        "author_name": str(user.get("nickname") or user.get("nick_name") or ""),
        "text": str(card.get("desc") or card.get("content") or ""),
        "images": images,
        "like_count": _int(interact.get("liked_count", card.get("liked_count", 0))),
        "comment_count": _int(interact.get("comment_count", card.get("comment_count", 0))),
        "created_at": _timestamp_to_date(
            card.get("time") or card.get("create_time") or card.get("last_update_time")
        ),
    }


class ScrapePipeline:
    def __init__(
        self,
        client: XhsClient,
        *,
        keywords: list[str],
        limit: int,
        run_dir: Path,
        sort: str = "general",
        note_time: str = "all",
        note_range: str = "all",
        account: str = "",
        sync_target: str = API_URL,
        max_search_pages: int = 20,
        max_comment_pages_per_run: int = 100,
        max_reply_pages_per_root: int = 20,
        max_notes_per_run: int = 0,
        dry_run: bool = False,
        uploader: Callable[[dict[str, Any]], dict[str, Any]] = sync_to_api,
        comment_fetcher: Callable[..., dict[str, Any]] = fetch_comments_with_checkpoint,
    ):
        self.client = client
        self.keywords = keywords
        self.limit = limit
        self.run_dir = run_dir
        self.sort = sort
        self.note_time = note_time
        self.note_range = note_range
        self.account = normalize_account_name(account)
        self.max_search_pages = max_search_pages
        self.max_comment_pages_per_run = max_comment_pages_per_run
        self.max_reply_pages_per_root = max_reply_pages_per_root
        self.max_notes_per_run = max(0, max_notes_per_run)
        self.dry_run = dry_run
        self.uploader = uploader
        self.comment_fetcher = comment_fetcher
        self.manifest_path = run_dir / "manifest.json"
        self.manifest = _load_json(self.manifest_path) or {
            "schema": "xhs-keyword-sync-run.v1",
            "keywords": keywords,
            "limit_per_keyword": limit,
            "sort": sort,
            "note_time": note_time,
            "note_range": note_range,
            "sync_target": sync_target,
            "account": self.account,
            "selections": {},
            "notes": {},
            "field_policy": {
                "raw_platform_responses_preserved": True,
                "api_payload": "known-note-fields-plus-raw-comments",
            },
        }
        self.manifest["field_policy"] = {
            "raw_platform_responses_preserved": True,
            "api_payload": "known-note-fields-plus-raw-comments",
        }
        expected_filters = {
            "sort": sort,
            "note_time": note_time,
            "note_range": note_range,
        }
        for key, expected in expected_filters.items():
            existing = self.manifest.get(key, "all" if key != "sort" else "general")
            if existing != expected:
                raise ValueError(
                    f"Run directory filter mismatch for {key}: checkpoint={existing!r}, requested={expected!r}"
                )
            self.manifest[key] = expected
        self.manifest["sync_target"] = sync_target
        existing_account = str(self.manifest.get("account") or "")
        if existing_account and existing_account != self.account:
            raise ValueError(
                f"Run directory account mismatch: checkpoint={existing_account!r}, requested={self.account!r}"
            )
        self.manifest["account"] = self.account

    def save(self) -> None:
        self.manifest["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _atomic_json(self.manifest_path, self.manifest)

    def discover(self) -> None:
        """Persist the first N unique notes per keyword in API-returned filter order."""
        for keyword in self.keywords:
            existing = self.manifest["selections"].get(keyword, [])
            selected = []
            for entry in existing:
                note_id = str(entry.get("note_id") or "")
                if not note_id or "#" in note_id:
                    self.manifest["notes"].pop(note_id, None)
                    continue
                selected.append({**entry, "rank": len(selected) + 1})
            self.manifest["selections"][keyword] = selected
            self.save()
            selected_ids = {entry["note_id"] for entry in selected}
            _restore_search_contexts(self.run_dir, keyword, selected_ids)
            page = 1
            while len(selected) < self.limit and page <= self.max_search_pages:
                result = self.client.search_notes(
                    keyword,
                    page=page,
                    sort=SEARCH_SORTS[self.sort],
                    note_type=0,
                    note_time=self.note_time,
                    note_range=self.note_range,
                )
                raw_search_path = self.run_dir / "raw" / "search" / _keyword_key(keyword) / f"page-{page}.json"
                _atomic_json(raw_search_path, result)
                items = result.get("items", []) if isinstance(result, dict) else []
                for item in items:
                    if not isinstance(item, dict) or not _is_note_search_item(item):
                        continue
                    note_id = _note_id(item)
                    if not note_id or note_id in selected_ids:
                        continue
                    card = item.get("note_card") or {}
                    token = str(item.get("xsec_token") or card.get("xsec_token") or "")
                    if token:
                        cache_note_context(note_id, token, "pc_search", context=f"keyword:{keyword}")
                    selected.append({"note_id": note_id, "rank": len(selected) + 1, "search_page": page})
                    selected_ids.add(note_id)
                    if len(selected) >= self.limit:
                        break
                self.manifest["selections"][keyword] = selected
                self.save()
                if len(selected) >= self.limit or not bool(result.get("has_more", False)):
                    break
                page += 1
            if len(selected) < self.limit:
                raise RuntimeError(
                    f"Keyword {keyword!r} returned only {len(selected)} unique notes; requested {self.limit}."
                )

    def _queue(self) -> list[tuple[str, list[str]]]:
        ordered: list[str] = []
        matches: dict[str, list[str]] = {}
        for keyword in self.keywords:
            for entry in self.manifest["selections"].get(keyword, []):
                note_id = entry["note_id"]
                if note_id not in matches:
                    ordered.append(note_id)
                    matches[note_id] = []
                matches[note_id].append(keyword)
        return [(note_id, matches[note_id]) for note_id in ordered]

    def process(self) -> dict[str, int]:
        stats = {
            "uploaded": 0,
            "ready": 0,
            "partial": 0,
            "failed": 0,
            "unavailable": 0,
            "attempted": 0,
            "deferred": 0,
            "skipped_uploaded": 0,
            "skipped_unavailable": 0,
        }
        comment_dir = self.run_dir / "comments"
        queue = self._queue()
        for index, (note_id, matched_keywords) in enumerate(queue):
            existing_state = self.manifest["notes"].get(note_id, {})
            if existing_state.get("status") == "uploaded":
                stats["skipped_uploaded"] += 1
                continue
            if existing_state.get("status") == "unavailable":
                stats["skipped_unavailable"] += 1
                continue
            if self.max_notes_per_run and stats["attempted"] >= self.max_notes_per_run:
                stats["deferred"] = sum(
                    1
                    for pending_id, _keywords in queue[index:]
                    if self.manifest["notes"].get(pending_id, {}).get("status")
                    not in {"uploaded", "unavailable"}
                )
                break
            state = self.manifest["notes"].setdefault(note_id, {})
            state["matched_keywords"] = matched_keywords
            stats["attempted"] += 1
            if state.get("status") == "ready" and state.get("payload"):
                payload = _load_json(Path(state["payload"]))
                if payload:
                    if self.dry_run:
                        stats["ready"] += 1
                        continue
                    response = self.uploader(payload)
                    if response.get("error"):
                        state.update({"status": "failed", "error": response["error"]})
                        stats["failed"] += 1
                    else:
                        state.update({"status": "uploaded", "error": "", "api_response": response})
                        stats["uploaded"] += 1
                    self.save()
                    continue
            try:
                note = state.get("note")
                if not isinstance(note, dict) or not note.get("note_id"):
                    detail = self.client.get_note_detail(note_id)
                    raw_note_path = self.run_dir / "raw" / "notes" / f"{note_id}.json"
                    _atomic_json(raw_note_path, detail)
                    note = normalize_note_for_api(detail, note_id)
                    state["note"] = note
                    state["raw_note"] = str(raw_note_path)
                    self.save()
                if note["comment_count"]:
                    fetched = self.comment_fetcher(
                        self.client,
                        note_id,
                        checkpoint_dir=comment_dir,
                        max_pages_per_run=self.max_comment_pages_per_run,
                        max_reply_pages_per_root=self.max_reply_pages_per_root,
                    )
                else:
                    fetched = {"complete": True, "comments": [], "checkpoint": "", "pages_fetched": 0}

                state["comment_checkpoint"] = fetched.get("checkpoint", "")
                state["top_comment_count"] = len(fetched["comments"])
                if not fetched["complete"]:
                    state.update({"status": "partial", "error": fetched.get("error", "comment fetch incomplete")})
                    stats["partial"] += 1
                    self.save()
                    continue

                payload = {**note, "comments": fetched["comments"]}
                payload_path = self.run_dir / "payloads" / f"{note_id}.json"
                _atomic_json(payload_path, payload)
                state["payload"] = str(payload_path)
                if self.dry_run:
                    state.update({"status": "ready", "error": ""})
                    stats["ready"] += 1
                    self.save()
                    continue

                response = self.uploader(payload)
                if response.get("error"):
                    state.update({"status": "failed", "error": response["error"]})
                    stats["failed"] += 1
                else:
                    state.update({"status": "uploaded", "error": "", "api_response": response})
                    stats["uploaded"] += 1
                self.save()
            except (
                RiskPausedError,
                RunLimitReachedError,
                RateLimitedError,
                NeedVerifyError,
                SessionExpiredError,
                IpBlockedError,
            ) as exc:
                state.update({"status": "partial", "error": str(exc)})
                stats["partial"] += 1
                self.save()
                break
            except Exception as exc:
                reason = str(exc)
                if "Note not found in HTML state" in reason:
                    state.update({"status": "unavailable", "error": reason})
                    stats["unavailable"] += 1
                else:
                    state.update({"status": "failed", "error": reason})
                    stats["failed"] += 1
                self.save()
        return stats

    def run(self) -> dict[str, int]:
        self.discover()
        return self.process()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search keywords, collect the first N notes, and sync them")
    parser.add_argument("--keyword", action="append", dest="keywords", required=True, help="Repeat for each keyword")
    parser.add_argument("--limit", type=int, required=True, help="First N returned notes per keyword")
    parser.add_argument("--sort", choices=tuple(SEARCH_SORTS), default="general")
    parser.add_argument("--time", dest="note_time", choices=SEARCH_TIMES, default="all")
    parser.add_argument("--scope", dest="note_range", choices=SEARCH_RANGES, default="all")
    parser.add_argument("--api-url", default=API_URL, help="Destination API endpoint")
    parser.add_argument("--account", default="", help="Local account alias for isolated credentials and locks")
    parser.add_argument(
        "--cookie-source",
        choices=("webbridge", "saved"),
        default="webbridge",
        help="Kimi WebBridge session source",
    )
    parser.add_argument("--run-dir", type=Path, help="Override the deterministic checkpoint directory")
    parser.add_argument("--max-search-pages", type=int, default=20)
    parser.add_argument("--max-comment-pages-per-run", type=int, default=8)
    parser.add_argument("--max-reply-pages-per-root", type=int, default=20)
    parser.add_argument(
        "--max-notes-per-run",
        type=int,
        default=2,
        help="Maximum non-complete notes attempted in one invocation; 0 disables the limit",
    )
    parser.add_argument("--dry-run", action="store_true", help="Collect and save payloads without API upload")
    parser.add_argument("--restart", action="store_true", help="Discard checkpoints for this keyword request")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    keywords = [keyword.strip() for keyword in args.keywords if keyword.strip()]
    if not keywords or args.limit < 1:
        raise SystemExit("At least one non-empty --keyword and --limit >= 1 are required.")
    if not args.dry_run and not args.api_url:
        raise SystemExit("--api-url or XHS_SYNC_API_URL is required unless --dry-run is used.")
    try:
        account = normalize_account_name(args.account)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if account:
        os.environ["XHS_ACCOUNT"] = account
    runs_root = DEFAULT_RUNS_DIR / "accounts" / account if account else DEFAULT_RUNS_DIR
    run_dir = args.run_dir or runs_root / _run_key(
        keywords,
        args.limit,
        args.sort,
        args.note_time,
        args.note_range,
    )
    if args.restart and run_dir.exists():
        shutil.rmtree(run_dir)

    _browser, cookies = get_cookies(
        args.cookie_source,
    )
    with RunLock(), XhsClient(cookies, timeout=60, max_retries=1) as client:
        pipeline = ScrapePipeline(
            client,
            keywords=keywords,
            limit=args.limit,
            run_dir=run_dir,
            sort=args.sort,
            note_time=args.note_time,
            note_range=args.note_range,
            account=account,
            sync_target=args.api_url,
            max_search_pages=args.max_search_pages,
            max_comment_pages_per_run=args.max_comment_pages_per_run,
            max_reply_pages_per_root=args.max_reply_pages_per_root,
            max_notes_per_run=args.max_notes_per_run,
            dry_run=args.dry_run,
            uploader=lambda payload: sync_to_api(payload, api_url=args.api_url),
        )
        stats = pipeline.run()

    print(json.dumps({"run_dir": str(run_dir), **stats}, ensure_ascii=False, indent=2))
    return 1 if stats["partial"] or stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
