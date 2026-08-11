"""Tests for the keyword-driven production pipeline."""

import json

import pytest

from scrape_and_sync import ScrapePipeline, normalize_note_for_api


def _search_item(note_id, token="token"):
    return {"id": note_id, "model_type": "note", "xsec_token": token, "note_card": {"title": note_id}}


def _related_search_item(item_id="uuid#timestamp"):
    return {"id": item_id, "model_type": "rec_query", "rec_query": {"title": "相关搜索"}}


def _detail(note_id, comment_count=0):
    return {
        "items": [{
            "note_card": {
                "note_id": note_id,
                "title": f"title-{note_id}",
                "desc": "body",
                "user": {"nickname": "author"},
                "interact_info": {"liked_count": "7", "comment_count": str(comment_count)},
                "image_list": [{"url_default": "https://image.test/1.jpg"}],
                "time": 1_700_000_000_000,
            }
        }]
    }


class _PipelineClient:
    def __init__(self, search_pages, comment_count=0):
        self.search_pages = search_pages
        self.comment_count = comment_count
        self.search_calls = []
        self.read_calls = []

    def search_notes(
        self,
        keyword,
        page=1,
        sort="general",
        note_type=0,
        note_time="all",
        note_range="all",
    ):
        self.search_calls.append((keyword, page, sort, note_type, note_time, note_range))
        items = self.search_pages[keyword][page - 1]
        return {"items": items, "has_more": page < len(self.search_pages[keyword])}

    def get_note_detail(self, note_id):
        self.read_calls.append(note_id)
        return _detail(note_id, self.comment_count)


def test_discovery_uses_general_sort_and_first_n_unique_results(monkeypatch, tmp_path):
    monkeypatch.setattr("scrape_and_sync.cache_note_context", lambda *_args, **_kwargs: None)
    client = _PipelineClient({
        "美加线": [
            [_search_item("n1"), _search_item("n1")],
            [_search_item("n2"), _search_item("n3")],
        ],
    })
    pipeline = ScrapePipeline(client, keywords=["美加线"], limit=3, run_dir=tmp_path)

    pipeline.discover()

    assert [item["note_id"] for item in pipeline.manifest["selections"]["美加线"]] == ["n1", "n2", "n3"]
    assert client.search_calls == [
        ("美加线", 1, "general", 0, "all", "all"),
        ("美加线", 2, "general", 0, "all", "all"),
    ]
    raw_pages = sorted((tmp_path / "raw" / "search").glob("*/page-*.json"))
    assert len(raw_pages) == 2
    assert json.loads(raw_pages[0].read_text())["items"][0]["xsec_token"] == "token"
    assert raw_pages[0].stat().st_mode & 0o777 == 0o600


def test_discovery_skips_related_search_cards_and_backfills(monkeypatch, tmp_path):
    monkeypatch.setattr("scrape_and_sync.cache_note_context", lambda *_args, **_kwargs: None)
    client = _PipelineClient({
        "加拿大线货代": [
            [_search_item("n1"), _related_search_item()],
            [_search_item("n2")],
        ],
    })
    pipeline = ScrapePipeline(client, keywords=["加拿大线货代"], limit=2, run_dir=tmp_path)

    pipeline.discover()

    assert [item["note_id"] for item in pipeline.manifest["selections"]["加拿大线货代"]] == ["n1", "n2"]


def test_discovery_resume_restores_tokens_from_raw_search(monkeypatch, tmp_path):
    cached = []
    monkeypatch.setattr(
        "scrape_and_sync.cache_note_context",
        lambda note_id, token, source, **kwargs: cached.append((note_id, token, source, kwargs)),
    )
    client = _PipelineClient({"美国货代": [[_search_item("n1", "saved-token")]]})
    first = ScrapePipeline(client, keywords=["美国货代"], limit=1, run_dir=tmp_path)
    first.discover()
    cached.clear()

    resumed = ScrapePipeline(client, keywords=["美国货代"], limit=1, run_dir=tmp_path)
    resumed.discover()

    assert cached == [("n1", "saved-token", "pc_search", {"context": "keyword:美国货代"})]
    assert client.search_calls == [("美国货代", 1, "general", 0, "all", "all")]


