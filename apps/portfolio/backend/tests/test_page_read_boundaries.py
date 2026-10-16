"""Page projections preserve facts and permissions while omitting unused inputs."""
from copy import deepcopy

import pytest
from sqlalchemy import event
from studio_identity import Principal

from portfolio_app.api.authorization import authenticated_principal
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_app.services import daily_snapshots, ledger, portfolio_store, taxonomy_configuration
from portfolio_app.services.financial_read_context import (
    financial_read_context, remember_opening_generation,
)
from .test_taxonomy_configuration import _create_planning_tree
from .test_transaction_cash_fx_reads import fx_detail
from investment_studio_instrument_core.fx_contract import fx_instrument_identity
from investment_studio_instrument_core.fx_rates import latest_spot_point_from_instrument

PID = 'investment-studio'


def _statements(read):
    statements = []
    def record(_c, _cu, sql, *_args):
        statements.append(sql.lower())
    event.listen(get_engine(), 'before_cursor_execute', record)
    try:
        result = read()
    finally:
        event.remove(get_engine(), 'before_cursor_execute', record)
    return result, statements


def test_navigation_options_never_read_valuations_and_keep_authorization(raw_client, monkeypatch):
    monkeypatch.setattr(portfolio_store, '_serialize_portfolio_row_with_materialized_summary',
                        lambda *_a: pytest.fail('Navigation must not load NAV'))
    response, sql = _statements(lambda: raw_client.get('/api/portfolios/options'))
    assert response.status_code == 200
    assert any(row['portfolio_id'] == PID for row in response.json())
    assert all(set(row) == {'portfolio_id', 'portfolio_name'} for row in response.json())
    assert not any('daily_snapshot' in item or 'calculation_state' in item for item in sql)
    raw_client.app.dependency_overrides[authenticated_principal] = lambda: Principal('outsider', 'Outsider', 'default')
    assert raw_client.get('/api/portfolios/options').json() == []
    assert raw_client.get(f'/api/portfolios/{PID}/instruments/options').status_code == 404


def test_instrument_options_keep_identity_and_selected_prices_without_global_history(raw_client, monkeypatch):
    from portfolio_app.api.routes import transactions
    from portfolio_app.services import instrument_registry
    monkeypatch.setattr(transactions, 'list_registry_instruments', instrument_registry.list_registry_instruments)
    base = f'/api/portfolios/{PID}/instruments'
    full = raw_client.get(base).json()['instruments']
    response, sql = _statements(lambda: raw_client.get(base + '/options'))
    assert response.status_code == 200, response.text
    expected = [{key: value for key, value in row.items() if key not in {'coverage_state', 'latest_market_data'}} for row in full]
    assert response.json()['instruments'] == expected
    assert not any('instrument_market_data' in item or 'corporate_action_event' in item or 'fund_nav_event' in item for item in sql)
    selected = full[0]
    response = raw_client.get(base + '/' + selected['instrument_core']['instrument_id'] + '/option')
    assert response.status_code == 200, response.text
    assert response.json() == selected


@pytest.mark.parametrize('profile', ['', None, 'futu'])
def test_instrument_serializers_preserve_existing_profile_value(raw_client, profile):
    from portfolio_app.api.routes import transactions
    record = deepcopy(transactions.list_registry_instruments()[0])
    record['source_settings'] = {'source_api_profile': profile}
    for serialize in [transactions._serialize_instrument_option,
                      transactions._serialize_instrument_metadata_option]:
        assert serialize(record)['source_settings']['source_api_profile'] == profile


def test_opening_generation_is_request_scoped_and_closing_check_remains_live(monkeypatch):
    checked = []
    monkeypatch.setattr(daily_snapshots, 'portfolio_financial_read_generation', lambda pid: checked.append(pid))
    with financial_read_context():
        remember_opening_generation(PID, ('published',))
        daily_snapshots.ensure_portfolio_daily_snapshots(PID)
        daily_snapshots.ensure_portfolio_daily_snapshots('other')
        with financial_read_context():
            daily_snapshots.ensure_portfolio_daily_snapshots(PID)
        daily_snapshots.ensure_portfolio_daily_snapshots(PID)
        # The closing fence calls the live function, never the ensure shortcut.
        daily_snapshots.portfolio_financial_read_generation(PID)
    daily_snapshots.ensure_portfolio_daily_snapshots(PID)
    assert checked == ['other', PID, PID, PID]


