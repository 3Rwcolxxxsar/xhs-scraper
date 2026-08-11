#!/usr/bin/env python3
"""Install the version-pinned xhs-scraper runtime overlay into an upstream clone."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


BASE_COMMIT = "4d63f3c0c85ccd9054fa8e96d7f761aaf2507449"
OVERLAY_ROOT = Path(__file__).with_name("runtime-overlay")
FILES = (
    "scrape_and_sync.py",
    "sync_xhs.py",
    "xhs_cli/capture.py",
    "xhs_cli/cli.py",
    "xhs_cli/client.py",
    "xhs_cli/client_mixins.py",
    "xhs_cli/cookies.py",
    "xhs_cli/error_codes.py",
    "xhs_cli/exceptions.py",
    "xhs_cli/risk_control.py",
    "xhs_cli/webbridge.py",
    "xhs_cli/commands/_common.py",
    "xhs_cli/commands/auth.py",
    "xhs_cli/commands/reading.py",
    "xhs_cli/commands/risk.py",
    "tests/conftest.py",
    "tests/test_anti_detection.py",
    "tests/test_capture.py",
    "tests/test_cli.py",
    "tests/test_client.py",
    "tests/test_cookies.py",
    "tests/test_integration.py",
    "tests/test_risk_control.py",
    "tests/test_scrape_and_sync.py",
    "tests/test_sync_xhs.py",
    "tests/test_webbridge.py",
)


def git_head(target: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(target), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install the xhs-scraper runtime overlay")
    parser.add_argument("--target", type=Path, required=True, help="Clean clone of jackwener/xiaohongshu-cli")
    parser.add_argument("--dry-run", action="store_true", help="List files without changing the target")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow a target that is not the pinned upstream commit; review the backup before running it.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = args.target.resolve()
    if not (target / "pyproject.toml").is_file() or not (target / "xhs_cli").is_dir():
        raise SystemExit("--target must be a source checkout of jackwener/xiaohongshu-cli.")

    head = git_head(target)
    if head != BASE_COMMIT and not args.force:
        raise SystemExit(
            f"Target commit is {head or 'unknown'}, expected {BASE_COMMIT}. "
            "Use a clean pinned clone, or inspect compatibility and rerun with --force."
        )

    if args.dry_run:
        print(f"Would install {len(FILES)} files into {target}")
        for relative in FILES:
            print(relative)
        return 0

    backup_root = target / ".xhs-scraper-overlay-backup" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for relative in FILES:
        source = OVERLAY_ROOT / relative
        destination = target / relative
        if not source.is_file():
            raise SystemExit(f"Overlay is incomplete: {source}")
        if destination.exists():
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    print(f"Installed {len(FILES)} files into {target}")
    print(f"Backup: {backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
