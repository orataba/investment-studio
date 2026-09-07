"""Investment arithmetic: a quote gap must never become a valuation/flow anchor."""
from copy import deepcopy
from datetime import date

import pytest

from portfolio_app.services import ledger, performance, return_chain
from portfolio_app.services.market_data import quote_is_stale

PORTFOLIO = dict(portfolio_id='strict-prices', base_currency='USD', inception_date='2026-08-03', as_of_date='2026-08-05')
ACCOUNTS = [dict(account_id='cash', account_type='deposit_account', account_category='cash', currency='USD'),
            dict(account_id='broker', account_type='securities_account', account_category='security', currency='USD', cost_basis_method='fifo', default_settlement_cash_account_id='cash')]
REF = dict(instrument_id='strict-stock', instrument_name='Strict Stock', instrument_type='equity', exchange_code='XNYS', currency='USD')


def transaction(n, kind, when, amount, quantity=None, price=None):
    return dict(transaction_id=f'strict-{n}', transaction_sequence=n, portfolio_id=PORTFOLIO['portfolio_id'],
                transaction_type=kind, trade_date=when, settlement_date=when,
                account_id='broker' if quantity is not None else 'cash',
                settlement_cash_account_id='cash' if quantity is not None else None,
                instrument_id=REF['instrument_id'] if quantity is not None else None,
                instrument_ref=REF if quantity is not None else None,
                quantity=quantity, price=price, gross_amount=amount, fees=0, taxes=0, currency='USD',
                created_at=when+'T12:00:00Z')


def detail(prices):
    return dict(**REF, source_settings={'expected_frequency':'daily', 'market_calendar':'XNYS'},
                quote_selection_policy={role:['close'] for role in ['valuation','trading','chart','total_return','reference']},
                market_data=[dict(metric_family='price', quote_basis='close', as_of_date=d, value=str(p), currency='USD',
                                  price_unit='per_unit', price_scale='1', status='complete') for d,p in prices.items()])


def source(monkeypatch, prices, fx=None):
    stock = detail(prices)
    payload = {'rates': []} if fx is None else {'rates':[dict(source_kind='direct', instrument_id='fx-usd-cny', base_currency='USD', quote_currency='CNY')]}
    for module in (performance, ledger):
        monkeypatch.setattr(module, 'get_shared_fx_rates', lambda: payload)
        monkeypatch.setattr(module, 'get_registry_instrument_detail', lambda key: fx if key == 'fx-usd-cny' else stock)
        monkeypatch.setattr(module, 'list_registry_corporate_actions', lambda *a, **kw: [])
    monkeypatch.setattr(performance, 'instrument_event_task_quality_warnings', lambda *a, **kw: [])
    return stock


def funded_facts():
    return [transaction(1,'deposit','2026-08-03',1000), transaction(2,'buy','2026-08-03',1000,10,100),
            transaction(3,'deposit','2026-08-05',1000)]


def test_gap_stops_before_cash_flow_and_only_backfill_restores_15_238_percent(monkeypatch):
    source(monkeypatch, {'2026-08-03':100, '2026-08-05':120})
    snapshots = performance.build_daily_portfolio_snapshots(PORTFOLIO, ACCOUNTS, funded_facts())
    assert [s['as_of_date'] for s in snapshots] == [date(2026,8,3), date(2026,8,4)]
    assert snapshots[-1]['nav'] is None
    assert 'strict-stock valuation price' in snapshots[-1]['valuation_blocked_reason']
    summary = performance.build_portfolio_performance_report_from_snapshots(PORTFOLIO, snapshots, transactions=funded_facts())['summary']
    assert summary['effective_end_date'] == date(2026,8,3)
    assert summary['end_nav'] == 1000
    assert summary['as_of_clamp_reason'] == 'required_market_data_missing'
    source(monkeypatch, {'2026-08-03':100, '2026-08-04':110, '2026-08-05':120})
    snapshots = performance.build_daily_portfolio_snapshots(PORTFOLIO, ACCOUNTS, funded_facts())
    assert snapshots[-1]['nav'] == 2200
    assert snapshots[-1]['total_pnl'] == 200
    # Hand calculation: 10% before the contribution, then 100 / (1100+1000).
    assert snapshots[-1]['cumulative_twr'] == pytest.approx(1.1 * (2200/2100) - 1)


