"""Tests for Kimi WebBridge session import without a live browser."""

import pytest

from xhs_cli.webbridge import WebBridgeError, extract_xhs_cookies, get_webbridge_url, wait_for_xhs_login


def test_webbridge_url_defaults_to_local_daemon(monkeypatch):
    monkeypatch.delenv("XHS_WEBBRIDGE_URL", raising=False)

    assert get_webbridge_url() == "http://127.0.0.1:10086/command"


def test_webbridge_url_allows_a_profile_specific_daemon(monkeypatch):
    monkeypatch.setenv("XHS_WEBBRIDGE_URL", "http://127.0.0.1:10087/command")

    assert get_webbridge_url() == "http://127.0.0.1:10087/command"


def test_webbridge_url_rejects_non_http_endpoint(monkeypatch):
    monkeypatch.setenv("XHS_WEBBRIDGE_URL", "not-a-url")

    with pytest.raises(WebBridgeError, match="absolute http"):
        get_webbridge_url()


def test_extract_xhs_cookies_navigates_and_filters_domains(monkeypatch):
    calls = []

    def fake_command(action, args, *, session, allow_failure=False):
        calls.append((action, args, session, allow_failure))
        if action == "find_tab":
            return {"ok": False}
        if action == "navigate":
            return {"ok": True, "data": {"success": True}}
        return {
            "ok": True,
            "data": {
                "cookies": [
                    {"name": "a1", "value": "xhs-a1", "domain": ".xiaohongshu.com"},
                    {"name": "web_session", "value": "session", "domain": ".xiaohongshu.com"},
                    {"name": "other", "value": "secret", "domain": ".example.com"},
                ]
            },
        }

    monkeypatch.setattr("xhs_cli.webbridge.command", fake_command)

    cookies = extract_xhs_cookies(account="worker-a")

    assert cookies == {"a1": "xhs-a1", "web_session": "session"}
    assert [call[0] for call in calls] == ["find_tab", "navigate", "cdp"]
    assert all(call[2] == "xhs-cli-worker-a" for call in calls)


def test_extract_xhs_cookies_reuses_session_tab(monkeypatch):
    calls = []

    def fake_command(action, args, *, session, allow_failure=False):
        calls.append(action)
        if action == "find_tab":
            return {"ok": True, "data": {"success": True}}
        return {
            "ok": True,
            "data": {"cookies": [{"name": "a1", "value": "a1", "domain": ".xiaohongshu.com"}]},
        }

    monkeypatch.setattr("xhs_cli.webbridge.command", fake_command)

    assert extract_xhs_cookies() == {"a1": "a1"}
    assert calls == ["find_tab", "cdp"]


def test_wait_for_xhs_login_uses_real_webbridge_tab(monkeypatch):
    calls = []

    def fake_command(action, args, *, session, allow_failure=False):
        calls.append(action)
        if action == "navigate":
            return {"ok": True}
        return {
            "ok": True,
            "data": {
                "cookies": [
                    {"name": "a1", "value": "a1", "domain": ".xiaohongshu.com"},
                    {"name": "web_session", "value": "session", "domain": ".xiaohongshu.com"},
                ]
            },
        }

    monkeypatch.setattr("xhs_cli.webbridge.command", fake_command)

    assert wait_for_xhs_login(timeout_seconds=0.1)["web_session"] == "session"
    assert calls == ["navigate", "cdp"]
