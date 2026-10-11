from copy import deepcopy
from datetime import date, timedelta

import pytest

from investment_studio_instrument_core.risk_statistics import empirical_tail
from watchlist_app.services.instrument_tail_risk import instrument_tail_risk

TODAY = date(2026, 10, 8)


def chart(count=200, returns=None):
    end = TODAY - timedelta(days=1)
    values = returns if returns is not None else [(-1 if i % 2 else 1) * i / 10000 for i in range(count)]
    return {"instrument_id": "asset", "return_series": {
        "metadata": {"return_kind": "total_return", "return_measure": "simple", "return_series_status": "ready", "quote_basis": "adjusted_close"},
        "frequency": {"resolved_frequency": "daily", "expected_frequency": "daily", "gap_detection_basis": "market_calendar:24/7"},
        "currency": "USD", "intervals": [
            {"start_date": (end - timedelta(days=len(values) - i)).isoformat(),
             "date": (end - timedelta(days=len(values) - i - 1)).isoformat(),
             "value": value, "status": "complete"}
            for i, value in enumerate(values)]}}


def calculate(payload, **kwargs):
    return instrument_tail_risk(payload, instrument_type="etf", today=TODAY, **kwargs)


def test_fractional_tail_probability_and_ties_share_portfolio_definition():
    values = [0.] * 101 + [-.1] * 4
    result = calculate(chart(returns=values))
    assert result["var"] == 0
    assert result["tail_effective_observations"] == pytest.approx(5.25)
    assert result["expected_shortfall"] == pytest.approx(.4 / 5.25)
    assert result["tail_observation_count"] == 6
    assert result["expected_shortfall"] == empirical_tail([-value for value in values], .95)["expected_shortfall"]


def test_daily_sample_is_not_display_prices_or_log_returns_and_has_partial_window():
    payload = chart()
    payload["price_series"] = {"points": [{"value": 100}, {"value": 1}]}
    for point in payload["return_series"]["intervals"]:
        point["log_return"] = -999
    result = calculate(payload, lookback_days=365)
    assert result["observation_count"] == 200
    assert result["expected_observation_count"] == 365
    assert result["uncovered_leading_count"] == 165
    assert result["uncovered_trailing_count"] == 0
    assert result["missing_internal_count"] == 0
    assert result["status"] == "partial"
    assert result["expected_shortfall"] < .02
    assert not result["source_stale"]


@pytest.mark.parametrize("frequency", ["weekly", "monthly", "event_driven"])
def test_low_frequency_never_time_scaled(frequency):
    payload = chart()
    payload["return_series"]["frequency"]["expected_frequency"] = frequency
    assert calculate(payload)["reason"] == "non_daily_source"


@pytest.mark.parametrize("key,value", [("return_kind", "price_return"), ("return_kind", None), ("return_measure", "log"), ("return_series_status", "unavailable")])
def test_unconfirmed_economic_returns_are_not_promoted(key, value):
    payload = chart()
    payload["return_series"]["metadata"][key] = value
    assert calculate(payload)["reason"] == "unconfirmed_economic_returns"


def test_confirmed_index_price_returns_and_crypto_are_allowed():
    payload = chart()
    payload["return_series"]["metadata"]["return_kind"] = "price_return"
    for kind in ["index", "crypto"]:
        assert instrument_tail_risk(payload, instrument_type=kind, today=TODAY)["var"] is not None


def test_breaks_and_multisession_intervals_block_quantile_without_deleting_samples():
    payload = chart()
    intervals = payload["return_series"]["intervals"]
    intervals[50].update(value=None, status="unavailable", reason="economic_segment_break")
    intervals[101]["start_date"] = intervals[100]["start_date"]
    intervals.pop(100)
    result = calculate(payload, lookback_days=365)
    assert result["excluded_interval_count"] == 2
    assert result["observation_count"] == 197
    assert result["missing_internal_count"] == 3
    assert result["status"] == "unavailable"
    assert result["var"] is None and result["expected_shortfall"] is None
    assert result["reason"] == "missing_daily_return_intervals"
    assert len(result["missing_intervals"]) == 3
    assert result["missing_return_dates"] == [intervals[50]["date"],
        (date.fromisoformat(intervals[100]["date"]) - timedelta(days=1)).isoformat(), intervals[100]["date"]]


def test_stale_end_keeps_requested_window_and_does_not_hide_trailing_gap():
    payload = chart()
    del payload["return_series"]["intervals"][-10:]
    result = calculate(payload, lookback_days=365)
    assert result["source_stale"]
    assert result["window_end_date"] == "2026-10-07"
    assert result["uncovered_trailing_count"] == 10
    assert result["latest_observation_date"] == "2026-09-27"
    assert result["status"] == "unavailable" and result["var"] is None
    assert result["missing_return_dates"][-1] == "2026-10-07"


def test_unavailable_latest_return_does_not_claim_current_risk():
    payload = chart()
    payload["return_series"]["intervals"][-1].update(status="unavailable", value=None)
    result = calculate(payload)
    assert result["source_stale"]
    assert result["latest_observation_date"] == "2026-10-06"
    assert result["uncovered_trailing_count"] == 1


