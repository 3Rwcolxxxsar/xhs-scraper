"""Tests for stable, redacted capture records."""

import json

from xhs_cli.capture import capture_comments, capture_note, capture_search, save_capture


def test_capture_comments_preserves_identity_parentage_and_audit_fields():
    capture = capture_comments(
        {
            "comments": [{
                "id": "comment-1",
                "content": "hello",
                "create_time": 123,
                "like_count": "4",
                "sub_comment_count": "2",
                "user_info": {"user_id": "user-1", "nickname": "Alice"},
            }],
            "pages_fetched": 2,
            "total_fetched": 1,
            "stopped_reason": "exhausted",
        },
        note_id="note-1",
    )

    comment = capture["comments"][0]
    assert comment["note_id"] == "note-1"
    assert comment["comment_id"] == "comment-1"
    assert comment["author_id"] == "user-1"
    assert comment["text"] == "hello"
    assert comment["source_url"].endswith("/note-1")
    assert capture["meta"]["stopped_reason"] == "exhausted"


def test_capture_replies_records_parent_relationship():
    capture = capture_comments(
        {
            "comments": [],
            "replies": [{"id": "reply-1", "content": "reply", "_root_comment_id": "root-1"}],
        },
        note_id="note-1",
    )

    assert capture["replies"][0]["parent_comment_id"] == "root-1"
    assert capture["replies"][0]["root_comment_id"] == "root-1"


def test_capture_search_and_note_do_not_write_security_tokens():
    search = capture_search(
        {"items": [{"id": "note-1", "xsec_token": "secret", "note_card": {"title": "Title"}}]},
        keyword="test", sort="general", note_type="all", note_time="week", note_range="unseen", page=1,
    )
    note = capture_note({"items": [{"note_card": {"title": "Title"}}]}, note_id="note-1")

    assert search["notes"][0]["note_id"] == "note-1"
    assert "secret" not in json.dumps(search)
    assert search["meta"]["note_time"] == "week"
    assert search["meta"]["note_range"] == "unseen"
    assert note["note"]["note_id"] == "note-1"


def test_save_capture_writes_manifest(monkeypatch, tmp_path):
    capture = capture_comments({"comments": []}, note_id="note-1")
    paths = save_capture(capture, tmp_path / "comments.json")

    assert json.loads((tmp_path / "comments.manifest.json").read_text())["capture_file"] == "comments.json"
    assert paths["capture"].endswith("comments.json")