def test_discovery_passes_sort_time_and_scope_filters(monkeypatch, tmp_path):
    monkeypatch.setattr("scrape_and_sync.cache_note_context", lambda *_args, **_kwargs: None)
    client = _PipelineClient({"加拿大货代": [[_search_item("n1")]]})
    pipeline = ScrapePipeline(
        client,
        keywords=["加拿大货代"],
        limit=1,
        run_dir=tmp_path,
        sort="most-commented",
        note_time="week",
        note_range="unseen",
    )

    pipeline.discover()

    assert client.search_calls == [
        ("加拿大货代", 1, "comment_descending", 0, "week", "unseen")
    ]
    assert pipeline.manifest["sort"] == "most-commented"
    assert pipeline.manifest["note_time"] == "week"
    assert pipeline.manifest["note_range"] == "unseen"


def test_manifest_records_and_validates_account(monkeypatch, tmp_path):
    monkeypatch.setattr("scrape_and_sync.cache_note_context", lambda *_args, **_kwargs: None)
    client = _PipelineClient({"美线货代": [[_search_item("n1")]]})
    pipeline = ScrapePipeline(
        client,
        keywords=["美线货代"],
        limit=1,
        run_dir=tmp_path,
        account="worker-a",
    )
    pipeline.discover()
    assert pipeline.manifest["account"] == "worker-a"

    with pytest.raises(ValueError, match="account mismatch"):
        ScrapePipeline(
            client,
            keywords=["美线货代"],
            limit=1,
            run_dir=tmp_path,
            account="worker-b",
        )


def test_comment_count_over_300_is_processed_and_uploaded(monkeypatch, tmp_path):
    monkeypatch.setattr("scrape_and_sync.cache_note_context", lambda *_args, **_kwargs: None)
    client = _PipelineClient({"美国货代": [[_search_item("large-note")]]}, comment_count=501)
    fetch_calls = []
    uploads = []

    def fetcher(_client, note_id, **kwargs):
        fetch_calls.append((note_id, kwargs))
        return {"complete": True, "comments": [{"id": "c1"}], "checkpoint": "cp", "pages_fetched": 51}

    def uploader(payload):
        uploads.append(payload)
        return {"code": 200}

    pipeline = ScrapePipeline(
        client,
        keywords=["美国货代"],
        limit=1,
        run_dir=tmp_path,
        comment_fetcher=fetcher,
        uploader=uploader,
    )
    stats = pipeline.run()

    assert stats["uploaded"] == 1
    assert fetch_calls[0][0] == "large-note"
    assert uploads[0]["comment_count"] == 501
    assert uploads[0]["comments"] == [{"id": "c1"}]


def test_same_note_across_keywords_uploads_once(monkeypatch, tmp_path):
    monkeypatch.setattr("scrape_and_sync.cache_note_context", lambda *_args, **_kwargs: None)
    client = _PipelineClient({
        "美国货代": [[_search_item("same")]],
        "美加线": [[_search_item("same")]],
    })
    uploads = []
    pipeline = ScrapePipeline(
        client,
        keywords=["美国货代", "美加线"],
        limit=1,
        run_dir=tmp_path,
        uploader=lambda payload: uploads.append(payload) or {"code": 200},
    )

    stats = pipeline.run()

    assert stats["uploaded"] == 1
    assert len(uploads) == 1
    assert pipeline.manifest["notes"]["same"]["matched_keywords"] == ["美国货代", "美加线"]


def test_max_notes_per_run_defers_remaining_notes(monkeypatch, tmp_path):
    monkeypatch.setattr("scrape_and_sync.cache_note_context", lambda *_args, **_kwargs: None)
    client = _PipelineClient({"美国货代": [[_search_item("n1"), _search_item("n2"), _search_item("n3")]]})
    pipeline = ScrapePipeline(
        client,
        keywords=["美国货代"],
        limit=3,
        run_dir=tmp_path,
        max_notes_per_run=1,
        uploader=lambda _payload: {"code": 200},
    )

    stats = pipeline.run()

    assert stats["attempted"] == 1
    assert stats["uploaded"] == 1
    assert stats["deferred"] == 2
    assert client.read_calls == ["n1"]


def test_unavailable_note_is_not_retried(monkeypatch, tmp_path):
    monkeypatch.setattr("scrape_and_sync.cache_note_context", lambda *_args, **_kwargs: None)
    client = _PipelineClient({"美国货代": [[_search_item("n1")]]})
    client.get_note_detail = lambda _note_id: (_ for _ in ()).throw(
        ValueError("Note not found in HTML state: empty noteDetailMap")
    )
    first = ScrapePipeline(client, keywords=["美国货代"], limit=1, run_dir=tmp_path)
    assert first.run()["unavailable"] == 1

    client.get_note_detail = lambda _note_id: (_ for _ in ()).throw(AssertionError("retried"))
    resumed = ScrapePipeline(client, keywords=["美国货代"], limit=1, run_dir=tmp_path)
    stats = resumed.run()

    assert stats["attempted"] == 0
    assert stats["skipped_unavailable"] == 1