def test_liquidation_day_still_requires_held_security_observation(monkeypatch):
    source(monkeypatch, {'2026-08-03':100, '2026-08-05':120})
    facts = funded_facts()[:2] + [transaction(3,'sell','2026-08-04',1100,10,110)]
    snapshots = performance.build_daily_portfolio_snapshots(PORTFOLIO, ACCOUNTS, facts)
    assert snapshots[-1]['as_of_date'] == date(2026,8,4)
    assert snapshots[-1]['nav'] is None


def test_confirmed_weekend_and_labor_day_are_not_missing_sessions(monkeypatch):
    source(monkeypatch, {'2026-09-04':100, '2026-09-08':110})
    portfolio = {**PORTFOLIO, 'inception_date':'2026-09-04', 'as_of_date':'2026-09-08'}
    facts = [transaction(1,'deposit','2026-09-04',1000), transaction(2,'buy','2026-09-04',1000,10,100)]
    snapshots = performance.build_daily_portfolio_snapshots(portfolio, ACCOUNTS, facts)
    assert len(snapshots) == 5
    assert all(not s['stale_price_flag'] for s in snapshots)
    assert [s['nav'] for s in snapshots] == [1000,1000,1000,1000,1100]
    assert snapshots[-1]['cumulative_twr'] == pytest.approx(.1)
    assert quote_is_stale(detail({}), point_date=date(2026,9,4), as_of_date=date(2026,9,8))


def test_shenzhen_calendar_recognizes_qingming_closure():
    shenzhen = {'source_settings': {'market_calendar':'XSHE'}}
    assert not quote_is_stale(shenzhen, point_date=date(2026,4,3), as_of_date=date(2026,4,6))
    assert quote_is_stale(shenzhen, point_date=date(2026,4,3), as_of_date=date(2026,4,7))


def test_foreign_cash_stops_on_missing_fx_and_reliable_window_rejects_stale_fx(monkeypatch):
    fx = dict(instrument_id='fx-usd-cny', instrument_type='fx', currency='CNY',
              source_settings={'expected_frequency':'daily', 'market_calendar':'24/5'},
              market_data=[dict(metric_family='fx',quote_basis='spot',as_of_date='2026-08-03',value='7',currency='CNY',price_unit='rate',price_scale='1',status='complete')])
    source(monkeypatch, {}, fx=fx)
    portfolio = {**PORTFOLIO, 'base_currency':'CNY'}
    snapshots = performance.build_daily_portfolio_snapshots(portfolio, ACCOUNTS, [transaction(1,'opening_balance','2026-08-03',1000)])
    assert snapshots[0]['nav'] == 7000
    assert snapshots[-1]['as_of_date'] == date(2026,8,4)
    assert snapshots[-1]['nav'] is None
    assert 'FX USD/CNY' in snapshots[-1]['valuation_blocked_reason']
    stale = {**snapshots[0], 'as_of_date':date(2026,8,4), 'stale_fx_flag':True}
    later = {**snapshots[0], 'as_of_date':date(2026,8,5)}
    window = return_chain.resolve_reliable_snapshot_window([snapshots[0], stale, later])
    assert window['effective_end_date'] == date(2026,8,3)


def test_later_requested_period_cannot_restart_after_unfilled_gap(monkeypatch):
    source(monkeypatch, {'2026-08-03':100, '2026-08-05':120})
    for builder in (performance.build_portfolio_performance_report, performance.build_period_calculation_report, performance.build_contribution_report):
        report = builder(PORTFOLIO, ACCOUNTS, funded_facts(), start_date=date(2026,8,5))
        assert report['summary']['coverage_state'] == 'unavailable'
        assert report['summary']['as_of_clamp_reason'] == 'required_market_data_missing'
        assert report['summary'].get('end_nav') is None
        assert report['summary'].get('final_value') is None


