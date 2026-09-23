from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date
from threading import Event, current_thread

import pytest
from sqlalchemy import select

from portfolio_app.db.models import (
    PortfolioRecordModel, TargetSetLineRecordModel, TargetSetRecordModel,
    TaxonomyAssignmentRecordModel, TaxonomyConfigurationRevisionModel,
    TaxonomyNodeRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services import taxonomy_configuration, portfolio_store
from .test_postgres_instrument_registry_constraints import postgres_portfolio_env


pytestmark = pytest.mark.postgresql_integration


def test_subtree_delete_is_atomic_and_keeps_surviving_target_lines(postgres_portfolio_env, monkeypatch):
    portfolio_id = 'taxonomy-delete'
    with get_session_factory()() as session:
        session.add(PortfolioRecordModel(portfolio_id=portfolio_id, portfolio_name='Delete planning',
            base_currency='USD', valuation_timezone='UTC', valuation_cutoff_policy='close', inception_date=date(2026, 9, 19)))
        session.commit()
    taxonomy = portfolio_store.create_taxonomy(portfolio_id,
        name='Planning', taxonomy_type='custom', purpose=None, primary_assignment_scope='instrument',
        root_allocation_basis='weight',
        status='active', source_template_ref=None)
    taxonomy_id = taxonomy['taxonomy_id']
    with get_session_factory()() as session:
        for node_id, parent in [('removed', None), ('child', 'removed'), ('survivor', None)]:
            session.add(TaxonomyNodeRecordModel(taxonomy_node_id=node_id, taxonomy_id=taxonomy_id,
                parent_taxonomy_node_id=parent, node_name=node_id, is_terminal=node_id != 'removed'))
        session.add(TaxonomyAssignmentRecordModel(assignment_id='assigned', taxonomy_id=taxonomy_id,
            taxonomy_node_id='child', target_scope='instrument', target_entity_id=postgres_portfolio_env['instrument_id']))
        session.add_all([
            TargetSetRecordModel(target_set_id='root-target', taxonomy_id=taxonomy_id,
                target_set_type='saa', name='Root'),
            TargetSetRecordModel(target_set_id='child-target', taxonomy_id=taxonomy_id,
                comparator_taxonomy_node_id='removed', target_set_type='saa', name='Child'),
        ])
        session.flush()
        for line_id, set_id, node_id, share in [('removed-line', 'root-target', 'removed', .4),
                                               ('survivor-line', 'root-target', 'survivor', .6),
                                               ('child-line', 'child-target', 'child', 1)]:
            session.add(TargetSetLineRecordModel(target_line_id=line_id, target_set_id=set_id,
                taxonomy_node_id=node_id, target_member_type='taxonomy_node', target_member_id=node_id,
                target_value=share))
        session.commit()

    original_mark = portfolio_store._mark_daily_snapshots_stale
    def fail_publication(*_args, **_kwargs):
        raise RuntimeError('Interrupted delete')
    monkeypatch.setattr(portfolio_store, '_mark_daily_snapshots_stale', fail_publication)
    with pytest.raises(RuntimeError, match='Interrupted delete'):
        portfolio_store.delete_taxonomy_node(portfolio_id, taxonomy_id, 'removed')
    with get_session_factory()() as session:
        assert session.get(TaxonomyNodeRecordModel, 'removed') is not None
        assert session.get(TaxonomyNodeRecordModel, 'child') is not None
        assert session.get(TaxonomyAssignmentRecordModel, 'assigned') is not None
        assert session.get(TargetSetRecordModel, 'child-target') is not None
        assert session.get(TargetSetLineRecordModel, 'removed-line') is not None
        assert len(session.scalars(select(TaxonomyConfigurationRevisionModel).where(
            TaxonomyConfigurationRevisionModel.taxonomy_id == taxonomy_id)).all()) == 1

    monkeypatch.setattr(portfolio_store, '_mark_daily_snapshots_stale', original_mark)
    assert portfolio_store.delete_taxonomy_node(portfolio_id, taxonomy_id, 'removed')
    with get_session_factory()() as session:
        assert session.get(TaxonomyNodeRecordModel, 'removed') is None
        assert session.get(TaxonomyNodeRecordModel, 'child') is None
        assert session.get(TaxonomyAssignmentRecordModel, 'assigned') is None
        assert session.get(TargetSetRecordModel, 'child-target') is None
        assert session.get(TargetSetLineRecordModel, 'removed-line') is None
        assert session.get(TargetSetLineRecordModel, 'child-line') is None
        assert session.get(TargetSetLineRecordModel, 'survivor-line').target_value == .6
        assert session.get(TargetSetRecordModel, 'root-target') is not None


def test_concurrent_taxonomy_edits_publish_complete_serial_revisions(postgres_portfolio_env, monkeypatch):
    portfolio_id = 'taxonomy-concurrent'
    inception_date = date(2026, 9, 19)
    with get_session_factory()() as session:
        session.add(PortfolioRecordModel(portfolio_id=portfolio_id, portfolio_name='Concurrent planning',
            base_currency='USD', valuation_timezone='UTC', valuation_cutoff_policy='close', inception_date=inception_date))
        session.commit()
    taxonomy = portfolio_store.create_taxonomy(portfolio_id,
        name='Planning', taxonomy_type='custom', purpose=None, primary_assignment_scope='instrument',
        root_allocation_basis='weight',
        status='active', source_template_ref=None)
    taxonomy_id = taxonomy['taxonomy_id']
    nodes = [portfolio_store.create_taxonomy_node(portfolio_id, taxonomy_id=taxonomy_id,
        node_name=name, node_code=None, parent_taxonomy_node_id=None,
        sort_order=ordinal, is_terminal=True, allocation_basis='weight', status='active')
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


def test_concurrent_target_editors_reject_stale_draft_after_waiting_for_lock(postgres_portfolio_env, monkeypatch):
    from fastapi import HTTPException
    portfolio_id = 'target-editor-concurrent'
    with get_session_factory()() as session:
        session.add(PortfolioRecordModel(portfolio_id=portfolio_id, portfolio_name='Concurrent targets',
            base_currency='USD', valuation_timezone='UTC', valuation_cutoff_policy='close', inception_date=date(2026, 9, 23)))
        session.commit()
    taxonomy = portfolio_store.create_taxonomy(portfolio_id, name='Targets', taxonomy_type='custom',
        purpose=None, primary_assignment_scope='instrument', root_allocation_basis='weight',
        status='active', source_template_ref=None)
    taxonomy_id = taxonomy['taxonomy_id']
    version = taxonomy_configuration.taxonomy_configuration_version(portfolio_id)
    first_inside, second_started, release_first = Event(), Event(), Event()
    original_record = portfolio_store._record_taxonomy_configuration_revision_in_session

    def record(session, **kwargs):
        if current_thread().name.startswith('first-target-editor'):
            first_inside.set()
            assert release_first.wait(timeout=10)
        return original_record(session, **kwargs)

    monkeypatch.setattr(portfolio_store, '_record_taxonomy_configuration_revision_in_session', record)

    def save(basis, second=False):
        if second:
            second_started.set()
        return portfolio_store.save_taxonomy_target_configuration(portfolio_id, taxonomy_id,
            expected_configuration_version=version, root_allocation_basis=basis,
            node_allocation_bases={}, target_sets=[])

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='first-target-editor') as first_pool, \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix='second-target-editor') as second_pool:
        first = first_pool.submit(save, 'risk_budget')
        assert first_inside.wait(timeout=10)
        second = second_pool.submit(save, 'weight', True)
        assert second_started.wait(timeout=10)
        try:
            with pytest.raises(TimeoutError):
                second.result(timeout=.1)
        finally:
            release_first.set()
        first.result(timeout=10)
        with pytest.raises(HTTPException) as error:
            second.result(timeout=10)
        assert error.value.status_code == 409
    current = taxonomy_configuration.current_taxonomy_configuration(portfolio_id, taxonomy_id)
    assert current['taxonomy']['root_allocation_basis'] == 'risk_budget'
    assert current['configuration_version'] == version + 1


