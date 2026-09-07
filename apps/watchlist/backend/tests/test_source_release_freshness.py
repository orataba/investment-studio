from datetime import UTC, date, datetime

import pytest

from watchlist_app.services.calculation_frequency import assess_latest_observation_freshness, build_calculation_frequency_context, source_calendar_date


@pytest.mark.parametrize("current,expected", [
    ("2026-07-25", "2026-07-23"),
    ("2026-07-26", "2026-07-23"),
    ("2026-07-27", "2026-07-23"),
    ("2026-07-28", "2026-07-24"),
    ("2026-02-24", "2026-02-12"),
    ("2026-02-25", "2026-02-13"),
])
def test_email_private_fund_release_lag_uses_actual_trading_sessions(current, expected):
    reading = assess_latest_observation_freshness(latest_observation_date=date.fromisoformat(expected), current_date=date.fromisoformat(current),
        resolved_frequency="daily", expected_frequency="daily", market_calendar="XSHG", source_mode="email", instrument_type="private_fund")
    assert reading["status"] == "fresh"
    assert reading["expected_latest_date"] == expected
    assert reading["release_lag_trading_days"] == 1


@pytest.mark.parametrize("mode,kind,lag,expected", [
    ("email", "private_fund", 0, "2026-07-27"),
    ("email", "private_fund", 2, "2026-07-23"),
    ("api", "private_fund", None, "2026-07-27"),
    ("api", "public_fund", None, "2026-07-27"),
    ("email", "equity", None, "2026-07-27"),
])
def test_declared_lag_wins_and_email_default_is_not_applied_to_other_sources(mode, kind, lag, expected):
    reading = assess_latest_observation_freshness(latest_observation_date=date(2026, 7, 24), current_date=date(2026, 7, 28),
        resolved_frequency="daily", expected_frequency="daily", market_calendar="XSHG", release_lag_days=lag, source_mode=mode, instrument_type=kind)
    assert reading["expected_latest_date"] == expected


def test_release_rule_does_not_hide_real_internal_gaps_or_create_missing_tail_sessions():
    latest = date(2026, 7, 23)
    observation = assess_latest_observation_freshness(latest_observation_date=latest, current_date=date(2026, 7, 27),
        resolved_frequency="daily", expected_frequency="daily", market_calendar="XSHG", release_lag_days=1)
    assert observation["status"] == "fresh"
    profile = build_calculation_frequency_context([
        {"as_of_date": date(2026, 7, 21), "value": 1}, {"as_of_date": latest, "value": 1.01},
    ], expected_frequency="daily", market_calendar="XSHG")["profile"]
    assert profile["gap_count"] == 1
    assert profile["missing_observation_date_sample"] == ["2026-07-22"]
    aligned = build_calculation_frequency_context([
        {"as_of_date": date(2026, 7, 22), "value": 1}, {"as_of_date": latest, "value": 1.01},
    ], expected_frequency="daily", market_calendar="XSHG")["profile"]
    assert aligned["gap_count"] == 0


def test_expectation_uses_market_local_date_at_the_utc_boundary():
    instant = datetime(2026, 7, 26, 16, 30, tzinfo=UTC)
    assert source_calendar_date(instant, "XSHG") == date(2026, 7, 27)
    assert source_calendar_date(instant, "XNYS") == date(2026, 7, 26)


def test_old_stale_projection_is_refreshed_once_when_corrected_schedule_is_fresh(monkeypatch):
    from watchlist_app.services import read_model_freshness as service
    monkeypatch.setattr(service, "_source_today", lambda _calendar: date(2026, 7, 27))
    shared = {"instrument_type": "private_fund", "source_settings": {"source_mode": "email", "expected_frequency": "daily", "market_calendar": "XSHG"}}
    assert service._freshness_needs_refresh(shared_instrument=shared, local_latest_date=date(2026, 7, 23), local_data_freshness_status="stale")
    assert not service._freshness_needs_refresh(shared_instrument=shared, local_latest_date=date(2026, 7, 23), local_data_freshness_status="fresh")
    assert not service._freshness_needs_refresh(shared_instrument=shared, local_latest_date=date(2026, 7, 22), local_data_freshness_status="stale")
