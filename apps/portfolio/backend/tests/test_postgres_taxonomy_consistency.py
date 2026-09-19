from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date
from threading import Event, current_thread

import pytest
from sqlalchemy import select

from portfolio_app.db.models import PortfolioRecordModel, TaxonomyConfigurationRevisionModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import analytics_scope, portfolio_store
from .test_postgres_instrument_registry_constraints import postgres_portfolio_env


pytestmark = pytest.mark.postgresql_integration


def test_concurrent_taxonomy_edits_publish_complete_serial_revisions(postgres_portfolio_env, monkeypatch):
    portfolio_id = 'taxonomy-concurrent'
    inception_date = date(2026, 9, 19)
    with get_session_factory()() as session:
        session.add(PortfolioRecordModel(portfolio_id=portfolio_id, portfolio_name='Concurrent planning',
            base_currency='USD', valuation_timezone='UTC', valuation_cutoff_policy='close', inception_date=inception_date))
        session.commit()
    taxonomy = portfolio_store.create_taxonomy(portfolio_id,
        name='Planning', taxonomy_type='custom', purpose=None, primary_assignment_scope='instrument',
        planning_enabled=True, budgeting_level='weight_and_risk_budget', root_default_target_dimension='weight',
        status='active', source_template_ref=None)
    taxonomy_id = taxonomy['taxonomy_id']
    nodes = [portfolio_store.create_taxonomy_node(portfolio_id, taxonomy_id=taxonomy_id,
        node_name=name, node_code=None, parent_taxonomy_node_id=None,
        sort_order=ordinal, is_terminal=True, default_target_dimension='weight', status='active')
        for ordinal, name in enumerate(['Equity', 'Rates'])]
    first_inside_revision, second_attempted, release_first = Event(), Event(), Event()
    original_record = portfolio_store._record_taxonomy_configuration_revision_in_session

    def record(session, **kwargs):
        if current_thread().name.startswith('first-editor'):
            first_inside_revision.set()
            assert release_first.wait(timeout=10)
        return original_record(session, **kwargs)

    monkeypatch.setattr(portfolio_store, '_record_taxonomy_configuration_revision_in_session', record)

    def edit(index):
        if index == 1:
            second_attempted.set()
        return portfolio_store.update_taxonomy_node(portfolio_id, taxonomy_id, nodes[index]['taxonomy_node_id'],
            node_name=['Global Equity', 'Global Rates'][index])

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='first-editor') as first_pool, \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix='second-editor') as second_pool:
        first = first_pool.submit(edit, 0)
        assert first_inside_revision.wait(timeout=10)
        second = second_pool.submit(edit, 1)
        assert second_attempted.wait(timeout=10)
        try:
            with pytest.raises(TimeoutError):
                second.result(timeout=.1)
        finally:
            release_first.set()
        first.result(timeout=10)
        second.result(timeout=10)
    with get_session_factory()() as session:
        current = session.scalars(select(TaxonomyConfigurationRevisionModel).where(
            TaxonomyConfigurationRevisionModel.taxonomy_id == taxonomy_id,
            TaxonomyConfigurationRevisionModel.superseded_by_revision_id.is_(None))).all()
        assert len(current) == 1
        assert {item['node_name'] for item in current[0].configuration_json['taxonomy_nodes']} == {
            'Global Equity', 'Global Rates',
        }


def test_scope_policy_and_taxonomy_writers_share_portfolio_first_lock_order(postgres_portfolio_env, monkeypatch):
    portfolio_id = 'planning-lock-order'
    inception_date = date(2026, 9, 19)
    with get_session_factory()() as session:
        session.add(PortfolioRecordModel(portfolio_id=portfolio_id, portfolio_name='Planning lock order',
            base_currency='USD', valuation_timezone='UTC', valuation_cutoff_policy='close', inception_date=inception_date))
        session.commit()
    taxonomy = portfolio_store.create_taxonomy(portfolio_id,
        name='Planning', taxonomy_type='custom', purpose=None, primary_assignment_scope='instrument',
        planning_enabled=True, budgeting_level='weight_and_risk_budget', root_default_target_dimension='weight',
        status='active', source_template_ref=None)
    taxonomy_id = taxonomy['taxonomy_id']
    taxonomy_locked, release_taxonomy, policy_attempted, policy_version_locked = Event(), Event(), Event(), Event()
    original_record = portfolio_store._record_taxonomy_configuration_revision_in_session
    original_next_version = analytics_scope._next_policy_version

    def record(session, **kwargs):
        if current_thread().name.startswith('taxonomy-editor'):
            taxonomy_locked.set()
            assert release_taxonomy.wait(timeout=10)
        return original_record(session, **kwargs)

    def next_version(session, requested_portfolio_id):
        version = original_next_version(session, requested_portfolio_id)
        if current_thread().name.startswith('policy-editor'):
            policy_version_locked.set()
        return version

    def edit_policy():
        policy_attempted.set()
        return analytics_scope.replace_analytics_scope_policy(portfolio_id, taxonomy_id=taxonomy_id,
            taxonomy_node_id=analytics_scope.ROOT_POLICY_NODE_ID,
            risk_eligible=False, risk_budget_eligible=False, performance_scope='ordinary',
            valuation_basis='market', exclusion_reason='Excluded by PM')

    monkeypatch.setattr(portfolio_store, '_record_taxonomy_configuration_revision_in_session', record)
    monkeypatch.setattr(analytics_scope, '_next_policy_version', next_version)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='taxonomy-editor') as taxonomy_pool, \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix='policy-editor') as policy_pool:
        first = taxonomy_pool.submit(portfolio_store.update_taxonomy, portfolio_id, taxonomy_id,
            name='Current planning')
        assert taxonomy_locked.wait(timeout=10)
        second = policy_pool.submit(edit_policy)
        assert policy_attempted.wait(timeout=10)
        try:
            # A policy writer must wait for the portfolio before taking the
            # version row that the taxonomy writer will need to finish.
            assert not policy_version_locked.wait(timeout=.2)
            with pytest.raises(TimeoutError):
                second.result(timeout=.1)
        finally:
            release_taxonomy.set()
        assert first.result(timeout=10)['name'] == 'Current planning'
        assert second.result(timeout=10)['risk_eligible'] is False
    policies = analytics_scope.list_analytics_scope_policies(portfolio_id, taxonomy_id=taxonomy_id)
    root_policies = [item for item in policies if item['taxonomy_node_id'] == analytics_scope.ROOT_POLICY_NODE_ID
                     and item['superseded_by_policy_id'] is None]
    assert len(root_policies) == 1
    assert root_policies[0]['risk_eligible'] is False