def test_assignments_in_different_portfolios_do_not_compete_for_global_sequence(postgres_portfolio_env, monkeypatch):
    taxonomies = []
    for portfolio_id in ['assignment-owner-a', 'assignment-owner-b']:
        with get_session_factory()() as session:
            session.add(PortfolioRecordModel(portfolio_id=portfolio_id, portfolio_name=portfolio_id,
                base_currency='USD', valuation_timezone='UTC', valuation_cutoff_policy='close', inception_date=date(2026, 9, 23)))
            session.commit()
        taxonomy = portfolio_store.create_taxonomy(portfolio_id, name='Industry', taxonomy_type='custom',
            purpose=None, primary_assignment_scope='instrument', root_allocation_basis='weight',
            status='active', source_template_ref=None)
        node = portfolio_store.create_taxonomy_node(portfolio_id, taxonomy_id=taxonomy['taxonomy_id'],
            node_name='Technology', node_code=None, parent_taxonomy_node_id=None,
            sort_order=0, is_terminal=True, allocation_basis='weight', status='active')
        taxonomies.append((portfolio_id, taxonomy['taxonomy_id'], node['taxonomy_node_id']))
    first_inside, release_first = Event(), Event()
    original_record = portfolio_store._record_taxonomy_configuration_revision_in_session

    def record(session, **kwargs):
        if current_thread().name.startswith('first-assignment'):
            first_inside.set()
            assert release_first.wait(timeout=10)
        return original_record(session, **kwargs)

    monkeypatch.setattr(portfolio_store, '_record_taxonomy_configuration_revision_in_session', record)

    def assign(index):
        portfolio_id, taxonomy_id, node_id = taxonomies[index]
        return portfolio_store.create_taxonomy_assignment(portfolio_id, taxonomy_id=taxonomy_id,
            taxonomy_node_id=node_id, target_scope='instrument',
            target_entity_id=postgres_portfolio_env['instrument_id'], status='active')

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='first-assignment') as first_pool, \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix='second-assignment') as second_pool:
        first = first_pool.submit(assign, 0)
        assert first_inside.wait(timeout=10)
        try:
            second_result = second_pool.submit(assign, 1).result(timeout=3)
        finally:
            release_first.set()
        first_result = first.result(timeout=10)
    assert first_result['assignment_id'] != second_result['assignment_id']


