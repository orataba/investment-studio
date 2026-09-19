from datetime import date
from collections import OrderedDict

from portfolio_app.services import ledger, performance, research, research_solver, valuation_fx
from .test_research_api import _create_planning_taxonomy
from portfolio_app.services import source_cache, workspace_cache


def test_research_context_batches_security_details_and_shares_one_fx_read(client, monkeypatch):
    taxonomy_id, _ = _create_planning_taxonomy(client)
    research._run_portfolio_daily_snapshot_recalculation_synchronously('investment-studio')
    fx_calls, detail_batches = [], []
    load_fx = research.get_shared_fx_rates
    load_details = research.get_registry_instrument_details

    def fx():
        fx_calls.append(True)
        return load_fx()

    def details(ids):
        detail_batches.append(set(ids))
        return load_details(ids)

    def redundant_fx():
        raise AssertionError('Context financial views must reuse the supplied FX map.')

    monkeypatch.setattr(research, 'get_shared_fx_rates', fx)
    monkeypatch.setattr(research, 'get_registry_instrument_details', details)
    monkeypatch.setattr(performance, 'get_shared_fx_rates', redundant_fx)
    monkeypatch.setattr(ledger, 'get_shared_fx_rates', redundant_fx)
    context = research._build_research_context('investment-studio', planning_taxonomy_id=taxonomy_id,
        as_of_date=date(2026, 4, 15), lookback_days=30)
    assert context['as_of_date'] == '2026-04-15'
    assert len(fx_calls) == 1
    assert len(detail_batches) == 1
    transaction_ids = {row['instrument_id'] for row in research.list_transactions('investment-studio')
                       if row.get('instrument_id')}
    assert transaction_ids <= detail_batches[0]


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
    original = research._build_research_context

    def build(*args, **kwargs):
        builds.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(research, '_build_research_context', build)
    first = client.get(path + '/workbench')
    second = client.get(path + '/workbench')
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(builds) == 1

    # A saved selection changes the analysis key immediately, without waiting
    # for an elapsed-time cache expiry or changing valuation dates.
    settings['comparator_taxonomy_node_id'] = nodes['Rates']
    assert client.put(path + '/settings', json=settings).status_code == 200
    third = client.get(path + '/workbench')
    assert third.status_code == 200
    assert third.json()['settings']['comparator_taxonomy_node_id'] == nodes['Rates']
    assert len(builds) == 2
