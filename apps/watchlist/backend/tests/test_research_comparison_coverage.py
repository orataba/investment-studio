"""Requested coverage is checked before an intersection can erase a missing day."""
from datetime import UTC, date, datetime

import pytest

from investment_studio_instrument_core import build_return_intervals
from watchlist_app.services.calculation_frequency import build_calculation_frequency_context
from watchlist_app.services.research_workbench import compare_series


CUTOFF = datetime(2026, 10, 9, tzinfo=UTC)
DAYS = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]


def series(days, *, calendar="XNYS", frequency="daily", **schedule):
    points = [{"date": day, "as_of_date": date.fromisoformat(day), "value": 100 + i}
              for i, day in enumerate(days)]
    context = build_calculation_frequency_context(points, expected_frequency=frequency, market_calendar=calendar)
    return {"currency": "USD", "metadata": {"return_kind": "total_return", "return_series_status": "ready"},
            "points": points, "frequency": context["profile"],
            "observation_schedule": {"market_calendar": calendar, "expected_frequency": frequency, **schedule},
            "intervals": build_return_intervals(points, break_dates=context["missing_observation_dates"])}


@pytest.mark.parametrize("missing", [DAYS[0], DAYS[2], DAYS[-1]])
def test_requested_leading_internal_and_trailing_missing_session_cannot_be_intersected_away(missing):
    result = compare_series({"complete": series(DAYS), "missing": series([day for day in DAYS if day != missing])},
                            date(2026, 10, 1), date(2026, 10, 8), "complete", "missing", cutoff=CUTOFF)
    assert [row["instrument_id"] for row in result["rows"]] == ["complete"]
    assert result["sample_end"] == DAYS[-1]
    assert result["rows"][0]["excess_return_pp"] is None
    assert result["window_coverage"]["missing"]["missing_dates"] == [missing]
    assert result["excluded"][0]["instrument_id"] == "missing"


def test_complete_historical_window_is_not_blocked_by_a_later_source_gap_or_current_staleness():
    observed = series(DAYS[:3] + DAYS[4:])
    observed["freshness"] = "stale"
    result = compare_series({"asset": observed}, date(2026, 10, 1), date(2026, 10, 5), cutoff=CUTOFF)
    assert result["rows"] and not result["excluded"]
    assert result["sample_end"] == "2026-10-05"
    assert any("不表示全历史完整" in value for value in result["limitations"])


@pytest.mark.parametrize("calendar,days,end,before,after", [
    ("XNYS", ["2026-11-24", "2026-11-25"], "2026-11-27", "2026-11-27T17:59:00+00:00", "2026-11-27T18:00:00+00:00"),
    ("XHKG", ["2026-12-22", "2026-12-23"], "2026-12-24", "2026-12-24T04:09:00+00:00", "2026-12-24T04:10:00+00:00"),
    ("24/7", ["2026-10-09", "2026-10-10"], "2026-10-11", "2026-10-11T23:59:00+00:00", "2026-10-12T00:00:00+00:00"),
])
def test_frozen_cutoff_honors_early_close_hk_auction_and_utc_day(calendar, days, end, before, after):
    item = series(days, calendar=calendar)
    args = ({"asset": item}, date.fromisoformat(days[0]), date.fromisoformat(end))
    pending = compare_series(*args, cutoff=datetime.fromisoformat(before))
    assert pending["rows"] and not pending["excluded"]
    assert pending["window_coverage"]["asset"]["not_yet_due_dates"] == [end]
    assert any("尚未收盘或尚未到披露期限" in value for value in pending["limitations"])
    missing = compare_series(*args, cutoff=datetime.fromisoformat(after))
    assert not missing["rows"]
    assert missing["window_coverage"]["asset"]["missing_dates"] == [end]


