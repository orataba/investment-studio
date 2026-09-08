from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
import math
import statistics
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from .conftest import (
    TEST_SHARED_INSTRUMENTS,
    canonical_quote_policy,
    mutate_shared_instrument_metadata_for_drift,
    publish_provider_explicit_nav,
    seed_shared_instrument,
)


PUBLIC_FUND_IDS = {"savf63"}
PRIVATE_FUND_IDS = {"sxv264"}


def test_peer_metric_percentile_uses_midrank_for_ties() -> None:
    from watchlist_app.services.canonical_recalc import _rank_metric_value

    tied = _rank_metric_value(
        value=1.0,
        samples=[("a", 1.0), ("b", 1.0), ("c", 1.0)],
        direction="higher",
    )
    assert tied["rank"] == 1
    assert tied["percentile"] == pytest.approx(50.0)
    assert tied["quartile"] == 2

    unique_best = _rank_metric_value(
        value=3.0,
        samples=[("a", 3.0), ("b", 2.0), ("c", 1.0)],
        direction="higher",
    )
    assert unique_best["rank"] == 1
    assert unique_best["percentile"] == pytest.approx(100.0)

    lower_is_better_tied = _rank_metric_value(
        value=1.0,
        samples=[("a", 1.0), ("b", 1.0), ("c", 1.0)],
        direction="lower",
    )
    assert lower_is_better_tied["percentile"] == pytest.approx(50.0)


def test_risk_metrics_annualize_from_actual_observation_spacing() -> None:
    from watchlist_app.services.canonical_recalc import _compute_sharpe, _compute_volatility

    nav_points = [
        {"as_of_date": date(2026, 1, 1), "value": 100.0},
        {"as_of_date": date(2026, 1, 8), "value": 101.0},
        {"as_of_date": date(2026, 1, 15), "value": 99.0},
        {"as_of_date": date(2026, 1, 22), "value": 102.0},
    ]
    returns = [
        nav_points[index]["value"] / nav_points[index - 1]["value"] - 1
        for index in range(1, len(nav_points))
    ]
    periods_per_year = len(returns) / 21 * 365.25
    expected_volatility = statistics.stdev(returns) * math.sqrt(periods_per_year) * 100
    expected_sharpe = statistics.fmean(returns) / statistics.stdev(returns) * math.sqrt(periods_per_year)

    assert _compute_volatility(nav_points) == pytest.approx(expected_volatility, abs=1e-12)
    assert _compute_sharpe(nav_points) == pytest.approx(expected_sharpe, abs=1e-12)


def test_path_dependent_risk_metrics_accept_decimal_nav_values() -> None:
    from decimal import Decimal

    from watchlist_app.services.canonical_recalc import (
        _compute_downside_deviation,
        _compute_sharpe,
        _compute_sortino,
        _compute_volatility,
    )

    points = [
        {"as_of_date": date(2025, 1, 1), "value": Decimal("100")},
        {"as_of_date": date(2025, 2, 1), "value": Decimal("101")},
        {"as_of_date": date(2025, 3, 1), "value": Decimal("99")},
        {"as_of_date": date(2025, 4, 1), "value": Decimal("102")},
    ]

    assert _compute_volatility(points) is not None
    assert _compute_downside_deviation(points) is not None
    assert _compute_sharpe(points) is not None
    assert _compute_sortino(points) is not None


def test_monthly_return_series_uses_adjacent_month_end_observations_only() -> None:
    from watchlist_app.services.canonical_recalc import _monthly_return_series

    points = [
        {"as_of_date": date(2025, 12, 31), "value": 100.0},
        # The first January observation must not be used as January's close;
        # the last January observation is the month-end proxy.
        {"as_of_date": date(2026, 1, 2), "value": 101.0},
        {"as_of_date": date(2026, 1, 30), "value": 110.0},
        # February is intentionally absent.  March must not be labelled as a
        # one-month return from January.
        {"as_of_date": date(2026, 3, 31), "value": 150.0},
    ]

    assert _monthly_return_series(points) == [
        {
            "as_of_date": date(2026, 1, 30),
            "value": pytest.approx(10.0),
        }
    ]


def test_calendar_year_returns_do_not_bridge_missing_prior_year() -> None:
    from watchlist_app.services.canonical_recalc import _compute_calendar_year_returns

    points = [
        {"as_of_date": date(2024, 1, 2), "value": 100.0},
        {"as_of_date": date(2025, 1, 2), "value": 120.0},
        {"as_of_date": date(2026, 1, 2), "value": 150.0},
    ]

    rows = _compute_calendar_year_returns(points)

    # 2025 has a prior 2024 observation and is valid.  2024 has no prior-year
    # anchor, and must not be synthesized from an older inception point.
    assert [row["year"] for row in rows] == [2026, 2025]
    assert rows[0]["investment_nav"] == pytest.approx(25.0)
    assert rows[1]["investment_nav"] == pytest.approx(20.0)

    last_close_rows = _compute_calendar_year_returns(
        [
            {"as_of_date": date(2024, 1, 2), "value": 100.0},
            {"as_of_date": date(2024, 12, 30), "value": 110.0},
            {"as_of_date": date(2025, 1, 2), "value": 120.0},
            {"as_of_date": date(2025, 12, 31), "value": 132.0},
        ]
    )
    assert last_close_rows[0]["year"] == 2025
    assert last_close_rows[0]["investment_nav"] == pytest.approx(20.0)
    assert last_close_rows[0]["anchor_date"] == "2024-12-30"
    assert last_close_rows[0]["end_date"] == "2025-12-31"

    missing_prior_year_rows = _compute_calendar_year_returns(
        [
            {"as_of_date": date(2023, 12, 29), "value": 100.0},
            {"as_of_date": date(2025, 12, 31), "value": 120.0},
        ]
    )
    assert missing_prior_year_rows == []


def test_monthly_risk_helpers_reset_at_calendar_gaps() -> None:
    from watchlist_app.services.canonical_recalc import (
        _rolling_annualized_volatility,
        _trailing_negative_month_count,
    )

    # The rows on either side of the missing February are both negative, but
    # they are not two trailing *months* and must not be counted as such.
    returns = [
        {"as_of_date": date(2026, 3, 31), "value": -2.0},
        {"as_of_date": date(2026, 1, 31), "value": -1.0},
    ]
    assert _trailing_negative_month_count(returns) == 1

    # Twelve rows are not enough for a valid 12-month rolling window when one
    # calendar transition is missing.
    contiguous_prefix = [
        {"as_of_date": date(2025, month, 28), "value": 1.0}
        for month in range(1, 13)
    ]
    assert _rolling_annualized_volatility(contiguous_prefix, window=12)
    with_gap = [
        *[row for row in contiguous_prefix if row["as_of_date"].month != 6],
        {"as_of_date": date(2026, 1, 28), "value": 1.0},
    ]
    assert _rolling_annualized_volatility(with_gap, window=12) == []


def test_calculation_frequency_context_keeps_all_points_on_daily_basis() -> None:
    from watchlist_app.services.calculation_frequency import build_calculation_frequency_context

    nav_points = [
        {"as_of_date": date(2026, 1, 5), "value": 100.0, "frequency": "daily"},
        {"as_of_date": date(2026, 1, 6), "value": 101.0, "frequency": "daily"},
        {"as_of_date": date(2026, 1, 9), "value": 102.0, "frequency": "daily"},
        {"as_of_date": date(2026, 1, 12), "value": 103.0, "frequency": "daily"},
        {"as_of_date": date(2026, 1, 16), "value": 104.0, "frequency": "daily"},
    ]

    context = build_calculation_frequency_context(nav_points)

    assert context["profile"]["resolved_frequency"] == "daily"
    assert context["profile"]["raw_observation_count"] == 5
    assert context["profile"]["observation_count"] == 5
    assert [point["as_of_date"] for point in context["points"]] == [
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 9),
        date(2026, 1, 12),
        date(2026, 1, 16),
    ]


def test_registry_daily_frequency_is_reflected_in_profile() -> None:
    from watchlist_app.services.calculation_frequency import build_calculation_frequency_context

    nav_points = [
        {"as_of_date": date(2026, 1, 5), "value": 100.0, "frequency": "daily"},
        {"as_of_date": date(2026, 1, 6), "value": 101.0, "frequency": "daily"},
        {"as_of_date": date(2026, 1, 7), "value": 102.0, "frequency": "daily"},
    ]

    context = build_calculation_frequency_context(
        nav_points,
        expected_frequency="daily",
    )

    assert context["profile"]["expected_frequency"] == "daily"
    assert context["profile"]["resolved_frequency"] == "daily"
    assert context["profile"]["frequency_source"] == "daily_policy"
    assert context["profile"]["observation_count"] == 3


def test_market_calendar_gap_detection_ignores_holidays_but_detects_missing_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.services import calculation_frequency

    sessions = (
        date(2026, 2, 13),
        date(2026, 2, 24),
        date(2026, 2, 25),
    )
    monkeypatch.setattr(
        calculation_frequency,
        "_market_calendar_sessions",
        lambda _calendar, _start, _end: sessions,
    )
    aligned = calculation_frequency.build_calculation_frequency_context(
        [
            {"as_of_date": session_date, "value": float(index + 100)}
            for index, session_date in enumerate(sessions)
        ],
        expected_frequency="daily",
        market_calendar="XSHG",
    )
    missing = calculation_frequency.build_calculation_frequency_context(
        [
            {"as_of_date": sessions[0], "value": 100.0},
            {"as_of_date": sessions[-1], "value": 102.0},
        ],
        expected_frequency="daily",
        market_calendar="XSHG",
    )

    assert aligned["profile"]["largest_gap_days"] == 11
    assert aligned["profile"]["gap_count"] == 0
    assert aligned["profile"]["gap_detection_basis"] == "market_calendar:XSHG"
    assert missing["profile"]["gap_count"] == 1
    assert missing["profile"]["missing_observation_date_sample"] == ["2026-02-24"]


def test_xshg_calendar_treats_lunar_new_year_closure_as_non_sessions() -> None:
    from watchlist_app.services import calculation_frequency

    if calculation_frequency.exchange_calendars is None:
        pytest.skip("exchange_calendars is not installed in this legacy local test environment.")

    context = calculation_frequency.build_calculation_frequency_context(
        [
            {"as_of_date": date(2026, 2, 13), "value": 100.0},
            {"as_of_date": date(2026, 2, 24), "value": 101.0},
            {"as_of_date": date(2026, 2, 25), "value": 102.0},
        ],
        expected_frequency="daily",
        market_calendar="XSHG",
    )

    assert context["profile"]["largest_gap_days"] == 11
    assert context["profile"]["gap_count"] == 0
    assert context["profile"]["gap_detection_basis"] == "market_calendar:XSHG"


def test_xshe_calendar_uses_the_mainland_session_calendar() -> None:
    from watchlist_app.services import calculation_frequency

    context = calculation_frequency.build_calculation_frequency_context(
        [
            {"as_of_date": date(2026, 2, 13), "value": 100.0},
            {"as_of_date": date(2026, 2, 24), "value": 101.0},
            {"as_of_date": date(2026, 2, 25), "value": 102.0},
        ],
        expected_frequency="daily",
        market_calendar="XSHE",
    )

    assert context["profile"]["gap_count"] == 0
    assert context["profile"]["gap_detection_basis"] == "market_calendar:XSHE"


def test_daily_freshness_uses_instrument_calendar_and_release_lag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.services import calculation_frequency

    monkeypatch.setattr(
        calculation_frequency,
        "_market_calendar_sessions",
        lambda _calendar, _start, _end: (
            date(2026, 7, 23),
            date(2026, 7, 24),
            date(2026, 7, 27),
            date(2026, 7, 28),
        ),
    )

    fresh = calculation_frequency.assess_latest_observation_freshness(
        latest_observation_date=date(2026, 7, 24),
        current_date=date(2026, 7, 28),
        resolved_frequency="daily",
        expected_frequency="daily",
        market_calendar="XSHG",
        release_lag_days=1,
    )
    stale = calculation_frequency.assess_latest_observation_freshness(
        latest_observation_date=date(2026, 7, 23),
        current_date=date(2026, 7, 28),
        resolved_frequency="daily",
        expected_frequency="daily",
        market_calendar="XSHG",
        release_lag_days=1,
    )
    future = calculation_frequency.assess_latest_observation_freshness(
        latest_observation_date=date(2026, 7, 29),
        current_date=date(2026, 7, 28),
        resolved_frequency="daily",
        expected_frequency="daily",
        market_calendar="XSHG",
        release_lag_days=1,
    )

    assert fresh["status"] == "fresh"
    assert fresh["expected_latest_date"] == "2026-07-24"
    assert stale["status"] == "stale"
    assert stale["expected_latest_date"] == "2026-07-24"
    assert future["status"] == "stale"
    assert "future-dated" in str(future["reason"])


def test_daily_freshness_treats_release_lag_as_trading_days_across_a_weekend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.services import calculation_frequency

    monkeypatch.setattr(
        calculation_frequency,
        "_market_calendar_sessions",
        lambda _calendar, _start, _end: (
            date(2026, 7, 23),
            date(2026, 7, 24),
            date(2026, 7, 27),
        ),
    )

    freshness = calculation_frequency.assess_latest_observation_freshness(
        latest_observation_date=date(2026, 7, 24),
        current_date=date(2026, 7, 27),
        resolved_frequency="daily",
        expected_frequency="daily",
        market_calendar="XSHG",
        release_lag_days=1,
    )

    assert freshness["status"] == "fresh"
    assert freshness["expected_latest_date"] == "2026-07-23"


def test_daily_freshness_does_not_require_an_observation_on_its_release_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.services import calculation_frequency

    monkeypatch.setattr(
        calculation_frequency,
        "_market_calendar_sessions",
        lambda _calendar, _start, _end: (
            date(2026, 7, 24),
            date(2026, 7, 27),
            date(2026, 7, 28),
        ),
    )

    freshness = calculation_frequency.assess_latest_observation_freshness(
        latest_observation_date=date(2026, 7, 24),
        current_date=date(2026, 7, 28),
        resolved_frequency="daily",
        expected_frequency="daily",
        market_calendar="XSHG",
        release_lag_days=1,
    )

    assert freshness["status"] == "fresh"
    assert freshness["expected_latest_date"] == "2026-07-24"


def test_downside_deviation_uses_all_observations_in_the_denominator() -> None:
    from watchlist_app.services.canonical_recalc import _compute_downside_deviation

    nav_points = [
        {"as_of_date": date(2026, 1, 1), "value": 100.0},
        {"as_of_date": date(2026, 1, 2), "value": 110.0},
        {"as_of_date": date(2026, 1, 3), "value": 99.0},
        {"as_of_date": date(2026, 1, 4), "value": 108.9},
    ]
    periods_per_year = 3 / 3 * 365.25
    expected = math.sqrt((0.1 ** 2) / 3) * math.sqrt(periods_per_year) * 100

    assert _compute_downside_deviation(nav_points) == pytest.approx(expected, abs=1e-12)


def test_return_nav_basis_does_not_fallback_to_ordinary_nav() -> None:
    from watchlist_app.services.canonical_recalc import _select_quote_series, _shared_quote_points_by_basis

    points_by_basis = _shared_quote_points_by_basis(
        [
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-01-01",
                "value": "1.000000",
                "currency": "CNY",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-01-08",
                "value": "1.010000",
                "currency": "CNY",
                "status": "complete",
            },
        ],
        expected_currency="CNY",
    )

    shared_instrument = {
        "quote_selection_policy": {
            "valuation": ["official_nav"],
            "total_return": ["total_return_nav"],
            "chart": ["official_nav"],
        }
    }
    auto_selection = _select_quote_series(
        points_by_basis,
        shared_instrument=shared_instrument,
        role="total_return",
        preference="auto",
        instrument_type="public_fund",
    )
    explicit_nav_selection = _select_quote_series(
        points_by_basis,
        shared_instrument=shared_instrument,
        role="total_return",
        preference="nav",
        instrument_type="public_fund",
    )
    quote_selection = _select_quote_series(
        points_by_basis,
        shared_instrument=shared_instrument,
        role="chart",
        preference="auto",
        instrument_type="public_fund",
    )
    valuation_selection = _select_quote_series(
        points_by_basis,
        shared_instrument=shared_instrument,
        role="valuation",
        preference="auto",
        instrument_type="public_fund",
    )

    assert auto_selection["nav_basis_type"] is None
    assert auto_selection["points"] == []
    assert explicit_nav_selection["nav_basis_type"] is None
    assert explicit_nav_selection["points"] == []
    assert quote_selection["nav_basis_type"] is None
    assert quote_selection["points"] == []
    assert valuation_selection["nav_basis_type"] == "nav"
    assert valuation_selection["selected_quote_basis"] == "official_nav"
    assert valuation_selection["points"][-1]["value"] == pytest.approx(1.01)


def test_latest_watchlist_value_uses_valuation_role_and_keeps_cumulative_nav() -> None:
    from watchlist_app.services.read_models import build_latest_quote_overrides

    chart = SimpleNamespace(
        instrument_id="avf63b",
        payload_json={
            "latest_values": {
                "valuation": {
                    "quote_basis": "official_nav",
                    "date": "2026-08-28",
                    "value": 1.0177,
                },
                "total_return": {
                    "quote_basis": "total_return_nav",
                    "date": "2026-08-28",
                    "value": 1.0538,
                },
            },
            "series": [
                {
                    "quote_basis": "total_return_nav",
                    "points": [{"date": "2026-08-28", "value": 1.0538}],
                }
            ],
        },
    )
    old_chart_without_role_snapshots = SimpleNamespace(
        instrument_id="legacy-fund",
        payload_json={
            "series": [
                {
                    "quote_basis": "total_return_nav",
                    "points": [{"date": "2026-08-28", "value": 9.9999}],
                }
            ],
        },
    )

    assert build_latest_quote_overrides([chart, old_chart_without_role_snapshots]) == {
        "avf63b": {
            "latest_quote": 1.0177,
            "latest_quote_date": "2026-08-28",
            "latest_cumulative_nav": 1.0538,
            "latest_cumulative_nav_date": "2026-08-28",
        }
    }


def test_fund_summary_exposes_unit_and_cumulative_nav_independently() -> None:
    from watchlist_app.services.canonical_recalc import CanonicalRecalcService

    now = datetime(2026, 8, 28, 8, tzinfo=UTC)
    payload = CanonicalRecalcService()._summary_payload(
        instrument=SimpleNamespace(
            instrument_id="avf63b",
            instrument_name="云谷3号B",
            instrument_type="private_fund",
            primary_identifier_value="AVF63B",
            metadata_json={},
        ),
        nav_selection={
            "points": [{"as_of_date": date(2026, 8, 28), "value": 1.0538}],
            "nav_basis_type": "nav_with_dividend",
            "nav_basis_source": "shared",
            "nav_basis_status": "ready",
            "selected_quote_basis": "total_return_nav",
            "return_series_status": "ready",
        },
        valuation_selection={
            "points": [{"as_of_date": date(2026, 8, 28), "value": 1.0177}],
            "nav_basis_type": "nav",
            "nav_basis_source": "shared",
            "nav_basis_status": "ready",
            "selected_quote_basis": "official_nav",
        },
        performance_snapshot=None,
        risk_snapshot=None,
        exposure_snapshot=None,
        attributes={},
        taxonomy_context={},
        source_cutoff_at=now,
        now=now,
    )

    assert payload["series_snapshot"]["latest_nav"] == pytest.approx(1.0177)
    assert payload["series_snapshot"]["latest_nav_date"] == "2026-08-28"
    assert payload["series_snapshot"]["latest_nav_with_dividend"] == pytest.approx(1.0538)
    assert payload["series_snapshot"]["latest_nav_with_dividend_date"] == "2026-08-28"


def test_completed_recalc_marks_missing_canonical_series_unavailable() -> None:
    from watchlist_app.services.canonical_recalc import CanonicalRecalcService

    now = datetime(2026, 7, 16, 8, tzinfo=UTC)
    payload = CanonicalRecalcService()._summary_payload(
        instrument=SimpleNamespace(
            instrument_id="fund-no-total-return",
            instrument_name="无可信复权序列基金",
            instrument_type="public_fund",
            primary_identifier_value="NO-TWR",
            metadata_json={},
        ),
        nav_selection={
            "points": [],
            "nav_basis_type": None,
            "nav_basis_source": "unavailable",
            "nav_basis_status": "unavailable",
        },
        performance_snapshot=None,
        risk_snapshot=None,
        exposure_snapshot=None,
        attributes={},
        taxonomy_context={},
        source_cutoff_at=now,
        now=now,
    )

    assert payload["freshness"] == {
        "data_freshness_status": "unavailable",
        "last_fact_update_at": "2026-07-16T08:00:00Z",
        "last_recalculated_at": "2026-07-16T08:00:00Z",
        "last_successful_snapshot_at": "2026-07-16T08:00:00Z",
        "staleness_reason": "No canonical series available.",
        "latest_observation_date": None,
        "expected_latest_date": None,
        "observation_lag_days": None,
        "release_lag_trading_days": None,
    }


