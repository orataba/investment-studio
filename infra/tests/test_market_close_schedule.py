import importlib.util
from datetime import datetime
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/market_close_schedule.py"
spec = importlib.util.spec_from_file_location("market_close_schedule", SCRIPT)
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)


@pytest.mark.parametrize("market, clock, expected", [
    ("us", "2026-11-27T13:30:00-05:00", True),  # Black Friday half-day
    ("us", "2026-11-27T16:30:00-05:00", False),
    ("hk", "2026-02-16T12:30:00+08:00", True),  # Lunar New Year's Eve half-day
    ("hk", "2026-02-16T16:30:00+08:00", False),
    ("us", "2026-07-23T04:30:00+08:00", True),  # Normal US close in DST
    ("us", "2026-01-06T05:30:00+08:00", True),  # Normal US close in winter
    ("hk", "2026-07-22T16:30:00+08:00", True),
    ("hk", "2026-07-22T16:00:00+08:00", False),
    ("hk", "2026-07-22T17:00:00+08:00", False),
    ("us", "2026-11-26T16:30:00-05:00", False),  # Thanksgiving
    ("hk", "2026-02-17T16:30:00+08:00", False),  # Lunar New Year
])
def test_actual_close_controls_refresh(market, clock, expected):
    assert schedule.is_refresh_due(market, datetime.fromisoformat(clock)) is expected


@pytest.mark.parametrize("clock, expected", [
    ("2026-11-27T13:30:00-05:00", 0),
    ("2026-11-27T16:30:00-05:00", 1),
    ("2026-11-27T13:30:00", 255),
])
def test_condition_exit_status_distinguishes_skip_from_error(clock, expected):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--market-scope", "us", "--now", clock],
        capture_output=True,
    )
    assert result.returncode == expected
