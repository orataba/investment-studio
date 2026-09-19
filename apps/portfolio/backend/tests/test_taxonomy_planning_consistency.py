from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import select

from portfolio_app.db.models import PortfolioCalculationStateModel, TaxonomyConfigurationRevisionModel
from portfolio_app.db.session import get_session_factory
from .test_research_api import _create_planning_taxonomy, _create_target_sets


PORTFOLIO = 'investment-studio'
BASE = f'/api/portfolios/{PORTFOLIO}'


def _target_change(client, taxonomy_id, node_ids):
    catalog = client.get(f'{BASE}/taxonomies').json()
    target = next(item for item in catalog['target_sets'] if item['taxonomy_id'] == taxonomy_id
                  and item['comparator_taxonomy_node_id'] == node_ids['Risk Assets']
                  and item['target_set_type'] == 'taa')
    lines = [deepcopy(item) for item in catalog['target_set_lines'] if item['target_set_id'] == target['target_set_id']]
    for line in lines:
        line['target_weight'] = .51 if line['target_member_id'] == node_ids['Defensive Equity'] else .49
    return {**target, 'lines': lines}


def _revision_state(taxonomy_id):
    with get_session_factory()() as session:
        rows = session.scalars(select(TaxonomyConfigurationRevisionModel).where(
            TaxonomyConfigurationRevisionModel.taxonomy_id == taxonomy_id)).all()
        state = session.get(PortfolioCalculationStateModel, PORTFOLIO)
        return len(rows), state.refresh_request_id


def test_target_configuration_is_atomic_and_publishes_one_revision(client):
    taxonomy_id, nodes = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, nodes)
    target = _target_change(client, taxonomy_id, nodes)
    original_catalog = client.get(f'{BASE}/taxonomies').json()
    before = _revision_state(taxonomy_id)
    payload = {'node_defaults': {nodes['Risk Assets']: 'risk_budget'},
               'target_sets': [target, {**target, 'target_set_id': None, 'target_set_type': 'saa',
                                      'comparator_taxonomy_node_id': 'missing-node'}]}
    invalid = client.put(f'{BASE}/taxonomies/{taxonomy_id}/target-configuration', json=payload)
    assert invalid.status_code == 400, invalid.text
    assert _revision_state(taxonomy_id) == before
    after_failure = client.get(f'{BASE}/taxonomies').json()
    for key in ['target_sets', 'target_set_lines', 'taxonomy_nodes']:
        assert after_failure[key] == original_catalog[key]

    payload['target_sets'] = [target]
    saved = client.put(f'{BASE}/taxonomies/{taxonomy_id}/target-configuration', json=payload)
    assert saved.status_code == 200, saved.text
    after_count, after_generation = _revision_state(taxonomy_id)
    assert after_count == before[0] + 1
    assert after_generation != before[1]
    with get_session_factory()() as session:
        state = session.get(PortfolioCalculationStateModel, PORTFOLIO)
        assert state.daily_snapshot_status == 'stale'
    updated = client.get(f'{BASE}/taxonomies').json()
    assert next(item for item in updated['taxonomy_nodes'] if item['taxonomy_node_id'] == nodes['Risk Assets'])['default_target_dimension'] == 'risk_budget'


def test_research_discloses_unassigned_holdings_even_when_their_values_net_to_zero(client, monkeypatch):
    from portfolio_app.services import research
    taxonomy_id, _ = _create_planning_taxonomy(client)
    monkeypatch.setattr(research, '_build_planning_group_snapshot', lambda *args, **kwargs: [
        {'group_key': 'unassigned', 'group_label': 'Unassigned', 'position_count': 2, 'end_value_base': 0.0},
    ])
    monkeypatch.setattr(research, 'get_cached_materialized_performance_report', lambda *args, **kwargs: None)
    context = research._build_research_context(PORTFOLIO, planning_taxonomy_id=taxonomy_id,
        as_of_date=date(2026, 4, 15), lookback_days=30)
    assert any('2 unassigned security holding(s)' in warning for warning in context['quality_warnings'])