def test_unclosed_bar_is_not_a_complete_observation_even_when_present():
    item = series(DAYS)
    result = compare_series({"asset": item}, date(2026, 10, 1), date(2026, 10, 8),
                            cutoff=datetime(2026, 10, 8, 19, 59, tzinfo=UTC))
    assert not result["rows"]
    assert "尚未完成交易时段" in result["excluded"][0]["reason"]


def test_closed_holiday_request_end_is_not_a_missing_session():
    item = series(["2026-11-24", "2026-11-25", "2026-11-27"])
    result = compare_series({"asset": item}, date(2026, 11, 24), date(2026, 11, 28),
                            cutoff=datetime(2026, 11, 29, tzinfo=UTC))
    assert result["rows"] and result["sample_end"] == "2026-11-27"
    assert result["window_coverage"]["asset"]["missing_dates"] == []


def test_frozen_nav_publication_lag_is_respected_without_reading_current_registry():
    item = series(DAYS[:-1], source_mode="email", instrument_type="private_fund")
    result = compare_series({"fund": item}, date(2026, 10, 1), date(2026, 10, 8), cutoff=CUTOFF)
    assert result["rows"] and result["sample_end"] == "2026-10-07"
    assert result["window_coverage"]["fund"]["expected_end"] == "2026-10-07"
    overdue = compare_series({"fund": item}, date(2026, 10, 1), date(2026, 10, 8),
                             cutoff=datetime(2026, 10, 10, tzinfo=UTC))
    assert not overdue["rows"]
    assert overdue["window_coverage"]["fund"]["missing_dates"] == ["2026-10-08"]


@pytest.mark.parametrize("damage", ["missing_schedule", "unknown_calendar"])
def test_missing_frozen_schedule_or_unconfirmed_calendar_cannot_certify_coverage(damage):
    item = series(DAYS)
    if damage == "missing_schedule":
        item.pop("observation_schedule")
    else:
        item["frequency"]["gap_detection_basis"] = "calendar_day_threshold"
    result = compare_series({"asset": item}, date(2026, 10, 1), date(2026, 10, 8), cutoff=CUTOFF)
    assert not result["rows"] and result["excluded"]


@pytest.mark.parametrize("frequency,days,start,end", [
    ("monthly", ["2026-06-30", "2026-07-31", "2026-08-31"], "2026-06-30", "2026-09-30"),
    ("weekly", ["2026-09-04", "2026-09-11", "2026-09-18"], "2026-09-01", "2026-09-27"),
])
def test_low_frequency_missing_last_complete_period_is_not_silently_shortened(frequency, days, start, end):
    item = series(days, calendar=None, frequency=frequency)
    result = compare_series({"fund": item}, date.fromisoformat(start), date.fromisoformat(end), cutoff=CUTOFF)
    assert not result["rows"]
    assert "缺少整期披露观察值" in result["excluded"][0]["reason"]


def test_low_frequency_existing_period_does_not_claim_exact_endpoint_publication():
    item = series(["2026-07-31", "2026-08-28"], calendar=None, frequency="monthly")
    result = compare_series({"fund": item}, date(2026, 7, 1), date(2026, 8, 31), cutoff=CUTOFF)
    assert not result["rows"]
    assert "未确认请求端点" in result["excluded"][0]["reason"]
    observed = compare_series({"fund": item}, date(2026, 7, 31), date(2026, 8, 28), cutoff=CUTOFF)
    assert observed["rows"] and observed["sample_end"] == "2026-08-28"
    assert observed["window_coverage"]["fund"]["status"] == "observed_endpoints_only"
    assert any("实际起止观察值" in value for value in observed["limitations"])


def test_event_driven_comparison_requires_actual_request_endpoints():
    item = series(["2026-09-01", "2026-09-10"], calendar=None, frequency="event_driven")
    result = compare_series({"fund": item}, date(2026, 9, 1), date(2026, 9, 30), cutoff=CUTOFF)
    assert not result["rows"]
    observed = compare_series({"fund": item}, date(2026, 9, 1), date(2026, 9, 10), cutoff=CUTOFF)
    assert observed["rows"] and observed["window_coverage"]["fund"]["status"] == "observed_endpoints_only"


