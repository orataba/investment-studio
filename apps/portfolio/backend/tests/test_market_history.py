from __future__ import annotations

from datetime import date

import pytest

from portfolio_app.services.instrument_charts import (
    build_instrument_price_chart_from_detail,
    build_instrument_trend_metrics_from_detail,
)
from portfolio_app.services.market_data import benchmark_total_return_quote_bases


def _point(
    quote_basis: str,
    as_of_date: str,
    value: str,
) -> dict[str, object]:
    return {
        "metric_family": "price",
        "quote_basis": quote_basis,
        "as_of_date": as_of_date,
        "value": value,
        "currency": "USD",
        "price_unit": "per_unit",
        "price_scale": "1",
        "status": "complete",
    }


def _detail(
    points: list[dict[str, object]],
    *,
    corporate_actions: list[dict[str, object]] | None = None,
    instrument_type: str = "equity",
    return_semantics: str | None = None,
) -> dict[str, object]:
    detail: dict[str, object] = {
        "instrument_id": "equity-history",
        "instrument_name": "History Equity",
        "instrument_type": instrument_type,
        "currency": "USD",
        "identifiers": [],
        "quote_selection_policy": {
            "total_return": ["adjusted_close", "close"],
            "chart": ["adjusted_close", "close"],
            "valuation": ["close"],
            "reference": ["close"],
        },
        "market_data": points,
        "corporate_actions": corporate_actions or [],
    }
    if return_semantics is not None:
        detail["source_settings"] = {"return_semantics": return_semantics}
    return detail


def _split_event(**overrides: object) -> dict[str, object]:
    return {
        "corporate_action_event_id": "split-1",
        "instrument_id": "equity-history",
        "action_type": "share_split",
        "effective_date": "2026-01-02",
        "new_units": "2",
        "old_units": "1",
        "quantity_rounding": "exact",
        "status": "confirmed",
        **overrides,
    }


def test_holdings_total_return_does_not_switch_to_a_longer_price_series() -> None:
    detail = _detail(
        [
            _point("adjusted_close", "2026-07-15", "999"),
            _point("close", "2025-07-14", "80"),
            _point("close", "2025-12-31", "90"),
            _point("close", "2026-06-30", "95"),
            _point("close", "2026-07-08", "96"),
            _point("close", "2026-07-15", "100"),
        ]
    )

    trend = build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=date(2026, 7, 15),
    )
    chart = build_instrument_price_chart_from_detail(
        detail,
        instrument_id="equity-history",
        as_of_date=date(2026, 7, 15),
        range_key="all",
    )

    assert trend["instrument_trend_basis"] == "adjusted_close"
    assert trend["instrument_trend_reason"] == "selected_series_has_single_observation"
    assert trend["instrument_trend_coverage"] == {
        "state": "partial",
        "observation_count": 1,
        "start_date": "2026-07-15",
        "end_date": "2026-07-15",
        "available_return_windows": [],
    }
    assert trend["instrument_return_1w"] is None
    assert trend["instrument_return_1m"] is None
    assert chart is not None
    assert chart["chart_basis"] == "close"
    assert chart["return_semantics"] == "unknown"
    assert chart["selection_reason"] == "selected_more_complete_alternate_series"
    assert [point["value"] for point in chart["points"]] == [80.0, 90.0, 95.0, 96.0, 100.0]


@pytest.mark.parametrize(
    ("configured_semantics", "expected_semantics"),
    [
        ("total_return", "total_return"),
        ("price_return", "price_return"),
        ("unknown", "unknown"),
    ],
)
def test_index_close_chart_reports_explicit_return_semantics(
    configured_semantics: str,
    expected_semantics: str,
) -> None:
    chart = build_instrument_price_chart_from_detail(
        _detail(
            [
                _point("close", "2026-07-14", "100"),
                _point("close", "2026-07-15", "101"),
            ],
            instrument_type="index",
            return_semantics=configured_semantics,
        ),
        instrument_id="index-history",
        as_of_date=date(2026, 7, 15),
        range_key="all",
    )

    assert chart is not None
    assert chart["chart_basis"] == "close"
    assert chart["return_semantics"] == expected_semantics


@pytest.mark.parametrize(
    ("configured_semantics", "expected_bases"),
    [
        ("total_return", ["close"]),
        ("price_return", []),
        ("unknown", []),
    ],
)
def test_benchmark_bases_require_confirmed_total_return_semantics(
    configured_semantics: str,
    expected_bases: list[str],
) -> None:
    detail = _detail(
        [_point("close", "2026-07-15", "101")],
        instrument_type="index",
        return_semantics=configured_semantics,
    )
    detail["quote_selection_policy"] = {
        "total_return": ["close"],
        "chart": ["close"],
        "valuation": ["close"],
        "reference": ["close"],
    }

    assert benchmark_total_return_quote_bases(detail) == expected_bases


def test_adjusted_close_reports_total_return_without_manual_semantics() -> None:
    chart = build_instrument_price_chart_from_detail(
        _detail(
            [
                _point("adjusted_close", "2026-07-14", "100"),
                _point("adjusted_close", "2026-07-15", "101"),
            ]
        ),
        instrument_id="equity-history",
        as_of_date=date(2026, 7, 15),
        range_key="all",
    )

    assert chart is not None
    assert chart["chart_basis"] == "adjusted_close"
    assert chart["return_semantics"] == "total_return"