def test_current_targets_apply_to_dynamic_and_pinned_data_dates_and_run_snapshot_is_stable(client):
    taxonomy_id, nodes = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, nodes)
    target = _target_change(client, taxonomy_id, nodes)
    settings = {'planning_taxonomy_id': taxonomy_id, 'comparator_taxonomy_node_id': nodes['Risk Assets'],
                'lookback_days': 30, 'target_dimension': 'scope_default', 'capital_mode': 'unit_notional'}
    response = client.put(f'{BASE}/research/settings', json={**settings, 'as_of_mode': 'dynamic'})
    assert response.status_code == 200, response.text
    previous = client.post(f'{BASE}/research/runs', json={})
    assert previous.status_code == 200, previous.text
    save = client.put(f'{BASE}/taxonomies/{taxonomy_id}/target-configuration', json={
        'target_sets': [target],
    })
    assert save.status_code == 200, save.text
    stale = client.get(f'{BASE}/research/workbench').json()['selected_run']
    assert stale['research_run_id'] == previous.json()['research_run_id']
    assert stale['reliability_state'] == 'stale' and stale['is_current'] is False
    current = client.post(f'{BASE}/research/runs', json={})
    assert current.status_code == 200, current.text
    current_run = current.json()
    assert current_run['as_of_date'] == '2026-04-15'
    current_targets = {row['member_id']: row for row in current_run['detail']['member_targets']}
    assert current_targets[nodes['Defensive Equity']]['configured_weight'] == pytest.approx(.51)
    workbench = client.get(f'{BASE}/research/workbench').json()
    assert workbench['current_context']['as_of_date'] == '2026-04-15'
    assert workbench['current_context']['target_configuration'] == 'current_snapshot'
    assert workbench['selected_run']['is_current'] is True
    assert 'same captured current' in current_run['detail']['coverage_note']

    response = client.put(f'{BASE}/research/settings', json={**settings, 'as_of_mode': 'pinned', 'as_of_date': '2026-04-15'})
    assert response.status_code == 200, response.text
    pinned = client.post(f'{BASE}/research/runs', json={})
    assert pinned.status_code == 200, pinned.text
    historical_targets = {row['member_id']: row for row in pinned.json()['detail']['member_targets']}
    assert historical_targets[nodes['Defensive Equity']]['configured_weight'] == pytest.approx(.51)
    assert pinned.json()['detail']['backtest']['points'] == current_run['detail']['backtest']['points']
    pinned_context = client.get(f'{BASE}/research/workbench').json()['current_context']
    assert pinned_context['target_snapshot_fingerprint'] == workbench['current_context']['target_snapshot_fingerprint']
    from portfolio_app.db.models import ResearchRunRecordModel
    with get_session_factory()() as session:
        stored_run = session.get(ResearchRunRecordModel, pinned.json()['research_run_id'])
        snapshot = stored_run.request_payload_json['target_configuration_snapshot']
        assert snapshot['target_snapshot_fingerprint'] == pinned_context['target_snapshot_fingerprint']
        assert snapshot['taxonomy_nodes'] and snapshot['taxonomy_assignments']
        assert snapshot['instrument_analytics_scopes']
        assert snapshot['captured_at']
        saved_snapshot = deepcopy(snapshot)
    for line in target['lines']:
        line['target_weight'] = .6 if line['target_member_id'] == nodes['Defensive Equity'] else .4
    assert client.put(f'{BASE}/taxonomies/{taxonomy_id}/target-configuration', json={'target_sets': [target]}).status_code == 200
    with get_session_factory()() as session:
        stored_run = session.get(ResearchRunRecordModel, pinned.json()['research_run_id'])
        assert stored_run.request_payload_json['target_configuration_snapshot'] == saved_snapshot
    changed = client.get(f'{BASE}/research/workbench').json()
    assert changed['current_context']['target_snapshot_fingerprint'] != saved_snapshot['target_snapshot_fingerprint']
    assert changed['selected_run']['reliability_state'] == 'stale'



def test_current_target_snapshot_identity_is_stable_until_a_policy_changes(client):
    from portfolio_app.services.research_inputs import capture_current_target_configuration
    taxonomy_id, nodes = _create_planning_taxonomy(client)
    _create_target_sets(client, taxonomy_id, nodes)
    before = capture_current_target_configuration(PORTFOLIO, taxonomy_id)
    repeat = capture_current_target_configuration(PORTFOLIO, taxonomy_id)
    assert before['captured_at'] != repeat['captured_at']
    assert before['target_snapshot_fingerprint'] == repeat['target_snapshot_fingerprint']
    response = client.put(f'{BASE}/taxonomies/{taxonomy_id}/analytics-scope-policies/{nodes["Defensive Equity"]}', json={
        'risk_eligible': False, 'risk_budget_eligible': False,
        'performance_scope': 'ordinary', 'valuation_basis': 'market', 'exclusion_reason': 'Scope review',
    })
    assert response.status_code == 200, response.text
    changed = capture_current_target_configuration(PORTFOLIO, taxonomy_id)
    assert before['target_snapshot_fingerprint'] != changed['target_snapshot_fingerprint']
    assert before['instrument_analytics_scopes']['equity-us-abbv']['risk_eligible'] is True
    assert changed['instrument_analytics_scopes']['equity-us-abbv']['risk_eligible'] is False