def test_listed_summary_exposes_type_specific_detail_tabs() -> None:
    from watchlist_app.services.canonical_recalc import CanonicalRecalcService

    now = datetime(2026, 8, 23, 8, tzinfo=UTC)
    payload = CanonicalRecalcService()._summary_payload(
        instrument=SimpleNamespace(
            instrument_id="listed-equity",
            instrument_name="Listed Equity",
            instrument_type="equity",
            primary_identifier_value="AAPL",
            metadata_json={},
        ),
        nav_selection={
            "points": [],
            "nav_basis_type": None,
            "nav_basis_source": "unavailable",
            "nav_basis_status": "unavailable",
        },
        performance_snapshot=None,
        risk_snapshot=None,
        exposure_snapshot=None,
        attributes={},
        taxonomy_context={},
        source_cutoff_at=now,
        now=now,
    )

    assert payload["tabs"] == [
        "overview",
        "research",
        "performance",
        "risk",
        "price",
        "fundamentals",
        "events",
        "monitoring",
    ]
    assert payload["instrument_name"] == "Listed Equity"
    assert "fund_name" not in payload
    assert "series_snapshot" in payload
    assert "nav_snapshot" not in payload


def test_partial_total_return_with_source_gaps_remains_fresh_without_event_break() -> None:
    from watchlist_app.services.canonical_recalc import CanonicalRecalcService

    now = datetime(2026, 7, 16, 8, tzinfo=UTC)
    payload = CanonicalRecalcService()._summary_payload(
        instrument=SimpleNamespace(
            instrument_id="public-fund-with-calendar-gaps",
            instrument_name="Calendar Gap Fund",
            instrument_type="public_fund",
            primary_identifier_value="GAP.OF",
            metadata_json={},
        ),
        nav_selection={
            "points": [
                {
                    "as_of_date": date(2026, 7, 16),
                    "value": 1.25,
                }
            ],
            "nav_basis_type": "nav_with_dividend",
            "nav_basis_source": "shared",
            "nav_basis_status": "partial",
            "return_series_status": "partial",
            "return_segment_breaks": [],
        },
        performance_snapshot=None,
        risk_snapshot=None,
        exposure_snapshot=None,
        attributes={},
        taxonomy_context={},
        source_cutoff_at=now,
        now=now,
    )

    assert payload["freshness"]["data_freshness_status"] == "fresh"
    assert payload["freshness"]["staleness_reason"] is None


def test_partial_total_return_with_segment_break_stays_actionable() -> None:
    from watchlist_app.services.canonical_recalc import CanonicalRecalcService

    now = datetime(2026, 7, 16, 8, tzinfo=UTC)
    payload = CanonicalRecalcService()._summary_payload(
        instrument=SimpleNamespace(
            instrument_id="fund-with-event-break",
            instrument_name="Event Break Fund",
            instrument_type="public_fund",
            primary_identifier_value="BREAK",
            metadata_json={},
        ),
        nav_selection={
            "points": [
                {
                    "as_of_date": date(2026, 7, 15),
                    "value": 1.10,
                }
            ],
            "nav_basis_type": "nav_with_dividend",
            "nav_basis_source": "shared",
            "nav_basis_status": "partial",
            "return_series_status": "partial",
            "return_segment_breaks": [
                {
                    "as_of_date": "2026-07-16",
                    "reason": "cash_disclosure_conflict",
                }
            ],
        },
        performance_snapshot=None,
        risk_snapshot=None,
        exposure_snapshot=None,
        attributes={},
        taxonomy_context={},
        source_cutoff_at=now,
        now=now,
    )

    assert payload["freshness"]["data_freshness_status"] == "partial"
    assert payload["freshness"]["staleness_reason"] == (
        "Canonical total-return series stops at an unconfirmed return-series event."
    )


def test_listed_index_policy_can_select_close_over_stale_total_return_series() -> None:
    from watchlist_app.services.canonical_recalc import (
        _group_shared_nav_rows,
        _rows_with_selected_series,
        _select_quote_series,
        _shared_quote_points_by_basis,
    )

    shared_instrument = {
        "source_settings": {"return_semantics": "price_return"},
        "quote_selection_policy": {
            "total_return": ["close", "adjusted_close", "total_return_nav", "official_nav"],
            "chart": ["close", "adjusted_close", "total_return_nav", "official_nav"],
        }
    }
    rows = _group_shared_nav_rows(
        [
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "test_total_return_nav"},
                },
                "as_of_date": "2026-06-15",
                "value": "1.7993",
                "currency": "CNY",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-06-15",
                "value": "1.8000",
                "currency": "CNY",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-06-25",
                "value": "1.8670",
                "currency": "CNY",
                "status": "complete",
            },
        ],
        expected_currency="CNY",
    )
    points_by_basis = _shared_quote_points_by_basis(
        [
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "test_total_return_nav"},
                },
                "as_of_date": "2026-06-15",
                "value": "1.7993",
                "currency": "CNY",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-06-15",
                "value": "1.8000",
                "currency": "CNY",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-06-25",
                "value": "1.8670",
                "currency": "CNY",
                "status": "complete",
            },
        ],
        expected_currency="CNY",
    )

    selection = _select_quote_series(
        points_by_basis,
        shared_instrument=shared_instrument,
        role="total_return",
        preference="auto",
        instrument_type="index",
    )
    rows = _rows_with_selected_series(rows, selection)

    assert selection["nav_basis_type"] == "nav"
    assert selection["selected_metric_family"] == "price"
    assert selection["selected_quote_basis"] == "close"
    assert selection["selected_series_label"] == "Close"
    assert selection["return_kind"] == "price_return"
    assert rows[-1]["basis_metadata"]["nav"]["quote_basis"] == "close"
    assert rows[-1]["basis_metadata"]["nav"]["metric_family"] == "price"
    assert [point["as_of_date"] for point in selection["points"]] == [
        date(2026, 6, 15),
        date(2026, 6, 25),
    ]
    assert [point["value"] for point in selection["points"]] == [1.8, 1.867]


def test_total_return_index_close_preserves_field_identity_and_return_semantics() -> None:
    from watchlist_app.services.canonical_recalc import (
        _group_shared_nav_rows,
        _rows_with_selected_series,
        _select_quote_series,
        _shared_quote_points_by_basis,
    )

    shared_instrument = {
        "instrument_type": "index",
        "source_settings": {"return_semantics": "total_return"},
        "quote_selection_policy": {
            "total_return": ["close"],
            "chart": ["close"],
        },
    }
    market_data = [
        {
            "metric_family": "price",
            "quote_basis": "close",
            "as_of_date": "2026-06-24",
            "value": "260.0000",
            "currency": "CNY",
            "status": "complete",
        },
        {
            "metric_family": "price",
            "quote_basis": "close",
            "as_of_date": "2026-06-25",
            "value": "261.0000",
            "currency": "CNY",
            "status": "complete",
        },
    ]
    points_by_basis = _shared_quote_points_by_basis(
        market_data,
        expected_currency="CNY",
    )

    selection = _select_quote_series(
        points_by_basis,
        shared_instrument=shared_instrument,
        role="total_return",
        preference="auto",
        instrument_type="index",
    )
    rows = _rows_with_selected_series(
        _group_shared_nav_rows(market_data, expected_currency="CNY"),
        selection,
    )

    assert selection["selected_quote_basis"] == "close"
    assert selection["selected_metric_family"] == "price"
    assert selection["nav_basis_type"] == "nav_with_dividend"
    assert selection["return_kind"] == "total_return"
    assert selection["selected_series_label"] == "Close · Total Return"
    assert rows[-1]["nav"] is None
    assert rows[-1]["nav_with_dividend"] == pytest.approx(261.0)
    assert rows[-1]["basis_metadata"]["nav_with_dividend"]["quote_basis"] == "close"


def test_quote_selection_rejects_partial_and_identity_mismatched_points() -> None:
    from watchlist_app.services.canonical_recalc import (
        _select_quote_series,
        _shared_quote_points_by_basis,
    )

    points_by_basis = _shared_quote_points_by_basis(
        [
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "test_total_return_nav"},
                },
                "as_of_date": "2026-06-25",
                "value": "1.8670",
                "currency": "CNY",
                "status": "partial",
            },
            {
                "metric_family": "nav",
                "quote_basis": "close",
                "as_of_date": "2026-06-25",
                "value": "1.8670",
                "currency": "CNY",
                "status": "complete",
            },
        ],
        expected_currency="CNY",
    )

    selection = _select_quote_series(
        points_by_basis,
        shared_instrument=None,
        role="total_return",
        preference="auto",
        instrument_type="public_fund",
    )

    assert points_by_basis == {}
    assert selection["nav_basis_status"] == "unavailable"
    assert selection["points"] == []


def test_quote_selection_rejects_noncanonical_currency_and_missing_policy() -> None:
    from watchlist_app.services.canonical_recalc import (
        _select_quote_series,
        _shared_quote_points_by_basis,
    )

    points_by_basis = _shared_quote_points_by_basis(
        [
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "test_total_return_nav"},
                },
                "as_of_date": "2026-06-24",
                "value": "1.8",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "test_total_return_nav"},
                },
                "as_of_date": "2026-06-25",
                "value": "1.9",
                "currency": "CNY",
                "status": "complete",
            },
        ],
        expected_currency="CNY",
    )

    assert [point["currency"] for point in points_by_basis["total_return_nav"]] == [
        "CNY"
    ]
    selection = _select_quote_series(
        points_by_basis,
        shared_instrument={"currency": "CNY"},
        role="total_return",
        preference="auto",
        instrument_type="public_fund",
    )
    assert selection["nav_basis_status"] == "unavailable"
    assert selection["points"] == []


def test_create_watchlist_generates_unique_ids_and_required_columns(
    client: TestClient,
) -> None:
    first = client.post(
        "/api/watchlists",
        json={"name": "My Focus", "description": "Primary coverage list"},
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["watchlist_id"] == "my-focus"

    duplicate = client.post(
        "/api/watchlists",
        json={"name": "My Focus", "description": "Second list with same name"},
    )
    assert duplicate.status_code == 200
    duplicate_payload = duplicate.json()
    assert duplicate_payload["watchlist_id"] == "my-focus-2"

    detail = client.get(f"/api/watchlists/{first_payload['watchlist_id']}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["default_view_id"] == "overview"
    overview_view = next(
        item for item in detail_payload["views"] if item["view_id"] == "overview"
    )
    assert overview_view["columns"] == [
        "instrument_name", "instrument_type", "attr.instrument_taxonomy_path",
        "return_chart_1m", "return_ytd",
        "attr.current_drawdown", "latest_quote_date", "metric_as_of_date",
    ]
    classification_view = next(
        item
        for item in detail_payload["views"]
        if item["view_id"] == "classification"
    )
    assert classification_view["name"] == "分类"
    assert classification_view["default_group_by"] == "taxonomy"
    assert classification_view["default_filters"] == {
        "instrument_type": [
            "public_fund",
            "private_fund",
            "etf",
            "equity",
            "index",
            "crypto",
        ],
    }
    assert classification_view["columns"] == [
        "instrument_name",
        "instrument_type",
        "attr.instrument_taxonomy_path",
        "latest_quote",
        "latest_quote_date",
        "data_freshness_status",
        "attr.coverage_status",
    ]


def test_watchlist_options_and_queries_do_not_depend_on_list_members(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/watchlists",
        json={"name": "Empty Fund Scope", "description": None},
    )
    assert created.status_code == 200
    watchlist_id = created.json()["watchlist_id"]

    detail = client.get(f"/api/watchlists/{watchlist_id}")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["item_count"] == 0
    assert payload["instrument_types"] == []
    assert [item["code"] for item in payload["available_group_bys"]] == [
        "none",
        "taxonomy",
        "currency",
        "attr.coverage_status",
    ]
    assert "attr.coverage_status" in payload["default_filters_summary"]
    for group in payload["available_group_bys"]:
        query = client.post(
            "/api/screener/query",
            json={
                "watchlist_id": watchlist_id,
                "selected_fields": ["instrument_name", "attr.investment_edge_quality"],
                "group_by": group["code"],
            },
        )
        assert query.status_code == 200
        assert query.json()["rows"] == []

    for instrument_id in ("sxv264", "fund-us-agg"):
        added = client.post(
            f"/api/watchlists/{watchlist_id}/items",
            json={"instrument_ids": [instrument_id]},
        )
        assert added.status_code == 200
        populated = client.get(f"/api/watchlists/{watchlist_id}").json()
        assert populated["available_group_bys"] == payload["available_group_bys"]
        assert populated["default_filters_summary"] == payload["default_filters_summary"]


def test_adding_shared_registry_instrument_to_created_watchlist_materializes_rows(
    client: TestClient,
) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Tactical Ideas", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["accepted_count"] == 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["ticker_or_isin", "instrument_name", "metric_as_of_date"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    payload = screener.json()
    assert payload["total_rows"] == 1
    assert payload["rows"][0]["instrument_name"] == "iShares Core U.S. Aggregate Bond ETF"
    assert payload["rows"][0]["ticker_or_isin"] == "AGG"


def test_screener_fetch_all_returns_one_filtered_snapshot_without_page_truncation(
    client: TestClient,
) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Full Export", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": list(TEST_SHARED_INSTRUMENTS)},
    )
    assert add_response.status_code == 200

    response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "return_1m"],
            "sort": [{"field": "instrument_name", "direction": "asc"}],
            "group_by": "none",
            "pagination": {"page": 2, "page_size": 1},
            "fetch_all": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_rows"] == 3
    assert len(payload["rows"]) == 3
    assert all("metric_as_of_date" in row for row in payload["rows"])


def test_screener_rejects_unknown_financial_fields_instead_of_silently_ignoring_them(
    client: TestClient,
) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Strict Query Contract", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "selected_fields": ["overall_rating"],
            "filters": {"analyst_stance": ["buy"]},
            "pagination": {"page": 1, "page_size": 20},
        },
    )

    assert response.status_code == 422
    assert "Unknown selected field 'overall_rating'" in response.json()["detail"]


def test_adding_index_shared_registry_instrument_is_supported(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "index-csi-300",
            "instrument_name": "CSI 300 Index",
            "instrument_type": "index",
            "currency": "CNY",
            "quote_selection_policy": canonical_quote_policy("index"),
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "000300", "is_primary": True},
            ],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-04-15",
                    "value": "3600.1200",
                    "currency": "CNY",
                    "price_unit": "per_unit",
                    "price_scale": "1",
                    "status": "complete",
                }
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Index Watch", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["index-csi-300"]},
    )
    assert add_response.status_code == 200

    resolve_response = client.post("/api/instruments/index-csi-300/resolve")
    assert resolve_response.status_code == 200
    payload = resolve_response.json()
    assert payload["detail_supported"] is True
    assert payload["detail_view_type"] == "index"
    assert payload["detail_subject_id"] == "index-csi-300"

    from investment_studio_instrument_core import instrument_store as shared_store
    from watchlist_app.db.session import get_session_factory

    assert shared_store.upsert_price_bars(
        get_session_factory(),
        instrument_id="index-csi-300",
        rows=[
            {
                "as_of_date": "2026-04-14",
                "open": "3588.10",
                "high": "3610.20",
                "low": "3579.50",
                "close": "3598.80",
                "previous_close": "3580.00",
                "volume": "123456",
                "turnover": "2345678",
                "currency": "CNY",
                "volume_unit": "lot",
                "turnover_unit": "thousand_cny",
                "provider": "tushare:index_daily",
                "status": "complete",
            },
            {
                "as_of_date": "2026-04-15",
                "open": "3599.00",
                "high": "3620.00",
                "low": "3590.00",
                "close": "3600.12",
                "previous_close": "3598.80",
                "volume": "130000",
                "turnover": "2500000",
                "currency": "CNY",
                "volume_unit": "lot",
                "turnover_unit": "thousand_cny",
                "provider": "tushare:index_daily",
                "status": "complete",
            },
        ],
    ) == 2
    bars_response = client.get(
        "/api/instruments/index-csi-300/price-bars",
        params={"limit": 1},
    )
    assert bars_response.status_code == 200
    bars_payload = bars_response.json()
    assert bars_payload["adjustment_mode"] == "raw"
    assert bars_payload["count"] == 1
    assert bars_payload["bars"][0]["date"] == "2026-04-15"
    assert bars_payload["bars"][0]["close"] == "3600.12"

    tree_response = client.get("/api/taxonomies/instrument-taxonomy")
    assert tree_response.status_code == 200
    tree_payload = tree_response.json()
    assert tree_payload["instrument_types"] == [
        "public_fund",
        "private_fund",
        "etf",
        "equity",
        "index",
        "crypto",
    ]
    node_ids = {node["node_id"] for node in tree_payload["nodes"]}
    assert "equity-us-information-technology" in node_ids
    assert "index-equity-broad-market" in node_ids

    taxonomy_response = client.get("/api/taxonomies/instrument-taxonomy/instruments/index-csi-300")
    assert taxonomy_response.status_code == 200
    taxonomy_payload = taxonomy_response.json()
    assert taxonomy_payload["taxonomy_code"] == "instrument_taxonomy"
    assert taxonomy_payload["assigned_node_id"] is None
    assert taxonomy_payload["derived_values"] == {}

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "instrument_type"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    screener_payload = screener.json()
    assert screener_payload["rows"][0]["instrument_type"] == "index"

    screening_screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "classification",
            "selected_fields": ["instrument_name", "instrument_type"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screening_screener.status_code == 200
    assert screening_screener.json()["rows"][0]["instrument_type"] == "index"

    attribute_response = client.get(
        "/api/instrument-attributes/instruments/index-csi-300"
    )
    assert attribute_response.status_code == 200
    definition_keys = {
        item["attribute_key"] for item in attribute_response.json()["definitions"]
    }
    assert "index_methodology_quality" in definition_keys
    assert "primary_geographic_exposure" in definition_keys
    assert "fund_vehicle" not in definition_keys
    assert "equity_business_quality" not in definition_keys
    assert "etf_index_fit" not in definition_keys


def test_archived_shared_alias_resolves_to_canonical_without_recreating_duplicate(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "sh7639",
            "instrument_name": "龙旗巨星一号私募投资基金",
            "instrument_type": "public_fund",
            "currency": "CNY",
            "quote_selection_policy": canonical_quote_policy("public_fund"),
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "SH7639", "is_primary": True},
            ],
            "market_data": [],
            "lifecycle_state": {"status": "active"},
        }
    )
    seed_shared_instrument(
        {
            "instrument_id": "nav-8c76dae71a",
            "instrument_name": "龙旗巨星一号",
            "instrument_type": "public_fund",
            "currency": "CNY",
            "quote_selection_policy": canonical_quote_policy("public_fund"),
            "identifiers": [
                {"identifier_type": "internal", "identifier_value": "legacy-longqi", "is_primary": True},
            ],
            "market_data": [],
            "lifecycle_state": {
                "status": "archived",
                "canonical_instrument_id": "sh7639",
            },
        }
    )

    response = client.post("/api/instruments/nav-8c76dae71a/resolve")

    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_instrument_id"] == "nav-8c76dae71a"
    assert payload["canonical_instrument_id"] == "sh7639"
    assert payload["detail_subject_id"] == "sh7639"

    from watchlist_app.db.models.instruments import InstrumentDetail
    from watchlist_app.db.session import get_session_factory

    with get_session_factory()() as session:
        assert session.get(InstrumentDetail, "nav-8c76dae71a") is None


def test_adding_equity_shared_registry_instrument_uses_listed_detail(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "equity-demo",
            "instrument_name": "Demo Equity",
            "instrument_type": "equity",
            "currency": "USD",
            "exchange_code": "XNAS",
            "quote_selection_policy": canonical_quote_policy("equity"),
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "DEMO", "is_primary": True},
            ],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-04-15",
                    "value": "12.3400",
                    "currency": "USD",
                    "price_unit": "per_unit",
                    "price_scale": "1",
                    "status": "complete",
                }
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Unsupported Type", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["equity-demo"]},
    )

    assert add_response.status_code == 200
    resolve_response = client.post("/api/instruments/equity-demo/resolve")
    assert resolve_response.status_code == 200
    assert resolve_response.json()["detail_view_type"] == "equity"
    assert resolve_response.json()["detail_subject_id"] == "equity-demo"

    tree_response = client.get("/api/taxonomies/instrument-taxonomy")
    assert tree_response.status_code == 200
    equity_node = next(
        node
        for node in tree_response.json()["nodes"]
        if node["node_id"] == "equity-market-us"
    )
    assert equity_node["instrument_type"] == "equity"
    sector_node = next(
        node
        for node in tree_response.json()["nodes"]
        if node["node_id"] == "equity-us-information-technology"
    )
    assert sector_node["instrument_type"] == "equity"
    assert sector_node["is_leaf"] is True
    assert sector_node["path_labels"] == ["美股", "信息技术"]
    europe_node = next(
        node
        for node in tree_response.json()["nodes"]
        if node["node_id"] == "equity-market-eu"
    )
    assert europe_node["instrument_type"] == "equity"
    europe_sector_node = next(
        node
        for node in tree_response.json()["nodes"]
        if node["node_id"] == "equity-eu-information-technology"
    )
    assert europe_sector_node["path_labels"] == ["欧洲股市", "信息技术"]

    settings_response = client.put(
        "/api/instrument-attributes/instruments/equity-demo/settings",
        json={
            "taxonomy_node_id": "equity-us-information-technology",
            "coverage_status": "Invested",
            "updated_by": "test",
        },
    )
    assert settings_response.status_code == 200
    settings_payload = settings_response.json()
    assert settings_payload["updated"] is True
    assert settings_payload["taxonomy_updated"] is True
    assert settings_payload["status_updated"] is True
    assert settings_payload["values"]["coverage_status"] == "Invested"
    assert settings_payload["taxonomy"]["taxonomy_code"] == "instrument_taxonomy"
    assert settings_payload["taxonomy"]["derived_values"] == {
        "instrument_taxonomy_level_1": "美股",
        "instrument_taxonomy_level_2": "信息技术",
        "instrument_taxonomy_leaf": "信息技术",
        "instrument_taxonomy_path": "美股 / 信息技术",
    }
    definition_keys = {
        item["attribute_key"] for item in settings_payload["definitions"]
    }
    assert "equity_business_quality" in definition_keys
    assert "fund_vehicle" not in definition_keys
    assert "primary_geographic_exposure" not in definition_keys
    assert "etf_index_fit" not in definition_keys
    assert "index_methodology_quality" not in definition_keys

    wrong_market_response = client.put(
        "/api/instrument-attributes/instruments/equity-demo/settings",
        json={
            "taxonomy_node_id": "equity-hk-information-technology",
            "coverage_status": "Invested",
            "updated_by": "test",
        },
    )
    assert wrong_market_response.status_code == 422
    assert "Registry exchange market" in wrong_market_response.json()["detail"]

    from watchlist_app.db.session import get_session_factory
    from watchlist_app.repositories.sqlalchemy.taxonomy import SQLAlchemyTaxonomyRepository
    from watchlist_app.services.canonical_recalc import _active_peer_instrument_ids

    with get_session_factory()() as session:
        history = SQLAlchemyTaxonomyRepository().list_assignment_history(
            session,
            instrument_id="equity-demo",
        )
        assert history[-1].node_id == "equity-us-information-technology"
        assert history[-1].path_labels_json == ["美股", "信息技术"]
        assert "equity-demo" in _active_peer_instrument_ids(session)

    grouped_response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "taxonomy",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert grouped_response.status_code == 200
    assert grouped_response.json()["groups"] == [
        {
            "group_value": "美股",
            "row_count": 1,
            "group_depth": 0,
            "group_path": ["美股"],
        },
        {
            "group_value": "美股 / 信息技术",
            "row_count": 1,
            "group_depth": 1,
            "group_path": ["美股", "信息技术"],
        },
    ]

    assert client.get("/api/taxonomies/fund-taxonomy").status_code == 404


