import importlib.util
import json
from datetime import datetime
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/settlement_refresh_schedule.py"
spec = importlib.util.spec_from_file_location("settlement_refresh_schedule", SCRIPT)
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)

LATE = datetime.fromisoformat("2026-10-12T23:00:00+08:00")
SUCCESS = {
    "channel": "settlement",
    "status": "succeeded",
    "exit_code": 0,
    "started_at": "2026-10-12T21:00:00+08:00",
    "finished_at": "2026-10-12T21:48:00+08:00",
}


def write_summary(tmp_path, **changes):
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({**SUCCESS, **changes}), encoding="utf-8")
    return path


def test_success_skips_late_slot_without_overwriting_collection_evidence(tmp_path):
    path = write_summary(tmp_path)
    before = path.read_bytes()
    assert schedule.should_run(path, LATE) is False
    assert path.read_bytes() == before
    assert schedule.should_run(path, LATE.replace(hour=21)) is True


@pytest.mark.parametrize("changes", [
    {"status": "failed", "exit_code": 1},  # Includes strict coverage failures.
    {"status": "running", "finished_at": None},
    {"exit_code": 1},
    {"exit_code": False},
    {"channel": "market"},
    {"started_at": "2026-10-11T21:00:00+08:00", "finished_at": "2026-10-11T22:00:00+08:00"},
    {"started_at": "2026-10-11T23:00:00+08:00", "finished_at": "2026-10-12T00:30:00+08:00"},
    {"started_at": "2026-10-12T23:10:00+08:00", "finished_at": "2026-10-12T23:20:00+08:00"},
    {"finished_at": "2026-10-12T20:00:00+08:00"},
    {"started_at": "2026-10-12T21:00:00"},
    {"started_at": "bad clock"},
    {"finished_at": None},
])
def test_unproven_success_allows_one_late_calendar_attempt(tmp_path, changes):
    assert schedule.should_run(write_summary(tmp_path, **changes), LATE) is True


@pytest.mark.parametrize("raw", [b"{", b"[]", b"{}", b"\xff"])
def test_missing_or_invalid_summary_does_not_suppress_catchup(tmp_path, raw):
    path = tmp_path / "summary.json"
    assert schedule.should_run(path, LATE) is True
    path.write_bytes(raw)
    assert schedule.should_run(path, LATE) is True


def test_summary_is_bounded_and_oversize_is_not_success_evidence(tmp_path):
    path = write_summary(tmp_path)
    with path.open("ab") as stream:
        stream.write(b" " * schedule.MAX_SUMMARY_BYTES)
    assert schedule.should_run(path, LATE) is True


def test_shanghai_date_and_hour_are_independent_of_host_timezone(tmp_path):
    # An early same-day success can cover only the previous trading session.
    path = write_summary(
        tmp_path,
        started_at="2026-10-11T16:30:00+00:00",
        finished_at="2026-10-11T17:00:00+00:00",
    )
    assert schedule.should_run(path, datetime.fromisoformat("2026-10-12T15:00:00Z")) is True
    # The primary boundary is 21:00 Shanghai even when all clocks are UTC.
    path = write_summary(
        tmp_path,
        started_at="2026-10-12T13:00:00+00:00",
        finished_at="2026-10-12T14:00:00+00:00",
    )
    assert schedule.should_run(path, datetime.fromisoformat("2026-10-12T15:00:00Z")) is False
    assert schedule.should_run(path, datetime.fromisoformat("2026-10-13T15:00:00Z")) is True


def test_success_started_before_primary_slot_does_not_suppress_late_retry(tmp_path):
    path = write_summary(
        tmp_path,
        started_at="2026-10-12T20:59:59+08:00",
        finished_at="2026-10-12T21:48:00+08:00",
    )
    assert schedule.should_run(path, LATE) is True


@pytest.mark.parametrize("clock, expected", [
    ("2026-10-12T21:00:00+08:00", 0),
    ("2026-10-12T23:00:00+08:00", 1),
    ("2026-10-12T23:00:00", 255),
])
def test_condition_cli_distinguishes_run_skip_and_clock_error(tmp_path, clock, expected):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--summary-file", str(write_summary(tmp_path)), "--now", clock],
        capture_output=True,
    )
    assert result.returncode == expected


def test_io_error_fails_condition_without_starting_collection(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--summary-file", str(tmp_path), "--now", LATE.isoformat()],
        capture_output=True,
    )
    assert result.returncode == 255


@pytest.mark.parametrize('coverage', [
    {'status': 'pending', 'pending_count': 100},
    {'status': 'failed', 'failed_count': 1, 'pending_count': 3},
])
def test_waiting_publication_does_not_suppress_existing_late_slot(tmp_path, coverage):
    assert schedule.should_run(write_summary(tmp_path, listed_close_coverage=coverage), LATE)
