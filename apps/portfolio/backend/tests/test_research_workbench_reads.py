from datetime import date
from collections import OrderedDict
from copy import deepcopy

import pytest

from portfolio_app.services import instrument_registry, ledger, market_data, performance, research, research_solver, valuation_fx
from .test_research_api import _create_planning_taxonomy
from .test_transaction_cash_fx_reads import fx_detail
from .test_initial_purchase_valuation import facts, setup_source
from .test_strict_market_valuation import ACCOUNTS, PORTFOLIO, REF
from portfolio_app.services import source_cache, workspace_cache
from investment_studio_instrument_core.fx_contract import fx_instrument_identity
from investment_studio_instrument_core.fx_rates import latest_spot_point_from_instrument


def test_research_context_batches_details_and_reuses_actual_fx_and_quote_indexes(client, monkeypatch):
    taxonomy_id, _ = _create_planning_taxonomy(client)
    research._run_portfolio_daily_snapshot_recalculation_synchronously('investment-studio')
    arguments = dict(planning_taxonomy_id=taxonomy_id, as_of_date=date(2026, 4, 15), lookback_days=30)
    expected = research._build_research_context(
        'investment-studio', **arguments, instrument_detail_cache={},
        direct_fx_instruments=valuation_fx.fx_direct_instrument_map(instrument_registry.get_shared_fx_rates()),
    )
    fx_calls, detail_batches, inputs, indexed_series = [], [], [], []
    load_detail = research.get_registry_instrument_detail
    load_details = research.get_registry_instrument_details
    resolve_series = market_data.resolve_quote_series

    def detail(key):
        assert key.startswith('fx-'), 'Securities must retain the batched read.'
        fx_calls.append(key)
        return load_detail(key)

    def details(ids):
        detail_batches.append(set(ids))
        return load_details(ids)

    def redundant_fx():
        raise AssertionError('Context financial views must not read the global FX catalog.')

    def index(detail, **kwargs):
        indexed_series.append((id(detail), tuple(kwargs['candidate_bases'])))
        return resolve_series(detail, **kwargs)

    def record_inputs(fn):
        def wrapped(*args, **kwargs):
            inputs.append((kwargs['instrument_detail_cache'], kwargs['direct_fx_instruments']))
            return fn(*args, **kwargs)
        return wrapped

    monkeypatch.setattr(research, 'get_registry_instrument_detail', detail)
    monkeypatch.setattr(research, 'get_registry_instrument_details', details)
    monkeypatch.setattr(performance, 'get_shared_fx_rates', redundant_fx)
    monkeypatch.setattr(ledger, 'get_shared_fx_rates', redundant_fx)
    monkeypatch.setattr(research, 'build_holdings_report', record_inputs(research.build_holdings_report))
    monkeypatch.setattr(research, 'build_account_workspace', record_inputs(research.build_account_workspace))
    monkeypatch.setattr(market_data, 'resolve_quote_series', index)
    context = research._build_research_context('investment-studio', **arguments)
    assert context == expected
    assert context['as_of_date'] == '2026-04-15'
    assert fx_calls and len(fx_calls) == len(set(fx_calls))
    assert inputs[0][0] is inputs[1][0]
    assert inputs[0][1] is inputs[1][1]
    assert isinstance(inputs[0][0], valuation_fx.HistoricalInstrumentDetails)
    assert isinstance(inputs[0][1], valuation_fx.HistoricalFxInstruments)
    # Successful quote series are validated once across holdings and accounts.
    assert indexed_series and len(indexed_series) == len(set(indexed_series))
    assert len(detail_batches) == 1
    transaction_ids = {row['instrument_id'] for row in research.list_transactions('investment-studio')
                       if row.get('instrument_id')}
    assert transaction_ids <= detail_batches[0]