def test_instrument_settings_roll_back_taxonomy_and_status_together(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException
    from watchlist_app.api.routes import attributes as attributes_route
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.repositories.sqlalchemy.taxonomy import SQLAlchemyTaxonomyRepository

    watchlist = client.post(
        "/api/watchlists",
        json={"name": "Atomic Instrument Settings", "description": None},
    ).json()
    add_response = client.post(
        f"/api/watchlists/{watchlist['watchlist_id']}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    before = client.get("/api/instrument-attributes/instruments/sxv264")
    assert before.status_code == 200
    before_payload = before.json()
    before_node_id = before_payload["taxonomy"]["assigned_node_id"]
    before_status = before_payload["values"].get("coverage_status")

    tree = client.get("/api/taxonomies/instrument-taxonomy").json()
    next_node_id = next(
        node["node_id"]
        for node in tree["nodes"]
        if node["instrument_type"] == "private_fund"
        and node["is_leaf"]
        and node["node_id"] != before_node_id
    )
    next_status = "Invested" if before_status != "Invested" else "Watch"
    with get_session_factory()() as session:
        history_count_before = len(
            SQLAlchemyTaxonomyRepository().list_assignment_history(
                session,
                instrument_id="sxv264",
            )
        )

    def _fail_recalc(*_args, **_kwargs):
        raise HTTPException(status_code=500, detail="recalc failed")

    monkeypatch.setattr(
        attributes_route.canonical_recalc_service,
        "execute_recalc",
        _fail_recalc,
    )
    response = client.put(
        "/api/instrument-attributes/instruments/sxv264/settings",
        json={
            "taxonomy_node_id": next_node_id,
            "coverage_status": next_status,
            "updated_by": "atomic-test",
        },
    )
    assert response.status_code == 500

    after = client.get("/api/instrument-attributes/instruments/sxv264")
    assert after.status_code == 200
    after_payload = after.json()
    assert after_payload["taxonomy"]["assigned_node_id"] == before_node_id
    assert after_payload["values"].get("coverage_status") == before_status
    with get_session_factory()() as session:
        history_count_after = len(
            SQLAlchemyTaxonomyRepository().list_assignment_history(
                session,
                instrument_id="sxv264",
            )
        )
    assert history_count_after == history_count_before


def test_move_watchlist_items_transfers_membership_to_target_watchlist(
    client: TestClient,
) -> None:
    source = client.post(
        "/api/watchlists",
        json={"name": "Source List", "description": None},
    )
    target = client.post(
        "/api/watchlists",
        json={"name": "Target List", "description": None},
    )
    source_watchlist_id = source.json()["watchlist_id"]
    target_watchlist_id = target.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    move_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items/move",
        json={"instrument_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
    )
    assert move_response.status_code == 200
    assert move_response.json() == {
        "source_watchlist_id": source_watchlist_id,
        "target_watchlist_id": target_watchlist_id,
        "moved_count": 1,
        "added_count": 1,
        "already_present_count": 0,
    }

    source_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": source_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert source_rows.status_code == 200
    assert source_rows.json()["total_rows"] == 0

    target_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": target_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "latest_quote", "latest_quote_date"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert target_rows.status_code == 200
    target_payload = target_rows.json()
    assert target_payload["total_rows"] == 1
    assert target_payload["rows"][0]["instrument_name"] == "SXV264 Total Return Fund"
    assert target_payload["rows"][0]["latest_quote"] == pytest.approx(101.2365, abs=1e-6)
    assert target_payload["rows"][0]["latest_quote_date"] == "2026-04-14"


def test_copy_watchlist_items_adds_membership_to_target_without_removing_source(
    client: TestClient,
) -> None:
    source = client.post(
        "/api/watchlists",
        json={"name": "Copy Source", "description": None},
    )
    target = client.post(
        "/api/watchlists",
        json={"name": "Copy Target", "description": None},
    )
    source_watchlist_id = source.json()["watchlist_id"]
    target_watchlist_id = target.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    copy_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items/copy",
        json={"instrument_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
    )
    assert copy_response.status_code == 200
    assert copy_response.json() == {
        "source_watchlist_id": source_watchlist_id,
        "target_watchlist_id": target_watchlist_id,
        "copied_count": 1,
        "added_count": 1,
        "already_present_count": 0,
    }

    source_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": source_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert source_rows.status_code == 200
    assert source_rows.json()["total_rows"] == 1

    target_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": target_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "latest_quote", "latest_quote_date"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert target_rows.status_code == 200
    target_payload = target_rows.json()
    assert target_payload["total_rows"] == 1
    assert target_payload["rows"][0]["instrument_name"] == "SXV264 Total Return Fund"
    assert target_payload["rows"][0]["latest_quote"] == pytest.approx(101.2365, abs=1e-6)
    assert target_payload["rows"][0]["latest_quote_date"] == "2026-04-14"


def test_move_watchlist_items_rejects_instruments_missing_from_source(
    client: TestClient,
) -> None:
    source = client.post(
        "/api/watchlists",
        json={"name": "Move Missing Source", "description": None},
    )
    target = client.post(
        "/api/watchlists",
        json={"name": "Move Missing Target", "description": None},
    )
    source_watchlist_id = source.json()["watchlist_id"]
    target_watchlist_id = target.json()["watchlist_id"]

    move_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items/move",
        json={"instrument_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
    )
    assert move_response.status_code == 400
    assert move_response.json()["detail"] == "Instruments not found in source watchlist: sxv264."

    target_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": target_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert target_rows.status_code == 200
    assert target_rows.json()["total_rows"] == 0


def test_copy_watchlist_items_rejects_instruments_missing_from_source(
    client: TestClient,
) -> None:
    source = client.post(
        "/api/watchlists",
        json={"name": "Copy Missing Source", "description": None},
    )
    target = client.post(
        "/api/watchlists",
        json={"name": "Copy Missing Target", "description": None},
    )
    source_watchlist_id = source.json()["watchlist_id"]
    target_watchlist_id = target.json()["watchlist_id"]

    copy_response = client.post(
        f"/api/watchlists/{source_watchlist_id}/items/copy",
        json={"instrument_ids": ["sxv264"], "target_watchlist_id": target_watchlist_id},
    )
    assert copy_response.status_code == 400
    assert copy_response.json()["detail"] == "Instruments not found in source watchlist: sxv264."

    target_rows = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": target_watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert target_rows.status_code == 200
    assert target_rows.json()["total_rows"] == 0


def test_adding_shared_nav_instrument_recalculates_last_nav_fields(
    client: TestClient,
) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "FOF Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["accepted_count"] == 1
    assert add_response.json()["recalculated_instrument_ids"] == ["sxv264"]

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": [
                "ticker_or_isin",
                "instrument_name",
                "latest_quote",
                "latest_quote_date",
                "latest_cumulative_nav",
                "latest_cumulative_nav_date",
                "metric_as_of_date",
                "data_freshness_status",
                "return_1w",
                "return_mtd",
                "return_3m",
                "return_6m",
                "annualized_return",
            ],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    payload = screener.json()
    assert payload["total_rows"] == 1
    assert payload["rows"][0]["ticker_or_isin"] == "SXV264"
    assert payload["rows"][0]["latest_quote"] == pytest.approx(101.2365, abs=1e-6)
    assert payload["rows"][0]["latest_quote_date"] == "2026-04-14"
    assert payload["rows"][0]["latest_cumulative_nav"] == pytest.approx(101.2365, abs=1e-6)
    assert payload["rows"][0]["latest_cumulative_nav_date"] == "2026-04-14"
    assert payload["rows"][0]["metric_as_of_date"] == "2026-04-14"
    assert payload["rows"][0]["data_freshness_status"] == "stale"
    assert payload["rows"][0]["return_1w"] == pytest.approx(1.236476, abs=1e-6)
    assert payload["rows"][0]["return_mtd"] == pytest.approx(2.259067, abs=1e-6)
    assert payload["rows"][0]["return_3m"] == pytest.approx(3.832283, abs=1e-6)
    assert payload["rows"][0]["return_6m"] is None
    assert payload["rows"][0]["annualized_return"] is None
    assert payload["snapshot_metadata"]["as_of_date"] == "2026-04-14"


def test_shared_total_return_fixture_is_backed_by_current_projection_factors(
    client: TestClient,
) -> None:
    from watchlist_app.services.shared_instrument_registry import get_shared_instrument

    detail = get_shared_instrument("sxv264")
    assert detail is not None
    current_run_id = detail["current_fund_nav_projection_run_id"]
    factors = {
        item["fund_nav_adjustment_factor_id"]: item
        for item in detail["fund_nav_adjustment_factors"]
    }
    total_rows = [
        point
        for point in detail["market_data"]
        if point["quote_basis"] == "total_return_nav"
    ]

    assert len(total_rows) == len(factors) == 4
    for point in total_rows:
        factor_id = point["nav_lineage"]["evidence"]["factor_record_id"]
        factor = factors[factor_id]
        assert factor["fund_nav_projection_run_id"] == current_run_id
        assert factor["factor_kind"] == "provider_implied"
        assert factor["as_of_date"] == point["as_of_date"]


def test_adding_existing_watchlist_item_is_noop_without_membership_recalc(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Duplicate Add Guard", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    first_add = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert first_add.status_code == 200
    assert first_add.json()["accepted_count"] == 1

    def _unexpected_recalc(*_args, **_kwargs):
        raise AssertionError("duplicate membership add should not execute recalc")

    monkeypatch.setattr(
        watchlists_route.canonical_recalc_service,
        "execute_recalc",
        _unexpected_recalc,
    )

    duplicate_add = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )

    assert duplicate_add.status_code == 200
    assert duplicate_add.json() == {
        "watchlist_id": watchlist_id,
        "accepted_count": 0,
        "pending_recalc_instrument_ids": [],
        "recalculated_instrument_ids": [],
    }


def test_screener_sort_keeps_missing_values_last_for_descending_metrics(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "fund-no-return",
            "instrument_name": "No Return Fund",
            "instrument_type": "public_fund",
            "currency": "USD",
            "quote_selection_policy": canonical_quote_policy("public_fund"),
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "NORET", "is_primary": True},
            ],
            "market_data": [],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Sort Missing Returns", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-no-return", "sxv264"]},
    )
    assert add_response.status_code == 200

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "return_ytd"],
            "sort": [{"field": "return_ytd", "direction": "desc"}],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )

    assert screener.status_code == 200
    rows = screener.json()["rows"]
    assert [row["instrument_id"] for row in rows] == ["sxv264", "fund-no-return"]
    assert rows[0]["return_ytd"] is not None
    assert rows[1]["return_ytd"] is None


def test_calendar_period_returns_use_prior_close_as_base(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "calendar-boundary-fund",
            "instrument_name": "Calendar Boundary Fund",
            "instrument_type": "public_fund",
            "currency": "USD",
            "quote_selection_policy": canonical_quote_policy("public_fund"),
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "CBF", "is_primary": True},
            ],
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "nav_lineage": {
                        "kind": "provider_explicit",
                        "evidence": {"source_field": "test_total_return_nav"},
                    },
                    "as_of_date": as_of_date,
                    "value": value,
                    "currency": "USD",
                    "price_unit": "per_unit",
                    "price_scale": "1",
                    "status": "complete",
                }
                for as_of_date, value in (
                    ("2025-12-31", "100.000000"),
                    ("2026-01-01", "110.000000"),
                    ("2026-03-31", "120.000000"),
                    ("2026-04-01", "130.000000"),
                    ("2026-04-14", "156.000000"),
                )
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Calendar Return Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["calendar-boundary-fund"]},
    )
    assert add_response.status_code == 200

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["ticker_or_isin", "return_mtd", "return_ytd"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    row = screener.json()["rows"][0]
    assert row["ticker_or_isin"] == "CBF"
    assert row["return_mtd"] == pytest.approx(30.0, abs=1e-6)
    assert row["return_ytd"] == pytest.approx(56.0, abs=1e-6)


def test_aligned_decimal_nav_series_materializes_path_risk_metrics(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "aligned-decimal-risk-fund",
            "instrument_name": "Aligned Decimal Risk Fund",
            "instrument_type": "public_fund",
            "currency": "USD",
            "source_settings": {"expected_frequency": "event_driven"},
            "quote_selection_policy": canonical_quote_policy("public_fund"),
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "ADRF", "is_primary": True},
            ],
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "nav_lineage": {
                        "kind": "provider_explicit",
                        "evidence": {"source_field": "test_total_return_nav"},
                    },
                    "as_of_date": as_of_date,
                    "value": value,
                    "currency": "USD",
                    "price_unit": "per_unit",
                    "price_scale": "1",
                    "status": "complete",
                }
                for as_of_date, value in (
                    ("2026-01-02", "100.000000"),
                    ("2026-01-09", "101.000000"),
                    ("2026-01-16", "99.000000"),
                    ("2026-01-23", "102.000000"),
                )
            ],
            "lifecycle_state": {"status": "active"},
        }
    )

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Aligned Decimal Risk", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["aligned-decimal-risk-fund"]},
    )
    assert add_response.status_code == 200

    risk_response = client.get("/api/instruments/aligned-decimal-risk-fund/risk")
    assert risk_response.status_code == 200
    risk_payload = risk_response.json()
    metrics = {row["metric"]: row["investment"] for row in risk_payload["risk_metrics"]}
    assert risk_payload["data_quality"]["status"] == "ready"
    assert metrics["volatility"] is not None
    assert metrics["sharpe_ratio"] is not None


def test_index_close_series_calculates_watchlist_performance_metrics(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "index-close-only",
            "instrument_name": "Close Only Index",
            "instrument_type": "index",
            "currency": "USD",
            "source_settings": {"return_semantics": "price_return"},
            "quote_selection_policy": canonical_quote_policy("index"),
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "identifier_value": "CLOSEIDX",
                    "is_primary": True,
                },
            ],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": as_of_date,
                    "value": value,
                    "currency": "USD",
                    "price_unit": "per_unit",
                    "price_scale": "1",
                    "status": "complete",
                }
                for as_of_date, value in (
                    ("2025-10-15", "90.000000"),
                    ("2025-12-31", "100.000000"),
                    ("2026-03-15", "110.000000"),
                    ("2026-04-01", "115.000000"),
                    ("2026-04-15", "121.000000"),
                )
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Index Return Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["index-close-only"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["recalculated_instrument_ids"] == ["index-close-only"]

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": [
                "ticker_or_isin",
                "instrument_type",
                "return_ytd",
                "return_mtd",
                "return_1m",
                "return_3m",
                "return_6m",
                "annualized_return",
                "max_drawdown",
                "volatility",
                "sharpe_ratio",
                "attr.current_drawdown",
            ],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    row = screener.json()["rows"][0]
    assert row["ticker_or_isin"] == "CLOSEIDX"
    assert row["instrument_type"] == "index"
    assert row["return_ytd"] == pytest.approx(21.0, abs=1e-6)
    assert row["return_mtd"] == pytest.approx(10.0, abs=1e-6)
    assert row["return_1m"] == pytest.approx(10.0, abs=1e-6)
    assert row["return_3m"] == pytest.approx(21.0, abs=1e-6)
    assert row["return_6m"] == pytest.approx(34.444444, abs=1e-6)
    # Endpoint-to-endpoint returns remain available because their boundaries
    # exist. Annualized and path-dependent risk metrics fail closed because the
    # intentionally sparse daily series cannot reveal the path inside gaps.
    assert row["annualized_return"] is None
    assert row["max_drawdown"] is None
    assert row["volatility"] is None
    assert row["sharpe_ratio"] is None
    assert row["attr.current_drawdown"] is None

    field_registry = client.get("/api/field-registry", params={"instrument_type": "index"})
    assert field_registry.status_code == 200
    index_field_keys = {item["field_key"] for item in field_registry.json()["fields"]}
    assert {
        "return_ytd",
        "return_mtd",
        "return_1m",
        "return_3m",
        "return_6m",
        "annualized_return",
        "max_drawdown",
        "volatility",
        "sharpe_ratio",
    }.issubset(index_field_keys)


def test_screener_returns_are_anchored_to_each_instruments_own_as_of_date(
    client: TestClient,
) -> None:
    for instrument_id, instrument_name, ticker, observations in (
        (
            "asof-fund-0724",
            "As Of July 24 Fund",
            "AOF24",
            (("2026-06-23", "100.000000"), ("2026-07-24", "110.000000")),
        ),
        (
            "asof-fund-0727",
            "As Of July 27 Fund",
            "AOF27",
            (("2026-06-26", "100.000000"), ("2026-07-27", "120.000000")),
        ),
    ):
        seed_shared_instrument(
            {
                "instrument_id": instrument_id,
                "instrument_name": instrument_name,
                "instrument_type": "private_fund",
                "currency": "USD",
                "quote_selection_policy": canonical_quote_policy("private_fund"),
                "identifiers": [
                    {
                        "identifier_type": "ticker",
                        "identifier_value": ticker,
                        "is_primary": True,
                    },
                ],
                "market_data": [
                    {
                        "metric_family": "nav",
                        "quote_basis": "total_return_nav",
                        "nav_lineage": {
                            "kind": "provider_explicit",
                            "evidence": {"source_field": "test_total_return_nav"},
                        },
                        "as_of_date": as_of_date,
                        "value": value,
                        "currency": "USD",
                        "price_unit": "per_unit",
                        "price_scale": "1",
                        "status": "complete",
                    }
                    for as_of_date, value in observations
                ],
                "lifecycle_state": {"status": "active"},
            }
        )

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Independent As Of Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["asof-fund-0724", "asof-fund-0727"]},
    )
    assert add_response.status_code == 200

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            # Deliberately do not select the as-of column: the API must still
            # expose the endpoint required to interpret each return.
            "selected_fields": ["instrument_name", "return_1m"],
            "sort": [{"field": "instrument_name", "direction": "asc"}],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )

    assert screener.status_code == 200
    payload = screener.json()
    rows_by_id = {row["instrument_id"]: row for row in payload["rows"]}
    assert rows_by_id["asof-fund-0724"]["metric_as_of_date"] == "2026-07-24"
    assert rows_by_id["asof-fund-0724"]["return_1m"] == pytest.approx(10.0)
    assert rows_by_id["asof-fund-0727"]["metric_as_of_date"] == "2026-07-27"
    assert rows_by_id["asof-fund-0727"]["return_1m"] == pytest.approx(20.0)
    assert payload["snapshot_metadata"]["as_of_date"] == "2026-07-27"
    assert payload["snapshot_metadata"]["as_of_date_min"] == "2026-07-24"
    assert payload["snapshot_metadata"]["as_of_date_max"] == "2026-07-27"
    assert payload["snapshot_metadata"]["has_mixed_as_of_dates"] is True
    assert payload["snapshot_metadata"]["as_of_date_missing_count"] == 0
    assert payload["snapshot_metadata"]["methodology_version"] == "watchlist-row/v2"


