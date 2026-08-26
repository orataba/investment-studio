from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess

from portfolio_app.core.settings import get_settings
from portfolio_app.services.transaction_captures import (
    finish_transaction_capture_analysis_run,
    has_transaction_capture_agent_revision,
    mark_transaction_capture_analysis_run_started,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
HARNESS_RUNNER = BACKEND_ROOT / "scripts" / "run_portfolio_copilot_harness.sh"


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _run_harness_process(*, portfolio_id: str, batch_id: str) -> tuple[int, bool]:
    process = subprocess.Popen(
        [str(HARNESS_RUNNER), portfolio_id, batch_id],
        cwd=BACKEND_ROOT.parents[2],
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        return process.wait(
            timeout=get_settings().copilot_analysis_timeout_seconds,
        ), False
    except subprocess.TimeoutExpired:
        _stop_process_group(process)
        return process.returncode or -1, True


def run_transaction_capture_analysis(
    *,
    portfolio_id: str,
    batch_id: str,
    attempt: int,
) -> None:
    started, starting_revision = mark_transaction_capture_analysis_run_started(
        portfolio_id=portfolio_id,
        batch_id=batch_id,
        attempt=attempt,
    )
    if not started:
        return

    try:
        return_code, timed_out = _run_harness_process(
            portfolio_id=portfolio_id,
            batch_id=batch_id,
        )
        revision_created = has_transaction_capture_agent_revision(
            portfolio_id=portfolio_id,
            batch_id=batch_id,
            after_revision=starting_revision,
        )
        if revision_created:
            finish_transaction_capture_analysis_run(
                portfolio_id=portfolio_id,
                batch_id=batch_id,
                attempt=attempt,
                succeeded=True,
            )
            return
        if timed_out:
            error = "Analysis timed out before producing a review revision."
        elif return_code != 0:
            error = "DeepSeek analysis failed before producing a review revision."
        else:
            error = "The agent finished without producing a review revision."
        finish_transaction_capture_analysis_run(
            portfolio_id=portfolio_id,
            batch_id=batch_id,
            attempt=attempt,
            succeeded=False,
            error=error,
        )
    except Exception:
        finish_transaction_capture_analysis_run(
            portfolio_id=portfolio_id,
            batch_id=batch_id,
            attempt=attempt,
            succeeded=False,
            error="The restricted agent runner could not start or complete.",
        )