def _cash_context(monkeypatch, *, cash_currency, base_currency):
    portfolio = {'portfolio_id': 'cash', 'portfolio_name': 'Cash', 'base_currency': base_currency}
    accounts = [{'account_id': 'cash', 'account_name': 'Cash', 'account_type': 'deposit_account',
                 'currency': cash_currency}]
    transactions = [
        {'transaction_id': key, 'portfolio_id': 'cash', 'account_id': 'cash',
         'transaction_type': kind, 'currency': cash_currency, 'gross_amount': amount,
         'trade_date': f'2026-08-0{day}', 'trade_at': f'2026-08-0{day}T09:00:00Z',
         'settlement_date': settlement, 'created_at': f'2026-08-0{day}T10:00:00Z',
         'fees': 0, 'taxes': 0, 'transaction_sequence': index}
        for index, (key, kind, amount, day, settlement) in enumerate([
            ('opening', 'opening_balance', 100, 3, '2026-08-03'),
            ('pending', 'interest', 10, 4, '2026-08-06'),
            ('withdraw', 'withdrawal', 20, 5, '2026-08-05'),
            ('future', 'withdrawal', 10, 6, '2026-08-06'),
        ], 1)
    ]
    monkeypatch.setattr(research, 'get_portfolio', lambda _pid: deepcopy(portfolio))
    monkeypatch.setattr(research, 'list_accounts', lambda _pid: deepcopy(accounts))
    monkeypatch.setattr(research, 'list_transactions', lambda _pid: deepcopy(transactions))
    monkeypatch.setattr(research, 'get_cached_materialized_performance_report', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ledger, 'list_registry_corporate_actions', lambda *_args, **_kwargs: [])
    return dict(planning_taxonomy_id=None, as_of_date=date(2026, 8, 5), lookback_days=30)


@pytest.mark.parametrize('pair', [('USD', 'HKD'), ('HKD', 'USD'), ('HKD', 'CNY')])
@pytest.mark.parametrize('future_fault', [False, True])
def test_research_actual_fx_paths_preserve_catalog_eligibility_and_cash_dates(monkeypatch, pair, future_fault):
    args = _cash_context(monkeypatch, cash_currency=pair[0], base_currency=pair[1])
    details = {
        'fx-usd-hkd': fx_detail('fx-usd-hkd', 'HKD', {3: 7.8, 4: 7.9, 5: 8.0, 6: 8.1}),
        'fx-usd-cny': fx_detail('fx-usd-cny', 'CNY', {3: 7.1, 4: 7.2, 5: 7.3, 6: 7.4}),
    }
    if future_fault:
        details['fx-usd-hkd']['market_data'][-1]['currency'] = 'EUR'
    original = deepcopy(details)
    eligible = {}
    for key, detail in details.items():
        if latest_spot_point_from_instrument(detail, detail['market_data']) is not None:
            identity = fx_instrument_identity(key)
            eligible[(identity.base_currency, identity.quote_currency)] = key
    for module in (research, ledger, performance):
        monkeypatch.setattr(module, 'get_registry_instrument_detail', lambda key: deepcopy(details.get(key)))
    expected = research._build_research_context(
        'cash', **args, direct_fx_instruments=eligible, instrument_detail_cache={},
    )
    actual = research._build_research_context('cash', **args)
    assert actual == expected
    if future_fault:
        assert actual['nav'] is None
    else:
        assert actual['nav'] is not None
    assert details == original


def test_research_preserves_empty_injected_maps_and_does_not_load_same_currency_fx(monkeypatch):
    args = _cash_context(monkeypatch, cash_currency='USD', base_currency='USD')
    def no_fx(*_args, **_kwargs):
        pytest.fail('No FX input is needed for same-currency cash.')
    monkeypatch.setattr(research, 'get_registry_instrument_detail', no_fx)
    expected = research._build_research_context('cash', **args)
    cache, fx_map = {}, {}
    original = research.build_holdings_report
    def holdings(*args, **kwargs):
        assert kwargs['instrument_detail_cache'] is cache
        assert kwargs['direct_fx_instruments'] is fx_map
        return original(*args, **kwargs)
    monkeypatch.setattr(research, 'build_holdings_report', holdings)
    assert research._build_research_context(
        'cash', **args, instrument_detail_cache=cache, direct_fx_instruments=fx_map,
    ) == expected
    assert expected['nav'] == 90


