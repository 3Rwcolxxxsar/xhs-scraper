"""Resume and checkpoint tests for the standalone synchronization script."""

import json

from sync_xhs import build_sync_payload, fetch_comments_with_checkpoint, simplify_comment, sync_to_api


class _TopTimeoutClient:
    def __init__(self):
        self.calls = 0

    def get_comments(self, note_id, cursor=""):
        self.calls += 1
        if self.calls == 1:
            return {"comments": [{"id": "c-1", "sub_comment_count": 0}], "has_more": True, "cursor": "next"}
        raise TimeoutError("page timed out")


class _TopResumeClient:
    def get_comments(self, note_id, cursor=""):
        assert cursor == "next"
        return {"comments": [{"id": "c-2", "sub_comment_count": 0}], "has_more": False, "cursor": ""}


def test_top_level_timeout_keeps_page_and_resumes_from_cursor(tmp_path):
    first = fetch_comments_with_checkpoint(_TopTimeoutClient(), "note-1", checkpoint_dir=tmp_path)
    assert first["complete"] is False
    assert [comment["id"] for comment in first["comments"]] == ["c-1"]

    resumed = fetch_comments_with_checkpoint(_TopResumeClient(), "note-1", checkpoint_dir=tmp_path)
    assert resumed["complete"] is True
    assert [comment["id"] for comment in resumed["comments"]] == ["c-1", "c-2"]


class _ReplyTimeoutClient:
    def __init__(self):
        self.reply_calls = 0

    def get_comments(self, note_id, cursor=""):
        return {
            "comments": [{"id": "root-1", "sub_comment_count": 2, "sub_comments": []}],
            "has_more": False,
            "cursor": "",
        }

    def get_sub_comments(self, note_id, root_comment_id, cursor=""):
        self.reply_calls += 1
        if self.reply_calls == 1:
            return {"comments": [{"id": "r-1"}], "has_more": True, "cursor": "reply-next"}
        raise TimeoutError("reply timed out")


class _ReplyResumeClient:
    def get_sub_comments(self, note_id, root_comment_id, cursor=""):
        assert cursor == "reply-next"
        return {"comments": [{"id": "r-2"}], "has_more": False, "cursor": ""}


def test_reply_timeout_keeps_replies_and_resumes_from_cursor(tmp_path):
    first = fetch_comments_with_checkpoint(_ReplyTimeoutClient(), "note-2", checkpoint_dir=tmp_path)
    assert first["complete"] is False
    assert [reply["id"] for reply in first["comments"][0]["sub_comments"]] == ["r-1"]
    failure = json.loads((tmp_path / "note-2.json").read_text())["failure"]
    assert failure["stage"] == "sub_comments"
    assert failure["root_comment_id"] == "root-1"
    assert failure["cursor"] == "reply-next"

    resumed = fetch_comments_with_checkpoint(_ReplyResumeClient(), "note-2", checkpoint_dir=tmp_path)
    assert resumed["complete"] is True
    assert [reply["id"] for reply in resumed["comments"][0]["sub_comments"]] == ["r-1", "r-2"]


class _ReplyBudgetClient:
    def get_comments(self, note_id, cursor=""):
        return {
            "comments": [{"id": "root-1", "sub_comment_count": 2, "sub_comments": []}],
            "has_more": False,
            "cursor": "",
        }

    def get_sub_comments(self, note_id, root_comment_id, cursor=""):
        if not cursor:
            return {"comments": [{"id": "r-1"}], "has_more": True, "cursor": "reply-next"}
        assert cursor == "reply-next"
        return {"comments": [{"id": "r-2"}], "has_more": False, "cursor": ""}


def test_page_budget_covers_top_and_sub_comment_requests(tmp_path):
    client = _ReplyBudgetClient()
    first = fetch_comments_with_checkpoint(
        client,
        "note-budget",
        checkpoint_dir=tmp_path,
        max_pages_per_run=2,
    )
    assert first["complete"] is False
    assert "during sub_comments" in first["error"]
    assert [reply["id"] for reply in first["comments"][0]["sub_comments"]] == ["r-1"]

    resumed = fetch_comments_with_checkpoint(
        client,
        "note-budget",
        checkpoint_dir=tmp_path,
        max_pages_per_run=2,
    )
    assert resumed["complete"] is True
    assert [reply["id"] for reply in resumed["comments"][0]["sub_comments"]] == ["r-1", "r-2"]


def test_simplify_comment_matches_business_api_shape():
    result = simplify_comment({
        "id": "root-1",
        "content": "top",
        "user_info": {"nickname": "Alice", "user_id": "user-1"},
        "ip_location": "上海",
        "like_count": 3,
        "sub_comment_count": "1",
        "sub_comments": [{
            "id": "reply-1",
            "content": "reply",
            "user_info": {"nickname": "Bob", "user_id": "user-2"},
            "liked_count": 2,
        }],
    })

    assert result["id"] == "root-1"
    assert result["user_nickname"] == "Alice"
    assert result["liked_count"] == 3
    assert result["sub_comments"][0]["id"] == "reply-1"
    assert result["sub_comments"][0]["user_id"] == "user-2"


def test_sync_payload_preserves_complete_raw_comment_shape():
    raw_comment = {
        "id": "root-1",
        "note_id": "note-1",
        "content": "top",
        "liked": True,
        "like_count": "3",
        "status": 0,
        "show_tags": ["is_author"],
        "at_users": [{"user_id": "mentioned"}],
        "pictures": [{"url": "https://image.test/comment.jpg"}],
        "user_info": {
            "nickname": "Alice",
            "user_id": "user-1",
            "image": "https://image.test/avatar.jpg",
            "xsec_token": "token",
            "ai_agent": False,
        },
        "sub_comment_count": 1,
        "sub_comments": [{
            "id": "reply-1",
            "target_comment": {"id": "root-1", "user_info": {"user_id": "user-1"}},
        }],
    }
    note = {
        "note_id": "note-1",
        "title": "title",
        "author_name": "author",
        "text": "body",
        "comments": [raw_comment],
    }

    payload = build_sync_payload(note)

    assert payload["comments"] == [raw_comment]


def test_sync_to_api_uses_explicit_destination(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"code":200}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("sync_xhs.urllib.request.urlopen", fake_urlopen)
    note = {
        "note_id": "note-1",
        "title": "title",
        "author_name": "author",
        "text": "body",
        "comments": [{"id": "comment-1", "status": 0}],
    }

    result = sync_to_api(note, api_url="https://collector.example/xhs")

    assert result == {"code": 200}
    assert captured["url"] == "https://collector.example/xhs"
    assert captured["payload"]["comments"] == note["comments"]