def test_catalog_integrity_and_member_list_share_configuration_read_lock(postgres_portfolio_env, monkeypatch):
    portfolio_id = 'catalog-snapshot'
    with get_session_factory()() as session:
        session.add(PortfolioRecordModel(portfolio_id=portfolio_id, portfolio_name='Catalog',
            base_currency='USD', valuation_timezone='UTC', valuation_cutoff_policy='close', inception_date=date(2026, 9, 23)))
        session.commit()
    taxonomy = portfolio_store.create_taxonomy(portfolio_id, name='Original', taxonomy_type='custom',
        purpose=None, primary_assignment_scope='instrument', root_allocation_basis='weight',
        status='active', source_template_ref=None)
    version = taxonomy_configuration.taxonomy_configuration_version(portfolio_id)
    inside_read, writer_started, release_read = Event(), Event(), Event()
    original_integrity = portfolio_store.list_target_set_integrity_issues

    def integrity(*args, **kwargs):
        assert kwargs.get('_session') is not None
        inside_read.set()
        assert release_read.wait(timeout=10)
        return original_integrity(*args, **kwargs)

    monkeypatch.setattr(portfolio_store, 'list_target_set_integrity_issues', integrity)

    def rename():
        writer_started.set()
        return portfolio_store.update_taxonomy(portfolio_id, taxonomy['taxonomy_id'], name='Current')

    with ThreadPoolExecutor(max_workers=1) as reader, ThreadPoolExecutor(max_workers=1) as writer:
        read = reader.submit(taxonomy_configuration.current_taxonomy_catalog, portfolio_id)
        assert inside_read.wait(timeout=10)
        write = writer.submit(rename)
        assert writer_started.wait(timeout=10)
        try:
            with pytest.raises(TimeoutError):
                write.result(timeout=.1)
        finally:
            release_read.set()
        catalog = read.result(timeout=10)
        write.result(timeout=10)
    assert catalog['taxonomies'][0]['name'] == 'Original'
    assert catalog['taxonomy_configuration_version'] == version
    assert catalog['target_set_integrity_issues'] == []
    assert catalog['instrument_universe'] == []
