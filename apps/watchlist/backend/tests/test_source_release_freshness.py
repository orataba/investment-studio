from datetime import UTC, date, datetime

import pytest

from watchlist_app.services.calculation_frequency import assess_latest_observation_freshness, build_calculation_frequency_context, source_calendar_date


@pytest.mark.parametrize("calendar", ["XNYS", "XSHG", "XHKG"])
def test_older_history_does_not_silently_skip_missing_sessions_outside_default_calendar_window(calendar):
    profile = build_calculation_frequency_context([
        {"as_of_date": date(2001, 1, 2), "value": 100},
        {"as_of_date": date(2001, 3, 1), "value": 105},
    ], market_calendar=calendar)["profile"]
    assert profile["gap_detection_basis"] == f"market_calendar:{calendar}"
    assert profile["gap_count"] > 20
    assert "2001-01-03" in profile["missing_observation_date_sample"]


def test_older_continuous_sessions_remain_valid_when_calendar_is_expanded():
    profile = build_calculation_frequency_context([
        {"as_of_date": date(2001, 1, day), "value": 100 + day}
        for day in (5, 8, 9)
    ], market_calendar="XNYS")["profile"]
    assert profile["gap_count"] == 0  # January 6 and 7 were a weekend.


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


@pytest.mark.parametrize('confirmed,expected', [
    ((), 'stale'),
    ((date(2026, 10, 2),), 'fresh'),
    ((date(2026, 10, 1),), 'stale'),  # A holiday cannot replace the missing session.
])
def test_official_no_trade_evidence_changes_freshness_without_changing_observation_date(confirmed, expected):
    reading = assess_latest_observation_freshness(latest_observation_date=date(2026, 9, 30),
        current_date=date(2026, 10, 4), resolved_frequency='daily', market_calendar='XHKG',
        confirmed_no_trade_dates=confirmed)
    assert reading['status'] == expected
    assert reading['expected_latest_date'] == '2026-10-02'
    assert reading['lag_days'] == 4


def test_proof_for_latest_session_does_not_cover_an_earlier_missing_session():
    reading = assess_latest_observation_freshness(latest_observation_date=date(2026, 9, 30),
        current_date=date(2026, 10, 6), resolved_frequency='daily', market_calendar='XHKG',
        confirmed_no_trade_dates=(date(2026, 10, 5),))
    assert reading['status'] == 'stale'


