"""Custom exceptions for XHS API client."""


class XhsApiError(Exception):
    """Base exception for XHS API errors."""

    def __init__(self, message: str, code: int | str | None = None, response: dict | None = None):
        super().__init__(message)
        self.code = code
        self.response = response


class NeedVerifyError(XhsApiError):
    """Raised when XHS requires captcha verification."""

    def __init__(self, verify_type: str, verify_uuid: str):
        super().__init__(f"Captcha required: type={verify_type}, uuid={verify_uuid}")
        self.verify_type = verify_type
        self.verify_uuid = verify_uuid


class SessionExpiredError(XhsApiError):
    """Raised when the session has expired."""

    def __init__(self):
        super().__init__(
            "Session expired. xhs-cli could not recover a valid browser session. "
            "Log in through Kimi WebBridge with: xhs --cookie-source webbridge login --qrcode",
            code=-100,
        )


class IpBlockedError(XhsApiError):
    """Raised when IP is blocked by XHS."""

    def __init__(self):
        super().__init__("IP blocked by XHS — try a different network", code=300012)


class SignatureError(XhsApiError):
    """Raised when signature verification fails."""

    def __init__(self):
        super().__init__("Signature verification failed", code=300015)


class RiskPausedError(XhsApiError):
    """Raised before a request when the local safety guard has paused work."""

    def __init__(self, reason: str, paused_until: float):
        super().__init__(
            f"Requests paused for account safety ({reason}). "
            f"Check with: xhs risk-status; resume manually with: xhs risk-resume --yes",
            code="risk_paused",
        )
        self.reason = reason
        self.paused_until = paused_until


class RunLimitReachedError(XhsApiError):
    """Raised when an explicitly configured per-process request budget is exhausted."""

    def __init__(self):
        super().__init__(
            "Local run request budget reached; progress is checkpointed. Start another run when appropriate.",
            code="run_limit_reached",
        )


class RateLimitedError(XhsApiError):
    """Raised on HTTP 429 after the guard pauses the local run."""

    def __init__(self):
        super().__init__("Server rate limited this account; requests have been paused locally.", code="rate_limited")


class UnsupportedOperationError(XhsApiError):
    """Raised when the current web API no longer supports an exposed CLI action."""

    def __init__(self, message: str):
        super().__init__(message, code="unsupported_operation")


class NoCookieError(XhsApiError):
    """Raised when no valid cookies are found."""

    def __init__(self, source: str, details: str = ""):
        if source == "saved":
            msg = "No saved 'a1' cookie found for this local account alias."
        elif source == "webbridge":
            msg = "No 'a1' cookie found in the Xiaohongshu profile connected to Kimi WebBridge."
        elif source == "auto":
            msg = "No 'a1' cookie found for xiaohongshu.com in any installed browser."
        else:
            msg = f"No 'a1' cookie found for xiaohongshu.com in {source}."
        if details:
            msg += f"\n{details}"
        msg += "\n\nTroubleshooting:\n"
        msg += "  1. Connect Kimi WebBridge in the intended browser profile\n"
        msg += "  2. Open https://www.xiaohongshu.com/ and make sure you are logged in\n"
        msg += "  3. Run: xhs --cookie-source webbridge login\n"
        msg += "  4. If needed, scan the QR code in the WebBridge-controlled browser"
        super().__init__(msg)