@pytest.mark.parametrize("settings,kind", [({"release_lag_days": 1}, "public_fund"), ({"source_mode": "email"}, "private_fund")])
def test_expected_disclosure_lag_does_not_become_a_false_missing_tail(settings, kind):
    payload = chart()
    payload["return_series"]["intervals"].pop()
    result = instrument_tail_risk(payload, instrument_type=kind, source_settings=settings, today=TODAY)
    assert not result["source_stale"]
    assert result["latest_expected_date"] == "2026-10-06"
    assert result["window_end_date"] == "2026-10-06"
    assert result["uncovered_trailing_count"] == 0


@pytest.mark.parametrize("mutation", ["duplicate", "future", "nan", "bad_start", "below_total_loss", "missing_status"])
def test_bad_intervals_fail_closed(mutation):
    payload = chart()
    intervals = payload["return_series"]["intervals"]
    if mutation == "duplicate":
        intervals.append(deepcopy(intervals[-1]))
    elif mutation == "future":
        intervals[-1]["date"] = "2027-01-01"
    elif mutation == "nan":
        intervals[-1]["value"] = float("nan")
    elif mutation == "bad_start":
        intervals[-1]["start_date"] = "bad"
    elif mutation == "below_total_loss":
        intervals[-1]["value"] = -1.1
    else:
        del intervals[-1]["status"]
    result = calculate(payload)
    assert result["reason"] == "invalid_return_interval"
    assert result["var"] is None


def test_short_tail_and_unverified_calendar_unavailable():
    assert calculate(chart(99), confidence=.99)["reason"] == "less_than_one_tail_observation"
    assert calculate(chart(100), confidence=.99)["var"] is not None
    payload = chart()
    payload["return_series"]["frequency"]["gap_detection_basis"] = "calendar_day_threshold"
    assert calculate(payload)["reason"] == "unverified_daily_calendar"


def test_hk_adjacent_sessions_include_holiday_weekend_and_no_filled_returns():
    from watchlist_app.services.calculation_frequency import _market_calendar_sessions
    sessions = _market_calendar_sessions("XHKG", date(2026, 1, 1), date(2026, 10, 7))
    payload = chart()
    payload["return_series"]["frequency"]["gap_detection_basis"] = "market_calendar:XHKG"
    payload["return_series"]["intervals"] = [
        {"start_date": start.isoformat(), "date": end.isoformat(), "value": -.01, "status": "complete"}
        for start, end in zip(sessions, sessions[1:])]
    result = calculate(payload)
    assert result["observation_count"] == len(sessions) - 1
    assert result["excluded_interval_count"] == 0
    assert result["missing_internal_count"] == 0
    assert result["expected_shortfall"] == pytest.approx(.01)


def test_endpoint_reuses_canonical_chart_validates_identity_and_options(client, monkeypatch):
    from watchlist_app.api.routes import funds as api
    from .conftest import seed_shared_instrument
    seed_shared_instrument({"instrument_id": "risk-api", "instrument_name": "Risk", "instrument_type": "etf",
        "currency": "USD", "exchange_code": "XNYS", "identifiers": [{"identifier_type": "ticker", "identifier_value": "RISK-API", "is_primary": True}], "lifecycle_state": {"status": "active"}})
    assert client.post("/api/instruments/risk-api/resolve").status_code == 200
    monkeypatch.setattr(api, "get_instrument_chart_data", lambda *args: {**chart(), "instrument_id": "risk-api"})
    monkeypatch.setattr(api, "instrument_tail_risk", lambda payload, **kwargs: instrument_tail_risk(payload, today=TODAY, **kwargs))
    response = client.get("/api/instruments/risk-api/tail-risk?confidence=.99&lookback_days=365")
    assert response.status_code == 200
    assert response.json()["instrument_id"] == "risk-api"
    assert response.json()["confidence"] == .99
    assert response.json()["var"] is not None
    assert client.get("/api/instruments/risk-api/tail-risk?confidence=.9").status_code == 422
    assert client.get("/api/instruments/risk-api/tail-risk?lookback_days=20").status_code == 422
    assert client.get("/api/instruments/unknown/tail-risk").status_code == 404


@pytest.mark.parametrize('hour,minute,expected,available', [(7, 0, '2026-10-07', True), (8, 5, '2026-10-07', True), (8, 10, '2026-10-08', False), (15, 0, '2026-10-08', False)])
def test_hk_tail_uses_actual_closed_session_including_auction(hour, minute, expected, available):
    from datetime import UTC, datetime
    from watchlist_app.services.calculation_frequency import _market_calendar_sessions
    sessions = _market_calendar_sessions('XHKG', date(2026, 1, 1), date(2026, 10, 7))
    payload = chart()
    payload['return_series']['frequency']['gap_detection_basis'] = 'market_calendar:XHKG'
    payload['return_series']['intervals'] = [
        {'start_date': begin.isoformat(), 'date': end.isoformat(), 'value': -.01, 'status': 'complete'}
        for begin, end in zip(sessions, sessions[1:])]
    result = instrument_tail_risk(payload, instrument_type='etf', now=datetime(2026, 10, 8, hour, minute, tzinfo=UTC))
    assert result['latest_expected_date'] == expected
    assert (result['var'] is not None) == available
    assert result['source_stale'] != available
    if not available:
        assert result['missing_intervals'] == [{'start_date': '2026-10-07', 'end_date': '2026-10-08'}]
        assert result['reason'] == 'missing_daily_return_intervals'
