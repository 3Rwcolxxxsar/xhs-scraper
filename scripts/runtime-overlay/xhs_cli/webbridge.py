"""Kimi WebBridge client for importing the active browser's XHS session."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .exceptions import XhsApiError

DEFAULT_WEBBRIDGE_URL = "http://127.0.0.1:10086/command"
XHS_HOME_URL = "https://www.xiaohongshu.com/"
XHS_LOGIN_URL = "https://www.xiaohongshu.com/login"


class WebBridgeError(XhsApiError):
    """Raised when the local WebBridge daemon or extension is unavailable."""

    def __init__(self, message: str):
        super().__init__(message, code="webbridge_unavailable")


def get_webbridge_url() -> str:
    """Return the configured WebBridge command endpoint."""
    url = os.getenv("XHS_WEBBRIDGE_URL", DEFAULT_WEBBRIDGE_URL).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WebBridgeError("XHS_WEBBRIDGE_URL must be an absolute http(s) command endpoint.")
    return url


def _start_daemon() -> None:
    binary = Path.home() / ".kimi-webbridge" / "bin" / "kimi-webbridge"
    if not binary.exists():
        raise WebBridgeError("Kimi WebBridge is not installed.")
    subprocess.run(
        [str(binary), "start"],
        check=False,
        capture_output=True,
        timeout=10,
    )


def _post(payload: dict[str, Any], *, timeout: float = 20) -> dict[str, Any]:
    url = get_webbridge_url()
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except (OSError, urllib.error.URLError) as exc:
        if url != DEFAULT_WEBBRIDGE_URL:
            raise WebBridgeError(
                f"Configured Kimi WebBridge daemon is unavailable: {url}. "
                "Start or connect that daemon before retrying."
            ) from exc
        _start_daemon()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except (OSError, urllib.error.URLError) as exc:
            raise WebBridgeError(
                "Kimi WebBridge daemon or browser extension is unavailable. "
                "See https://www.kimi.com/zh-cn/features/webbridge"
            ) from exc
    except json.JSONDecodeError as exc:
        raise WebBridgeError("Kimi WebBridge returned invalid JSON.") from exc


def command(
    action: str,
    args: dict[str, Any],
    *,
    session: str,
    allow_failure: bool = False,
) -> dict[str, Any]:
    """Send one command using a stable task session."""
    result = _post({"action": action, "args": args, "session": session})
    if not result.get("ok") and not allow_failure:
        message = str(result.get("error") or result.get("message") or "command failed")
        if "Please update the Kimi WebBridge extension" in message:
            raise WebBridgeError(
                "Please update the Kimi WebBridge extension: "
                "https://www.kimi.com/zh-cn/features/webbridge"
            )
        raise WebBridgeError(f"Kimi WebBridge {action} failed: {message}")
    return result


def extract_xhs_cookies(*, account: str = "") -> dict[str, str] | None:
    """Import XHS cookies from the one browser profile connected to WebBridge."""
    session = os.getenv("XHS_WEBBRIDGE_SESSION", "").strip() or f"xhs-cli-{account or 'default'}"
    tab = command(
        "find_tab",
        {"url": XHS_HOME_URL},
        session=session,
        allow_failure=True,
    )
    if not tab.get("ok"):
        command(
            "navigate",
            {
                "url": XHS_HOME_URL,
                "newTab": True,
                "group_title": f"小红书 CLI - {account or 'default'}",
            },
            session=session,
        )

    result = command(
        "cdp",
        {"method": "Network.getAllCookies", "params": {}},
        session=session,
    )
    raw_cookies = (result.get("data") or {}).get("cookies") or []
    cookies = {
        str(cookie["name"]): str(cookie.get("value") or "")
        for cookie in raw_cookies
        if "xiaohongshu.com" in str(cookie.get("domain") or "") and cookie.get("name")
    }
    return cookies if cookies.get("a1") else None


def wait_for_xhs_login(
    *,
    account: str = "",
    timeout_seconds: float = 240,
    poll_seconds: float = 2,
) -> dict[str, str]:
    """Open the real WebBridge browser and wait for the user to finish login."""
    session = os.getenv("XHS_WEBBRIDGE_SESSION", "").strip() or f"xhs-cli-{account or 'default'}"
    command(
        "navigate",
        {
            "url": XHS_LOGIN_URL,
            "newTab": True,
            "group_title": f"小红书 CLI 登录 - {account or 'default'}",
        },
        session=session,
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = command(
            "cdp",
            {"method": "Network.getAllCookies", "params": {}},
            session=session,
        )
        raw_cookies = (result.get("data") or {}).get("cookies") or []
        cookies = {
            str(cookie["name"]): str(cookie.get("value") or "")
            for cookie in raw_cookies
            if "xiaohongshu.com" in str(cookie.get("domain") or "") and cookie.get("name")
        }
        if cookies.get("a1") and cookies.get("web_session"):
            return cookies
        time.sleep(poll_seconds)
    raise WebBridgeError("Timed out waiting for Xiaohongshu login in the WebBridge browser.")
