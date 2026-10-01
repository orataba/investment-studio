from datetime import date

import pytest
from fastapi import HTTPException

from portfolio_app.services import holdings_workspace, research, taxonomy_configuration
from portfolio_app.services.taxonomy_targets import resolve_taxonomy_targets


def test_holdings_resolution_clamps_future_and_rejects_before_inception(monkeypatch):
    portfolio = {'portfolio_id': 'p', 'as_of_date': '2026-09-30', 'inception_date': '2026-07-01'}
    monkeypatch.setattr(holdings_workspace, '_require_portfolio', lambda *a, **kw: portfolio)
    assert holdings_workspace.resolve_holdings_request('p', date(2026, 10, 2))[1] == date(2026, 9, 30)
    assert holdings_workspace.resolve_holdings_request('p', date(2026, 7, 1))[1] == date(2026, 7, 1)
    with pytest.raises(HTTPException) as error:
        holdings_workspace.resolve_holdings_request('p', date(2026, 6, 30))
    assert error.value.status_code == 422
    assert 'inception date (2026-07-01)' in error.value.detail
    assert 'Refresh' not in error.value.detail


def test_holdings_resolution_never_clamps_away_a_known_valuation_gap(monkeypatch):
    monkeypatch.setattr(holdings_workspace, '_require_portfolio', lambda *a, **kw: {
        'portfolio_id': 'p', 'as_of_date': '2026-09-29', 'valuation_blocked_from': '2026-09-30',
        'valuation_blocked_reason': 'Required FX is unavailable on 2026-09-30.',
    })
    with pytest.raises(HTTPException) as error:
        holdings_workspace.resolve_holdings_request('p', date(2026, 10, 2))
    assert error.value.status_code == 409
    assert 'Required FX' in error.value.detail


@pytest.mark.parametrize('members, ready, source', [(1, True, 'single_member'), (2, False, 'incomplete')])
def test_research_readiness_uses_the_same_effective_target_resolver(monkeypatch, members, ready, source):
    taxonomy = {'taxonomy_id': 't', 'name': 'Allocation', 'taxonomy_type': 'allocation', 'status': 'active', 'primary_assignment_scope': 'instrument', 'root_allocation_basis': 'weight'}
    configuration = {
        'taxonomy': taxonomy,
        'taxonomy_nodes': [{'taxonomy_node_id': 'equities', 'taxonomy_id': 't', 'node_name': 'Equities', 'allocation_basis': 'weight'}],
        'taxonomy_assignments': [{'taxonomy_id': 't', 'taxonomy_node_id': 'equities', 'target_scope': 'instrument', 'target_entity_id': f'stock-{i}'} for i in range(members)],
        'target_sets': [], 'target_set_lines': [],
    }
    resolution = resolve_taxonomy_targets(configuration)
    monkeypatch.setattr(taxonomy_configuration, 'current_taxonomy_catalog', lambda _: {'taxonomies': [taxonomy], 'target_resolution': [resolution]})
    assert research._planning_taxonomy_options('p') == [{
        'taxonomy_id': 't', 'name': 'Allocation', 'taxonomy_type': 'allocation', 'targets_available': ready, 'target_source': source,
    }]