def test_materialized_gap_removes_published_suffix_and_resumes_after_backfill(monkeypatch, client):
    from portfolio_app.db.models import PortfolioCalculationStateModel, PortfolioDailySnapshotModel, PortfolioDailyContributionSliceModel, PortfolioDailyHoldingSnapshotModel
    from portfolio_app.db.session import get_session_factory
    from portfolio_app.services import daily_snapshots, portfolio_store
    from sqlalchemy import select

    portfolio = {**PORTFOLIO, 'portfolio_name':'Strict prices', 'valuation_timezone':'America/New_York', 'valuation_cutoff_policy':'latest_complete_eod', 'sort_order':0}
    accounts = [{**a, 'portfolio_id':PORTFOLIO['portfolio_id'], 'account_name':a['account_id'], 'institution':'Scenario', 'opened_at':'2026-08-03', 'status':'active'} for a in ACCOUNTS]
    portfolio_store.reset_store({'portfolios':[portfolio], 'accounts':accounts, 'transactions':funded_facts(), 'taxonomies':[], 'taxonomy_nodes':[], 'taxonomy_assignments':[]})
    source(monkeypatch, {'2026-08-03':100, '2026-08-04':110, '2026-08-05':120})
    pid = PORTFOLIO['portfolio_id']
    daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(pid, end_date=date(2026,8,5))
    source(monkeypatch, {'2026-08-03':100, '2026-08-05':120})
    daily_snapshots.mark_portfolio_daily_snapshots_stale(pid, dirty_from=date(2026,8,4))
    daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(pid, end_date=date(2026,8,5))
    with get_session_factory()() as session:
        state = session.get(PortfolioCalculationStateModel, pid)
        assert state.daily_snapshot_status == 'failed'
        assert state.dirty_from == date(2026,8,4)
        assert state.refreshed_to == date(2026,8,3)
        assert 'strict-stock' in state.error_message
        for model in (PortfolioDailySnapshotModel, PortfolioDailyContributionSliceModel, PortfolioDailyHoldingSnapshotModel):
            assert session.scalars(select(model).where(model.portfolio_id == pid, model.as_of_date >= date(2026,8,5))).all() == []
    # Reads consume the valid prefix without retrying the same deterministic gap.
    with monkeypatch.context() as context:
        context.setattr(daily_snapshots, '_run_portfolio_daily_snapshot_recalculation_synchronously', lambda *a, **kw: pytest.fail('unchanged missing data retried'))
        summary = daily_snapshots.build_materialized_performance_report(pid)['summary']
        assert summary['effective_end_date'] == date(2026,8,3)
        for suffix in ('performance', 'performance/calculation', 'performance/contribution', 'performance/calculation/groups'):
            response = client.get(f'/api/portfolios/{pid}/{suffix}', params={'start_date':'2026-08-05','end_date':'2026-08-05'})
            assert response.status_code == 200, (suffix, response.text)
            assert response.json()['summary']['coverage_state'] == 'unavailable'
            if suffix == 'performance/calculation/groups':
                assert response.json()['groups'] == []
                for field in ('total_initial_value', 'total_final_value', 'total_pnl'):
                    assert response.json()['summary'][field] is None
    source(monkeypatch, {'2026-08-03':100, '2026-08-04':110, '2026-08-05':120})
    daily_snapshots.mark_portfolio_daily_snapshots_stale(pid, dirty_from=date(2026,8,4))
    daily_snapshots._run_portfolio_daily_snapshot_recalculation_synchronously(pid, end_date=date(2026,8,5))
    restored = daily_snapshots.build_materialized_performance_report(pid)['summary']
    assert restored['end_nav'] == 2200
    assert restored['total_pnl'] == 200
    assert restored['cumulative_twr'] == pytest.approx(1.1 * (2200/2100) - 1)
    with get_session_factory()() as session:
        assert session.get(PortfolioCalculationStateModel, pid).daily_snapshot_status == 'current'