def test_taxonomy_read_count_is_bounded_and_scope_validation_matches_commands(client):
    def add_tree(index):
        tree = _create_planning_tree(client, name=f'Bulk {index}')
        response = client.post(f'/api/portfolios/{PID}/taxonomies/{tree["taxonomy_id"]}/target-sets', json={
            'comparator_taxonomy_node_id': tree['child_id'], 'target_set_type': 'saa', 'name': f'Target {index}',
            'lines': [{'target_member_type': 'instrument', 'target_member_id': 'equity-us-abbv', 'target_value': 1.0}],
        })
        assert response.status_code == 200, response.text
    add_tree(0)
    _, first_sql = _statements(lambda: taxonomy_configuration.current_taxonomy_catalog(PID))
    for i in range(1, 5):
        add_tree(i)
    catalog, many_sql = _statements(lambda: taxonomy_configuration.current_taxonomy_catalog(PID))
    assert len(many_sql) == len(first_sql)
    assert catalog['target_set_integrity_issues'] == []
    with get_session_factory()() as session:
        inputs = taxonomy_configuration.load_taxonomy_read_inputs(session, PID)
        for taxonomy in inputs.taxonomies:
            assert taxonomy_configuration._taxonomy_configuration_payload(session, taxonomy.taxonomy_id) == taxonomy_configuration._taxonomy_configuration_payload(session, taxonomy.taxonomy_id, _read_inputs=inputs)
        for target in inputs.target_sets:
            taxonomy = next(row for row in inputs.taxonomies if row.taxonomy_id == target.taxonomy_id)
            arguments = dict(taxonomy=taxonomy, comparator_taxonomy_node_id=target.comparator_taxonomy_node_id,
                target_set_type=target.target_set_type, status=target.status, exclude_target_set_id=target.target_set_id,
                lines=[{'target_member_type': row.target_member_type, 'target_member_id': row.target_member_id,
                        'taxonomy_node_id': row.taxonomy_node_id, 'target_value': row.target_value}
                       for row in inputs.target_lines if row.target_set_id == target.target_set_id])
            for lines in (arguments['lines'], [], [{**arguments['lines'][0], 'target_value': .5}]):
                outcomes = []
                for read_inputs in (None, inputs):
                    try:
                        parent, members = portfolio_store._validate_target_set_lines(session, **{**arguments, 'lines': lines}, _read_inputs=read_inputs)
                        outcomes.append((parent.taxonomy_node_id if parent else None, members))
                    except ValueError as error:
                        outcomes.append(str(error))
                assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize('pair', [('USD', 'USD'), ('USD', 'HKD'), ('HKD', 'USD'), ('HKD', 'CNY')])
@pytest.mark.parametrize('fault', [False, True])
def test_disposal_impact_retains_historical_fx_origins_without_global_catalog(monkeypatch, pair, fault):
    details = {'fx-usd-hkd': fx_detail('fx-usd-hkd', 'HKD', {3: 7.8, 4: 7.9, 5: 8.0, 6: 8.1}),
               'fx-usd-cny': fx_detail('fx-usd-cny', 'CNY', {3: 7.1, 4: 7.2, 5: 7.3, 6: 7.4})}
    if fault:
        details['fx-usd-hkd']['market_data'][-1]['currency'] = 'EUR'
    eligible = {}
    for key, detail in details.items():
        if latest_spot_point_from_instrument(detail, detail['market_data']) is not None:
            identity = fx_instrument_identity(key)
            eligible[identity.base_currency, identity.quote_currency] = key
    reads = []
    def load(key):
        reads.append(key)
        return deepcopy(details.get(key))
    monkeypatch.setattr(ledger, 'get_registry_instrument_detail', load)
    monkeypatch.setattr(ledger, 'get_shared_fx_rates', lambda: pytest.fail('Unrelated global FX catalog'))
    tx = {'transaction_id': 'sale', 'transaction_type': 'sell', 'currency': pair[0],
          'trade_date': '2026-08-05', 'position_effective_date': '2026-08-05', 'settlement_date': '2026-08-06'}
    lots = [{'realizations': [{'transaction_id': 'sale', 'cost_basis_released': 100, 'proceeds': 120,
             'cost_basis_origins': [{'cost_basis_released': 40, 'acquisition_date': '2026-08-03'},
                                   {'cost_basis_released': 60, 'acquisition_date': '2026-08-04'}]}]}]
    expected = ledger.build_transaction_accounting_impact(tx, lots, base_currency=pair[1], direct_fx_instruments=eligible, instrument_detail_cache={})
    reads.clear()
    actual = ledger.build_transaction_accounting_impact(tx, lots, base_currency=pair[1])
    assert actual == expected
    assert len(reads) == len(set(reads))
    if pair[0] == pair[1]:
        assert reads == []
        assert actual['realized_price_pnl_base'] == 20
        assert actual['realized_position_fx_pnl_base'] == 0


def test_transaction_facts_projection_preserves_every_nonvaluation_field(raw_client, monkeypatch):
    base = f'/api/portfolios/{PID}/transactions/workspace'
    transactions = portfolio_store.list_transactions(PID)
    selected = next(tx for tx in transactions if tx.get('instrument_id'))
    params = {'transaction_id': selected['transaction_id']}
    full = raw_client.get(base, params=params)
    assert full.status_code == 200, full.text
    monkeypatch.setattr(ledger, '_resolve_pricing_quote_map', lambda *_a, **_k: pytest.fail('Unused current prices'))
    facts = raw_client.get(base, params={**params, 'include_valuation': False})
    assert facts.status_code == 200, facts.text
    expected = full.json()
    for payload in (expected, actual := facts.json()):
        for lot in payload['related_position_lots']:
            lot.pop('current_market_value')
            lot.pop('unrealized_pnl')
    assert actual == expected
