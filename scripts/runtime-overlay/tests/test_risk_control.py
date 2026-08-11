"""Deterministic tests for the local fail-closed risk guard."""

from __future__ import annotations

import httpx
import pytest

from xhs_cli.client import XhsClient
from xhs_cli.exceptions import RateLimitedError, RiskPausedError, RunLimitReachedError
from xhs_cli.risk_control import RiskController, RiskSettings, RunLock


def _settings(**overrides):
    values = {
        "enabled": True,
        "search_min_seconds": 8.0,
        "search_max_seconds": 8.0,
        "note_min_seconds": 10.0,
        "note_max_seconds": 10.0,
        "comment_min_seconds": 5.0,
        "comment_max_seconds": 5.0,
        "max_requests_per_run": 50,
        "cooldown_seconds": 1800.0,
        "max_consecutive_failures": 2,
    }
    values.update(overrides)
    return RiskSettings(**values)


def test_default_intervals_match_keyword_pipeline_policy(monkeypatch):
    for name in (
        "XHS_RISK_SEARCH_MIN_SECONDS",
        "XHS_RISK_SEARCH_MAX_SECONDS",
        "XHS_RISK_NOTE_MIN_SECONDS",
        "XHS_RISK_NOTE_MAX_SECONDS",
        "XHS_RISK_COMMENT_MIN_SECONDS",
        "XHS_RISK_COMMENT_MAX_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = RiskSettings.from_env()

    assert (settings.search_min_seconds, settings.search_max_seconds) == (3.0, 10.0)
    assert (settings.note_min_seconds, settings.note_max_seconds) == (8.0, 24.0)
    assert (settings.comment_min_seconds, settings.comment_max_seconds) == (5.0, 12.0)


def test_risk_guard_enforces_interval_and_persists_request_time(monkeypatch, tmp_path):
    monkeypatch.setenv("XHS_RISK_STATE_FILE", str(tmp_path / "risk.json"))
    now = [100.0]
    sleeps = []
    monkeypatch.setattr("xhs_cli.risk_control.time.time", lambda: now[0])
    monkeypatch.setattr("xhs_cli.risk_control.time.sleep", lambda seconds: sleeps.append(seconds))

    controller = RiskController(_settings())
    controller.before_request("search")
    now[0] = 103.0
    controller.before_request("search")

    assert sleeps == [5.0]
    assert controller.status()["last_request_at"]["search"] == 103.0


def test_risk_guard_pauses_after_a_risk_signal(monkeypatch, tmp_path):
    monkeypatch.setenv("XHS_RISK_STATE_FILE", str(tmp_path / "risk.json"))
    controller = RiskController(_settings(cooldown_seconds=60))

    controller.record_failure("verification_required", immediate_pause=True)

    status = controller.status()
    assert status["paused"] is True
    assert status["pause_reason"] == "verification_required"
    with pytest.raises(RiskPausedError):
        controller.before_request("note")


def test_explicit_run_limit_stops_without_persistent_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv("XHS_RISK_STATE_FILE", str(tmp_path / "risk.json"))
    controller = RiskController(_settings(max_requests_per_run=1))

    controller.before_request("note")
    with pytest.raises(RunLimitReachedError):
        controller.before_request("note")

    assert controller.status()["paused"] is False


def test_run_lock_isolated_by_account(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XHS_RISK_ENABLED", "1")
    monkeypatch.setenv("XHS_ACCOUNT", "worker-a")

    with RunLock():
        with pytest.raises(RiskPausedError, match="another_cli_run_is_active"):
            with RunLock():
                pass

        monkeypatch.setenv("XHS_ACCOUNT", "worker-b")
        with RunLock():
            pass


def test_comment_endpoints_have_their_own_request_kind():
    assert XhsClient._request_kind("https://edith.xiaohongshu.com/api/sns/web/v2/comment/page") == "comment"


def test_http_429_pauses_without_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("XHS_RISK_ENABLED", "1")
    monkeypatch.setenv("XHS_RISK_STATE_FILE", str(tmp_path / "risk.json"))
    monkeypatch.setenv("XHS_RISK_NOTE_MIN_SECONDS", "0")
    monkeypatch.setenv("XHS_RISK_NOTE_MAX_SECONDS", "0")
    client = XhsClient({"a1": "cookie"}, request_delay=0)
    calls = []

    def fake_request(*_args, **_kwargs):
        calls.append(1)
        return httpx.Response(429, request=httpx.Request("GET", "https://example.test"))

    monkeypatch.setattr(client._http, "request", fake_request)
    try:
        with pytest.raises(RateLimitedError):
            client._request_with_retry("GET", "https://example.test/note")
    finally:
        client.close()

    assert len(calls) == 1
    assert RiskController().status()["pause_reason"] == "rate_limited"