def test_mtd_and_ytd_remain_unavailable_without_preperiod_anchors() -> None:
    trend = build_instrument_trend_metrics_from_detail(
        _detail(
            [
                _point("close", "2026-01-02", "100"),
                _point("close", "2026-01-10", "110"),
            ]
        ),
        as_of_date=date(2026, 1, 10),
    )

    assert trend["instrument_return_mtd"] is None
    assert trend["instrument_return_ytd"] is None
    assert "mtd" not in trend["instrument_trend_coverage"]["available_return_windows"]
    assert "ytd" not in trend["instrument_trend_coverage"]["available_return_windows"]


def test_one_year_return_uses_a_calendar_year_boundary() -> None:
    trend = build_instrument_trend_metrics_from_detail(
        _detail(
            [
                _point("adjusted_close", "2023-03-01", "100"),
                _point("adjusted_close", "2023-03-02", "105"),
                _point("adjusted_close", "2024-03-01", "120"),
            ]
        ),
        as_of_date=date(2024, 3, 1),
    )

    assert trend["instrument_return_1y"] == pytest.approx(0.2)
    assert "1y" in trend["instrument_trend_coverage"]["available_return_windows"]


def test_total_return_windows_match_watchlist_calendar_boundary_contract() -> None:
    detail = _detail(
        [
            _point("total_return_nav", "2026-01-23", "1.0000"),
            _point("total_return_nav", "2026-04-24", "1.1000"),
            _point("total_return_nav", "2026-06-23", "1.2018"),
            _point("total_return_nav", "2026-06-25", "1.1784"),
            _point("total_return_nav", "2026-07-24", "1.2849"),
        ],
        instrument_type="fund",
    )
    detail["quote_selection_policy"] = {
        role: ["total_return_nav"]
        for role in ("total_return", "chart", "valuation", "reference")
    }
    for point in detail["market_data"]:
        point["metric_family"] = "nav"
    trend = build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=date(2026, 7, 24),
    )

    assert trend["instrument_return_1m"] == pytest.approx(1.2849 / 1.2018 - 1)
    assert trend["instrument_return_3m"] == pytest.approx(1.2849 / 1.1000 - 1)
    assert trend["instrument_return_6m"] == pytest.approx(1.2849 / 1.0000 - 1)
    assert "1m" in trend["instrument_trend_coverage"]["available_return_windows"]
    assert "3m" in trend["instrument_trend_coverage"]["available_return_windows"]
    assert "6m" in trend["instrument_trend_coverage"]["available_return_windows"]


def test_total_return_window_anchor_stays_on_requested_as_of_when_latest_nav_is_stale() -> None:
    detail = _detail(
        [
            _point("total_return_nav", "2026-06-23", "1.20"),
            _point("total_return_nav", "2026-06-24", "1.25"),
            _point("total_return_nav", "2026-07-23", "1.30"),
        ],
        instrument_type="fund",
    )
    detail["quote_selection_policy"] = {
        role: ["total_return_nav"]
        for role in ("total_return", "chart", "valuation", "reference")
    }
    for point in detail["market_data"]:
        point["metric_family"] = "nav"

    trend = build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=date(2026, 7, 24),
    )

    assert trend["instrument_trend_as_of_date"] == "2026-07-23"
    assert trend["instrument_return_1m"] == pytest.approx(1.30 / 1.25 - 1)
    assert "1m" in trend["instrument_trend_coverage"]["available_return_windows"]


def test_raw_close_history_is_adjusted_with_confirmed_split_ratio_within_one_basis() -> None:
    detail = _detail(
        [
            _point("close", "2026-01-01", "100"),
            _point("close", "2026-01-02", "50"),
            _point("close", "2026-01-03", "55"),
        ],
        corporate_actions=[_split_event()],
    )
    detail["quote_selection_policy"] = {
        "total_return": ["close"],
        "chart": ["close"],
        "valuation": ["close"],
        "reference": ["close"],
    }

    chart = build_instrument_price_chart_from_detail(
        detail,
        instrument_id="equity-history",
        as_of_date=date(2026, 1, 3),
        range_key="all",
    )
    trend = build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=date(2026, 1, 3),
    )

    assert chart is not None
    assert chart["chart_basis"] == "close"
    assert chart["split_adjusted"] is True
    assert chart["selection_reason"] == "selected_split_adjusted_raw_price_series"
    assert [point["value"] for point in chart["points"]] == [50.0, 50.0, 55.0]
    assert trend["instrument_trend_basis"] is None
    assert trend["instrument_trend_reason"] == "total_return_basis_unavailable"
    assert trend["instrument_max_drawdown"] is None


@pytest.mark.parametrize(
    ("event", "expected_reason"),
    [
        (
            _split_event(status="detected"),
            "raw_price_split_evidence_unconfirmed",
        ),
        (
            _split_event(old_units="0"),
            "raw_price_split_ratio_invalid",
        ),
        (
            _split_event(quantity_rounding="cash_in_lieu"),
            "raw_price_split_fraction_treatment_insufficient",
        ),
    ],
)
def test_raw_price_history_crossing_unsafe_split_is_withheld(
    event: dict[str, object],
    expected_reason: str,
) -> None:
    detail = _detail(
        [
            _point("close", "2026-01-01", "100"),
            _point("close", "2026-01-02", "50"),
        ],
        corporate_actions=[event],
    )

    chart = build_instrument_price_chart_from_detail(
        detail,
        instrument_id="equity-history",
        as_of_date=date(2026, 1, 2),
        range_key="all",
    )
    trend = build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=date(2026, 1, 2),
    )

    assert chart is not None
    assert chart["points"] == []
    assert chart["coverage_state"] == "unavailable"
    assert chart["selection_reason"] == expected_reason
    assert trend["instrument_trend_basis"] is None
    assert trend["instrument_trend_coverage"]["state"] == "unavailable"
    assert trend["instrument_trend_reason"] == "total_return_series_unavailable"
