"""Upgrade real dated rows without losing facts or archived research."""
from datetime import date

from alembic import command
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from .test_postgres_schema_reconciliation import postgres_reconciliation_database, _portfolio_config

pytestmark = pytest.mark.postgresql_integration


def test_current_taxonomy_migration_keeps_latest_edits_and_rebuilds_all_history(postgres_reconciliation_database):
    config = _portfolio_config(postgres_reconciliation_database)
    command.upgrade(config, "20260908_0063")
    engine = sa.create_engine(postgres_reconciliation_database)
    metadata = sa.MetaData(schema="portfolio")
    tables = {name: sa.Table(name, metadata, autoload_with=engine) for name in (
        'portfolio_record', 'taxonomy_record', 'analytics_scope_policy_record',
        'analytics_taxonomy_selection_record', 'taxonomy_configuration_revision',
        'portfolio_calculation_state', 'portfolio_analytics_policy_state', 'research_run_record')}
    payload = {'methodology': {'point_in_time_taxonomy': True}, 'points': [{'as_of_date': '2026-01-02', 'nav': 123}]}
    try:
        with engine.begin() as c:
            for pid in ('current', 'unassigned', 'no-policy', 'deleted-selection', 'legacy-default'):
                c.execute(tables['portfolio_record'].insert().values(portfolio_id=pid, portfolio_name=pid,
                    base_currency='USD', valuation_timezone='Asia/Shanghai', valuation_cutoff_policy='close',
                    inception_date=date(2020, 1, 1), as_of_date=date(2026, 9, 17), nav=123,
                    day_change_value=0, day_change_pct=0, securities_count=0, sort_order=0,
                    default_planning_taxonomy_id='legacy-valid' if pid == 'legacy-default' else 'old'))
            for tid in ('old', 'new'):
                c.execute(tables['taxonomy_record'].insert().values(taxonomy_id=tid, portfolio_id='current',
                    name=tid, taxonomy_type='custom', primary_assignment_scope='instrument',
                    planning_enabled=True, root_default_target_dimension='weight', status='active'))
            c.execute(tables['taxonomy_record'].insert().values(taxonomy_id='legacy-valid', portfolio_id='legacy-default',
                name='Legacy valid default', taxonomy_type='custom', primary_assignment_scope='instrument',
                planning_enabled=True, root_default_target_dimension='weight', status='active'))
            c.execute(tables['analytics_scope_policy_record'].insert().values(analytics_scope_policy_id='p0',
                portfolio_id='current', taxonomy_id='new', taxonomy_node_id='__root__', risk_eligible=False,
                risk_budget_eligible=False, performance_scope='ordinary', valuation_basis='market',
                effective_from=date(2019, 1, 1), policy_version=0, superseded_by_policy_id='p1', created_at='old'))
            c.execute(tables['analytics_taxonomy_selection_record'].insert().values(
                analytics_taxonomy_selection_id='deleted-future', portfolio_id='deleted-selection',
                taxonomy_id='no-longer-exists', effective_from=date(2099, 1, 1), selection_version=50, created_at='old'))
            for version, effective, eligible in ((1, date(2020, 1, 1), False), (2, date(2099, 1, 1), True)):
                c.execute(tables['analytics_scope_policy_record'].insert().values(analytics_scope_policy_id=f'p{version}',
                    portfolio_id='current', taxonomy_id='new', taxonomy_node_id='__root__',
                    risk_eligible=eligible, risk_budget_eligible=eligible, performance_scope='ordinary',
                    valuation_basis='market', effective_from=effective, effective_to=None,
                    policy_version=version, created_at='2026-09-19T00:00:00Z'))
                c.execute(tables['taxonomy_configuration_revision'].insert().values(taxonomy_configuration_revision_id=f'c{version}',
                    portfolio_id='current', taxonomy_id='new', effective_from=effective, effective_to=None,
                    configuration_version=version + 10, configuration_json={'taxonomy': {'name': f'version{version}'}},
                    created_at='2026-09-19T00:00:00Z'))
                for pid in ('current', 'unassigned'):
                    c.execute(tables['analytics_taxonomy_selection_record'].insert().values(
                        analytics_taxonomy_selection_id=f'{pid}s{version}', portfolio_id=pid,
                        taxonomy_id=('new' if pid == 'current' else None) if version == 2 else 'old',
                        effective_from=effective, effective_to=None, selection_version=version + 20,
                        created_at='2026-09-19T00:00:00Z'))
            c.execute(tables['portfolio_calculation_state'].insert().values(portfolio_id='current',
                daily_snapshot_status='current', dirty_from=date(2026, 9, 1), refresh_request_id='old'))
            c.execute(tables['portfolio_analytics_policy_state'].insert().values(portfolio_id='current', current_version=22,
                updated_at='old'))
            c.execute(tables['research_run_record'].insert().values(research_run_id='saved', portfolio_id='current',
                job_type='target_weight_solve', status='completed', lookback_days=90, as_of_date=date(2026, 1, 2),
                detail_json=payload, request_payload_json={'effective_from': '2020-01-01'}))
        command.upgrade(config, 'head')
        inspector = sa.inspect(engine)
        for name in ('analytics_scope_policy_record', 'analytics_taxonomy_selection_record', 'taxonomy_configuration_revision'):
            columns = {col['name'] for col in inspector.get_columns(name, schema='portfolio')}
            assert 'effective_from' not in columns and 'effective_to' not in columns
        with engine.connect() as c:
            assert c.execute(sa.text("SELECT analytics_scope_policy_id, risk_eligible FROM portfolio.analytics_scope_policy_record WHERE superseded_by_policy_id IS NULL")).one() == ('p2', True)
            assert c.scalar(sa.text("SELECT superseded_by_policy_id FROM portfolio.analytics_scope_policy_record WHERE analytics_scope_policy_id='p1'")) == 'p2'
            assert c.scalar(sa.text("SELECT superseded_by_policy_id FROM portfolio.analytics_scope_policy_record WHERE analytics_scope_policy_id='p0'")) == 'p1'
            assert c.scalar(sa.text("SELECT count(*) FROM portfolio.taxonomy_configuration_revision")) == 2
            assert c.scalar(sa.text("SELECT configuration_json FROM portfolio.taxonomy_configuration_revision WHERE superseded_by_revision_id IS NULL")) == {'taxonomy': {'name': 'version2'}}
            defaults = dict(c.execute(sa.text("SELECT portfolio_id, default_planning_taxonomy_id FROM portfolio.portfolio_record")).all())
            assert defaults == {'current': 'new', 'unassigned': None, 'no-policy': None, 'deleted-selection': None, 'legacy-default': 'legacy-valid'}
            assert c.execute(sa.text("SELECT taxonomy_id, selection_version FROM portfolio.analytics_taxonomy_selection_record WHERE portfolio_id='deleted-selection' AND superseded_by_selection_id IS NULL")).one() == (None, 51)
            assert c.scalar(sa.text("SELECT taxonomy_id FROM portfolio.analytics_taxonomy_selection_record WHERE analytics_taxonomy_selection_id='deleted-future'")) == 'no-longer-exists'
            assert c.scalar(sa.text("SELECT taxonomy_id FROM portfolio.analytics_taxonomy_selection_record WHERE portfolio_id='legacy-default' AND superseded_by_selection_id IS NULL")) == 'legacy-valid'
            assert c.scalar(sa.text("SELECT current_version FROM portfolio.portfolio_analytics_policy_state WHERE portfolio_id='current'")) == 23
            states = c.execute(sa.text("SELECT daily_snapshot_status, dirty_from, refresh_request_id FROM portfolio.portfolio_calculation_state")).all()
            assert len(states) == 5 and all(s == 'stale' and d is None and r and r != 'old' for s, d, r in states)
            assert c.execute(sa.text("SELECT nav, as_of_date, inception_date FROM portfolio.portfolio_record WHERE portfolio_id='current'")).one() == (123, date(2026, 9, 17), date(2020, 1, 1))
            assert c.execute(sa.text("SELECT detail_json, request_payload_json FROM portfolio.research_run_record WHERE research_run_id='saved'")).one() == (payload, {'effective_from': '2020-01-01'})
            for table, superseded in (
                ('analytics_scope_policy_record', 'superseded_by_policy_id'),
                ('analytics_taxonomy_selection_record', 'superseded_by_selection_id'),
                ('taxonomy_configuration_revision', 'superseded_by_revision_id'),
            ):
                with pytest.raises(IntegrityError), c.begin_nested():
                    c.execute(sa.text(f"UPDATE portfolio.{table} SET {superseded}=NULL WHERE {superseded} IS NOT NULL"))
        with pytest.raises(RuntimeError, match='pre-migration backup'):
            command.downgrade(config, '20260908_0063')
    finally:
        engine.dispose()