def test_instrument_performance_and_risk_payloads_include_materialized_metrics(
    client: TestClient,
) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Performance Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    performance_response = client.get("/api/instruments/sxv264/performance")
    assert performance_response.status_code == 200
    performance_payload = performance_response.json()
    trailing_by_window = {
        row["window"]: row
        for row in performance_payload["trailing_returns"]
    }
    assert performance_payload["snapshot_metadata"]["as_of_date"] == "2026-04-14"
    assert trailing_by_window["MTD"]["investment_nav"] == pytest.approx(2.259067, abs=1e-6)
    assert trailing_by_window["MTD"]["requested_start_date"] == "2026-04-01"
    assert trailing_by_window["MTD"]["anchor_mode"] == "strictly_before"
    assert trailing_by_window["MTD"]["anchor_date"] == "2026-03-14"
    assert trailing_by_window["MTD"]["end_date"] == "2026-04-14"
    assert trailing_by_window["YTD"]["investment_nav"] == pytest.approx(3.832283, abs=1e-6)
    assert trailing_by_window["YTD"]["anchor_date"] == "2025-12-31"
    annual_by_year = {
        row["year"]: row
        for row in performance_payload["annual_returns"]
    }
    assert annual_by_year[2026]["investment_nav"] == pytest.approx(3.832283, abs=1e-6)
    assert annual_by_year[2026]["anchor_date"] == "2025-12-31"
    assert annual_by_year[2026]["end_date"] == "2026-04-14"
    assert trailing_by_window["Ann."]["investment_nav"] is None
    assert performance_payload["return_window_policy"] == "return-window/v2"

    risk_response = client.get("/api/instruments/sxv264/risk")
    assert risk_response.status_code == 200
    risk_payload = risk_response.json()
    risk_metrics = {
        row["metric"]: row
        for row in risk_payload["risk_metrics"]
    }
    assert risk_payload["snapshot_metadata"]["as_of_date"] == "2026-04-14"
    assert risk_payload["scatter_points"] == []
    assert risk_metrics["annualized_return"]["investment"] is None
    assert risk_metrics["volatility"]["investment"] is None
    assert risk_metrics["sharpe_ratio"]["investment"] is None
    assert risk_metrics["max_drawdown"]["investment"] is None
    assert risk_metrics["calmar_ratio"]["investment"] is None
    assert risk_payload["drawdown_summary"] is None
    assert risk_payload["current_drawdown"] is None
    assert risk_payload["risk_structure"] is None
    assert risk_payload["current_watch"] is None
    assert risk_payload["change_monitor"] is None
    assert risk_payload["data_quality"]["status"] == "partial_missing_observations"
    assert risk_payload["data_quality"]["note"]
    assert risk_payload["calculation_frequency_profile"]["resolved_frequency"] == "daily"
    assert risk_payload["calculation_frequency_profile"]["gap_count"] > 0
    assert performance_payload["calculation_frequency_profile"]["resolved_frequency"] == "daily"


def test_instrument_detail_payload_uses_daily_calculation_frequency(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
            "instrument_id": "weekly-risk-fund",
            "instrument_name": "Weekly Risk Fund",
            "instrument_type": "public_fund",
            "currency": "USD",
            "source_settings": {
                "expected_frequency": "daily",
                "market_calendar": None,
                "release_lag_days": 2,
            },
            "quote_selection_policy": canonical_quote_policy("public_fund"),
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "WRF", "is_primary": True},
            ],
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "nav_lineage": {
                        "kind": "provider_explicit",
                        "evidence": {"source_field": "test_total_return_nav"},
                    },
                    "as_of_date": as_of_date,
                    "value": value,
                    "currency": "USD",
                    "price_unit": "per_unit",
                    "price_scale": "1",
                    "frequency": "daily",
                    "status": "complete",
                }
                for as_of_date, value in zip(
                    ["2026-01-02", "2026-01-09", "2026-01-16", "2026-01-23"],
                    ["100.000000", "101.000000", "99.500000", "102.000000"],
                    strict=True,
                )
            ],
            "lifecycle_state": {"status": "active"},
        }
    )
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Weekly Frequency Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["weekly-risk-fund"]},
    )
    assert add_response.status_code == 200

    nav_response = client.get("/api/instruments/weekly-risk-fund/nav-series")
    assert nav_response.status_code == 200
    nav_payload = nav_response.json()
    assert nav_payload["calculation_frequency_profile"]["resolved_frequency"] == "daily"
    assert [
        row["date"]
        for row in nav_payload["rows"]
        if row["calculation_included"]
    ] == [
        "2026-01-02",
        "2026-01-09",
        "2026-01-16",
        "2026-01-23",
    ]

    risk_response = client.get("/api/instruments/weekly-risk-fund/risk")
    assert risk_response.status_code == 200
    risk_payload = risk_response.json()
    assert risk_payload["snapshot_metadata"]["methodology_version"] == "canonical-risk/v8"
    assert risk_payload["calculation_frequency_profile"]["resolved_frequency"] == "daily"
    assert risk_payload["calculation_frequency_profile"]["annualization_periods_per_year"] == pytest.approx(
        52.178571,
        abs=1e-6,
    )


