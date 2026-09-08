from datetime import date

from sqlalchemy import select

from portfolio_app.db.models import DerivativeContractRecordModel, PortfolioInstrumentUniverseRecordModel, TransactionRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.research_eligibility import contract_only_instrument_ids
from portfolio_app.services import research_solver


def _seed_contract(session, name, instrument_id, day):
    session.add(DerivativeContractRecordModel(portfolio_id='investment-studio', derivative_contract_id=name,
        account_id='broker-us-core', contract_name=name, contract_type='fcn', currency='USD',
        terms_json={'underlyings': [{'instrument_id': instrument_id}]}, created_at=f'{day}T00:00:00Z'))
    session.flush()
    template = session.scalar(select(TransactionRecordModel).order_by(TransactionRecordModel.transaction_sequence))
    values = {column.name: getattr(template, column.name) for column in TransactionRecordModel.__table__.columns}
    values.update(transaction_id=f'txn-{name}', transaction_sequence=10000 + len(name), transaction_type='buy',
        trade_date=day, position_effective_date=day, settlement_date=day, account_id='broker-us-core',
        instrument_id=None, instrument_ref_json=None, derivative_contract_id=name, quantity=1,
        asset_deliveries_json=None)
    transaction = TransactionRecordModel(**values)
    session.add(transaction)
    session.flush()
    return transaction


def test_contract_classification_respects_date_and_explicit_security_selection(client):
    with get_session_factory()() as session:
        first = _seed_contract(session, 'first', 'linked-only', date(2026, 4, 1))
        _seed_contract(session, 'future-contract', 'future-only', date(2026, 5, 1))
        assert contract_only_instrument_ids('investment-studio', as_of_date=date(2026, 4, 15), session=session) == {'linked-only'}
        assert contract_only_instrument_ids('investment-studio', as_of_date=date(2026, 4, 15),
            explicitly_selected={'linked-only'}, session=session) == set()
        # A future direct trade cannot authorize a prior allocation.
        values = {column.name: getattr(first, column.name) for column in TransactionRecordModel.__table__.columns}
        values.update(transaction_id='direct-later', transaction_sequence=20000, derivative_contract_id=None,
                      instrument_id='linked-only', trade_date=date(2026, 4, 20), position_effective_date=date(2026, 4, 20))
        session.add(TransactionRecordModel(**values))
        session.flush()
        assert contract_only_instrument_ids('investment-studio', as_of_date=date(2026, 4, 15), session=session) == {'linked-only'}
        assert contract_only_instrument_ids('investment-studio', as_of_date=date(2026, 5, 2), session=session) == {'future-only'}
        session.add(PortfolioInstrumentUniverseRecordModel(portfolio_id='investment-studio', instrument_id='future-only',
            source='manual', holding_state='not_held', transaction_count=0, status='active',
            created_at='2026-05-01T00:00:00Z', updated_at='2026-05-01T00:00:00Z'))
        session.flush()
        assert contract_only_instrument_ids('investment-studio', as_of_date=date(2026, 5, 2), session=session) == set()


def test_research_ignores_contract_only_assignments_but_keeps_explicit_target_members(client, monkeypatch):
    with get_session_factory()() as session:
        _seed_contract(session, 'classified-fcn', 'linked-only', date(2026, 4, 1))
        session.commit()
    configuration = {
        'taxonomy': {'taxonomy_id': 'test-taxonomy', 'name': 'Custom', 'planning_enabled': True, 'status': 'active'},
        'taxonomy_nodes': [{'taxonomy_node_id': 'leaf', 'node_name': 'Leaf', 'status': 'active'}],
        'taxonomy_assignments': [{'taxonomy_node_id': 'leaf', 'target_scope': 'instrument', 'target_entity_id': 'linked-only', 'status': 'active'}],
        'target_sets': [], 'target_set_lines': [],
    }
    monkeypatch.setattr(research_solver, 'taxonomy_configuration_as_of', lambda *args: configuration)
    monkeypatch.setattr(research_solver, 'resolve_instrument_analytics_scopes', lambda *args, **kwargs: {})
    def state():
        return research_solver._build_taxonomy_state('investment-studio', planning_taxonomy_id='test-taxonomy',
            as_of_date=date(2026, 4, 15), direct_fx_instruments={})
    assert not state().direct_assignments_by_node
    configuration['target_sets'] = [{'target_set_id': 'explicit', 'status': 'active', 'target_set_type': 'saa'}]
    configuration['target_set_lines'] = [{'target_set_id': 'explicit', 'target_member_type': 'instrument',
                                         'target_member_id': 'linked-only', 'target_weight': 0.1}]
    assert state().direct_assignments_by_node['leaf'][0]['target_entity_id'] == 'linked-only'


def test_research_can_select_custom_taxonomy_before_targets_but_cannot_run(client):
    base = '/api/portfolios/investment-studio'
    response = client.post(f'{base}/taxonomies', json={
        'effective_from': '2020-01-01', 'name': 'Industry classification', 'taxonomy_type': 'custom',
        'primary_assignment_scope': 'instrument', 'planning_enabled': False,
    })
    assert response.status_code == 200, response.json()
    taxonomy_id = response.json()['taxonomy_id']
    response = client.put(f'{base}/research/settings', json={'planning_taxonomy_id': taxonomy_id})
    assert response.status_code == 200, response.json()
    workbench = client.get(f'{base}/research/workbench')
    assert workbench.status_code == 200, workbench.json()
    option = next(item for item in workbench.json()['planning_taxonomy_options'] if item['taxonomy_id'] == taxonomy_id)
    assert option['targets_available'] is False
    response = client.post(f'{base}/research/runs', json={'requested_by': 'pytest'})
    assert response.status_code == 400, response.json()
    assert 'Configure active weight or risk-contribution targets' in response.json()['detail']