def test_research_fx_loader_errors_propagate_without_fallback(monkeypatch):
    args = _cash_context(monkeypatch, cash_currency='USD', base_currency='HKD')
    error = OSError('FX source is unavailable')
    def broken(_key):
        raise error
    monkeypatch.setattr(research, 'get_registry_instrument_detail', broken)
    with pytest.raises(OSError) as captured:
        research._build_research_context('cash', **args)
    assert captured.value is error


@pytest.mark.parametrize('source_case', [
    'clean', 'invalid_prior', 'invalid_future', 'duplicate', 'initial_price', 'prohibited_valuation',
])
def test_indexed_boundary_and_account_outputs_match_explicit_plain_inputs(monkeypatch, source_case):
    stock = setup_source(monkeypatch, {'2026-08-03': 100, '2026-08-04': 110, '2026-08-05': 120})
    as_of = date(2026, 8, 4)
    if source_case == 'invalid_prior':
        stock['market_data'][0]['currency'] = 'EUR'
    elif source_case == 'invalid_future':
        stock['market_data'][-1]['currency'] = 'EUR'
    elif source_case == 'duplicate':
        stock['market_data'].append(deepcopy(stock['market_data'][0]))
    elif source_case == 'initial_price':
        stock['market_data'] = stock['market_data'][1:]
        as_of = date(2026, 8, 3)
    elif source_case == 'prohibited_valuation':
        stock['quote_selection_policy']['valuation'] = ['adjusted_close']
    original = deepcopy(stock)

    def views(cache):
        cache[REF['instrument_id']] = deepcopy(stock)
        holding = performance.build_holdings_report(
            PORTFOLIO, ACCOUNTS, facts(), as_of_date=as_of,
            instrument_detail_cache=cache, direct_fx_instruments={},
        )
        accounts = ledger.build_account_workspace(
            PORTFOLIO['portfolio_id'], ACCOUNTS, facts(), as_of_date=as_of,
            instrument_detail_cache=cache, direct_fx_instruments={},
        )
        return holding, accounts

    expected = views({})
    actual = views(valuation_fx.HistoricalInstrumentDetails(end_date=as_of))
    assert actual == expected
    row = next(item for item in actual[0]['positions'] if item.get('instrument_id') == REF['instrument_id'])
    if source_case == 'initial_price':
        assert row['market_value'] == 990
        assert row['valuation_basis'] == 'transaction_price'
    elif source_case in {'clean', 'invalid_future'}:
        assert row['market_value'] == 1100
    else:
        assert row['market_value'] is None
    assert stock == original


def test_research_scope_and_daily_frequency_metadata_never_convert_fx(client, monkeypatch):
    taxonomy_id, _ = _create_planning_taxonomy(client)

    def no_fx(*args, **kwargs):
        raise AssertionError('Scope and daily source-frequency metadata do not value or convert assets.')

    monkeypatch.setattr(research_solver, 'get_shared_fx_rates', no_fx)
    monkeypatch.setattr(valuation_fx, 'convert_amount_on', no_fx)
    monkeypatch.setattr(valuation_fx, 'resolve_fx_rate_on', no_fx)
    options = research_solver.build_research_scope_options('investment-studio',
        planning_taxonomy_id=taxonomy_id, as_of_date=date(2026, 4, 15))
    frequency = research_solver.build_research_calculation_frequency_profile('investment-studio',
        planning_taxonomy_id=taxonomy_id, comparator_taxonomy_node_id=None,
        as_of_date=date(2026, 4, 15), lookback_days=30)
    assert len(options) > 1
    assert frequency['resolved_frequency'] == 'daily'
    assert frequency['source_frequency_counts']['daily'] > 0


