from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

import pytest

from portfolio_app.services import instrument_charts, risk_basis
from portfolio_app.services.instrument_charts import (
    build_instrument_holdings_market_profile_from_detail,
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


def test_full_return_history_coverage_uses_return_basis_not_valuation_or_holding_dates(monkeypatch) -> None:
    detail = _detail([
        _point("total_return_nav", "2026-06-23", "1.00"),
        _point("official_nav", "2026-06-24", "1.00"),
        _point("total_return_nav", "2026-06-25", "1.01"),
        _point("total_return_nav", "2026-06-26", "1.02"),
    ], instrument_type="private_fund")
    detail["source_settings"] = {"expected_frequency": "daily", "market_calendar": "XSHG"}
    detail["quote_selection_policy"]["total_return"] = ["total_return_nav"]
    for point in detail["market_data"]:
        point["metric_family"] = "nav"
    monkeypatch.setattr(risk_basis, "_market_calendar_sessions", lambda *_args: [
        date(2026, 6, day) for day in (23, 24, 25, 26)
    ])

    profile = build_instrument_holdings_market_profile_from_detail(
        detail, instrument_id="fund", as_of_date=date(2026, 6, 26),
        holding_start_date=date(2026, 6, 25),
    )

    assert profile["instrument_return_series_all"]["observation_coverage"] == {
        "start_date": "2026-06-23", "end_date": "2026-06-26",
        "gap_dates": ["2026-06-24"], "gap_detection_basis": "market_calendar:XSHG",
    }
    assert profile["instrument_return_series_all"]["points"][0]["start_date"] == "2026-06-23"


@pytest.mark.parametrize(
    ("holding_day", "missing_days", "expected_drawdown"),
    [(24, (24,), 116 / 120 - 1), (25, (24,), 116 / 118 - 1),
     (23, (24,), None), (24, (24, 26), None)],
)
def test_held_history_excludes_only_gaps_on_or_before_recognition(
    monkeypatch, holding_day, missing_days, expected_drawdown,
) -> None:
    sessions = [date(2026, 6, day) for day in (23, 24, 25, 26, 29, 30)]
    monkeypatch.setattr(risk_basis, "_market_calendar_sessions", lambda *_args: sessions)
    levels = {23: 120, 25: 118, 26: 116, 29: 119, 30: 121}
    detail = _detail([
        _point("total_return_nav", f"2026-06-{day}", str(value))
        for day, value in levels.items() if day not in missing_days
    ], instrument_type="private_fund")
    detail["source_settings"] = {"expected_frequency": "daily", "market_calendar": "XSHG"}
    detail["quote_selection_policy"]["total_return"] = ["total_return_nav"]
    for point in detail["market_data"]:
        point["metric_family"] = "nav"
    original = deepcopy(detail)

    profile = build_instrument_holdings_market_profile_from_detail(
        detail, instrument_id="fund", as_of_date=date(2026, 6, 30),
        holding_start_date=date(2026, 6, holding_day),
    )

    if expected_drawdown is None:
        assert profile["instrument_holding_max_drawdown"] is None
    else:
        assert profile["instrument_holding_max_drawdown"] == pytest.approx(expected_drawdown)
    holding = profile["instrument_holding_return_series"]
    assert holding["observation_coverage"]["start_date"] == f"2026-06-{holding_day}"
    assert holding["observation_coverage"]["gap_dates"] == [
        f"2026-06-{day}" for day in missing_days if day > holding_day
    ]
    if holding_day == 24:
        # Keep the actual prior NAV and first post-confirmation change, rather
        # than silently restarting at June 25 or inventing a June 24 NAV.
        first = holding["points"][0]
        assert first["start_date"] == "2026-06-23"
        assert first["date"] == "2026-06-25"
        assert first["value"] == pytest.approx(118 / 120 - 1)
        assert first["status"] == "complete"
    if 26 in missing_days:
        assert next(point for point in holding["points"] if point["date"] == "2026-06-29")["status"] != "complete"
    full = profile["instrument_return_series_all"]
    assert full["observation_coverage"]["gap_dates"] == [f"2026-06-{day}" for day in missing_days]
    assert full["points"][0]["status"] != "complete"
    assert profile["instrument_max_drawdown"] is None
    assert detail == original


def test_held_history_keeps_interval_break_without_explicit_source_gap() -> None:
    points = tuple({
        "date": date(2026, 6, day), "date_iso": f"2026-06-{day}", "value": value,
        **({"risk_break_reason": "missing_observation_interval"} if day == 25 else {}),
    } for day, value in ((23, 120), (25, 118), (26, 116)))
    selection = instrument_charts.ChartSeriesSelection(
        points=points, selected_basis="total_return_nav",
        return_metadata={"observation_coverage": {
            "start_date": "2026-06-23", "end_date": "2026-06-26", "gap_dates": [],
            "gap_detection_basis": "schedule_unverified",
        }},
    )

    profile = instrument_charts._build_instrument_trend_metrics_from_selection(
        selection, as_of_date=date(2026, 6, 26), holding_start_date=date(2026, 6, 24),
        calculation_frequency="daily", source_settings={},
    )

    assert profile["instrument_holding_max_drawdown"] is None
    first = profile["instrument_holding_return_series"]["points"][0]
    assert first["status"] == "unavailable"
    assert first["reason"] == "missing_observation_interval"
    assert selection.points[1]["risk_break_reason"] == "missing_observation_interval"


@pytest.mark.parametrize(
    "scenario",
    ["different_bases", "full_history", "split", "unconfirmed_split", "duplicate", "empty"],
)
def test_holdings_profile_shares_raw_series_and_preserves_chart_and_return_contracts(
    monkeypatch,
    scenario: str,
) -> None:
    as_of_date = date(2026, 7, 15)
    start_date = as_of_date - timedelta(days=450)
    holding_start_date = as_of_date - timedelta(days=120)
    points = [
        _point("close", (start_date + timedelta(days=index)).isoformat(), str(100 + index))
        for index in range(451)
    ]
    actions = []
    if scenario in {"different_bases", "full_history"}:
        # Chart coverage and total-return preference are separate policies.
        points.extend([
            _point("adjusted_close", (as_of_date - timedelta(days=30 - index)).isoformat(), str(100 + index))
            for index in range(31)
        ])
        if scenario == "full_history":
            points.extend([
                _point("adjusted_close", start_date.isoformat(), "400"),
                _point("adjusted_close", (start_date + timedelta(days=1)).isoformat(), "100"),
            ])
    elif scenario in {"split", "unconfirmed_split"}:
        actions = [_split_event(status="confirmed" if scenario == "split" else "proposed")]
    elif scenario == "duplicate":
        points.append(dict(points[-1]))
    else:
        points = []
    detail = _detail(points, corporate_actions=actions)
    detail["exchange_code"] = "XNYS"
    original_detail = deepcopy(detail)
    expected = {
        **{
            f"price_chart_{range_key}": build_instrument_price_chart_from_detail(
                detail,
                instrument_id="equity-history",
                as_of_date=as_of_date,
                range_key=range_key,
                max_points=48,
                price_mode="raw",
            )["points"]
            for range_key in instrument_charts.HOLDINGS_PRICE_CHART_RANGE_KEYS
        },
        **build_instrument_trend_metrics_from_detail(
            detail,
            as_of_date=as_of_date,
            holding_start_date=holding_start_date,
        ),
    }
    calls: list[tuple[str, ...]] = []
    original_resolve = instrument_charts.resolve_quote_series

    def counted_resolve(detail, *, candidate_bases, end_date):
        calls.append(tuple(candidate_bases))
        return original_resolve(detail, candidate_bases=candidate_bases, end_date=end_date)

    def unused_calendar(*_args, **_kwargs):
        raise AssertionError("A holdings point array does not need full chart calendar metadata")

    monkeypatch.setattr(instrument_charts, "resolve_quote_series", counted_resolve)
    monkeypatch.setattr(instrument_charts, "market_calendar_sessions", unused_calendar)
    actual = build_instrument_holdings_market_profile_from_detail(
        detail,
        instrument_id="equity-history",
        as_of_date=as_of_date,
        holding_start_date=holding_start_date,
    )
    assert actual == expected
    assert detail == original_detail
    assert len(calls) == len(set(calls))
    if scenario == "different_bases":
        assert actual["instrument_trend_basis"] == "adjusted_close"
        assert actual["price_chart_6m"][-1]["value"] == 550.0
        assert actual["instrument_return_1m"] == pytest.approx(0.3)
    if scenario == "full_history":
        assert actual["instrument_max_drawdown"] is None
        assert actual["instrument_return_series_all"]["observation_coverage"]["gap_dates"]

    calls.clear()
    compact = build_instrument_holdings_market_profile_from_detail(
        detail,
        instrument_id="equity-history",
        as_of_date=as_of_date,
        holding_start_date=holding_start_date,
        include_details=False,
    )
    assert compact == {
        **expected,
        "price_chart_1m": [],
        "price_chart_3m": [],
        "price_chart_1y": [],
    }
    assert len(calls) == len(set(calls))


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
        price_mode="raw",
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
    assert chart["selection_reason"] == "selected_raw_valuation_series"
    assert [point["value"] for point in chart["points"]] == [80.0, 90.0, 95.0, 96.0, 100.0]


def test_derivative_reference_chart_uses_price_level_not_adjusted_close() -> None:
    chart = build_instrument_price_chart_from_detail(
        _detail(
            [
                _point("adjusted_close", "2026-07-14", "90"),
                _point("adjusted_close", "2026-07-15", "95"),
                _point("close", "2026-07-14", "100"),
                _point("close", "2026-07-15", "110"),
            ]
        ),
        instrument_id="equity-history",
        as_of_date=date(2026, 7, 15),
        range_key="all",
        price_level=True,
    )

    assert chart is not None
    assert chart["series_role"] == "price_level"
    assert chart["chart_basis"] == "close"
    assert [point["value"] for point in chart["points"]] == [100.0, 110.0]


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
    assert chart["chart_basis"] == ("close" if configured_semantics != "unknown" else None)
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
        instrument_type="public_fund",
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
        instrument_type="public_fund",
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


def test_raw_close_display_retains_actual_quotes_without_creating_a_return_source() -> None:
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
        price_mode="raw",
    )
    trend = build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=date(2026, 1, 3),
    )

    assert chart is not None
    assert chart["chart_basis"] == "close"
    assert chart["split_adjusted"] is False
    assert chart["selection_reason"] == "selected_raw_valuation_series"
    assert [point["value"] for point in chart["points"]] == [100.0, 50.0, 55.0]
    assert trend["instrument_trend_basis"] is None
    assert trend["instrument_trend_reason"] == "quote_policy_unavailable"
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
def test_raw_price_display_does_not_apply_unconfirmed_or_incomplete_adjustments(
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
        price_mode="raw",
    )
    trend = build_instrument_trend_metrics_from_detail(
        detail,
        as_of_date=date(2026, 1, 2),
    )

    assert chart is not None
    assert [point["value"] for point in chart["points"]] == [100, 50]
    assert chart["split_adjusted"] is False
    assert chart["selection_reason"] == "selected_raw_valuation_series"
    assert trend["instrument_trend_basis"] is None
    assert trend["instrument_trend_coverage"]["state"] == "unavailable"
    assert trend["instrument_trend_reason"] == "analytical_return_series_unavailable"
