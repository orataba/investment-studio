from datetime import UTC, datetime

import pytest

from watchlist_app.services.research_schedule import automatic_research_allowed


@pytest.mark.parametrize("clock,allowed", [
    ("2026-10-13T08:59:59+08:00", True),
    ("2026-10-13T09:00:00+08:00", False),
    ("2026-10-13T11:59:59+08:00", False),
    ("2026-10-13T12:00:00+08:00", True),
    ("2026-10-13T13:59:59+08:00", True),
    ("2026-10-13T14:00:00+08:00", False),
    ("2026-10-13T17:59:59+08:00", False),
    ("2026-10-13T18:00:00+08:00", True),
    ("2026-10-17T10:00:00+08:00", True),
    ("2026-10-18T15:00:00+08:00", True),
    ("2026-10-01T10:00:00+08:00", False),  # Conservative holiday handling.
    ("2026-10-12T01:00:00+00:00", False),
    ("2026-10-11T17:00:00+00:00", True),  # Monday 01:00 in Beijing.
])
def test_provider_window_boundaries_and_timezone(clock, allowed):
    instant = datetime.fromisoformat(clock)
    assert automatic_research_allowed(instant) is allowed
    assert automatic_research_allowed(instant.astimezone(UTC)) is allowed


def test_naive_time_is_not_interpreted_using_host_timezone():
    with pytest.raises(ValueError, match="timezone-aware"):
        automatic_research_allowed(datetime(2026, 10, 13, 10))