def test_retained_no_trade_evidence_is_currency_price_and_information_time_specific(client, monkeypatch):
    from watchlist_app.services import sector_market_data, shared_instrument_registry, read_model_freshness
    from studio_market.numeric.providers.hkex import daily_quotations_url
    store = sector_market_data.numeric_store()
    captured = datetime(2026, 10, 3, 8, tzinfo=UTC)
    day = date(2026, 10, 2)
    store.ingest('hkex_security_sessions', [[dict(symbol='2100.HK', date=day, currency='HKD',
        session_status='no_trade', official_close='0.265', source_url=daily_quotations_url(day))]],
        source='hkex', observed_at=captured, raw_ref='numeric/raw/hkex/evidence.gz')
    instrument = {'instrument_type': 'equity', 'exchange_code': 'XHKG', 'currency': 'HKD',
        'source_settings': {'market_calendar': 'XHKG', 'source_price_multiplier': '100'},
        'identifiers': [{'identifier_type': 'provider_symbol', 'identifier_value': 'fmp:2100.HK'}],
        'market_data': [{'metric_family': 'price', 'quote_basis': 'close', 'as_of_date': '2026-09-30',
                         'value': '26.5', 'currency': 'HKD', 'status': 'complete'}]}
    original = shared_instrument_registry.get_shared_no_trade_evidence
    def proof(value, **kw):
        return original(value, latest_date=kw['latest_date'], as_of=captured)
    rows = proof(instrument, latest_date=date(2026, 9, 30))
    assert len(rows) == 1 and rows[0]['date'] == '2026-10-02'
    from types import SimpleNamespace
    from watchlist_app.services.canonical_recalc import CanonicalRecalcService
    payload = CanonicalRecalcService()._summary_payload(
        instrument=SimpleNamespace(instrument_id='2100-hk', instrument_name='BAIOO',
            instrument_type='equity', primary_identifier_value='2100.HK', metadata_json={}),
        nav_selection={'points': [{'as_of_date': date(2026, 9, 30), 'value': .265, 'currency': 'HKD'}],
                       'nav_basis_type': 'adjusted_close', 'nav_basis_source': 'provider', 'nav_basis_status': 'complete'},
        performance_snapshot=None, risk_snapshot=None, exposure_snapshot=None, attributes={},
        taxonomy_context={}, source_cutoff_at=captured, now=captured,
        source_settings=instrument['source_settings'], no_trade_evidence=rows)
    assert payload['freshness']['data_freshness_status'] == 'fresh'
    assert payload['freshness']['latest_observation_date'] == '2026-09-30'
    assert payload['freshness']['no_trade_evidence'][0]['source_id'] == rows[0]['source_id']
    assert original(instrument, latest_date=date(2026, 9, 30),
        as_of=datetime(2026, 10, 2, 10, tzinfo=UTC)) == []
    monkeypatch.setattr(read_model_freshness, 'get_shared_no_trade_evidence', proof)
    monkeypatch.setattr(read_model_freshness, '_source_today', lambda _calendar: date(2026, 10, 3))
    assert read_model_freshness._freshness_needs_refresh(shared_instrument=instrument,
        local_latest_date=date(2026, 9, 30), local_data_freshness_status='stale')
    assert not read_model_freshness._freshness_needs_refresh(shared_instrument=instrument,
        local_latest_date=date(2026, 9, 30), local_data_freshness_status='fresh')
    instrument['market_data'][0]['value'] = '27'
    assert proof(instrument, latest_date=date(2026, 9, 30)) == []
    instrument['market_data'][0]['value'] = '26.5'
    instrument['currency'] = 'USD'
    assert proof(instrument, latest_date=date(2026, 9, 30)) == []
    instrument['currency'] = 'HKD'
    store.ingest('dividends', [], source='fixture', observed_at=captured, details={'observed_at': captured,
        'price_revision_requests': [{'symbol': '2100.HK', 'effective_date': '2026-10-02'}]})
    assert proof(instrument, latest_date=date(2026, 9, 30)) == []


def test_no_trade_proof_respects_identity_isolation_at_its_information_time(client):
    from watchlist_app.services import sector_market_data, shared_instrument_registry
    from studio_market.numeric.providers.hkex import daily_quotations_url
    from studio_market.numeric.price_identities import record_price_identities
    store = sector_market_data.numeric_store()
    captured = datetime(2026, 10, 3, 8, tzinfo=UTC)
    blocked_at = datetime(2026, 10, 3, 9, tzinfo=UTC)
    day = date(2026, 10, 2)
    store.ingest('hkex_security_sessions', [[dict(symbol='2100.HK', date=day, currency='HKD',
        session_status='no_trade', official_close='0.265', source_url=daily_quotations_url(day))]],
        source='hkex', observed_at=captured, raw_ref='numeric/raw/hkex/evidence.gz')
    instrument = {'exchange_code': 'XHKG', 'currency': 'HKD',
        'identifiers': [{'identifier_type': 'provider_symbol', 'identifier_value': 'fmp:2100.HK'}],
        'market_data': [{'metric_family': 'price', 'quote_basis': 'close', 'as_of_date': '2026-09-30',
                         'value': '0.265', 'currency': 'HKD', 'status': 'complete'}]}
    record_price_identities(store, [dict(symbol='2100.HK', status='blocked', reason='Identity requires review',
        source_refs=[daily_quotations_url(day)])], apply=True, observed_at=blocked_at)
    proof = shared_instrument_registry.get_shared_no_trade_evidence
    assert len(proof(instrument, latest_date=date(2026, 9, 30), as_of=captured)) == 1
    assert proof(instrument, latest_date=date(2026, 9, 30), as_of=blocked_at) == []
