from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from watchlist_app.services.read_models import build_sparkline_payload
from watchlist_app.services.return_windows import (
    named_return_window_spec,
    period_return_percent,
    resolve_return_window,
)


def _points(*rows: tuple[str, float]) -> list[dict[str, object]]:
    return [
        {"as_of_date": date.fromisoformat(point_date), "value": value}
        for point_date, value in rows
    ]


def test_custom_range_uses_start_close_and_end_close() -> None:
    window = resolve_return_window(
        _points(("2026-07-01", 100), ("2026-07-02", 102), ("2026-07-08", 108)),
        requested_start_date=date(2026, 7, 2),
        requested_end_date=date(2026, 7, 8),
    )

    assert window is not None
    assert window.anchor_date == date(2026, 7, 2)
    assert window.end_date == date(2026, 7, 8)
    assert period_return_percent(window) == (108 / 102 - 1) * 100


def test_missing_boundary_uses_last_valid_observation_and_exposes_it() -> None:
    window = resolve_return_window(
        _points(("2026-07-01", 100), ("2026-07-06", 106), ("2026-07-10", 110)),
        requested_start_date=date(2026, 7, 2),
        requested_end_date=date(2026, 7, 8),
    )

    assert window is not None
    assert window.anchor_date == date(2026, 7, 1)
    assert window.end_date == date(2026, 7, 6)


def test_mtd_and_ytd_use_last_close_strictly_before_period_start() -> None:
    points = _points(
        ("2025-12-26", 98),
        ("2025-12-31", 100),
        ("2026-01-02", 101),
        ("2026-06-30", 110),
        ("2026-07-01", 111),
        ("2026-07-15", 115),
    )
    ytd_spec = named_return_window_spec("YTD", date(2026, 7, 15))
    mtd_spec = named_return_window_spec("MTD", date(2026, 7, 15))

    ytd = resolve_return_window(points, **vars(ytd_spec))
    mtd = resolve_return_window(points, **vars(mtd_spec))

    assert ytd is not None and ytd.anchor_date == date(2025, 12, 31)
    assert mtd is not None and mtd.anchor_date == date(2026, 6, 30)


def test_one_day_uses_previous_valid_observation_for_sparse_series() -> None:
    points = _points(("2026-07-03", 100), ("2026-07-10", 105))
    spec = named_return_window_spec("1D", date(2026, 7, 10))

    window = resolve_return_window(points, **vars(spec))

    assert window is not None
    assert window.anchor_date == date(2026, 7, 3)
    assert window.end_date == date(2026, 7, 10)


def test_calendar_month_lookback_clamps_month_end() -> None:
    spec = named_return_window_spec("1M", date(2026, 3, 31))

    assert spec.requested_start_date == date(2026, 2, 28)
    assert spec.requested_end_date == date(2026, 3, 31)
    assert spec.anchor_mode == "on_or_before"


def test_total_return_windows_use_the_same_calendar_anchors_as_portfolio_holdings() -> None:
    points = _points(
        ("2026-01-23", 1.0000),
        ("2026-04-24", 1.1000),
        ("2026-06-23", 1.2018),
        ("2026-06-25", 1.1784),
        ("2026-07-24", 1.2849),
    )

    expected = {
        "1M": 1.2849 / 1.2018 - 1,
        "3M": 1.2849 / 1.1000 - 1,
        "6M": 1.2849 / 1.0000 - 1,
    }
    for window_name, expected_return in expected.items():
        spec = named_return_window_spec(window_name, date(2026, 7, 24))
        window = resolve_return_window(points, **vars(spec))

        assert window is not None
        assert period_return_percent(window) / 100 == pytest.approx(expected_return)


def test_total_return_window_anchor_stays_on_requested_as_of_when_latest_nav_is_stale() -> None:
    points = _points(
        ("2026-06-23", 1.20),
        ("2026-06-24", 1.25),
        ("2026-07-23", 1.30),
    )
    spec = named_return_window_spec("1M", date(2026, 7, 24))
    window = resolve_return_window(points, **vars(spec))

    assert window is not None
    assert window.anchor_date == date(2026, 6, 24)
    assert window.end_date == date(2026, 7, 23)
    assert period_return_percent(window) / 100 == pytest.approx(1.30 / 1.25 - 1)


def test_sparkline_is_normalized_by_calendar_window_and_keeps_quality_metadata() -> None:
    chart = SimpleNamespace(
        instrument_id="private-fund",
        payload_json={
            "selected_series": {
                "quote_basis": "total_return_nav",
                "return_kind": "total_return",
                "return_series_status": "partial",
            },
            "series": [
                {
                    "points": [
                        {"date": "2026-07-03", "value": 1.0},
                        {"date": "2026-07-10", "value": 1.05},
                    ]
                }
            ],
        },
    )

    payload = build_sparkline_payload(
        [chart],
        instrument_ids=["private-fund"],
        selected_fields=["return_chart_1d"],
    )["private-fund"]["return_chart_1d"]

    assert payload["anchor_date"] == "2026-07-03"
    assert payload["end_date"] == "2026-07-10"
    assert payload["status"] == "partial"
    assert payload["label"] == "Cumulative Total Return"
    assert payload["points"] == [
        {"date": "2026-07-03", "value": 0.0},
        {"date": "2026-07-10", "value": pytest.approx(5.0)},
    ]
