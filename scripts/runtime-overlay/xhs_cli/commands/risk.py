"""Local safety-control commands. These commands never contact Xiaohongshu."""

from __future__ import annotations

import click

from ..formatter import maybe_print_structured, print_info, print_success, success_payload
from ..risk_control import RiskController
from ._common import structured_output_options


@click.command("risk-status")
@structured_output_options
def risk_status(as_json: bool, as_yaml: bool):
    """Show local pacing, pause, and recent failure state."""
    status = RiskController().status()
    if maybe_print_structured(success_payload(status), as_json=as_json, as_yaml=as_yaml):
        return
    if status["paused"]:
        print_info(
            f"Paused for {status['seconds_until_resume']}s: {status['pause_reason']} "
            "(use xhs risk-resume --yes only after checking the account)"
        )
        return
    print_success("Risk guard is ready")
    print_info(f"Consecutive failures: {status['consecutive_failures']}")


@click.command("risk-resume")
@click.option("--yes", is_flag=True, help="Confirm that account status has been checked manually")
def risk_resume(yes: bool):
    """Clear a local pause after manually checking account status."""
    if not yes:
        raise click.UsageError("Review the account first, then rerun with --yes to resume.")
    RiskController().resume()
    print_success("Local risk pause cleared")


@click.command("risk-pause")
@click.option("--minutes", type=click.IntRange(min=1), default=30, show_default=True, help="Local pause duration")
@click.option("--reason", default="manual_pause", show_default=True, help="Reason stored in local audit state")
def risk_pause(minutes: int, reason: str):
    """Proactively pause local requests without contacting Xiaohongshu."""
    RiskController().pause(reason, seconds=minutes * 60)
    print_success(f"Local requests paused for {minutes} minutes")