def test_canonical_research_returns_retain_source_schedule_without_full_registry_secrets():
    from watchlist_app.services.canonical_recalc import _build_return_series, _select_quote_series
    points = [{"as_of_date": date.fromisoformat(day), "value": 100 + i, "currency": "USD", "status": "complete",
               "metric_family": "price", "quote_basis": "adjusted_close"} for i, day in enumerate(DAYS)]
    source = {"market_calendar": "XNYS", "expected_frequency": "daily", "release_lag_days": 1,
              "source_mode": "api", "unrelated_setting": "not part of the schedule"}
    selected = _select_quote_series({"adjusted_close": points}, shared_instrument={"source_settings": source,
        "quote_selection_policy": {"total_return": ["adjusted_close"]}}, role="total_return", preference="auto", instrument_type="etf")
    context = build_calculation_frequency_context(points, expected_frequency="daily", market_calendar="XNYS")
    returns = _build_return_series(selected, context)
    research_returns = {**returns, "points": returns["wealth_points"]}
    assert research_returns["observation_schedule"] == {"market_calendar": "XNYS", "expected_frequency": "daily",
        "release_lag_days": 1, "source_mode": "api", "instrument_type": "etf"}
    source["release_lag_days"] = 0
    assert research_returns["observation_schedule"]["release_lag_days"] == 1


def test_risk_peer_comparison_does_not_preintersect_away_a_missing_tail():
    from types import SimpleNamespace
    from watchlist_app.services import risk_performance as performance

    charts = {"target": SimpleNamespace(payload_json={"research_returns": series(DAYS)},
                                       source_cutoff_at=CUTOFF, data_freshness_status="fresh"),
              "benchmark": SimpleNamespace(payload_json={"research_returns": series(DAYS[:-1])},
                                           source_cutoff_at=CUTOFF, data_freshness_status="stale")}
    context = performance.PerformanceEvidenceContext(frozenset({"target"}), {
        performance.InstrumentChartReadModel: charts,
        performance.InstrumentManualProfile: {"target": SimpleNamespace(
            nav_settings_json={"default_benchmark_instrument_id": "benchmark"})},
        performance.InstrumentPerformanceReadModel: {},
        performance.InstrumentDetail: {"benchmark": SimpleNamespace(instrument_name="benchmark")},
    }, {"target": ([], {}, None)}, CUTOFF)
    evidence = performance.performance_evidence(None, "target", context=context)
    comparison = evidence["comparisons"][0]["comparison"]
    assert comparison["requested_end"] == "2026-10-08"
    assert comparison["window_coverage"]["benchmark"]["missing_dates"] == ["2026-10-08"]
    assert [row["instrument_id"] for row in comparison["rows"]] == ["target"]
    assert comparison["rows"][0]["excess_return_pp"] is None


