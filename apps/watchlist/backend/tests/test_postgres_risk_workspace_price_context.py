"""Workspace inputs publish atomically with risk, without reading chart history."""
from datetime import UTC, datetime

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import event, select, text

from watchlist_app.db.models import InstrumentDetail, InstrumentChartReadModel, InstrumentRiskReadModel
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services.risk_workspace import read_risk_workspace
from watchlist_app.services.risk_workspace_price_context import build_workspace_price_context
from .test_postgres_instrument_registry_constraints import BACKEND_ROOT, postgres_watchlist_env
from .test_price_risk import series

pytestmark = pytest.mark.postgresql_integration


def publish(session, iid, value, source):
    risk = session.get(InstrumentRiskReadModel, iid)
    risk.payload_json = {'current_drawdown': value, 'data_quality': {'status': 'ready'},
        'monitoring_summary': {'unselected_history': ['retained'] * 10000}}
    risk.workspace_price_context_json = build_workspace_price_context(source, risk.payload_json)
    session.get(InstrumentChartReadModel, iid).payload_json = {'research_returns': source}
    session.commit()


def prepare(factory, iid):
    with factory() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type='public_fund', detail_view_type='public_fund',
            instrument_name='Atomic workspace', is_active=True, metadata_json={}))
        session.flush()
        session.add(InstrumentRiskReadModel(instrument_id=iid, payload_json={}, data_freshness_status='fresh'))
        session.add(InstrumentChartReadModel(instrument_id=iid, payload_json={}, data_freshness_status='fresh'))
        session.commit()
        publish(session, iid, -10, series([100, 95, 90]))


def test_workspace_risk_and_price_context_cannot_mix_concurrent_publications(postgres_watchlist_env, monkeypatch):
    factory, iid = get_session_factory(), postgres_watchlist_env['instrument_id']
    prepare(factory, iid)
    from watchlist_app.services import risk_workspace_projection
    monkeypatch.setattr(risk_workspace_projection, 'return_series', lambda *_: pytest.fail('workspace read chart history'))
    published = False
    def publish_after_select(_conn, _cursor, statement, *_):
        nonlocal published
        if not published and statement.startswith('SELECT') and 'instrument_risk_read_model' in statement:
            published = True
            with factory() as writer:
                publish(writer, iid, -20, series([100, 90, 80]))
    event.listen(get_engine(), 'after_cursor_execute', publish_after_select)
    try:
        with factory() as reader:
            old = read_risk_workspace(reader, instrument_id=iid, workspace_projection=True)['instruments'][0]
        assert published
    finally:
        event.remove(get_engine(), 'after_cursor_execute', publish_after_select)
    with factory() as reader:
        new = read_risk_workspace(reader, instrument_id=iid, workspace_projection=True)['instruments'][0]
    assert old['risk']['current_drawdown'] == -10
    assert old['period_readings'][0]['return_pct'] == (90 / 95 - 1) * 100
    assert new['risk']['current_drawdown'] == -20
    assert new['period_readings'][0]['return_pct'] == (80 / 90 - 1) * 100
    assert 'monitoring_summary' not in old['risk'] and 'monitoring_summary' not in new['risk']


def test_workspace_context_migration_preserves_risk_original_and_clocks(postgres_watchlist_env):
    factory, iid = get_session_factory(), postgres_watchlist_env['instrument_id']
    prepare(factory, iid)
    with factory() as session:
        risk = session.get(InstrumentRiskReadModel, iid)
        risk.source_cutoff_at = datetime(2026, 10, 1, tzinfo=UTC)
        risk.last_recalculated_at = datetime(2026, 10, 2, tzinfo=UTC)
        session.commit()
    columns = [column for column in InstrumentRiskReadModel.__table__.c if column.name != 'workspace_price_context_json']
    def original():
        with factory() as session:
            return session.execute(select(*columns).where(InstrumentRiskReadModel.instrument_id == iid)).one()
    before = original()
    config = Config(str(BACKEND_ROOT / 'alembic.ini'))
    config.set_main_option('script_location', str(BACKEND_ROOT / 'alembic'))
    get_engine().dispose()
    command.downgrade(config, '20261016_0075')
    assert original() == before
    command.upgrade(config, 'head')
    assert original() == before
    with factory() as session:
        assert session.scalar(text('SELECT workspace_price_context_json FROM watchlist.instrument_risk_read_model WHERE instrument_id=:iid'), {'iid': iid}) is None
        # A retained row remains readable without enqueueing or manufacturing a
        # publication timestamp; it uses the exact legacy calculation.
        from watchlist_app.db.models.recalc import RecalcJob
        jobs = list(session.scalars(select(RecalcJob.recalc_job_id)))
        read_risk_workspace(session, instrument_id=iid, workspace_projection=True)
        assert not session.new and not session.dirty
        assert list(session.scalars(select(RecalcJob.recalc_job_id))) == jobs
