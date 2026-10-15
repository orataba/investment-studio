"""Estimate scope projections preserve identity and complete PIT comparisons."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from investment_studio_instrument_core.db_models import Instrument
from watchlist_app.db.session import get_engine, get_session_factory
from .conftest import canonical_quote_policy, seed_shared_instrument
from .test_sector_market_data import market_store, estimate_reader, register_company_source


def register(iid, kind='equity', identifiers=None):
    seed_shared_instrument({'instrument_id': iid, 'instrument_name': iid,
        'instrument_type': kind, 'currency': 'USD',
        'exchange_code': 'XNYS' if kind in {'equity', 'etf'} else None,
        'quote_selection_policy': canonical_quote_policy(kind),
        'identifiers': identifiers or [{'identifier_type': 'ticker', 'identifier_value': iid, 'is_primary': True}]})


def legacy_scope(record):
    if record is None or record.get('instrument_type') not in {'equity', 'etf', 'public_fund'}:
        return None
    symbol = next((str(row['identifier_value']).removeprefix('fmp:') for row in record.get('identifiers', [])
        if row.get('identifier_type') == 'provider_symbol' and str(row.get('identifier_value', '')).startswith('fmp:')), None)
    if not symbol:
        return None
    return {'symbol': symbol.upper(), 'instrument_type': record['instrument_type'], 'name': record.get('instrument_name')}


def test_estimate_scope_matches_full_registry_identity_order_and_aliases_without_histories(client):
    from watchlist_app.services.sector_estimates import estimate_scope
    from watchlist_app.services.shared_instrument_registry import get_shared_instrument
    register('scope-target', identifiers=[
        {'identifier_type': 'provider_symbol', 'identifier_value': 'fmp:ZZZ', 'is_primary': False},
        {'identifier_type': 'ticker', 'identifier_value': 'scope-target', 'is_primary': True},
        {'identifier_type': 'provider_symbol', 'identifier_value': 'fmp:aaa', 'is_primary': False}])
    for iid in ('scope-alias', 'scope-chain', 'scope-missing-target', 'scope-cycle-a', 'scope-cycle-b', 'scope-archived', 'scope-no-source'):
        register(iid)
    register('scope-index', 'index', [{'identifier_type': 'provider_symbol', 'identifier_value': 'fmp:INDEX', 'is_primary': True}])
    with get_session_factory()() as session:
        for iid, target in [('scope-alias', 'scope-target'), ('scope-chain', 'scope-alias'),
                            ('scope-missing-target', 'absent'), ('scope-cycle-a', 'scope-cycle-b'),
                            ('scope-cycle-b', 'scope-cycle-a'), ('scope-archived', None)]:
            session.get(Instrument, iid).lifecycle_state_json = {'status': 'archived', 'canonical_instrument_id': target}
        session.commit()
    ids = ('scope-target', 'scope-alias', 'scope-chain', 'scope-missing-target', 'scope-cycle-a',
           'scope-archived', 'scope-no-source', 'scope-index', 'absent', ' scope-target ')
    expected = {iid: legacy_scope(get_shared_instrument(iid)) for iid in ids}
    statements = []
    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
    event.listen(get_engine(), 'before_cursor_execute', record_sql)
    try:
        with get_session_factory()() as session:
            actual = {iid: estimate_scope(session, iid) for iid in ids}
        assert actual == expected
        assert actual['scope-target'] == {'symbol': 'ZZZ', 'instrument_type': 'equity', 'name': 'scope-target'}
        assert actual['scope-chain'] == actual['scope-target']
        assert statements  # The identity reads happened, without unrelated rows.
        assert not any(name in query for query in statements for name in (
            'instrument_market_data', 'corporate_action', 'fund_nav_'))
    finally:
        event.remove(get_engine(), 'before_cursor_execute', record_sql)
    assert estimate_scope(None, 'scope-target') is None


def test_metadata_identity_preserves_registry_failure_contract(client, monkeypatch):
    from watchlist_app.services import shared_instrument_registry as registry
    from watchlist_app.services.sector_estimates import estimate_scope
    failure = RuntimeError('private failure detail')
    def broken(*args, **kwargs):
        raise failure
    monkeypatch.setattr(registry.shared_store, 'get_instrument_metadata', broken)
    with get_session_factory()() as session, pytest.raises(registry.SharedInstrumentRegistryTransportError) as caught:
        estimate_scope(session, 'scope-target')
    assert str(caught.value) == 'Failed to query shared instrument registry.'
    assert caught.value.__cause__ is failure


def clock(day):
    return datetime(2026, 9, day, 12, tzinfo=UTC)


def full_market_reader(session, ticker, *, scope_only=False, **kwargs):
    """Reference projection from the unchanged complete market-data reader."""
    from watchlist_app.services import sector_market_data as market
    return market.read_sector_market_data(session, ticker, **kwargs)


@pytest.mark.parametrize('since', [None, clock(5)])
def test_complete_estimate_output_matches_full_market_path(market_store, estimate_reader, monkeypatch, since):
    from watchlist_app.services import sector_estimates as estimates
    store, ingest = market_store
    # Duplicate issuer exposures retain their original sorting/last-wins rule;
    # cash, funds, futures and uncertain profiles never become company EPS.
    ingest('etf_holdings', [
        {'etf_symbol': 'XLK', 'holding_key': key, 'holding_symbol': symbol,
         'holding_name': name, 'weight_percent': weight,
         'snapshot_date': '2026-09-06', 'report_date': '2026-08-31'}
        for key, symbol, name, weight in [
            ('AAA', 'AAA', 'Alpha', 70), ('AAA-2', 'AAA', 'Alpha secondary', 10),
            ('BBB', 'BBB', 'Unclassified company', 5), ('FUND', 'FUND', 'Fund', 5),
            ('IXZH6', 'IXZH6', 'Index futures', 5), ('cash', '', 'US DOLLAR', 5)]
    ], clock(6).isoformat())
    ingest('company_profiles', [{'symbol': 'FUND', 'company_name': 'Fund', 'is_etf': True, 'is_fund': False},
                                {'symbol': 'BBB', 'company_name': 'Unclassified company', 'is_etf': False}])
    income = {'symbol': 'AAA', 'statement_type': 'income', 'period_end': '2026-06-30',
              'fiscal_year': 2026, 'fiscal_period': 'Q2', 'reported_currency': 'USD'}
    ingest('financial_statements', [income], clock(4).isoformat())
    ingest('financial_statements', [{**income, 'reported_currency': 'EUR'}], clock(6).isoformat())
    ingest('financial_statements', [{**income, 'reported_currency': 'JPY',
                                    'available_at': clock(10).isoformat()}], clock(7).isoformat())
    for day, value in [(6, 110), (8, 100)]:
        ingest('analyst_estimates', [{'symbol': 'AAA', 'estimate_period': frequency,
            'target_period_end': '2026-12-31', 'revenue_avg': value, 'eps_avg': 2,
            'num_analysts_revenue': 10, 'num_analysts_eps': 9} for frequency in ['annual', 'quarter']],
            clock(day).isoformat())
    store.ingest('analyst_estimates', [[]], source='fixture', observed_at=clock(7),
        details={'snapshot_scopes': [{'symbol': 'AAA', 'frequency': 'annual', 'snapshot_at': clock(7).isoformat()}]})
    with monkeypatch.context() as full:
        full.setattr(estimates, 'read_sector_market_data', full_market_reader)
        expected = estimate_reader(as_of=clock(9), since=since)
    query, calls = store.query, []
    def counted(dataset, **kwargs):
        calls.append((dataset, kwargs))
        return query(dataset, **kwargs)
    monkeypatch.setattr(store, 'query', counted)
    actual = estimate_reader(as_of=clock(9), since=since)
    assert actual == expected
    assert actual['status'] == 'limited'
    assert actual['source_ids']
    assert actual['current_snapshot']['holdings_as_of'] == '2026-08-31'
    assert actual['current_snapshot']['holdings_observed_on'] == '2026-09-06'
    assert not any(dataset == 'us_eod_daily' for dataset, _ in calls)
    assert all(kwargs.get('observed_at') for dataset, kwargs in calls if dataset == 'analyst_estimates')
    financial = [kwargs for dataset, kwargs in calls if dataset == 'financial_statements']
    assert len(financial) == 1 and financial[0]['symbols'] == ['AAA'] and financial[0]['versions']


@pytest.mark.parametrize('capture_kind, expected_status', [
    ('absent', 'no_snapshot'), ('rows', 'baseline'),
    ('empty_after_rows', 'no_snapshot'), ('empty_only', 'no_snapshot')])
def test_issuer_without_profile_keeps_visible_snapshot_existence(
        market_store, estimate_reader, monkeypatch, capture_kind, expected_status):
    from watchlist_app.services import sector_estimates as estimates
    store, ingest = market_store
    register_company_source('issuer', 'equity', 'ISSUER')
    if capture_kind in {'rows', 'empty_after_rows'}:
        ingest('analyst_estimates', [{'symbol': 'ISSUER', 'estimate_period': 'annual',
            'target_period_end': '2026-12-31', 'revenue_avg': 100, 'currency': 'USD'}], clock(5).isoformat())
    if capture_kind in {'empty_after_rows', 'empty_only'}:
        store.ingest('analyst_estimates', [[]], source='fixture', observed_at=clock(6),
            details={'snapshot_scopes': [{'symbol': 'ISSUER', 'frequency': 'annual', 'snapshot_at': clock(6).isoformat()}]})
    with monkeypatch.context() as full:
        full.setattr(estimates, 'read_sector_market_data', full_market_reader)
        expected = estimate_reader('issuer', as_of=clock(7))
    actual = estimate_reader('issuer', as_of=clock(7))
    assert actual == expected
    assert actual['status'] == expected_status