def test_instrument_performance_payload_includes_taxonomy_peer_ranking(
    client: TestClient,
) -> None:
    for instrument_id, instrument_name, ticker, values in (
        (
            "peer-strong",
            "Peer Strong Fund",
            "PSTR",
            ["97.500000", "100.000000", "103.000000", "105.000000"],
        ),
        (
            "peer-weak",
            "Peer Weak Fund",
            "PWK",
            ["100.000000", "99.000000", "98.000000", "99.000000"],
        ),
        (
            "peer-archived",
            "Peer Archived Fund",
            "POLD",
            ["90.000000", "110.000000", "125.000000", "140.000000"],
        ),
        (
            "peer-top",
            "Peer Top Fund",
            "PTOP",
            ["90.000000", "102.000000", "107.000000", "112.000000"],
        ),
        (
            "peer-lower",
            "Peer Lower Fund",
            "PLOW",
            ["100.000000", "98.000000", "97.000000", "98.000000"],
        ),
        (
            "peer-other-region",
            "Peer Other Region Fund",
            "PREGION",
            ["100.000000", "101.000000", "102.000000", "103.000000"],
        ),
    ):
        seed_shared_instrument(
            {
                "instrument_id": instrument_id,
                "instrument_name": instrument_name,
                "instrument_type": "private_fund",
                "currency": "USD",
                "quote_selection_policy": canonical_quote_policy("private_fund"),
                "identifiers": [
                    {"identifier_type": "ticker", "identifier_value": ticker, "is_primary": True},
                ],
                "market_data": [
                    {
                        "metric_family": "nav",
                        "quote_basis": "total_return_nav",
                        "nav_lineage": {
                            "kind": "provider_explicit",
                            "evidence": {"source_field": "test_total_return_nav"},
                        },
                        "as_of_date": as_of_date,
                        "value": value,
                        "currency": "USD",
                        "price_unit": "per_unit",
                        "price_scale": "1",
                        "status": "complete",
                    }
                    for as_of_date, value in zip(
                        ["2025-12-31", "2026-03-14", "2026-04-07", "2026-04-14"],
                        values,
                        strict=True,
                    )
                ],
                "lifecycle_state": {"status": "active"},
            }
        )

    seed_shared_instrument(
        {
            "instrument_id": "peer-stale-date",
            "instrument_name": "Peer Stale Date Fund",
            "instrument_type": "private_fund",
            "currency": "USD",
            "quote_selection_policy": canonical_quote_policy("private_fund"),
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "identifier_value": "PSTALE",
                    "is_primary": True,
                },
            ],
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "nav_lineage": {
                        "kind": "provider_explicit",
                        "evidence": {"source_field": "test_total_return_nav"},
                    },
                    "as_of_date": as_of_date,
                    "value": value,
                    "currency": "USD",
                    "price_unit": "per_unit",
                    "price_scale": "1",
                    "status": "complete",
                }
                for as_of_date, value in zip(
                    ["2025-12-31", "2026-03-13", "2026-04-06", "2026-04-13"],
                    ["99.000000", "100.000000", "101.000000", "102.000000"],
                    strict=True,
                )
            ],
            "lifecycle_state": {"status": "active"},
        }
    )

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Peer Ranking Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={
            "instrument_ids": [
                "sxv264",
                "peer-strong",
                "peer-weak",
                "peer-archived",
                "peer-stale-date",
                "peer-top",
                "peer-lower",
                "peer-other-region",
            ]
        },
    )
    assert add_response.status_code == 200

    for instrument_id in (
        "sxv264",
        "peer-strong",
        "peer-weak",
        "peer-archived",
        "peer-stale-date",
        "peer-top",
        "peer-lower",
        "peer-other-region",
    ):
        update_response = client.put(
            f"/api/taxonomies/instrument-taxonomy/instruments/{instrument_id}",
            json={"node_id": "fund-private-equity-quant-index-enhanced", "updated_by": "test"},
        )
        assert update_response.status_code == 200

    missing_dimension_response = client.get("/api/instruments/sxv264/performance")
    assert missing_dimension_response.status_code == 200
    assert missing_dimension_response.json()["peer_comparison"]["status"] == "missing_peer_dimension"
    assert missing_dimension_response.json()["peer_comparison"]["summary"] == {
        "missing_dimensions": ["primary_geographic_exposure"]
    }

    for instrument_id in (
        "sxv264",
        "peer-strong",
        "peer-weak",
        "peer-archived",
        "peer-stale-date",
        "peer-top",
        "peer-lower",
        "peer-other-region",
    ):
        geography_response = client.post(
            f"/api/instrument-attributes/instruments/{instrument_id}",
            json={
                "values": [
                    {
                        "attribute_key": "primary_geographic_exposure",
                        "value": (
                            "美国"
                            if instrument_id == "peer-other-region"
                            else "中国 A 股"
                        ),
                    }
                ]
            },
        )
        assert geography_response.status_code == 200

    recalc_archived_peer_response = client.post(
        "/api/recalc/instruments/peer-archived/execute",
        json={
            "job_type": "performance",
            "trigger_type": "test",
            "trigger_ref_type": "taxonomy_peer_ranking",
            "trigger_ref_id": "peer-archived",
        },
    )
    assert recalc_archived_peer_response.status_code == 200

    from investment_studio_instrument_core import instrument_store as shared_store
    from watchlist_app.db import session as session_module

    shared_store.archive_instrument(
        session_module.get_session_factory(),
        instrument_id="peer-archived",
        updated_by="test",
    )
    with session_module.get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE instrument_detail
                   SET is_active = false
                 WHERE instrument_id = 'peer-archived'
                """
            )
        )
        session.commit()

    recalc_response = client.post(
        "/api/recalc/instruments/sxv264/execute",
        json={
            "job_type": "performance",
            "trigger_type": "test",
            "trigger_ref_type": "taxonomy_peer_ranking",
            "trigger_ref_id": "sxv264",
        },
    )
    assert recalc_response.status_code == 200

    performance_response = client.get("/api/instruments/sxv264/performance")
    assert performance_response.status_code == 200
    payload = performance_response.json()
    peer_comparison = payload["peer_comparison"]
    assert peer_comparison["status"] == "ready"
    assert peer_comparison["peer_node_id"] == "fund-private-equity-quant-index-enhanced"
    assert peer_comparison["peer_dimensions"] == {
        "primary_geographic_exposure": "中国 A 股"
    }
    assert peer_comparison["candidate_count"] == 5
    assert peer_comparison["sample_count"] == 4
    assert peer_comparison["excluded_dimension_mismatch_count"] == 1
    assert peer_comparison["excluded_mismatched_as_of_count"] == 1
    assert payload["ranking"] is None

    metrics_by_key = {row["metric_key"]: row for row in peer_comparison["metrics"]}
    assert metrics_by_key["return_ytd"]["rank"] == 3
    assert metrics_by_key["return_ytd"]["percentile"] == pytest.approx(50.0)
    assert metrics_by_key["return_ytd"]["excluded_mismatched_as_of_count"] == 1
    assert "annualized_return" not in metrics_by_key
    trailing_by_window = {row["window"]: row for row in payload["trailing_returns"]}
    assert trailing_by_window["1M"]["category_nav"] is not None
    assert trailing_by_window["Ann."]["category_nav"] is None

    screener_response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "classification",
            "selected_fields": [
                "instrument_name",
                "attr.peer_group",
                "attr.peer_sample_count",
                "attr.peer_return_1w_percentile",
                "attr.peer_return_1m_percentile",
                "attr.peer_annualized_return_percentile",
            ],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener_response.status_code == 200
    sxv_row = next(
        row
        for row in screener_response.json()["rows"]
        if row["instrument_id"] == "sxv264"
    )
    assert sxv_row["attr.peer_group"] == "股票策略 / 量化多头 / 指数增强"
    assert sxv_row["attr.peer_sample_count"] == 4
    assert sxv_row["attr.peer_return_1w_percentile"] == pytest.approx(50.0)
    assert sxv_row["attr.peer_return_1m_percentile"] == pytest.approx(50.0)
    assert sxv_row["attr.peer_annualized_return_percentile"] is None

    # Moving a peer must change the target's comparison immediately, without
    # requiring a target recalc that could leave the cross-section stale.
    peer_move_response = client.put(
        "/api/taxonomies/instrument-taxonomy/instruments/peer-strong",
        json={"node_id": "fund-private-equity-quant-stock-selection", "updated_by": "test"},
    )
    assert peer_move_response.status_code == 200
    refreshed_target = client.get("/api/instruments/sxv264/performance")
    assert refreshed_target.status_code == 200
    assert refreshed_target.json()["peer_comparison"]["status"] == "limited_sample"
    assert refreshed_target.json()["peer_comparison"]["sample_count"] == 3
    assert refreshed_target.json()["ranking"] is None


def test_instrument_nav_settings_round_trip_and_surface_compare_settings(
    client: TestClient,
) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Nav Settings Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264", "savf63", "fund-us-agg"]},
    )
    assert add_response.status_code == 200

    initial_response = client.get("/api/instruments/sxv264/nav-settings")
    assert initial_response.status_code == 200
    assert initial_response.json() == {
        "nav_basis_preference": "auto",
        "default_benchmark_instrument_id": None,
        "peer_baseline_instrument_ids": [],
    }

    update_response = client.put(
        "/api/instruments/sxv264/nav-settings",
        json={
            "nav_basis_preference": "nav_with_dividend",
            "default_benchmark_instrument_id": "savf63",
            "peer_baseline_instrument_ids": ["fund-us-agg", "savf63", "sxv264", "fund-us-agg"],
            "updated_by": "test-suite",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json() == {
        "nav_basis_preference": "nav_with_dividend",
        "default_benchmark_instrument_id": "savf63",
        "peer_baseline_instrument_ids": ["fund-us-agg", "savf63"],
    }

    nav_series_response = client.get("/api/instruments/sxv264/nav-series")
    assert nav_series_response.status_code == 200
    nav_series_payload = nav_series_response.json()
    assert nav_series_payload["nav_basis_preference"] == "nav_with_dividend"
    assert nav_series_payload["compare_settings"] == {
        "default_benchmark_instrument_id": "savf63",
        "peer_instrument_ids": ["fund-us-agg", "savf63"],
    }

    clear_response = client.put(
        "/api/instruments/sxv264/nav-settings",
        json={
            "default_benchmark_instrument_id": None,
            "peer_baseline_instrument_ids": [],
            "updated_by": "test-suite",
        },
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["default_benchmark_instrument_id"] is None
    assert clear_response.json()["peer_baseline_instrument_ids"] == []


def test_watchlist_add_rolls_back_when_recalc_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException
    from watchlist_app.api.routes import watchlists as watchlists_route

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Atomic Guardrail", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    call_count = 0

    def _recalc_then_fail(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise HTTPException(status_code=500, detail="recalc failed")
        return {"job_status": "completed", "result": {}}

    monkeypatch.setattr(watchlists_route.canonical_recalc_service, "execute_recalc", _recalc_then_fail)

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264", "savf63"]},
    )
    assert add_response.status_code == 500
    assert add_response.json()["detail"] == "recalc failed"

    detail = client.get(f"/api/watchlists/{watchlist_id}")
    assert detail.status_code == 200
    assert detail.json()["item_count"] == 0

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    assert screener.json()["total_rows"] == 0


def test_watchlist_rejects_unknown_shared_instrument_ids(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Unknown Guardrail", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["not-in-registry"]},
    )
    assert add_response.status_code == 404
    assert "backend maintenance" in add_response.json()["detail"]


def test_screener_query_triggers_async_refresh_when_shared_data_is_newer(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import screener as screener_route

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Freshness Repair", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    scheduled: list[dict[str, object]] = []

    monkeypatch.setattr(
        screener_route,
        "schedule_instrument_refreshes_if_stale",
        lambda **kwargs: scheduled.append(kwargs) or 1,
    )

    response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "latest_quote", "latest_quote_date", "metric_as_of_date"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )

    assert response.status_code == 200
    assert len(scheduled) == 1
    target = scheduled[0]["targets"][0]
    assert target["instrument_id"] == "sxv264"
    assert target["local_latest_date"] == date(2026, 4, 14)
    assert "local_source_cutoff_at" in target
    assert scheduled[0]["trigger_ref_type"] == "screener_query"
    assert scheduled[0]["trigger_ref_id"] == watchlist_id


def test_instrument_summary_triggers_async_refresh_when_shared_data_is_newer(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.services import read_model_freshness

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Instrument Freshness", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    scheduled: list[dict[str, object]] = []

    monkeypatch.setattr(
        read_model_freshness,
        "get_shared_instrument",
        lambda instrument_id: {
            "instrument_id": instrument_id,
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "official_nav",
                    "as_of_date": "2026-04-15",
                    "value": "101.500000",
                }
            ],
        },
    )
    monkeypatch.setattr(
        read_model_freshness,
        "_enqueue_stale_recalc_job",
        lambda **kwargs: scheduled.append(kwargs) or True,
    )

    response = client.get("/api/instruments/sxv264/summary")

    assert response.status_code == 200
    assert scheduled == [
        {
            "instrument_id": "sxv264",
            "trigger_ref_type": "instrument_summary_read",
            "trigger_ref_id": "sxv264",
        }
    ]


def test_stale_read_repair_enqueues_single_durable_recalc_job(client: TestClient) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.read_model_freshness import schedule_instrument_refresh_if_stale

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Durable Freshness Repair", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    first = schedule_instrument_refresh_if_stale(
        instrument_id="sxv264",
        local_latest_date=date(2026, 4, 13),
        trigger_ref_type="instrument_summary_read",
        trigger_ref_id="sxv264",
    )
    second = schedule_instrument_refresh_if_stale(
        instrument_id="sxv264",
        local_latest_date=date(2026, 4, 13),
        trigger_ref_type="instrument_summary_read",
        trigger_ref_id="sxv264",
    )

    assert first is True
    assert second is True

    session_factory = session_module.get_session_factory()
    with session_factory() as session:
        jobs = list(SQLAlchemyRecalcJobRepository().list_recent(session))

    matching = [
        job
        for job in jobs
        if job.instrument_id == "sxv264"
        and job.job_type == "all"
        and job.trigger_type == "stale_read_repair"
        and job.trigger_ref_type == "instrument_summary_read"
        and job.trigger_ref_id == "sxv264"
    ]
    assert len(matching) == 1
    assert matching[0].job_status == "queued"


def test_same_date_market_data_revision_triggers_refresh(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.services import read_model_freshness
    from watchlist_app.services.shared_instrument_registry import get_shared_instrument

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Revision Freshness Repair", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    assert client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    ).status_code == 200

    shared = get_shared_instrument("sxv264")
    assert shared is not None
    shared_updated_at = datetime.fromisoformat(
        str(shared["market_data_updated_at"]).replace("Z", "+00:00")
    )
    scheduled: list[dict[str, object]] = []
    monkeypatch.setattr(
        read_model_freshness,
        "_enqueue_stale_recalc_job",
        lambda **kwargs: scheduled.append(kwargs) or True,
    )

    assert read_model_freshness.schedule_instrument_refresh_if_stale(
        instrument_id="sxv264",
        local_latest_date=date(2026, 4, 14),
        local_source_cutoff_at=shared_updated_at - timedelta(seconds=1),
        trigger_ref_type="revision_test",
    ) is True
    assert read_model_freshness.schedule_instrument_refresh_if_stale(
        instrument_id="sxv264",
        local_latest_date=date(2026, 4, 14),
        local_source_cutoff_at=shared_updated_at + timedelta(seconds=1),
        trigger_ref_type="revision_test",
    ) is False
    assert len(scheduled) == 1


def test_worker_reconciliation_repairs_a_missed_market_data_notification(
    client: TestClient,
) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.db.models.recalc import RecalcJob
    from watchlist_app.services.read_model_freshness import (
        reconcile_stale_instrument_read_models,
    )

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Silent Source Change", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    assert client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    ).status_code == 200

    session_factory = session_module.get_session_factory()
    revised = deepcopy(TEST_SHARED_INSTRUMENTS["sxv264"])
    for point in revised["market_data"]:
        if point["as_of_date"] == "2026-04-07":
            point["provider"] = "silent-revision-test"
    publish_provider_explicit_nav(session_factory, revised)

    reconciliation = reconcile_stale_instrument_read_models(limit=500)

    with session_factory() as session:
        jobs = list(
            session.scalars(
                select(RecalcJob).where(
                    RecalcJob.instrument_id == "sxv264",
                    RecalcJob.trigger_ref_type == "worker_reconcile",
                )
            ).all()
        )
    assert reconciliation.scheduled_count == 1
    assert reconciliation.scanned_count >= 1
    assert reconciliation.cycle_completed is True
    assert len(jobs) == 1
    assert jobs[0].job_status == "queued"


def test_stale_read_repair_commits_requeued_existing_job(client: TestClient) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.read_model_freshness import schedule_instrument_refresh_if_stale
    from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Stale Repair Requeue", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    session_factory = session_module.get_session_factory()
    repo = SQLAlchemyRecalcJobRepository()
    with session_factory() as session:
        record = repo.create(
            session,
            recalc_job_id="stale-repair-running",
            job_type="all",
            instrument_id="sxv264",
            trigger_type="stale_read_repair",
            trigger_ref_type="instrument_summary_read",
            trigger_ref_id="sxv264",
            job_status="running",
            priority=95,
            dedupe_key=make_recalc_dedupe_key(
                job_type="all",
                instrument_id="sxv264",
            ),
            payload_json={"requested_by": "stale_read_repair"},
        )
        record.started_at = datetime.now(UTC) - timedelta(seconds=600)
        session.commit()

    scheduled = schedule_instrument_refresh_if_stale(
        instrument_id="sxv264",
        local_latest_date=date(2026, 4, 13),
        trigger_ref_type="instrument_summary_read",
        trigger_ref_id="sxv264",
    )

    assert scheduled is True

    with session_factory() as session:
        record = repo.get(session, "stale-repair-running")

    assert record is not None
    assert record.job_status == "queued"
    assert record.started_at is None
    assert record.error_message == "Recovered stale running job after worker interruption."


def test_manual_recalc_enqueue_reuses_open_dedupe_job(client: TestClient) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Manual Recalc Dedupe", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    first = client.post("/api/recalc/instruments/sxv264/performance")
    second = client.post("/api/recalc/instruments/sxv264/performance")

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert second_payload["recalc_job_id"] == first_payload["recalc_job_id"]
    assert second_payload["dedupe_key"] == first_payload["dedupe_key"]

    session_factory = session_module.get_session_factory()
    with session_factory() as session:
        jobs = list(SQLAlchemyRecalcJobRepository().list_recent(session))

    matching = [
        job
        for job in jobs
        if job.instrument_id == "sxv264"
        and job.job_type == "performance"
        and job.trigger_type == "manual_api"
        and job.trigger_ref_type == "api_request"
    ]
    assert len(matching) == 1


def test_bulk_recalc_provisions_supported_shared_instrument_before_enqueue(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/recalc/bulk",
        json={
            "instrument_ids": ["sxv264"],
            "job_type": "all",
            "trigger_type": "market_data_refresh",
            "trigger_ref_type": "shared_market_data",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "requested_count": 1,
        "accepted_count": 1,
        "provisioned_instrument_ids": ["sxv264"],
        "enqueued_instrument_ids": ["sxv264"],
        "existing_instrument_ids": [],
        "missing_instrument_ids": [],
    }
    detail = client.post("/api/instruments/sxv264/resolve")
    assert detail.status_code == 200
    assert detail.json()["detail_supported"] is True


def test_bulk_recalc_keeps_unknown_shared_instrument_explicitly_missing(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/recalc/bulk",
        json={
            "instrument_ids": ["not-in-registry"],
            "job_type": "all",
            "trigger_type": "market_data_refresh",
            "trigger_ref_type": "shared_market_data",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "requested_count": 1,
        "accepted_count": 0,
        "provisioned_instrument_ids": [],
        "enqueued_instrument_ids": [],
        "existing_instrument_ids": [],
        "missing_instrument_ids": ["not-in-registry"],
    }


def test_synchronous_recalc_reuses_queued_job(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Queued synchronous reuse", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    assert client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    ).status_code == 200

    queued = client.post("/api/recalc/instruments/sxv264/performance")
    assert queued.status_code == 200
    executed = client.post(
        "/api/recalc/instruments/sxv264/execute",
        json={
            "job_type": "performance",
            "trigger_type": "test",
            "trigger_ref_type": None,
            "trigger_ref_id": None,
        },
    )

    assert executed.status_code == 200
    assert executed.json()["recalc_job_id"] == queued.json()["recalc_job_id"]
    assert executed.json()["job_status"] == "completed"


def test_synchronous_recalc_rejects_overlapping_instrument_job(client: TestClient) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Running recalc conflict", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    assert client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    ).status_code == 200

    repository = SQLAlchemyRecalcJobRepository()
    with session_module.get_session_factory()() as session:
        running = repository.create(
            session,
            recalc_job_id=make_recalc_job_id(),
            job_type="exposure",
            instrument_id="sxv264",
            trigger_type="test",
            trigger_ref_type=None,
            trigger_ref_id=None,
            job_status="queued",
            priority=90,
            dedupe_key=make_recalc_dedupe_key(
                job_type="exposure",
                instrument_id="sxv264",
            ),
            payload_json={"requested_by": "test"},
        )
        repository.mark_running(session, running)
        session.commit()

    response = client.post(
        "/api/recalc/instruments/sxv264/execute",
        json={
            "job_type": "performance",
            "trigger_type": "test",
            "trigger_ref_type": None,
            "trigger_ref_id": None,
        },
    )
    assert response.status_code == 409
    assert "already has running recalc job" in response.json()["detail"]


def test_manual_recalc_enqueue_returns_existing_job_after_dedupe_race(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import recalc as recalc_route
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Manual Recalc Race", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    repo = SQLAlchemyRecalcJobRepository()
    session_factory = session_module.get_session_factory()
    with session_factory() as session:
        repo.create(
            session,
            recalc_job_id="manual-race-existing",
            job_type="performance",
            instrument_id="sxv264",
            trigger_type="manual_api",
            trigger_ref_type="api_request",
            trigger_ref_id=None,
            job_status="queued",
            priority=85,
            dedupe_key=make_recalc_dedupe_key(
                job_type="performance",
                instrument_id="sxv264",
            ),
            payload_json={"requested_by": "api"},
        )
        session.commit()

    original_find_open_job = recalc_route.recalc_repository.find_open_job
    find_calls = 0

    def _find_open_job(*args, **kwargs):
        nonlocal find_calls
        find_calls += 1
        if find_calls == 1:
            return None
        return original_find_open_job(*args, **kwargs)

    monkeypatch.setattr(recalc_route.recalc_repository, "find_open_job", _find_open_job)

    response = client.post("/api/recalc/instruments/sxv264/performance")

    assert response.status_code == 200
    assert response.json()["recalc_job_id"] == "manual-race-existing"
    assert find_calls == 2


def test_process_next_recalc_job_refreshes_shared_metadata_drift(
    client: TestClient,
) -> None:
    from investment_studio_instrument_core import instrument_store as shared_store
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.recalc_worker import process_next_recalc_job

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Metadata Drift Repair", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    session_factory = session_module.get_session_factory()
    before_drift = shared_store.get_instrument(session_factory, "sxv264")
    assert before_drift is not None
    ledger_identity = (
        before_drift["current_fund_nav_projection_run_id"],
        [
            item["fund_nav_projection_run_id"]
            for item in before_drift["fund_nav_projection_runs"]
        ],
        [
            item["fund_nav_adjustment_factor_id"]
            for item in before_drift["fund_nav_adjustment_factor_history"]
        ],
    )
    mutate_shared_instrument_metadata_for_drift(
        instrument_id="sxv264",
        instrument_name="SXV264 Renamed Total Return Fund",
        identifiers=[
            {
                "identifier_type": "ticker",
                "identifier_value": "SXV264X",
                "is_primary": True,
            }
        ],
    )
    after_drift = shared_store.get_instrument(session_factory, "sxv264")
    assert after_drift is not None
    assert (
        after_drift["current_fund_nav_projection_run_id"],
        [
            item["fund_nav_projection_run_id"]
            for item in after_drift["fund_nav_projection_runs"]
        ],
        [
            item["fund_nav_adjustment_factor_id"]
            for item in after_drift["fund_nav_adjustment_factor_history"]
        ],
    ) == ledger_identity

    summary_response = client.get("/api/instruments/sxv264/summary")
    assert summary_response.status_code == 200

    with session_factory() as session:
        jobs = list(SQLAlchemyRecalcJobRepository().list_recent(session))
    queued_jobs = [
        job
        for job in jobs
        if job.instrument_id == "sxv264"
        and job.trigger_type == "stale_read_repair"
        and job.job_status == "queued"
    ]
    assert len(queued_jobs) == 1

    assert process_next_recalc_job() is True

    with session_factory() as session:
        jobs = list(SQLAlchemyRecalcJobRepository().list_recent(session))
    completed_jobs = [
        job
        for job in jobs
        if job.instrument_id == "sxv264"
        and job.trigger_type == "stale_read_repair"
        and job.job_status == "completed"
    ]
    assert len(completed_jobs) == 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "ticker_or_isin"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    payload = screener.json()
    assert payload["rows"][0]["instrument_name"] == "SXV264 Renamed Total Return Fund"
    assert payload["rows"][0]["ticker_or_isin"] == "SXV264X"


def test_process_next_recalc_job_recovers_stale_running_job(
    client: TestClient,
) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.recalc_job_ids import make_recalc_job_id
    from watchlist_app.services.recalc_worker import process_next_recalc_job

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Worker Recovery", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    session_factory = session_module.get_session_factory()
    repository = SQLAlchemyRecalcJobRepository()
    job_id = make_recalc_job_id()
    with session_factory() as session:
        record = repository.create(
            session,
            recalc_job_id=job_id,
            job_type="all",
            instrument_id="sxv264",
            trigger_type="stale_read_repair",
            trigger_ref_type="instrument_summary_read",
            trigger_ref_id="sxv264",
            job_status="queued",
            priority=95,
            dedupe_key=f"all:sxv264:{job_id}",
            payload_json={"requested_by": "test"},
        )
        repository.mark_running(session, record)
        record.started_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)
        record.heartbeat_at = record.started_at
        session.commit()

    assert process_next_recalc_job() is True

    with session_factory() as session:
        recovered = repository.get(session, job_id)

    assert recovered is not None
    assert recovered.job_status == "completed"


def test_recalc_job_with_recent_heartbeat_is_not_recovered(
    client: TestClient,
) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.recalc_job_ids import make_recalc_job_id

    session_factory = session_module.get_session_factory()
    repository = SQLAlchemyRecalcJobRepository()
    job_id = make_recalc_job_id()
    with session_factory() as session:
        record = repository.create(
            session,
            recalc_job_id=job_id,
            job_type="all",
            instrument_id="sxv264",
            trigger_type="stale_read_repair",
            trigger_ref_type="instrument_summary_read",
            trigger_ref_id="sxv264",
            job_status="queued",
            priority=95,
            dedupe_key=f"all:sxv264:{job_id}",
            payload_json={"requested_by": "test"},
        )
        repository.mark_running(session, record)
        record.started_at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)
        session.commit()

    with session_factory() as session:
        recovered_count = repository.requeue_stale_running_jobs(
            session,
            timeout_seconds=300,
        )
        session.commit()
        active = repository.get(session, job_id)

    assert recovered_count == 0
    assert active is not None
    assert active.job_status == "running"


def test_open_recalc_identity_ignores_trigger_metadata(client: TestClient) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.read_model_freshness import _enqueue_stale_recalc_job

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Cross-endpoint dedupe", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    assert client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    ).status_code == 200

    assert _enqueue_stale_recalc_job(
        instrument_id="sxv264",
        trigger_ref_type="instrument_summary_read",
        trigger_ref_id="summary",
    ) is True
    assert _enqueue_stale_recalc_job(
        instrument_id="sxv264",
        trigger_ref_type="instrument_risk_read",
        trigger_ref_id="risk",
    ) is True

    with session_module.get_session_factory()() as session:
        open_jobs = [
            job
            for job in SQLAlchemyRecalcJobRepository().list_recent(session)
            if job.instrument_id == "sxv264"
            and job.job_type == "all"
            and job.job_status in {"queued", "running"}
        ]
    assert len(open_jobs) == 1


def test_recalc_claim_serializes_all_job_types_per_instrument(client: TestClient) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id

    repository = SQLAlchemyRecalcJobRepository()
    with session_module.get_session_factory()() as session:
        for job_type in ("performance", "exposure"):
            repository.create(
                session,
                recalc_job_id=make_recalc_job_id(),
                job_type=job_type,
                instrument_id="sxv264",
                trigger_type="test",
                trigger_ref_type=None,
                trigger_ref_id=None,
                job_status="queued",
                priority=90,
                dedupe_key=make_recalc_dedupe_key(
                    job_type=job_type,
                    instrument_id="sxv264",
                ),
                payload_json={"requested_by": "test"},
            )
        first = repository.claim_next_queued(session)
        assert first is not None
        assert first.lease_token
        assert repository.claim_next_queued(session) is None


def test_lost_recalc_lease_rolls_back_materialized_changes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.db.models.instruments import InstrumentDetail
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services.canonical_recalc import (
        CanonicalRecalcService,
        RecalcJobLeaseLostError,
    )
    from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Lease rollback", "description": None},
    )
    assert client.post(
        f"/api/watchlists/{created_watchlist.json()['watchlist_id']}/items",
        json={"instrument_ids": ["sxv264"]},
    ).status_code == 200

    repository = SQLAlchemyRecalcJobRepository()
    service = CanonicalRecalcService()
    session_factory = session_module.get_session_factory()
    with session_factory() as session:
        record = repository.create(
            session,
            recalc_job_id=make_recalc_job_id(),
            job_type="all",
            instrument_id="sxv264",
            trigger_type="test",
            trigger_ref_type=None,
            trigger_ref_id=None,
            job_status="queued",
            priority=100,
            dedupe_key=make_recalc_dedupe_key(
                job_type="all",
                instrument_id="sxv264",
            ),
            payload_json={"requested_by": "test"},
        )
        repository.mark_running(session, record)
        job_id = record.recalc_job_id
        session.commit()

    def _materialize_then_return(session, **_kwargs):
        instrument = session.get(InstrumentDetail, "sxv264")
        assert instrument is not None
        instrument.instrument_name = "MUST NOT COMMIT"
        session.flush()
        return {"source_changed_during_recalc": False}

    monkeypatch.setattr(service, "_execute_recalc_job", _materialize_then_return)
    monkeypatch.setattr(service.recalc_repository, "mark_completed", lambda *_args, **_kwargs: False)

    with session_factory() as session:
        claimed = repository.get(session, job_id)
        assert claimed is not None
        with pytest.raises(RecalcJobLeaseLostError):
            service.execute_claimed_job(session, record=claimed, commit=True)

    with session_factory() as session:
        instrument = session.get(InstrumentDetail, "sxv264")
        assert instrument is not None
        assert instrument.instrument_name == TEST_SHARED_INSTRUMENTS["sxv264"]["instrument_name"]


def test_recalc_uses_read_watermark_and_enqueues_follow_up_when_source_changes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.db.models.analytics import PerformanceSnapshot
    from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
    from watchlist_app.services import canonical_recalc
    from watchlist_app.services.shared_instrument_registry import get_shared_instrument

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Watermark fencing", "description": None},
    )
    assert client.post(
        f"/api/watchlists/{created_watchlist.json()['watchlist_id']}/items",
        json={"instrument_ids": ["sxv264"]},
    ).status_code == 200

    shared = get_shared_instrument("sxv264")
    assert shared is not None
    start_watermark = "2026-07-11T01:02:03.000001Z"
    end_watermark = "2026-07-11T01:02:03.000002Z"
    versions = [
        {**shared, "market_data_updated_at": start_watermark},
        {**shared, "market_data_updated_at": end_watermark},
    ]
    calls = 0

    def _changing_shared_instrument(_instrument_id: str):
        nonlocal calls
        result = versions[min(calls, len(versions) - 1)]
        calls += 1
        return result

    monkeypatch.setattr(canonical_recalc, "get_shared_instrument", _changing_shared_instrument)
    response = client.post(
        "/api/recalc/instruments/sxv264/execute",
        json={
            "job_type": "all",
            "trigger_type": "test",
            "trigger_ref_type": None,
            "trigger_ref_id": None,
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["source_watermark_at_start"] == start_watermark
    assert result["source_watermark_at_end"] == end_watermark
    assert result["source_changed_during_recalc"] is True

    session_factory = session_module.get_session_factory()
    with session_factory() as session:
        current_performance = session.query(PerformanceSnapshot).filter_by(
            instrument_id="sxv264",
            is_current=True,
        ).one()
        source_cutoff = current_performance.source_cutoff_at
        if source_cutoff.tzinfo is None:
            source_cutoff = source_cutoff.replace(tzinfo=UTC)
        assert source_cutoff == datetime.fromisoformat(start_watermark.replace("Z", "+00:00"))
        follow_ups = [
            job
            for job in SQLAlchemyRecalcJobRepository().list_recent(session)
            if job.instrument_id == "sxv264"
            and job.job_status == "queued"
            and job.trigger_type == "source_changed_during_recalc"
        ]
    assert len(follow_ups) == 1
    assert follow_ups[0].trigger_ref_id == end_watermark


def test_legacy_funds_summary_route_is_gone(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Removed Compat Alias", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    response = client.get("/api/funds/sxv264/summary")

    assert response.status_code == 404


def test_instruments_library_alias_lists_local_instruments(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Library Alias", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    response = client.get("/api/instruments/library")

    assert response.status_code == 200
    assert any(item["instrument_id"] == "sxv264" for item in response.json())


def test_execute_recalc_returns_404_for_unknown_asset(client: TestClient) -> None:
    response = client.post(
        "/api/recalc/instruments/not-in-watchlist/execute",
        json={
            "job_type": "all",
            "trigger_type": "instrument_registry_write",
            "trigger_ref_type": "instrument_nav_history_replace",
            "trigger_ref_id": "2026-04-16",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Instrument not found: not-in-watchlist"


def test_watchlist_rejects_archived_shared_instrument_ids(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Archived Guardrail", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    monkeypatch.setattr(
        watchlists_route,
        "get_shared_instrument",
        lambda instrument_id: {
            "instrument_id": instrument_id,
            "instrument_name": "Retired Asset",
            "instrument_type": "public_fund",
            "currency": "USD",
            "lifecycle_state": {"status": "archived"},
            "identifiers": [],
        },
    )

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-archived"]},
    )
    assert add_response.status_code == 404
    assert "backend maintenance" in add_response.json()["detail"]


def test_watchlist_add_returns_502_when_shared_registry_is_unreachable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route
    from watchlist_app.services.shared_instrument_registry import SharedInstrumentRegistryTransportError

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Registry Outage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    def _raise_registry_transport_error(instrument_id: str):
        raise SharedInstrumentRegistryTransportError("Failed to reach shared instrument registry.")

    monkeypatch.setattr(watchlists_route, "get_shared_instrument", _raise_registry_transport_error)

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 502
    assert "Failed to reach shared instrument registry" in add_response.json()["detail"]


def test_detail_resolution_fails_closed_when_shared_registry_returns_500(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.services import instrument_resolution
    from watchlist_app.services.shared_instrument_registry import SharedInstrumentRegistryHttpError

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Detail Fallback", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    def _raise_registry_error(instrument_id: str):
        raise SharedInstrumentRegistryHttpError(
            status_code=500,
            message="Shared instrument registry returned HTTP 500.",
        )

    monkeypatch.setattr(instrument_resolution, "get_shared_instrument", _raise_registry_error)

    response = client.post("/api/instruments/sxv264/resolve")
    assert response.status_code == 502
    assert response.json()["detail"] == "Shared instrument registry returned HTTP 500."


def test_detail_resolution_does_not_use_cached_row_when_registry_returns_500(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route
    from watchlist_app.services import instrument_resolution
    from watchlist_app.services.shared_instrument_registry import SharedInstrumentRegistryHttpError

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Stub Fallback", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    fund_record = {
        "instrument_id": "fund-msft-strategy",
        "instrument_name": "Microsoft Strategy Fund",
        "instrument_type": "public_fund",
        "currency": "USD",
        "lifecycle_state": {"status": "active"},
        "identifiers": [
            {
                "identifier_type": "ticker",
                "identifier_value": "MSFTX",
                "is_primary": True,
            }
        ],
    }

    monkeypatch.setattr(
        watchlists_route,
        "get_shared_instrument",
        lambda instrument_id: fund_record if instrument_id == "fund-msft-strategy" else None,
    )
    monkeypatch.setattr(
        instrument_resolution,
        "get_shared_instrument",
        lambda instrument_id: fund_record if instrument_id == "fund-msft-strategy" else None,
    )

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-msft-strategy"]},
    )
    assert add_response.status_code == 200

    def _raise_registry_error(instrument_id: str):
        raise SharedInstrumentRegistryHttpError(
            status_code=500,
            message="Shared instrument registry returned HTTP 500.",
        )

    monkeypatch.setattr(instrument_resolution, "get_shared_instrument", _raise_registry_error)

    response = client.post("/api/instruments/fund-msft-strategy/resolve")
    assert response.status_code == 502
    assert response.json()["detail"] == "Shared instrument registry returned HTTP 500."


def test_shared_registry_service_returns_none_for_missing_instrument(client: TestClient) -> None:
    from watchlist_app.services import shared_instrument_registry as registry

    del client
    assert registry.get_shared_instrument("stale-instrument") is None


def test_local_detail_support_is_explicit_by_instrument_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    assert watchlists_route._supports_local_detail({"instrument_type": "public_fund"}) is True
    assert watchlists_route._supports_local_detail({"instrument_type": "index"}) is True
    assert watchlists_route._supports_local_detail({"instrument_type": "equity"}) is True
    assert watchlists_route.local_detail_view_type("equity") == "equity"


def test_shared_registry_service_wraps_storage_errors_without_local_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.services import shared_instrument_registry as registry

    def _raise_transport_error(*_args, **_kwargs):
        raise registry.SharedInstrumentRegistryTransportError("Failed to reach shared instrument registry.")

    monkeypatch.setattr(registry.shared_store, "list_instruments", _raise_transport_error)
    monkeypatch.setattr(registry.shared_store, "find_instrument_by_identifier", _raise_transport_error)

    with pytest.raises(registry.SharedInstrumentRegistryTransportError):
        registry.list_shared_instruments()

    with pytest.raises(registry.SharedInstrumentRegistryTransportError):
        registry.resolve_shared_instrument(identifier_value="ARCH")


def test_default_all_public_funds_watchlist_syncs_active_shared_funds(
    client: TestClient,
) -> None:
    response = client.get("/api/watchlists")
    assert response.status_code == 200
    payload = response.json()
    assert [item["watchlist_id"] for item in payload] == [
        "all-instruments",
        "index",
        "all-public-funds",
        "all-private-funds",
    ]
    by_id = {item["watchlist_id"]: item for item in payload}
    public_funds = by_id["all-public-funds"]
    assert public_funds["name"] == "All 公募"
    assert public_funds["owner_type"] == "system"
    assert public_funds["is_default"] is True
    assert public_funds["is_shared"] is True
    assert public_funds["item_count"] == len(PUBLIC_FUND_IDS)
    assert by_id["all-private-funds"]["item_count"] == len(PRIVATE_FUND_IDS)
    assert by_id["index"]["item_count"] == 0
    assert "all-etfs" not in by_id

    detail = client.get("/api/watchlists/all-public-funds")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["item_count"] == len(PUBLIC_FUND_IDS)
    public_group_by_codes = [
        item["code"] for item in detail_payload["available_group_bys"]
    ]
    assert public_group_by_codes == [
        "none",
        "taxonomy",
        "currency",
        "attr.coverage_status",
    ]
    assert "instrument_type" not in public_group_by_codes
    overview_view = next(
        item for item in detail_payload["views"] if item["view_id"] == "overview"
    )
    assert overview_view["default_group_by"] == "none"
    assert "attr.current_drawdown" in overview_view["columns"]

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": "all-public-funds",
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    screener_payload = screener.json()
    assert screener_payload["total_rows"] == len(PUBLIC_FUND_IDS)
    assert {row["instrument_id"] for row in screener_payload["rows"]} == PUBLIC_FUND_IDS


def test_list_watchlists_syncs_each_system_list_by_instrument_type(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    original_list_shared_instruments = watchlists_route.list_shared_instruments
    requested_types: list[str] = []

    def track_list_shared_instruments(**kwargs):
        requested_types.append(str(kwargs["instrument_type"]))
        return original_list_shared_instruments(**kwargs)

    monkeypatch.setattr(
        watchlists_route,
        "list_shared_instruments",
        track_list_shared_instruments,
    )

    response = client.get("/api/watchlists")

    assert response.status_code == 200
    assert {
        item["watchlist_id"]: item["item_count"] for item in response.json()
    }["all-public-funds"] == len(PUBLIC_FUND_IDS)
    assert requested_types == ["None", "index", "public_fund", "private_fund"]


def test_system_watchlist_detail_syncs_only_its_instrument_type(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = client.get("/api/watchlists/all-public-funds")
    assert initial.status_code == 200
    assert initial.json()["item_count"] == len(PUBLIC_FUND_IDS)

    from watchlist_app.api.routes import watchlists as watchlists_route

    original_list_shared_instruments = watchlists_route.list_shared_instruments
    requested_types: list[str] = []

    def track_list_shared_instruments(**kwargs):
        requested_types.append(str(kwargs["instrument_type"]))
        return original_list_shared_instruments(**kwargs)

    monkeypatch.setattr(
        watchlists_route,
        "list_shared_instruments",
        track_list_shared_instruments,
    )

    response = client.get("/api/watchlists/all-public-funds")

    assert response.status_code == 200
    assert response.json()["item_count"] == len(PUBLIC_FUND_IDS)
    assert requested_types == ["public_fund"]


def test_default_all_public_funds_watchlist_resyncs_when_registry_grows(
    client: TestClient,
) -> None:
    initial = client.get("/api/watchlists")
    assert initial.status_code == 200
    assert {
        item["watchlist_id"]: item["item_count"] for item in initial.json()
    }["all-public-funds"] == len(PUBLIC_FUND_IDS)

    seed_shared_instrument(
        {
            **TEST_SHARED_INSTRUMENTS["savf63"],
            "instrument_id": "fund-new-income",
            "instrument_name": "New Income Fund",
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "identifier_value": "NEWINC",
                    "is_primary": True,
                },
            ],
        }
    )

    summary = client.get("/api/watchlists")
    assert summary.status_code == 200
    assert {
        item["watchlist_id"]: item["item_count"] for item in summary.json()
    }["all-public-funds"] == len(PUBLIC_FUND_IDS) + 1

    detail = client.get("/api/watchlists/all-public-funds")
    assert detail.status_code == 200
    assert detail.json()["item_count"] == len(PUBLIC_FUND_IDS) + 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": "all-public-funds",
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    assert "fund-new-income" in {row["instrument_id"] for row in screener.json()["rows"]}


def test_all_public_funds_reconcile_rolls_back_membership_when_materialization_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = client.get("/api/watchlists/all-public-funds")
    assert initial.status_code == 200
    assert initial.json()["item_count"] == len(PUBLIC_FUND_IDS)

    new_instrument_id = "fund-atomic-reconcile"
    seed_shared_instrument(
        {
            **TEST_SHARED_INSTRUMENTS["savf63"],
            "instrument_id": new_instrument_id,
            "instrument_name": "Atomic Reconcile Fund",
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "identifier_value": "ATOMIC",
                    "is_primary": True,
                },
            ],
        }
    )

    from watchlist_app.api.routes import watchlists as watchlists_route
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.services.shared_instrument_registry import (
        SharedInstrumentRegistryError,
    )

    original_materialize = watchlists_route._materialize_watchlist_rows

    def materialize_then_fail(*args, **kwargs) -> None:
        original_materialize(*args, **kwargs)
        raise SharedInstrumentRegistryError("injected materialization failure")

    monkeypatch.setattr(
        watchlists_route,
        "_materialize_watchlist_rows",
        materialize_then_fail,
    )

    failed_refresh = client.get("/api/watchlists/all-public-funds")
    assert failed_refresh.status_code == 200
    assert failed_refresh.json()["item_count"] == len(PUBLIC_FUND_IDS)

    with get_session_factory()() as session:
        record = watchlists_route.watchlist_repository.get(session, "all-public-funds")
        assert record is not None
        assert new_instrument_id not in {item.instrument_id for item in record.items}
        assert watchlists_route.instrument_repository.get(session, new_instrument_id) is None
        assert (
            watchlists_route.read_model_repository.get_watchlist_row(
                session,
                watchlist_id="all-public-funds",
                instrument_id=new_instrument_id,
            )
            is None
        )

    monkeypatch.setattr(
        watchlists_route,
        "_materialize_watchlist_rows",
        original_materialize,
    )
    recovered = client.get("/api/watchlists/all-public-funds")
    assert recovered.status_code == 200
    assert recovered.json()["item_count"] == len(PUBLIC_FUND_IDS) + 1


@pytest.mark.parametrize("reconcile_path", ["worker", "queued_recalc"])
def test_default_all_public_funds_watchlist_removes_archived_registry_members(
    client: TestClient,
    reconcile_path: str,
) -> None:
    initial = client.get("/api/watchlists/all-public-funds")
    assert initial.status_code == 200
    assert initial.json()["item_count"] == len(PUBLIC_FUND_IDS)

    from investment_studio_instrument_core import instrument_store as shared_store
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.db.models.instruments import InstrumentDetail
    from watchlist_app.db.models.read_models import InstrumentChartReadModel
    from watchlist_app.services.read_model_freshness import schedule_instrument_refreshes_if_stale

    original_nav = shared_store.get_instrument(get_session_factory(), "savf63")["market_data"]
    with get_session_factory()() as session:
        original_chart = session.execute(select(InstrumentChartReadModel.__table__).where(
            InstrumentChartReadModel.instrument_id == "savf63")).mappings().first()

    archived = shared_store.archive_instrument(
        get_session_factory(),
        instrument_id="savf63",
        updated_by="pytest",
    )
    assert archived is not None
    assert "savf63" not in shared_store.list_active_instrument_ids(
        get_session_factory(),
        instrument_types={"public_fund", "private_fund", "etf", "index"},
    )

    if reconcile_path == "worker":
        assert schedule_instrument_refreshes_if_stale(
            targets=[{"instrument_id": "savf63"}], trigger_ref_type="worker_reconcile",
            raise_on_error=True,
        ) == 0
    else:
        result = client.post("/api/recalc/instruments/savf63/execute", json={"job_type": "all"})
        assert result.status_code == 200
        assert result.json()["result"]["reason"] == "instrument_archived"

    with get_session_factory()() as session:
        assert session.get(InstrumentDetail, "savf63").is_active is False
        chart = session.execute(select(InstrumentChartReadModel.__table__).where(
            InstrumentChartReadModel.instrument_id == "savf63")).mappings().first()
        assert chart == original_chart
    assert shared_store.get_instrument(get_session_factory(), "savf63")["market_data"] == original_nav

    detail = client.get("/api/watchlists/all-public-funds")
    assert detail.status_code == 200
    assert detail.json()["item_count"] == len(PUBLIC_FUND_IDS) - 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": "all-public-funds",
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    assert "savf63" not in {row["instrument_id"] for row in screener.json()["rows"]}

    shared_store.restore_instrument(get_session_factory(), instrument_id="savf63", updated_by="pytest")
    restored = client.get("/api/watchlists/all-public-funds")
    assert restored.json()["item_count"] == len(PUBLIC_FUND_IDS)
    with get_session_factory()() as session:
        assert session.get(InstrumentDetail, "savf63").is_active is True


def test_default_index_watchlist_syncs_active_shared_indexes(
    client: TestClient,
) -> None:
    initial = client.get("/api/watchlists")
    assert initial.status_code == 200
    index = next(item for item in initial.json() if item["watchlist_id"] == "index")
    assert index["item_count"] == 0

    seed_shared_instrument(
        {
            "instrument_id": "index-csi-300",
            "instrument_name": "CSI 300 Index",
            "instrument_type": "index",
            "currency": "CNY",
            "quote_selection_policy": canonical_quote_policy("index"),
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "000300.SH", "is_primary": True},
            ],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-04-15",
                    "value": "3600.1200",
                    "currency": "CNY",
                    "price_unit": "per_unit",
                    "price_scale": "1",
                    "status": "complete",
                }
            ],
            "lifecycle_state": {"status": "active"},
        }
    )

    detail = client.get("/api/watchlists/index")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["item_count"] == 1
    assert detail_payload["instrument_types"] == ["index"]
    index_group_bys = [
        item["code"] for item in detail_payload["available_group_bys"]
    ]
    assert index_group_bys == [
        "none",
        "taxonomy",
        "currency",
        "attr.coverage_status",
    ]

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": "index",
            "view_id": "overview",
            "selected_fields": ["instrument_name", "instrument_type"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    screener_payload = screener.json()
    index_row = next(row for row in screener_payload["rows"] if row["instrument_id"] == "index-csi-300")
    assert index_row["instrument_type"] == "index"

    screening_screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": "index",
            "view_id": "classification",
            "selected_fields": ["instrument_name", "instrument_type"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screening_screener.status_code == 200
    assert "index-csi-300" in {row["instrument_id"] for row in screening_screener.json()["rows"]}


def test_default_all_public_funds_watchlist_cannot_be_reduced_manually(
    client: TestClient,
) -> None:
    list_response = client.get("/api/watchlists")
    assert list_response.status_code == 200

    delete_items = client.post(
        "/api/watchlists/all-public-funds/items/delete",
        json={"instrument_ids": ["savf63"]},
    )
    assert delete_items.status_code == 400
    assert "system-maintained" in delete_items.json()["detail"]

    target = client.post(
        "/api/watchlists",
        json={"name": "Copy Target", "description": None},
    )
    target_watchlist_id = target.json()["watchlist_id"]
    move_items = client.post(
        "/api/watchlists/all-public-funds/items/move",
        json={"instrument_ids": ["savf63"], "target_watchlist_id": target_watchlist_id},
    )
    assert move_items.status_code == 400
    assert "system-maintained" in move_items.json()["detail"]

    copy_items = client.post(
        "/api/watchlists/all-public-funds/items/copy",
        json={"instrument_ids": ["savf63"], "target_watchlist_id": target_watchlist_id},
    )
    assert copy_items.status_code == 200
    assert copy_items.json()["added_count"] == 1

    delete_watchlist = client.delete("/api/watchlists/all-public-funds")
    assert delete_watchlist.status_code == 400
    assert "system-maintained" in delete_watchlist.json()["detail"]


def test_duplicate_custom_view_name_returns_409(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "View Collisions", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    payload = {
        "name": "My View",
        "description": None,
        "default_group_by": "none",
        "default_sort": [],
        "default_filters": {},
        "default_advanced_filters": None,
        "columns": [
            {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
        ],
    }

    first = client.post(f"/api/watchlists/{watchlist_id}/views", json=payload)
    assert first.status_code == 200

    duplicate = client.post(f"/api/watchlists/{watchlist_id}/views", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Watchlist view name already exists"


def test_saved_view_create_rejects_unknown_field_references(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Strict Saved View", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    response = client.post(
        f"/api/watchlists/{watchlist_id}/views",
        json={
            "name": "Legacy Filter",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {"asset_type": ["fund"]},
            "default_advanced_filters": None,
            "columns": [
                {
                    "field_key": "instrument_name",
                    "display_order": 1,
                    "width": 320,
                    "is_visible": True,
                }
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown filter field 'asset_type'."


def test_saved_view_update_rejects_unknown_columns(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Strict View Update", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    payload = {
        "name": "Current View",
        "description": None,
        "default_group_by": "none",
        "default_sort": [],
        "default_filters": {},
        "default_advanced_filters": None,
        "columns": [
            {
                "field_key": "instrument_name",
                "display_order": 1,
                "width": 320,
                "is_visible": True,
            }
        ],
    }
    created = client.post(f"/api/watchlists/{watchlist_id}/views", json=payload)
    assert created.status_code == 200

    response = client.put(
        f"/api/watchlists/{watchlist_id}/views/{created.json()['view_id']}",
        json={
            **payload,
            "columns": [
                {
                    "field_key": "instrument_class",
                    "display_order": 1,
                    "width": 160,
                    "is_visible": True,
                }
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown selected field 'instrument_class'."


def test_custom_view_ids_are_slugged_to_path_safe_values(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Path Safe Views", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    payload = {
        "name": "A/B",
        "description": None,
        "default_group_by": "none",
        "default_sort": [],
        "default_filters": {},
        "default_advanced_filters": None,
        "columns": [
            {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
        ],
    }

    created = client.post(f"/api/watchlists/{watchlist_id}/views", json=payload)
    assert created.status_code == 200
    assert created.json()["view_id"] == "a-b"

    updated = client.put(
        f"/api/watchlists/{watchlist_id}/views/{created.json()['view_id']}",
        json={
            **payload,
            "name": "A/B Updated",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "A/B Updated"


def test_copy_watchlist_sanitizes_legacy_custom_view_ids(client: TestClient) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.db.models.watchlists import WatchlistView, WatchlistViewColumn

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Legacy View Copy", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    legacy_view_id = f"{watchlist_id}::a/b"
    with session_module.get_session_factory()() as session:
        session.add(
            WatchlistView(
                watchlist_view_id=legacy_view_id,
                watchlist_id=watchlist_id,
                name="A/B",
                description=None,
                kind="custom",
                default_sort_json=[],
                default_filters_json={},
                default_advanced_filter_json={},
                default_group_by="none",
                density="standard",
                is_default=False,
                created_at=datetime.now(UTC).replace(microsecond=0),
            )
        )
        session.add(
            WatchlistViewColumn(
                watchlist_view_id=legacy_view_id,
                field_key="instrument_name",
                display_order=1,
                width=320,
                is_visible=True,
                pin_side=None,
            )
        )
        session.commit()

    copied = client.post(f"/api/watchlists/{watchlist_id}/copy")
    assert copied.status_code == 200
    copied_watchlist_id = copied.json()["watchlist_id"]

    copied_detail = client.get(f"/api/watchlists/{copied_watchlist_id}")
    assert copied_detail.status_code == 200
    copied_view = next(
        item for item in copied_detail.json()["views"] if item["name"] == "A/B"
    )
    assert copied_view["view_id"] == "a-b"

    updated = client.put(
        f"/api/watchlists/{copied_watchlist_id}/views/{copied_view['view_id']}",
        json={
            "name": "A/B Updated",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {},
            "default_advanced_filters": None,
            "columns": [
                {
                    "field_key": "instrument_name",
                    "display_order": 1,
                    "width": 320,
                    "is_visible": True,
                }
            ],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "A/B Updated"


def test_default_view_remains_overview_after_creating_custom_view(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Stable Default", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    created_view = client.post(
        f"/api/watchlists/{watchlist_id}/views",
        json={
            "name": "A Custom View",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {},
            "default_advanced_filters": None,
            "columns": [
                {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
            ],
        },
    )
    assert created_view.status_code == 200

    detail = client.get(f"/api/watchlists/{watchlist_id}")
    assert detail.status_code == 200
    assert detail.json()["default_view_id"] == "overview"


def test_create_watchlist_retries_when_slug_conflicts_during_insert(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    original_create = watchlists_route.watchlist_repository.create
    generated_ids = iter(["retry-list", "retry-list-2"])
    attempted_ids: list[str] = []
    failed_once = False

    def _generate_retry_id(_session, _name: str) -> str:
        return next(generated_ids)

    def _create(*args, **kwargs):
        nonlocal failed_once
        attempted_ids.append(kwargs["watchlist_id"])
        if not failed_once:
            failed_once = True
            raise IntegrityError("insert", {}, Exception("duplicate key value violates unique constraint"))
        return original_create(*args, **kwargs)

    monkeypatch.setattr(watchlists_route, "_generate_watchlist_id", _generate_retry_id)
    monkeypatch.setattr(watchlists_route.watchlist_repository, "create", _create)

    response = client.post(
        "/api/watchlists",
        json={"name": "Retry List", "description": None},
    )
    assert response.status_code == 200
    assert response.json()["watchlist_id"] == "retry-list-2"
    assert attempted_ids == ["retry-list", "retry-list-2"]


def test_copy_watchlist_retries_when_slug_conflicts_during_insert(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Retry Source", "description": None},
    )
    source_watchlist_id = created_watchlist.json()["watchlist_id"]

    original_duplicate = watchlists_route.watchlist_repository.duplicate
    generated_ids = iter(["retry-source-copy", "retry-source-copy-2"])
    attempted_ids: list[str] = []
    failed_once = False

    def _generate_retry_id(_session, _name: str) -> str:
        return next(generated_ids)

    def _duplicate(*args, **kwargs):
        nonlocal failed_once
        attempted_ids.append(kwargs["watchlist_id"])
        if not failed_once:
            failed_once = True
            raise IntegrityError("insert", {}, Exception("duplicate key value violates unique constraint"))
        return original_duplicate(*args, **kwargs)

    monkeypatch.setattr(watchlists_route, "_generate_watchlist_id", _generate_retry_id)
    monkeypatch.setattr(watchlists_route.watchlist_repository, "duplicate", _duplicate)

    response = client.post(f"/api/watchlists/{source_watchlist_id}/copy")
    assert response.status_code == 200
    assert response.json()["watchlist_id"] == "retry-source-copy-2"
    assert attempted_ids == ["retry-source-copy", "retry-source-copy-2"]


def test_create_watchlist_view_retries_when_slug_conflicts_during_insert(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Retry View Parent", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    original_create_view = watchlists_route.watchlist_repository.create_view
    generated_ids = iter(["retry-view", "retry-view-2"])
    attempted_ids: list[str] = []
    failed_once = False

    def _generate_retry_id(_session, **_kwargs) -> str:
        return next(generated_ids)

    def _create_view(*args, **kwargs):
        nonlocal failed_once
        attempted_ids.append(kwargs["view_id"])
        if not failed_once:
            failed_once = True
            raise IntegrityError(
                "insert",
                {},
                Exception("duplicate key value violates unique constraint"),
            )
        return original_create_view(*args, **kwargs)

    monkeypatch.setattr(
        watchlists_route,
        "_generate_watchlist_view_id",
        _generate_retry_id,
    )
    monkeypatch.setattr(watchlists_route.watchlist_repository, "create_view", _create_view)

    response = client.post(
        f"/api/watchlists/{watchlist_id}/views",
        json={
            "name": "Retry View",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {},
            "default_advanced_filters": None,
            "columns": [
                {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["view_id"] == "retry-view-2"
    assert attempted_ids == ["retry-view", "retry-view-2"]


def test_screener_filters_match_multi_select_attribute_values(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Attribute Filters", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    definition_response = client.post(
        "/api/instrument-attributes/definitions",
        json={
            "attribute_key": "strategy_tags",
            "label": "Strategy Tags",
            "description": "Test multi-select attribute",
            "data_type": "multi_select",
            "domain_code": "research",
            "group_code": "custom",
            "options": ["市场中性", "套利", "CTA"],
            "is_groupable": True,
            "is_filterable": True,
            "is_view_column": True,
            "default_visible": False,
        },
    )
    assert definition_response.status_code == 200

    value_response = client.post(
        "/api/instrument-attributes/instruments/sxv264",
        json={
            "values": [
                {
                    "attribute_key": "strategy_tags",
                    "value": ["市场中性", "套利"],
                }
            ]
        },
    )
    assert value_response.status_code == 200

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "attr.strategy_tags"],
            "filters": {"attr.strategy_tags": ["市场中性"]},
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    payload = screener.json()
    assert payload["total_rows"] == 1
    assert payload["rows"][0]["attr.strategy_tags"] == ["市场中性", "套利"]


def test_explicit_empty_filters_override_view_defaults(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Explicit Filters", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    first_add = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-us-agg"]},
    )
    assert first_add.status_code == 200

    second_add = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert second_add.status_code == 200

    view_response = client.post(
        f"/api/watchlists/{watchlist_id}/views",
        json={
            "name": "Filtered View",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {"instrument_name": ["iShares Core U.S. Aggregate Bond ETF"]},
            "default_advanced_filters": None,
            "columns": [
                {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
            ],
        },
    )
    assert view_response.status_code == 200
    view_id = view_response.json()["view_id"]

    filtered = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": view_id,
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert filtered.status_code == 200
    assert filtered.json()["total_rows"] == 1

    unfiltered = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": view_id,
            "selected_fields": ["instrument_name"],
            "filters": {},
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert unfiltered.status_code == 200
    assert unfiltered.json()["total_rows"] == 2


def test_legacy_catalog_module_is_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("watchlist_app.domain.catalog")


def test_watchlist_api_runs_without_legacy_catalog_module(
    client: TestClient,
) -> None:

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Catalog Independence", "description": None},
    )
    assert created_watchlist.status_code == 200
    watchlist_id = created_watchlist.json()["watchlist_id"]

    detail = client.get(f"/api/watchlists/{watchlist_id}")
    assert detail.status_code == 200
    assert detail.json()["watchlist_id"] == watchlist_id

    created_view = client.post(
        f"/api/watchlists/{watchlist_id}/views",
        json={
            "name": "Independent View",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {},
            "default_advanced_filters": None,
            "columns": [
                {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
            ],
        },
    )
    assert created_view.status_code == 200

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["accepted_count"] == 1

    screener = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": ["instrument_name", "ticker_or_isin"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener.status_code == 200
    assert screener.json()["total_rows"] == 1


def test_watchlist_child_routes_return_404_for_missing_watchlists(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import watchlists as watchlists_route

    def _unexpected_registry_lookup(*args, **kwargs):
        raise AssertionError("shared registry lookup should not run for missing watchlists")

    monkeypatch.setattr(watchlists_route, "get_shared_instrument", _unexpected_registry_lookup)

    add_response = client.post(
        "/api/watchlists/missing/items",
        json={"instrument_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 404
    assert add_response.json()["detail"] == "Watchlist not found"

    delete_items_response = client.post(
        "/api/watchlists/missing/items/delete",
        json={"instrument_ids": ["fund-us-agg"]},
    )
    assert delete_items_response.status_code == 404
    assert delete_items_response.json()["detail"] == "Watchlist not found"

    list_views_response = client.get("/api/watchlists/missing/views")
    assert list_views_response.status_code == 404
    assert list_views_response.json()["detail"] == "Watchlist not found"

    create_view_response = client.post(
        "/api/watchlists/missing/views",
        json={
            "name": "Ghost View",
            "description": None,
            "default_group_by": "none",
            "default_sort": [],
            "default_filters": {},
            "default_advanced_filters": None,
            "columns": [
                {"field_key": "instrument_name", "display_order": 1, "width": 320, "is_visible": True}
            ],
        },
    )
    assert create_view_response.status_code == 404
    assert create_view_response.json()["detail"] == "Watchlist not found"


def test_screener_returns_404_for_missing_watchlists_and_views(client: TestClient) -> None:
    missing_watchlist = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": "missing",
            "view_id": "overview",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert missing_watchlist.status_code == 404
    assert missing_watchlist.json()["detail"] == "Watchlist not found"

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Missing View Guard", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    missing_view = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "not-a-view",
            "selected_fields": ["instrument_name"],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert missing_view.status_code == 404
    assert missing_view.json()["detail"] == "Watchlist view not found"


def test_delete_watchlist_removes_watchlist_read_model_rows(client: TestClient) -> None:
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Delete Cleanup", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 200

    delete_response = client.delete(f"/api/watchlists/{watchlist_id}")
    assert delete_response.status_code == 200

    session = get_session_factory()()
    try:
        repository = SQLAlchemyReadModelRepository()
        assert repository.list_watchlist_rows(session, watchlist_id) == []
    finally:
        session.close()


def test_instrument_attribute_routes_return_404_for_missing_instruments(client: TestClient) -> None:
    get_response = client.get("/api/instrument-attributes/instruments/missing-instrument")
    assert get_response.status_code == 404
    assert get_response.json()["detail"] == "Instrument not found"

    post_response = client.post(
        "/api/instrument-attributes/instruments/missing-instrument",
        json={
            "values": [
                {
                    "attribute_key": "coverage_status",
                    "value": "Watch",
                }
            ]
        },
    )
    assert post_response.status_code == 404
    assert post_response.json()["detail"] == "Instrument not found"


def test_seeded_private_fund_watchlist_tags_are_available(client: TestClient) -> None:
    definitions_response = client.get("/api/instrument-attributes/definitions")
    assert definitions_response.status_code == 200
    definitions = definitions_response.json()
    definitions_by_key = {item["attribute_key"]: item for item in definitions}

    expected_keys = {
        "fund_vehicle",
        "implementation_style",
        "trading_universe",
        "alpha_source",
        "research_evidence_level",
        "investment_edge_quality",
        "process_repeatability",
        "decision_discipline",
        "style_profile",
        "style_drift_risk",
        "manager_assessment",
        "team_stability_assessment",
        "portfolio_construction",
        "risk_management_quality",
        "capacity_bucket",
        "liquidity_terms_fit",
        "fee_value_assessment",
        "alignment_quality",
        "historical_delivery",
        "portfolio_role",
        "equity_correlation_bucket",
        "preferred_regime",
        "weak_regime",
        "transparency_quality",
    }
    assert expected_keys.issubset(definitions_by_key.keys())
    assert {
        "volatility_bucket",
        "drawdown_control",
        "style_stability",
    }.isdisjoint(definitions_by_key)
    assert definitions_by_key["fund_vehicle"]["domain_code"] == "overview"
    assert definitions_by_key["fund_vehicle"]["required_for_monitoring"] is True
    assert definitions_by_key["coverage_status"]["label"] == "Investment Status"
    assert definitions_by_key["coverage_status"]["options"] == [
        "Watch",
        "Proposed",
        "Invested",
        "Paused",
        "Exited",
    ]
    assert definitions_by_key["investment_edge_quality"]["group_code"] == "research_edge"
    assert definitions_by_key["process_repeatability"]["options"] == [
        "可重复",
        "部分可重复",
        "关键人驱动",
        "不透明",
    ]
    assert definitions_by_key["style_profile"]["group_code"] == "research_style"
    assert definitions_by_key["risk_management_quality"]["group_code"] == "research_risk"
    assert definitions_by_key["preferred_regime"]["data_type"] == "multi_select"

    field_registry_response = client.get("/api/field-registry")
    assert field_registry_response.status_code == 200
    fields = field_registry_response.json()["fields"]
    categories = {
        item["category_code"]: item["label"]
        for item in field_registry_response.json()["categories"]
    }
    fields_by_key = {item["field_key"]: item for item in fields}

    assert categories["instrument_taxonomy"] == "Instrument Taxonomy"
    assert categories["research_framework"] == "Investment Research"
    assert "attr.instrument_taxonomy_level_1" in fields_by_key
    assert fields_by_key["attr.instrument_taxonomy_level_1"]["filter_mode"] == "multi_select"
    assert "attr.instrument_taxonomy_level_2" in fields_by_key
    assert fields_by_key["attr.instrument_taxonomy_level_2"]["filter_mode"] == "multi_select"
    assert fields_by_key["attr.instrument_taxonomy_level_2"]["group_mode"] == "none"
    assert (
        fields_by_key["attr.instrument_taxonomy_level_2"]["category_code"]
        == "instrument_taxonomy"
    )
    assert fields_by_key["attr.coverage_status"]["product_scope_json"] == []
    assert fields_by_key["attr.coverage_status"]["label"] == "Investment Status"
    assert fields_by_key["attr.coverage_status"]["group_mode"] == "discrete"
    assert fields_by_key["currency"]["label"] == "Currency"
    assert fields_by_key["currency"]["source_metric_code"] == "instrument.currency"
    assert fields_by_key["currency"]["group_mode"] == "discrete"
    assert fields_by_key["attr.manual_rating"]["label"] == "Research Rating"
    assert fields_by_key["attr.manual_rating"]["group_mode"] == "discrete"
    assert fields_by_key["attr.investment_edge_quality"]["category_code"] == "research_framework"
    assert fields_by_key["attr.investment_edge_quality"]["group_mode"] == "none"
    assert fields_by_key["attr.investment_edge_quality"]["instrument_scope_json"] == [
        "public_fund",
        "private_fund",
    ]
    assert fields_by_key["latest_quote"]["instrument_scope_json"] == []
    assert fields_by_key["latest_quote"]["label"] == "Latest Value"
    assert fields_by_key["latest_quote"]["source_metric_code"] == (
        "instrument_chart_read_model.latest_values.valuation.value"
    )
    assert fields_by_key["latest_quote_date"]["data_type"] == "date"
    assert fields_by_key["latest_cumulative_nav"]["label"] == "Cumulative NAV"
    assert fields_by_key["latest_cumulative_nav"]["instrument_scope_json"] == []
    assert fields_by_key["latest_cumulative_nav_date"]["data_type"] == "date"
    assert fields_by_key["return_chart_1m"]["label"] == "Return 1M"
    assert "one-month boundary" in fields_by_key["return_chart_1m"]["description"]
    assert "price_chart_1m" not in fields_by_key
    assert fields_by_key["return_1w"]["label"] == "1W Return"
    assert fields_by_key["return_mtd"]["label"] == "MTD"
    assert fields_by_key["return_ytd"]["label"] == "YTD"
    assert fields_by_key["return_1m"]["label"] == "1M"
    assert fields_by_key["return_3m"]["label"] == "3M"
    assert fields_by_key["return_6m"]["label"] == "6M"
    assert fields_by_key["return_1y"]["label"] == "1Y"
    assert fields_by_key["annualized_return"]["label"] == "Ann."
    assert fields_by_key["return_3y"]["label"] == "3Y"
    assert fields_by_key["return_5y"]["label"] == "5Y"
    assert fields_by_key["max_drawdown"]["label"] == "Max DD"
    assert fields_by_key["attr.current_drawdown"]["label"] == "Current DD"
    assert fields_by_key["attr.peer_return_1w_percentile"]["label"] == "1W Return Pctl"
    assert fields_by_key["attr.peer_annualized_return_percentile"]["label"] == "Ann. Pctl"


def test_watchlist_menu_catalog_is_reviewed_and_keeps_detail_fields_queryable(client: TestClient) -> None:
    from watchlist_app.services.watchlist_query_contract import (
        WATCHLIST_COLUMN_FIELD_KEYS,
        WATCHLIST_FILTER_FIELD_KEYS,
    )

    response = client.get("/api/field-registry")
    assert response.status_code == 200
    payload = response.json()
    assert payload["column_field_keys"] == list(WATCHLIST_COLUMN_FIELD_KEYS)
    assert len(payload["column_field_keys"]) == 18
    assert payload["filter_field_keys"] == list(WATCHLIST_FILTER_FIELD_KEYS)
    assert len(payload["filter_field_keys"]) == 5
    assert set(payload["filter_field_keys"]).issubset(payload["column_field_keys"])
    fields = {item["field_key"] for item in payload["fields"]}
    detail_fields = {
        "attr.style_profile", "attr.research_current_view", "attr.research_note_count",
        "attr.manual_rating", "attr.peer_return_1y_percentile", "avg_credit_rating",
        "latest_cumulative_nav", "return_chart_1d", "attr.instrument_taxonomy_leaf",
        "return_6m", "return_mtd", "return_3y", "return_5y", "annualized_return",
        "max_drawdown", "volatility", "sharpe_ratio", "return_chart_1y",
    }
    assert detail_fields.issubset(fields)
    assert detail_fields.isdisjoint(payload["column_field_keys"])

    narrowed = client.get("/api/field-registry", params={"search": "return_1m"})
    assert narrowed.status_code == 200
    narrowed_payload = narrowed.json()
    assert narrowed_payload["column_field_keys"] == ["return_1m"]
    assert narrowed_payload["filter_field_keys"] == []


def test_risk_only_watchlist_query_does_not_read_legacy_research(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.db.models.workbench import RiskCase
    from watchlist_app.db.session import get_session_factory
    from watchlist_app.repositories.sqlalchemy.research import SQLAlchemyInstrumentResearchRepository

    created = client.post("/api/watchlists", json={"name": "Risk Projection"})
    assert created.status_code == 200
    watchlist_id = created.json()["watchlist_id"]
    added = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264", "fund-us-agg"]},
    )
    assert added.status_code == 200
    with get_session_factory()() as session:
        session.add(RiskCase(
            case_id="risk-only-projection", instrument_id="sxv264", signal="manual",
            title="需关注", body="已确认的风险事项", severity="attention", status="open",
            trigger_active=True, evidence_json={"direction": "risk"}, history_json=[],
        ))
        session.commit()

    def unexpected_research_read(*args, **kwargs):
        raise AssertionError("Risk-only queries must not read legacy research tables")

    monkeypatch.setattr(SQLAlchemyInstrumentResearchRepository, "list_profiles", unexpected_research_read)
    monkeypatch.setattr(SQLAlchemyInstrumentResearchRepository, "list_notes_for_instruments", unexpected_research_read)
    response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "selected_fields": ["instrument_name", "attr.risk_attention"],
            "group_by": "none",
        },
    )
    assert response.status_code == 200
    assert {
        row["instrument_id"]: row["attr.risk_attention"]
        for row in response.json()["rows"]
    } == {"sxv264": "attention", "fund-us-agg": "limited"}


def test_universal_watchlist_grouping_contract_is_exposed_and_executable(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Attribute Grouping", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-us-agg", "sxv264"]},
    )
    assert add_response.status_code == 200

    update_response = client.post(
        "/api/instrument-attributes/instruments/sxv264",
        json={"values": [{"attribute_key": "coverage_status", "value": "Invested"}]},
    )
    assert update_response.status_code == 200

    detail_response = client.get(f"/api/watchlists/{watchlist_id}")
    assert detail_response.status_code == 200
    group_by_codes = [item["code"] for item in detail_response.json()["available_group_bys"]]
    assert group_by_codes == [
        "none",
        "taxonomy",
        "currency",
        "attr.coverage_status",
    ]

    screener_response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "selected_fields": ["instrument_name", "attr.coverage_status"],
            "group_by": "attr.coverage_status",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener_response.status_code == 200
    assert {
        item["group_value"] for item in screener_response.json()["groups"]
    } == {"Invested", "Unspecified"}

    currency_response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "selected_fields": ["instrument_name", "currency"],
            "group_by": "currency",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert currency_response.status_code == 200
    assert {row["currency"] for row in currency_response.json()["rows"]} == {"USD"}
    assert currency_response.json()["groups"] == [
        {"group_value": "USD", "row_count": 2}
    ]

    removed_group_response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "selected_fields": ["instrument_name", "attr.thesis_status"],
            "group_by": "attr.thesis_status",
        },
    )
    assert removed_group_response.status_code == 422
    assert "not available" in removed_group_response.json()["detail"].lower()

    edge_update = client.post(
        "/api/instrument-attributes/instruments/sxv264",
        json={"values": [{"attribute_key": "investment_edge_quality", "value": "清晰且可持续"}]},
    )
    assert edge_update.status_code == 200

    fund_field = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "selected_fields": ["instrument_name", "attr.investment_edge_quality"],
            "group_by": "none",
        },
    )
    assert fund_field.status_code == 200
    assert {
        row["instrument_id"]: row["attr.investment_edge_quality"]
        for row in fund_field.json()["rows"]
    } == {"sxv264": "清晰且可持续", "fund-us-agg": None}

    filtered_fund_field = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "selected_fields": ["instrument_name", "attr.investment_edge_quality"],
            "filters": {"attr.investment_edge_quality": ["清晰且可持续"]},
            "sort": [{"field": "attr.investment_edge_quality", "direction": "asc"}],
            "group_by": "none",
        },
    )
    assert filtered_fund_field.status_code == 200
    assert [row["instrument_id"] for row in filtered_fund_field.json()["rows"]] == ["sxv264"]


def test_adding_funds_does_not_inject_product_framework_values(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Clean Product Framework", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-us-agg", "sxv264"]},
    )
    assert add_response.status_code == 200

    public_response = client.get("/api/instrument-attributes/instruments/fund-us-agg")
    assert public_response.status_code == 200
    public_payload = public_response.json()
    public_values = public_payload["values"]
    assert "fund_vehicle" not in public_values
    assert "alpha_source" not in public_values
    assert public_payload["taxonomy"]["assigned_node_id"] is None
    etf_definition_keys = {
        item["attribute_key"] for item in public_payload["definitions"]
    }
    assert "fund_vehicle" not in etf_definition_keys
    assert "primary_geographic_exposure" in etf_definition_keys
    assert "etf_index_fit" in etf_definition_keys
    assert "equity_business_quality" not in etf_definition_keys
    assert "index_methodology_quality" not in etf_definition_keys

    private_response = client.get("/api/instrument-attributes/instruments/sxv264")
    assert private_response.status_code == 200
    private_payload = private_response.json()
    private_values = private_payload["values"]
    assert "fund_vehicle" not in private_values
    assert "alpha_source" not in private_values
    assert private_payload["taxonomy"]["assigned_node_id"] is None
    private_definition_keys = {
        item["attribute_key"] for item in private_payload["definitions"]
    }
    assert "fund_vehicle" in private_definition_keys
    assert "primary_geographic_exposure" in private_definition_keys
    assert "equity_business_quality" not in private_definition_keys
    assert "etf_index_fit" not in private_definition_keys
    assert "index_methodology_quality" not in private_definition_keys


def test_fund_only_detail_sections_reject_etfs(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "ETF Detail Boundary", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["fund-us-agg"]},
    )
    assert add_response.status_code == 200

    for section in ("people", "strategy", "price", "nav-settings"):
        response = client.get(f"/api/instruments/fund-us-agg/{section}")
        assert response.status_code == 422
        assert "does not apply to etf instruments" in response.json()["detail"]

    assert client.get("/api/instruments/fund-us-agg/documents").status_code == 200
    assert client.get("/api/instruments/fund-us-agg/exposure/summary").status_code == 200


def test_instrument_research_profile_and_notes_are_first_class_records(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Research Profile", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264", "fund-us-agg"]},
    )
    assert add_response.status_code == 200

    default_response = client.get("/api/instruments/sxv264/research")
    assert default_response.status_code == 200
    default_payload = default_response.json()
    assert default_payload["profile"]["manual_rating"] is None
    assert default_payload["profile"]["thesis"] == ""
    assert default_payload["profile"]["disconfirming_evidence"] == ""
    assert default_payload["notes"] == []

    research_profile = {
        "thesis": "Repeatable security selection with controlled capacity.",
        "current_view": "Constructive",
        "people_assessment": "The CIO is candid about drawdowns.",
        "disconfirming_evidence": "Style drift or unexplained leverage.",
        "monitoring_plan": "Review monthly exposure and manager letters.",
        "dd_status": "In progress",
        "odd_status": "Pending",
        "ic_status": "Not scheduled",
        "primary_analyst": "Researcher A",
        "next_review_date": "2026-05-15",
        "manual_rating": 4,
    }
    upsert_response = client.put(
        "/api/instruments/sxv264/research",
        json={
            "profile": research_profile,
            "updated_by": "test",
        },
    )
    assert upsert_response.status_code == 200
    payload = upsert_response.json()
    assert payload["profile"]["manual_rating"] == 4
    assert payload["profile"]["thesis"].startswith("Repeatable")
    assert payload["profile"]["next_review_date"] == "2026-05-15"
    assert payload["profile"]["updated_by"] == "pm-one"
    assert payload["profile"]["revision_number"] == 1

    no_op_profile_response = client.put(
        "/api/instruments/sxv264/research",
        json={"profile": research_profile, "updated_by": "test-noop"},
    )
    assert no_op_profile_response.status_code == 200
    assert no_op_profile_response.json()["profile"]["revision_number"] == 1
    assert no_op_profile_response.json()["profile"]["updated_by"] == "pm-one"

    revised_profile_response = client.put(
        "/api/instruments/sxv264/research",
        json={
            "profile": {**research_profile, "current_view": "Cautious"},
            "updated_by": "reviewer",
        },
    )
    assert revised_profile_response.status_code == 200
    assert revised_profile_response.json()["profile"]["revision_number"] == 2
    profile_history = client.get("/api/instruments/sxv264/research/history")
    assert profile_history.status_code == 200
    assert [
        revision["revision_number"]
        for revision in profile_history.json()["profile_revisions"]
    ] == [2, 1]
    assert [
        revision["current_view"]
        for revision in profile_history.json()["profile_revisions"]
    ] == ["Cautious", "Constructive"]

    invalid_rating = client.put(
        "/api/instruments/sxv264/research",
        json={"profile": {"manual_rating": 9}},
    )
    assert invalid_rating.status_code == 422

    empty_title = client.post(
        "/api/instruments/sxv264/research/notes",
        json={
            "note": {
                "note_date": "2026-04-30",
                "title": "   ",
            }
        },
    )
    assert empty_title.status_code == 422

    research_note = {
        "note_date": "2026-04-30",
        "note_type": "meeting",
        "title": "Manager call",
        "summary": "Capacity now needs review.",
        "body": "The PM described a tighter soft-close threshold.",
        "importance": "high",
        "tags": ["capacity", "manager"],
        "source_refs": "Manager call notes, 2026-04-30",
        "people": "CIO, COO",
        "author": "Researcher A",
        "follow_up_date": "2026-05-15",
    }
    note_response = client.post(
        "/api/instruments/sxv264/research/notes",
        json={
            "note": research_note,
            "updated_by": "test",
        },
    )
    assert note_response.status_code == 200
    note_payload = note_response.json()
    assert len(note_payload["notes"]) == 1
    note = note_payload["notes"][0]
    assert note["note_type"] == "meeting"
    from watchlist_app.services.research_identity import research_identity
    actor = research_identity()
    assert note["author"] == actor["display_name"]
    assert note["author_user_id"] == actor["user_id"]
    assert note["people"] == "CIO, COO"
    assert note["source_refs"].startswith("Manager call")
    assert note["created_at"]
    assert note["updated_at"]

    note_id = note["note_id"]
    no_op_note_response = client.put(
        f"/api/instruments/sxv264/research/notes/{note_id}",
        json={"note": research_note, "updated_by": "test-noop"},
    )
    assert no_op_note_response.status_code == 200
    assert no_op_note_response.json()["notes"][0]["revision_number"] == 1
    assert no_op_note_response.json()["notes"][0]["updated_by"] == actor["user_id"]

    screener_response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "overview",
            "selected_fields": [
                "instrument_name",
                "attr.research_current_view",
                "attr.manual_rating",
                "attr.primary_analyst",
                "attr.research_next_review_date",
                "attr.research_note_count",
                "attr.research_next_follow_up_date",
                "attr.research_updated_at",
            ],
            "sort": [],
            "group_by": "none",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener_response.status_code == 200
    research_row = next(
        row
        for row in screener_response.json()["rows"]
        if row["instrument_id"] == "sxv264"
    )
    assert research_row["attr.research_current_view"] == "Cautious"
    assert research_row["attr.manual_rating"] == 4
    assert research_row["attr.primary_analyst"] == "Researcher A"
    assert research_row["attr.research_next_review_date"] == "2026-05-15"
    assert research_row["attr.research_note_count"] == 1
    assert research_row["attr.research_next_follow_up_date"] == "2026-05-15"
    assert research_row["attr.research_updated_at"]
    assert screener_response.json()["groups"] == []

    update_note_response = client.put(
        f"/api/instruments/sxv264/research/notes/{note_id}",
        json={
            "note": {
                "note_date": "2026-05-01",
                "note_type": "review",
                "title": "Capacity follow-up",
                "summary": "Soft-close threshold confirmed.",
            },
            "updated_by": "reviewer",
        },
    )
    assert update_note_response.status_code == 200
    assert update_note_response.json()["notes"][0]["note_type"] == "review"
    assert update_note_response.json()["notes"][0]["updated_by"] == actor["user_id"]
    assert update_note_response.json()["notes"][0]["revision_number"] == 2

    delete_note_response = client.delete(
        f"/api/instruments/sxv264/research/notes/{note_id}?deleted_by=deleter"
    )
    assert delete_note_response.status_code == 200
    assert delete_note_response.json()["notes"] == []
    note_history = client.get("/api/instruments/sxv264/research/history")
    assert note_history.status_code == 200
    assert [
        (revision["revision_number"], revision["change_type"])
        for revision in note_history.json()["note_revisions"]
    ] == [(3, "delete"), (2, "update"), (1, "create")]
    assert note_history.json()["note_revisions"][0]["recorded_by"] == actor["user_id"]

    listed_response = client.get("/api/instruments/fund-us-agg/research")
    assert listed_response.status_code == 200
    assert listed_response.json()["profile"]["thesis"] == ""


def test_fund_document_upload_adds_profile_row_and_allows_download(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Document Upload", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    upload_response = client.post(
        "/api/instruments/sxv264/documents/upload",
        files={"file": ("../Manager DD.pdf", b"manager diligence packet", "application/pdf")},
        data={
            "title": "Manager DD",
            "document_type": "due_diligence",
            "as_of_date": "2026-04-30",
            "source": "manager",
            "status": "uploaded",
            "updated_by": "test",
        },
    )
    assert upload_response.status_code == 200
    payload = upload_response.json()
    document = payload["current_documents"][0]
    assert document["title"] == "Manager DD"
    assert document["file_name"] == "Manager_DD.pdf"
    assert document["file_size"] == len(b"manager diligence packet")
    assert document["download_url"].startswith("/api/instruments/sxv264/documents/files/")
    assert payload["recent_imports"][0]["file_name"] == "Manager_DD.pdf"

    download_response = client.get(document["download_url"])
    assert download_response.status_code == 200
    assert download_response.content == b"manager diligence packet"

    empty_upload = client.post(
        "/api/instruments/sxv264/documents/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert empty_upload.status_code == 400


def test_fund_document_upload_rejects_oversized_file_without_partial_artifact(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from watchlist_app.api.routes import funds as funds_routes

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Oversized Document", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    monkeypatch.setattr(funds_routes.settings, "document_upload_max_bytes", 4)
    instrument_dir = funds_routes._instrument_document_dir("sxv264")
    files_before = set(instrument_dir.iterdir()) if instrument_dir.exists() else set()

    response = client.post(
        "/api/instruments/sxv264/documents/upload",
        files={"file": ("too-large.txt", b"12345", "text/plain")},
    )

    assert response.status_code == 413
    files_after = set(instrument_dir.iterdir()) if instrument_dir.exists() else set()
    assert files_after == files_before


def test_instrument_attributes_can_be_cleared_with_null_and_empty_list(client: TestClient) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Attribute Clear State", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]
    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    single_definition = client.post(
        "/api/instrument-attributes/definitions",
        json={
            "attribute_key": "tag_clear_single",
            "label": "Tag Clear Single",
            "description": "Single-select clear test",
            "data_type": "single_select",
            "domain_code": "research",
            "group_code": "custom",
            "options": ["低波", "高波"],
            "is_groupable": True,
            "is_filterable": True,
            "is_view_column": True,
            "default_visible": False,
        },
    )
    assert single_definition.status_code == 200

    multi_definition = client.post(
        "/api/instrument-attributes/definitions",
        json={
            "attribute_key": "tag_clear_multi",
            "label": "Tag Clear Multi",
            "description": "Multi-select clear test",
            "data_type": "multi_select",
            "domain_code": "research",
            "group_code": "custom",
            "options": ["市场中性", "CTA"],
            "is_groupable": True,
            "is_filterable": True,
            "is_view_column": True,
            "default_visible": False,
        },
    )
    assert multi_definition.status_code == 200

    first_upsert = client.post(
        "/api/instrument-attributes/instruments/sxv264",
        json={
            "values": [
                {"attribute_key": "tag_clear_single", "value": "低波"},
                {"attribute_key": "tag_clear_multi", "value": ["市场中性", "CTA"]},
            ]
        },
    )
    assert first_upsert.status_code == 200

    cleared_upsert = client.post(
        "/api/instrument-attributes/instruments/sxv264",
        json={
            "values": [
                {"attribute_key": "tag_clear_single", "value": None},
                {"attribute_key": "tag_clear_multi", "value": []},
            ]
        },
    )
    assert cleared_upsert.status_code == 200
    payload = cleared_upsert.json()
    assert payload["values"]["tag_clear_single"] is None
    assert payload["values"]["tag_clear_multi"] == []


def test_instrument_taxonomy_assignment_updates_summary_attribute_context_and_watchlist_rows(
    client: TestClient,
) -> None:
    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Taxonomy Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264"]},
    )
    assert add_response.status_code == 200

    tree_response = client.get("/api/taxonomies/instrument-taxonomy")
    assert tree_response.status_code == 200
    tree_payload = tree_response.json()
    assert tree_payload["taxonomy_code"] == "instrument_taxonomy"
    assert any(
        node["node_id"] == "fund-private-equity-quant-index-enhanced"
        and node["is_leaf"]
        for node in tree_payload["nodes"]
    )

    parent_response = client.put(
        "/api/taxonomies/instrument-taxonomy/instruments/sxv264",
        json={"node_id": "fund-private-equity", "updated_by": "test"},
    )
    assert parent_response.status_code == 422
    assert "require a leaf category" in parent_response.json()["detail"]

    update_response = client.put(
        "/api/taxonomies/instrument-taxonomy/instruments/sxv264",
        json={"node_id": "fund-private-equity-quant-index-enhanced", "updated_by": "test"},
    )
    assert update_response.status_code == 200
    update_payload = update_response.json()
    assert update_payload["path_labels"] == ["股票策略", "量化多头", "指数增强"]
    assert update_payload["derived_values"]["instrument_taxonomy_level_1"] == "股票策略"
    assert update_payload["derived_values"]["instrument_taxonomy_level_2"] == "量化多头"
    assert update_payload["derived_values"]["instrument_taxonomy_level_3"] == "指数增强"
    assert update_payload["derived_values"]["instrument_taxonomy_leaf"] == "指数增强"

    attributes_response = client.get("/api/instrument-attributes/instruments/sxv264")
    assert attributes_response.status_code == 200
    attributes_payload = attributes_response.json()
    assert attributes_payload["taxonomy"]["assigned_node_id"] == "fund-private-equity-quant-index-enhanced"
    assert "instrument_taxonomy_level_1" not in attributes_payload["values"]

    summary_response = client.get("/api/instruments/sxv264/summary")
    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert summary_payload["taxonomy"]["path_labels"] == ["股票策略", "量化多头", "指数增强"]

    detail_response = client.get(f"/api/watchlists/{watchlist_id}")
    assert detail_response.status_code == 200
    group_by_codes = [item["code"] for item in detail_response.json()["available_group_bys"]]
    assert group_by_codes == [
        "none",
        "taxonomy",
        "currency",
        "attr.coverage_status",
    ]

    screener_response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "classification",
            "selected_fields": [
                "instrument_name",
                "attr.instrument_taxonomy_level_1",
                "attr.instrument_taxonomy_level_2",
                "attr.instrument_taxonomy_leaf",
            ],
            "group_by": "attr.instrument_taxonomy_level_2",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert screener_response.status_code == 422
    assert "not available" in screener_response.json()["detail"].lower()

    taxonomy_group_response = client.post(
        "/api/screener/query",
        json={
            "watchlist_id": watchlist_id,
            "view_id": "classification",
            "selected_fields": ["instrument_name"],
            "filters": {
                "attr.instrument_taxonomy_level_1": ["股票策略"],
                "attr.instrument_taxonomy_level_2": ["量化多头"],
                "attr.instrument_taxonomy_level_3": ["指数增强"],
            },
            "group_by": "taxonomy",
            "pagination": {"page": 1, "page_size": 20},
        },
    )
    assert taxonomy_group_response.status_code == 200
    taxonomy_group_payload = taxonomy_group_response.json()
    assert taxonomy_group_payload["total_rows"] == 1
    assert taxonomy_group_payload["rows"][0]["attr.instrument_taxonomy_level_1"] == "股票策略"
    assert taxonomy_group_payload["rows"][0]["attr.instrument_taxonomy_level_2"] == "量化多头"
    assert taxonomy_group_payload["rows"][0]["attr.instrument_taxonomy_level_3"] == "指数增强"
    assert [
        (item["group_value"], item["group_depth"], item["row_count"])
        for item in taxonomy_group_payload["groups"]
    ] == [
        ("股票策略", 0, 1),
        ("股票策略 / 量化多头", 1, 1),
        ("股票策略 / 量化多头 / 指数增强", 2, 1),
    ]


def test_monitoring_dashboard_surfaces_missing_metadata_quotes_and_open_recalc_jobs(
    client: TestClient,
) -> None:
    seed_shared_instrument(
        {
        "instrument_id": "fund-no-data",
        "instrument_name": "No Data Fund",
        "instrument_type": "public_fund",
        "currency": "USD",
        "quote_selection_policy": canonical_quote_policy("public_fund"),
        "identifiers": [
            {
                "identifier_type": "ticker",
                "identifier_value": "NODATA",
                "is_primary": True,
            }
        ],
        "market_data": [],
        "lifecycle_state": {"status": "active"},
        },
    )

    created_watchlist = client.post(
        "/api/watchlists",
        json={"name": "Monitoring Coverage", "description": None},
    )
    watchlist_id = created_watchlist.json()["watchlist_id"]

    add_response = client.post(
        f"/api/watchlists/{watchlist_id}/items",
        json={"instrument_ids": ["sxv264", "fund-no-data"]},
    )
    assert add_response.status_code == 200
    assert add_response.json()["accepted_count"] == 2

    status_response = client.post(
        "/api/instrument-attributes/instruments/sxv264",
        json={
            "values": [
                {"attribute_key": "coverage_status", "value": "Invested"},
            ]
        },
    )
    assert status_response.status_code == 200
    research_response = client.put(
        "/api/instruments/sxv264/research",
        json={
            "profile": {
                "current_view": "Hold while manager edge remains intact.",
                "primary_analyst": "Researcher A",
                "next_review_date": "2020-01-02",
                "manual_rating": 4,
            },
            "updated_by": "test",
        },
    )
    assert research_response.status_code == 200
    research_note_response = client.post(
        "/api/instruments/sxv264/research/notes",
        json={
            "note": {
                "note_date": "2020-01-01",
                "note_type": "review",
                "title": "Outstanding manager follow-up",
                "follow_up_date": "2020-01-03",
            },
            "updated_by": "test",
        },
    )
    assert research_note_response.status_code == 200

    recalc_response = client.post("/api/recalc/instruments/sxv264/performance")
    assert recalc_response.status_code == 200

    response = client.get("/api/monitoring/dashboard")
    assert response.status_code == 200
    payload = response.json()

    assert payload["overview"] == {
            "watchlist_count": 4,
        "unique_instrument_count": 2,
        "needs_refresh_count": 2,
        "missing_quote_count": 1,
        "missing_required_metadata_count": 2,
        "research_review_due_count": 1,
        "research_follow_up_due_count": 1,
        "missing_investment_view_count": 0,
        "open_recalc_job_count": 1,
        "failed_recalc_job_count": 0,
    }

    watchlist_summary = next(
        item for item in payload["watchlists"] if item["watchlist_id"] == watchlist_id
    )
    assert watchlist_summary["watchlist_id"] == watchlist_id
    assert watchlist_summary["item_count"] == 2
    assert watchlist_summary["needs_refresh_count"] == 2
    assert watchlist_summary["missing_quote_count"] == 1
    assert watchlist_summary["missing_required_metadata_count"] == 2
    assert watchlist_summary["open_recalc_job_count"] == 1
    assert watchlist_summary["research_issue_count"] == 1

    attention_asset = next(
        item
        for item in payload["needs_attention_instruments"]
        if item["instrument_id"] == "fund-no-data"
    )
    assert attention_asset["data_freshness_status"] == "unavailable"
    assert "needs_refresh" in attention_asset["issue_flags"]
    assert "missing_quote" in attention_asset["issue_flags"]

    missing_metadata_instrument = next(
        item
        for item in payload["missing_required_metadata_instruments"]
        if item["instrument_id"] == "fund-no-data"
    )
    assert "instrument_taxonomy_level_1" in missing_metadata_instrument["missing_attribute_keys"]
    assert "instrument_taxonomy_leaf" in missing_metadata_instrument["missing_attribute_keys"]
    assert len(payload["missing_required_metadata_instruments"]) == 2

    research_item = next(
        item
        for item in payload["research_queue"]
        if item["instrument_id"] == "sxv264"
    )
    assert research_item["research"] == {
        "current_view": "Hold while manager edge remains intact.",
        "manual_rating": 4,
        "primary_analyst": "Researcher A",
        "next_review_date": "2020-01-02",
        "last_updated_at": research_item["research"]["last_updated_at"],
        "active_note_count": 1,
        "next_follow_up_date": "2020-01-03",
        "issue_flags": ["research_review_due", "research_follow_up_due"],
    }
    assert research_item["research"]["last_updated_at"]
    assert "research_review_due" in research_item["issue_flags"]
    assert "research_follow_up_due" in research_item["issue_flags"]

    assert len(payload["open_recalc_jobs"]) == 1
    assert payload["open_recalc_jobs"][0]["instrument_id"] == "sxv264"
    assert payload["open_recalc_jobs"][0]["job_type"] == "performance"
    assert payload["open_recalc_jobs"][0]["job_status"] == "queued"
    assert payload["open_recalc_jobs"][0]["primary_watchlist_id"] == watchlist_id


def test_holding_snapshots_are_append_only_idempotent_and_monotonic(
    client: TestClient,
) -> None:
    from watchlist_app.db import session as session_module
    from watchlist_app.db.models.facts import HoldingSnapshot

    assert client.post("/api/instruments/sxv264/resolve").status_code == 200

    initial_payload = {
        "as_of_date": "2026-08-01",
        "source_cutoff_at": "2026-08-02T09:00:00Z",
        "methodology_version": "custodian-statement/v1",
        "source_record_id": "statement-2026-08-v1",
        "auto_recalculate": False,
        "positions": [
            {
                "holding_name": "Initial Bond",
                "holding_type": "bond",
                "portfolio_weight": "100",
                "effective_duration": "2",
                "yield_to_worst": "3",
            }
        ],
    }
    initial = client.post(
        "/api/facts/instruments/sxv264/holdings",
        json=initial_payload,
    )
    assert initial.status_code == 200
    assert initial.json()["created"] is True
    assert initial.json()["is_current"] is True

    identical = client.post(
        "/api/facts/instruments/sxv264/holdings",
        json=initial_payload,
    )
    assert identical.status_code == 200
    assert identical.json()["created"] is False
    assert identical.json()["holding_snapshot_id"] == initial.json()["holding_snapshot_id"]

    revised_payload = deepcopy(initial_payload)
    revised_payload["source_cutoff_at"] = "2026-08-03T09:00:00Z"
    revised_payload["source_record_id"] = "statement-2026-08-v2"
    revised_payload["positions"][0]["holding_name"] = "Revised Bond"
    revised = client.post(
        "/api/facts/instruments/sxv264/holdings",
        json=revised_payload,
    )
    assert revised.status_code == 200
    assert revised.json()["created"] is True
    assert revised.json()["is_current"] is True
    assert revised.json()["holding_snapshot_id"] != initial.json()["holding_snapshot_id"]

    backfill_payload = deepcopy(initial_payload)
    backfill_payload["as_of_date"] = "2026-07-01"
    backfill_payload["source_cutoff_at"] = "2026-07-02T09:00:00Z"
    backfill_payload["source_record_id"] = "statement-2026-07-v1"
    backfill_payload["positions"][0]["holding_name"] = "Historical Bond"
    backfill = client.post(
        "/api/facts/instruments/sxv264/holdings",
        json=backfill_payload,
    )
    assert backfill.status_code == 200
    assert backfill.json()["created"] is True
    assert backfill.json()["is_current"] is False

    current = client.get("/api/facts/instruments/sxv264/holdings/current")
    assert current.status_code == 200
    assert current.json()["holding_snapshot_id"] == revised.json()["holding_snapshot_id"]
    assert current.json()["positions"][0]["holding_name"] == "Revised Bond"

    with session_module.get_session_factory()() as session:
        snapshots = list(
            session.scalars(
                select(HoldingSnapshot).where(
                    HoldingSnapshot.instrument_id == "sxv264"
                )
            ).all()
        )
    assert len(snapshots) == 3
    assert sum(1 for snapshot in snapshots if snapshot.is_current) == 1


def test_holding_exposure_uses_position_weights_and_statement_as_of(
    client: TestClient,
) -> None:
    assert client.post("/api/instruments/sxv264/resolve").status_code == 200
    response = client.post(
        "/api/facts/instruments/sxv264/holdings",
        json={
            "as_of_date": "2026-08-01",
            "source_cutoff_at": "2026-08-02T09:00:00Z",
            "methodology_version": "custodian-statement/v1",
            "source_record_id": "weighted-example",
            "auto_recalculate": True,
            "positions": [
                {
                    "holding_name": "Large Short Bond",
                    "holding_type": "bond",
                    "portfolio_weight": "90",
                    "effective_duration": "1",
                    "yield_to_worst": "2",
                },
                {
                    "holding_name": "Small Long Bond",
                    "holding_type": "bond",
                    "portfolio_weight": "10",
                    "effective_duration": "9",
                    "yield_to_worst": "10",
                },
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["auto_recalculated"] is True

    exposure = client.get("/api/instruments/sxv264/exposure/summary")
    assert exposure.status_code == 200
    payload = exposure.json()
    assert payload["style_box"]["weighted_duration"] == pytest.approx(1.8)
    assert payload["style_box"]["yield_to_worst"] == pytest.approx(2.8)
    assert payload["style_box"]["reported_weight_total"] == pytest.approx(100.0)
    assert payload["style_box"]["duration_weight_coverage"] == pytest.approx(100.0)
    assert payload["style_box"]["yield_to_worst_weight_coverage"] == pytest.approx(100.0)
    assert payload["snapshot_metadata"]["as_of_date"] == "2026-08-01"
    assert payload["snapshot_metadata"]["source_cutoff_at"] == "2026-08-02T09:00:00Z"


def test_holding_ingest_rejects_ambiguous_cutoff_and_overallocated_weights(
    client: TestClient,
) -> None:
    assert client.post("/api/instruments/sxv264/resolve").status_code == 200
    naive_cutoff = client.post(
        "/api/facts/instruments/sxv264/holdings",
        json={
            "as_of_date": "2026-08-01",
            "source_cutoff_at": "2026-08-02T09:00:00",
            "positions": [],
        },
    )
    assert naive_cutoff.status_code == 422

    overallocated = client.post(
        "/api/facts/instruments/sxv264/holdings",
        json={
            "as_of_date": "2026-08-01",
            "source_cutoff_at": "2026-08-02T09:00:00Z",
            "positions": [
                {
                    "holding_name": "A",
                    "holding_type": "bond",
                    "portfolio_weight": "60",
                },
                {
                    "holding_name": "B",
                    "holding_type": "bond",
                    "portfolio_weight": "50",
                },
            ],
        },
    )
    assert overallocated.status_code == 422