def test_monthly_performance_and_peer_excess_cannot_shorten_a_missing_final_session():
    from types import SimpleNamespace
    from watchlist_app.services import risk_performance as performance
    from watchlist_app.services.calculation_frequency import _market_calendar_sessions

    days = [day.isoformat() for day in _market_calendar_sessions("XNYS", date(2026, 4, 30), date(2026, 6, 2))]
    complete = series(days)
    incomplete = series([day for day in days if day != "2026-05-29"])
    cutoff = datetime(2026, 6, 3, 12, tzinfo=UTC)
    charts = {iid: SimpleNamespace(payload_json={"research_returns": item},
        source_cutoff_at=cutoff, data_freshness_status="fresh")
        for iid, item in (("target", incomplete), ("benchmark", complete))}
    context = performance.PerformanceEvidenceContext(frozenset({"target"}), {
        performance.InstrumentChartReadModel: charts,
        performance.InstrumentManualProfile: {"target": SimpleNamespace(
            nav_settings_json={"default_benchmark_instrument_id": "benchmark"})},
        performance.InstrumentPerformanceReadModel: {},
        performance.InstrumentDetail: {"benchmark": SimpleNamespace(instrument_name="benchmark")},
    }, {"target": ([], {}, None)}, cutoff)
    evidence = performance.performance_evidence(None, "target", context=context)
    may = evidence["monthly_periods"][0]
    assert may["month"] == "2026-05" and may["end_date"] == "2026-05-28"
    assert may["requested_end"] == "2026-05-31"
    assert may["window_coverage"]["missing_dates"] == ["2026-05-29"]
    assert may["return_pct"] is None and may["status"] == "unavailable"
    assert may["latest_month_to_date"] is False
    assert evidence["trailing_negative_completed_observed_months"] == 0
    assert evidence["comparisons"][0]["monthly_periods"][0]["excess_return_pp"] is None
    # A verified calendar handles the weekend boundary without demanding May 31.
    complete_may = performance._month_periods(performance._points(complete), complete, cutoff=cutoff)[0]
    assert complete_may["status"] == "complete" and complete_may["end_date"] == "2026-05-29"


def test_completed_month_awaiting_nav_release_does_not_publish_an_earlier_close():
    from watchlist_app.services.risk_performance import _month_periods, _points
    from watchlist_app.services.calculation_frequency import _market_calendar_sessions

    days = [day.isoformat() for day in _market_calendar_sessions("XNYS", date(2026, 7, 31), date(2026, 8, 28))]
    item = series(days, source_mode="email", instrument_type="private_fund", release_lag_days=1)
    pending = _month_periods(_points(item), item, cutoff=datetime(2026, 9, 1, 12, tzinfo=UTC))[0]
    assert pending["month"] == "2026-08" and pending["latest_month_to_date"] is False
    assert pending["window_coverage"]["not_yet_due_dates"] == ["2026-08-31"]
    assert pending["return_pct"] is None and "尚未到披露期限" in pending["limitation"]
    overdue = _month_periods(_points(item), item, cutoff=datetime(2026, 9, 2, 12, tzinfo=UTC))[0]
    assert overdue["window_coverage"]["missing_dates"] == ["2026-08-31"]
    assert overdue["return_pct"] is None


def test_low_frequency_month_table_requires_actual_calendar_month_anchors():
    from watchlist_app.services.risk_performance import _month_periods, _points

    item = series(["2026-06-26", "2026-07-31", "2026-08-28"], calendar=None, frequency="monthly")
    rows = _month_periods(_points(item), item, cutoff=CUTOFF)
    assert [row["month"] for row in rows] == ["2026-07", "2026-08"]
    assert all(row["status"] == "unavailable" and row["return_pct"] is None for row in rows)
    assert not any(row["latest_month_to_date"] for row in rows)


@pytest.mark.parametrize("future_evidence", ["observation", "source_cutoff_at", "last_recalculated_at"])
def test_performance_read_excludes_later_snapshot_without_truncating_it_to_historical_cutoff(future_evidence):
    from types import SimpleNamespace
    from watchlist_app.services import risk_performance as performance

    cutoff = datetime(2026, 10, 8, 23, tzinfo=UTC)
    item = series([*DAYS, "2026-10-09"] if future_evidence == "observation" else DAYS)
    chart = SimpleNamespace(payload_json={"research_returns": item}, data_freshness_status="fresh",
                            source_cutoff_at=cutoff, last_recalculated_at=cutoff)
    if future_evidence != "observation":
        setattr(chart, future_evidence, CUTOFF)
    context = performance.PerformanceEvidenceContext(frozenset({"target"}), {
        performance.InstrumentChartReadModel: {"target": chart},
        performance.InstrumentManualProfile: {"target": SimpleNamespace(
            nav_settings_json={"default_benchmark_instrument_id": "peer"})},
        performance.InstrumentPerformanceReadModel: {"target": SimpleNamespace(
            payload_json={"trailing_returns": [{"value": 10}]}, source_cutoff_at=cutoff)},
        performance.InstrumentDetail: {},
    }, {"target": ([], {}, None)}, cutoff)
    evidence = performance.performance_evidence(None, "target", context=context)
    assert evidence["available"] is False and evidence["sample_return_pct"] is None
    assert evidence["observations"] == 0 and evidence["sample_end"] is None
    assert evidence["monthly_periods"] == evidence["comparisons"] == evidence["trailing_returns"] == []
    assert any("冻结时刻" in value for value in evidence["limitations"])
    assert len(chart.payload_json["research_returns"]["points"]) == len(item["points"])