def test_research_analysis_cache_rechecks_sources_targets_scope_and_date(monkeypatch):
    state = {'generation': ('published-1',)}
    factory = object()
    monkeypatch.setattr(source_cache, '_cache', OrderedDict())
    monkeypatch.setattr(source_cache, '_cache_total_size_bytes', 0)
    monkeypatch.setattr(workspace_cache, 'get_session_factory', lambda: factory)
    monkeypatch.setattr(workspace_cache, '_snapshot_fingerprint', lambda _: state['generation'])
    builds = []

    def build():
        builds.append(True)
        return {'context': {'nav': 100, 'targets': [0.4, 0.6]}}

    args = dict(planning_state_fingerprint='current-target-and-market-1',
                as_of_date=date(2026, 4, 15), lookback_days=90,
                comparator_taxonomy_node_id=None, builder=build)
    first = workspace_cache.get_cached_research_analysis('p', **args)
    first['context']['targets'].clear()
    assert workspace_cache.get_cached_research_analysis('p', **args)['context']['targets'] == [0.4, 0.6]
    assert len(builds) == 1
    for change in [
        {'planning_state_fingerprint': 'current-target-and-market-2'},
        {'as_of_date': date(2026, 4, 14)},
        {'lookback_days': 180},
        {'comparator_taxonomy_node_id': 'rates'},
    ]:
        workspace_cache.get_cached_research_analysis('p', **{**args, **change})
    assert len(builds) == 5
    state['generation'] = None
    workspace_cache.get_cached_research_analysis('p', **args)
    workspace_cache.get_cached_research_analysis('p', **args)
    assert len(builds) == 7
    state['generation'] = ('published-2',)
    workspace_cache.get_cached_research_analysis('p', **args)
    assert len(builds) == 8
    for _ in range(2):
        workspace_cache.get_cached_research_analysis('p', **{**args, 'planning_state_fingerprint': None})
    assert len(builds) == 10


def test_workbench_reuses_only_analysis_and_keeps_settings_live(client, monkeypatch):
    taxonomy_id, nodes = _create_planning_taxonomy(client)
    settings = dict(planning_taxonomy_id=taxonomy_id, comparator_taxonomy_node_id=None,
                    as_of_date='2026-04-15', lookback_days=90,
                    calculation_frequency='daily', missing_return_policy='complete_case_drop',
                    covariance_model_id='sample_covariance', contribution_mode='abs',
                    target_dimension='scope_default', capital_mode='unit_notional')
    path = '/api/portfolios/investment-studio/research'
    assert client.put(path + '/settings', json=settings).status_code == 200
    research._run_portfolio_daily_snapshot_recalculation_synchronously('investment-studio')
    builds = []
    context_inputs, frequency_inputs = [], []
    original = research._build_research_context
    original_frequency = research.build_research_calculation_frequency_profile

    def build(*args, **kwargs):
        builds.append(True)
        context_inputs.append(kwargs['instrument_detail_cache'])
        return original(*args, **kwargs)

    def frequency(*args, **kwargs):
        frequency_inputs.append(kwargs['_instrument_detail_cache'])
        return original_frequency(*args, **kwargs)

    monkeypatch.setattr(research, '_build_research_context', build)
    monkeypatch.setattr(research, 'build_research_calculation_frequency_profile', frequency)
    first = client.get(path + '/workbench')
    second = client.get(path + '/workbench')
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(builds) == 1
    assert context_inputs[0] is frequency_inputs[0]
    assert isinstance(context_inputs[0], valuation_fx.HistoricalInstrumentDetails)

    # A saved selection changes the analysis key immediately, without waiting
    # for an elapsed-time cache expiry or changing valuation dates.
    settings['comparator_taxonomy_node_id'] = nodes['Rates']
    assert client.put(path + '/settings', json=settings).status_code == 200
    third = client.get(path + '/workbench')
    assert third.status_code == 200
    assert third.json()['settings']['comparator_taxonomy_node_id'] == nodes['Rates']
    assert len(builds) == 2
    assert context_inputs[1] is frequency_inputs[1]
    assert context_inputs[0] is not context_inputs[1]
