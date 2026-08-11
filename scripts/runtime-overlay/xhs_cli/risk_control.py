"""Conservative, local-only safeguards for XHS CLI runs.

This module intentionally limits requests and pauses work after risk signals.
It does not attempt to solve captchas, alter device fingerprints, or bypass
platform restrictions.
"""

from __future__ import annotations

import json
import os
import random
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .exceptions import RiskPausedError, RunLimitReachedError


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except ValueError:
        return default


def _env_nonnegative_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class RiskSettings:
    """Runtime settings. Environment variables keep policy out of scripts."""

    enabled: bool
    search_min_seconds: float
    search_max_seconds: float
    note_min_seconds: float
    note_max_seconds: float
    comment_min_seconds: float
    comment_max_seconds: float
    max_requests_per_run: int
    cooldown_seconds: float
    max_consecutive_failures: int

    @classmethod
    def from_env(cls) -> RiskSettings:
        return cls(
            enabled=os.getenv("XHS_RISK_ENABLED", "1").lower() not in {"0", "false", "no"},
            search_min_seconds=_env_float("XHS_RISK_SEARCH_MIN_SECONDS", 3.0),
            search_max_seconds=_env_float("XHS_RISK_SEARCH_MAX_SECONDS", 10.0),
            note_min_seconds=_env_float("XHS_RISK_NOTE_MIN_SECONDS", 8.0),
            note_max_seconds=_env_float("XHS_RISK_NOTE_MAX_SECONDS", 24.0),
            comment_min_seconds=_env_float("XHS_RISK_COMMENT_MIN_SECONDS", 5.0),
            comment_max_seconds=_env_float("XHS_RISK_COMMENT_MAX_SECONDS", 12.0),
            max_requests_per_run=_env_nonnegative_int("XHS_RISK_MAX_REQUESTS_PER_RUN", 0),
            cooldown_seconds=_env_float("XHS_RISK_COOLDOWN_SECONDS", 1800.0),
            max_consecutive_failures=_env_int("XHS_RISK_MAX_CONSECUTIVE_FAILURES", 2),
        )


def _config_dir() -> Path:
    from .cookies import get_config_dir

    configured = os.getenv("XHS_RISK_STATE_FILE", "").strip()
    if configured:
        path = Path(configured).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.parent
    return get_config_dir()


def _state_path() -> Path:
    configured = os.getenv("XHS_RISK_STATE_FILE", "").strip()
    return Path(configured).expanduser() if configured else _config_dir() / "risk_state.json"


def _default_state() -> dict[str, Any]:
    return {
        "last_run_at": 0.0,
        "last_request_at": {},
        "consecutive_failures": 0,
        "paused_until": 0.0,
        "pause_reason": "",
        "last_failure": {},
        "events": [],
    }


class RiskController:
    """Persisted request pacing and fail-closed pause state."""

    def __init__(self, settings: RiskSettings | None = None):
        self.settings = settings or RiskSettings.from_env()
        self._requests_in_run = 0

    def _load(self) -> dict[str, Any]:
        path = _state_path()
        if not path.exists():
            return _default_state()
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return _default_state()
        state = _default_state()
        if isinstance(data, dict):
            state.update({key: value for key, value in data.items() if key in state})
        return state

    def _save(self, state: dict[str, Any]) -> None:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        temp_path.replace(path)
        path.chmod(0o600)

    def _event(self, state: dict[str, Any], kind: str, **details: Any) -> None:
        events = state.setdefault("events", [])
        if not isinstance(events, list):
            events = state["events"] = []
        events.append({"at": time.time(), "kind": kind, **details})
        del events[:-20]

    def status(self) -> dict[str, Any]:
        state = self._load()
        now = time.time()
        paused_until = float(state.get("paused_until", 0) or 0)
        return {
            "enabled": self.settings.enabled,
            "paused": paused_until > now,
            "paused_until": paused_until,
            "pause_reason": state.get("pause_reason", ""),
            "seconds_until_resume": max(0, round(paused_until - now)),
            "last_run_at": state.get("last_run_at", 0.0),
            "last_request_at": state.get("last_request_at", {}),
            "consecutive_failures": state.get("consecutive_failures", 0),
            "last_failure": state.get("last_failure", {}),
            "recent_events": state.get("events", [])[-10:],
        }

    def resume(self) -> None:
        state = self._load()
        state.update({"paused_until": 0.0, "pause_reason": "", "consecutive_failures": 0})
        self._event(state, "manual_resume")
        self._save(state)

    def before_request(self, request_kind: str) -> None:
        if not self.settings.enabled:
            return
        state = self._load()
        now = time.time()
        paused_until = float(state.get("paused_until", 0) or 0)
        if paused_until > now:
            raise RiskPausedError(state.get("pause_reason", "risk pause"), paused_until)
        if self.settings.max_requests_per_run and self._requests_in_run >= self.settings.max_requests_per_run:
            raise RunLimitReachedError()

        minimum, maximum = self._interval(request_kind)
        previous = float((state.get("last_request_at") or {}).get(request_kind, 0) or 0)
        target = random.uniform(minimum, max(minimum, maximum))
        sleep_seconds = max(0.0, previous + target - now)
        if sleep_seconds:
            time.sleep(sleep_seconds)

        state = self._load()
        state["last_run_at"] = time.time()
        state.setdefault("last_request_at", {})[request_kind] = time.time()
        self._requests_in_run += 1
        self._save(state)

    def record_success(self) -> None:
        if not self.settings.enabled:
            return
        state = self._load()
        if state.get("consecutive_failures"):
            state["consecutive_failures"] = 0
            self._event(state, "request_recovered")
            self._save(state)

    def record_failure(self, reason: str, *, immediate_pause: bool = False) -> None:
        if not self.settings.enabled:
            return
        state = self._load()
        failures = int(state.get("consecutive_failures", 0) or 0) + 1
        state["consecutive_failures"] = failures
        state["last_failure"] = {"at": time.time(), "reason": reason}
        self._event(state, "request_failure", reason=reason, consecutive_failures=failures)
        if immediate_pause or failures >= self.settings.max_consecutive_failures:
            state["paused_until"] = time.time() + self.settings.cooldown_seconds
            state["pause_reason"] = reason
            self._event(state, "risk_paused", reason=reason)
        self._save(state)

    def pause(self, reason: str, *, seconds: float | None = None) -> None:
        state = self._load()
        state["paused_until"] = time.time() + (self.settings.cooldown_seconds if seconds is None else seconds)
        state["pause_reason"] = reason
        self._event(state, "risk_paused", reason=reason)
        self._save(state)

    def _interval(self, request_kind: str) -> tuple[float, float]:
        if request_kind == "search":
            return self.settings.search_min_seconds, self.settings.search_max_seconds
        if request_kind == "comment":
            return self.settings.comment_min_seconds, self.settings.comment_max_seconds
        return self.settings.note_min_seconds, self.settings.note_max_seconds


class RunLock(AbstractContextManager["RunLock"]):
    """One local CLI run at a time, preventing concurrent account activity."""

    def __init__(self):
        self._file = None

    def __enter__(self) -> RunLock:
        if not RiskSettings.from_env().enabled:
            return self
        import fcntl

        path = _config_dir() / "risk_run.lock"
        self._file = path.open("a+")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._file.close()
            self._file = None
            raise RiskPausedError("another_cli_run_is_active", time.time()) from None
        return self

    def __exit__(self, *_args: object) -> None:
        if self._file is not None:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