def test_month_batch_prepares_calendar_once_and_indexes_history_without_repeated_full_scans(monkeypatch):
    from watchlist_app.services import calculation_frequency as frequency
    from watchlist_app.services.risk_performance import _month_periods, _points

    days = [day.isoformat() for day in frequency._market_calendar_sessions("XNYS", date(2023, 1, 3), date(2026, 1, 6))]
    item = series(days)
    class CountedIntervals(list):
        scans = 0
        def __iter__(self):
            self.scans += 1
            return super().__iter__()

    item["intervals"] = CountedIntervals(item["intervals"])
    calls = []
    original = frequency.assess_latest_observation_freshness
    def freshness(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(frequency, "assess_latest_observation_freshness", freshness)
    cutoff = datetime(2026, 1, 7, 12, tzinfo=UTC)
    rows = _month_periods(_points(item), item, cutoff=cutoff)
    assert len(rows) == 36 and all(row["status"] == "complete" for row in rows)
    assert rows[0]["month"] == "2023-02" and rows[-1]["month"] == "2026-01"
    assert len(calls) == 1 and calls[0]["now"] == cutoff
    assert item["intervals"].scans == 1


@pytest.mark.parametrize("damage", ["duplicate", "missing", "unavailable"])
def test_month_interval_index_preserves_all_evidence_and_rejects_incomplete_pairs(damage):
    from watchlist_app.services.calculation_frequency import _market_calendar_sessions
    from watchlist_app.services.risk_performance import _month_periods, _points

    days = [day.isoformat() for day in _market_calendar_sessions("XNYS", date(2026, 4, 30), date(2026, 6, 30))]
    item = series(days)
    if damage == "duplicate":
        item["intervals"].append(dict(item["intervals"][0]))
    elif damage == "missing":
        item["intervals"].pop(0)
    else:
        item["intervals"][0]["status"] = "unavailable"
    rows = _month_periods(_points(item), item, cutoff=datetime(2026, 7, 1, 12, tzinfo=UTC))
    assert rows[0]["month"] == "2026-05" and rows[0]["status"] == "unavailable"
    assert rows[0]["return_pct"] is None and rows[0]["limitation"]
    assert rows[1]["month"] == "2026-06" and rows[1]["status"] == "complete"


def test_calendar_batch_without_oldest_history_still_verifies_each_supported_later_window(monkeypatch):
    from studio_market import calendars
    from watchlist_app.services.calculation_frequency import _market_calendar_sessions
    from watchlist_app.services.risk_performance import _month_periods, _points

    days = [day.isoformat() for day in _market_calendar_sessions("XNYS", date(2026, 4, 30), date(2026, 6, 30))]
    item = series(days)
    original = calendars.closed_market_sessions
    def limited_calendar(calendar, start, end, now):
        return None if start < date(2026, 5, 1) else original(calendar, start, end, now)
    monkeypatch.setattr(calendars, "closed_market_sessions", limited_calendar)
    rows = _month_periods(_points(item), item, cutoff=datetime(2026, 7, 1, 12, tzinfo=UTC))
    assert rows[0]["status"] == "unavailable" and rows[0]["return_pct"] is None
    assert rows[1]["status"] == "complete" and rows[1]["return_pct"] is not None