def test_partial_comments_are_checkpointed_and_not_uploaded(monkeypatch, tmp_path):
    monkeypatch.setattr("scrape_and_sync.cache_note_context", lambda *_args, **_kwargs: None)
    client = _PipelineClient({"美国货代": [[_search_item("n1")]]}, comment_count=10)
    uploads = []
    pipeline = ScrapePipeline(
        client,
        keywords=["美国货代"],
        limit=1,
        run_dir=tmp_path,
        comment_fetcher=lambda *_args, **_kwargs: {
            "complete": False,
            "comments": [{"id": "c1"}],
            "checkpoint": "checkpoint.json",
            "error": "timeout",
        },
        uploader=lambda payload: uploads.append(payload) or {"code": 200},
    )

    stats = pipeline.run()

    assert stats["partial"] == 1
    assert uploads == []
    assert pipeline.manifest["notes"]["n1"]["status"] == "partial"


def test_partial_resume_reuses_saved_note_detail(monkeypatch, tmp_path):
    monkeypatch.setattr("scrape_and_sync.cache_note_context", lambda *_args, **_kwargs: None)
    client = _PipelineClient({"美国货代": [[_search_item("n1")]]}, comment_count=10)
    fetches = iter([
        {"complete": False, "comments": [{"id": "c1"}], "checkpoint": "cp", "error": "timeout"},
        {"complete": True, "comments": [{"id": "c1"}, {"id": "c2"}], "checkpoint": "cp"},
    ])
    def fetcher(*_args, **_kwargs):
        return next(fetches)

    first = ScrapePipeline(
        client,
        keywords=["美国货代"],
        limit=1,
        run_dir=tmp_path,
        dry_run=True,
        comment_fetcher=fetcher,
    )
    assert first.run()["partial"] == 1
    assert client.read_calls == ["n1"]

    client.get_note_detail = lambda _note_id: (_ for _ in ()).throw(AssertionError("detail refetched"))
    second = ScrapePipeline(
        client,
        keywords=["美国货代"],
        limit=1,
        run_dir=tmp_path,
        dry_run=True,
        comment_fetcher=fetcher,
    )

    assert second.run()["ready"] == 1
    assert client.read_calls == ["n1"]


def test_normalize_note_for_api_keeps_images_and_metrics():
    note = normalize_note_for_api(_detail("n1", 12), "n1")
    assert note["note_id"] == "n1"
    assert note["images"] == ["https://image.test/1.jpg"]
    assert note["like_count"] == 7
    assert note["comment_count"] == 12


def test_raw_note_response_preserves_fields_not_in_business_payload(monkeypatch, tmp_path):
    monkeypatch.setattr("scrape_and_sync.cache_note_context", lambda *_args, **_kwargs: None)
    client = _PipelineClient({"美国货代": [[_search_item("n1")]]})
    detail = _detail("n1")
    detail["items"][0]["note_card"]["extra_platform_field"] = {"tags": ["物流"]}
    client.get_note_detail = lambda _note_id: detail
    pipeline = ScrapePipeline(client, keywords=["美国货代"], limit=1, run_dir=tmp_path, dry_run=True)

    stats = pipeline.run()

    assert stats["ready"] == 1
    raw_path = tmp_path / "raw" / "notes" / "n1.json"
    assert json.loads(raw_path.read_text()) == detail
    payload = json.loads((tmp_path / "payloads" / "n1.json").read_text())
    assert "extra_platform_field" not in payload
    assert pipeline.manifest["field_policy"]["api_payload"] == "known-note-fields-plus-raw-comments"


def test_ready_payload_is_reused_without_refetch(monkeypatch, tmp_path):
    monkeypatch.setattr("scrape_and_sync.cache_note_context", lambda *_args, **_kwargs: None)
    client = _PipelineClient({"美加线": [[_search_item("n1")]]})
    first = ScrapePipeline(client, keywords=["美加线"], limit=1, run_dir=tmp_path, dry_run=True)
    first_stats = first.run()
    assert first_stats["ready"] == 1
    assert client.read_calls == ["n1"]

    uploads = []
    second = ScrapePipeline(
        client,
        keywords=["美加线"],
        limit=1,
        run_dir=tmp_path,
        uploader=lambda payload: uploads.append(payload) or {"code": 200},
    )
    second_stats = second.run()

    assert second_stats["uploaded"] == 1
    assert client.read_calls == ["n1"]
    assert len(uploads) == 1
