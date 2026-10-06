"""Small read projection backfills exact originals and follows transactional writes."""
from copy import deepcopy
import json

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import defer

from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services.research_access import instrument_run_scope
from watchlist_app.services.research_read_projection import browser_source_view
from .test_postgres_instrument_registry_constraints import BACKEND_ROOT, postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


@pytest.mark.parametrize("postgres_watchlist_env", ["20261002_0064"], indirect=True)
def test_backfill_and_live_writes_preserve_originals_and_bound_read_queries(postgres_watchlist_env):
    source = {"source_id": "computed:nested", "source_type": "computed_metric", "title": "Exact\x00title",
        "metadata": {}, "methodology": {"metric": "ewma_volatility"},
        "sources": [{"text": "Large retained original\x00" * 100000}], "input_snapshot": {"nested": "snapshot"}}
    original = {"research_run": True, "instrument_ids": ["xlk"], "cutoff": "2026-10-01T00:00:00Z",
        "portfolio_id": "portfolio\x00exact", "sources": [source], "versions": [{"sources": [source]}],
        "research_dossiers": [{"instrument_id": "xlk", "themes": [{"theme_id": "theme", "created_at": "2026-09-30"}]}],
        "reviews": {"xlk": {"status": "completed", "research": {"investment_view": {"direction": "Retained judgment"},
            "sources": [source], "modules": [{"versions": [{"sources": [source]}]}]}}}}
    factory = get_session_factory()
    with factory() as session, session.begin():
        session.execute(text("INSERT INTO research_topic (topic_id,title,instrument_ids,status,created_at,updated_at,question,conclusion,team_id,visibility) "
            "VALUES ('projection-fixture','Fixture','[]','active',now(),now(),'','','default','team')"))
        session.execute(text("INSERT INTO research_entry (entry_id,topic_id,kind,title,body,source,context_json,status,team_id,created_at,updated_at) "
            "VALUES ('projection-row','projection-fixture','analysis','Fixture','','',CAST(:context AS json),'completed','default',now(),now())"),
            {"context": json.dumps(original)})
    def originals():
        with factory() as session:
            return session.execute(text("SELECT context_json::text,created_at,updated_at FROM research_entry WHERE entry_id='projection-row'")).one()
    before = originals()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "head")
    assert originals() == before
    with factory() as session:
        projection = session.scalar(select(ResearchEntry.read_context_json).where(ResearchEntry.entry_id == "projection-row"))
        assert projection["portfolio_id"] == "portfolio\x00exact"
        assert projection["sources"] == [browser_source_view(source)]
        assert projection["reviews"]["xlk"]["research"]["modules"][0]["versions"][0]["sources"] == [browser_source_view(source)]
        assert projection["attempted_theme_baselines"] == {"theme": "2026-09-30"}
        assert "versions" not in projection and "sources" not in projection["sources"][0]
        assert len(json.dumps(projection)) < 5000
        indexes = session.scalars(text("SELECT indexdef FROM pg_indexes WHERE schemaname='watchlist' AND indexname IN "
            "('ix_research_entry_instrument_scope','ix_research_entry_retained_portfolio_topic')")).all()
        assert len(indexes) == 2 and all("read_context_json" in value for value in indexes)
        assert session.scalar(select(ResearchEntry.entry_id).where(instrument_run_scope(session, "xlk"))) == "projection-row"

    # Updating only runtime state must neither hydrate nor recompute the input snapshot.
    with factory() as session:
        row = session.scalar(select(ResearchEntry).options(defer(ResearchEntry.context_json)).where(ResearchEntry.entry_id == "projection-row"))
        selects = []
        event.listen(session, "do_orm_execute", lambda state: selects.append(state.statement) if state.is_select else None)
        row.status = "failed"
        session.flush()
        assert selects == []
        session.rollback()
    with factory() as session:
        row = session.get(ResearchEntry, "projection-row")
        row.context_json = {**deepcopy(original), "instrument_ids": ["spy"], "portfolio_id": None}
        session.flush()
        assert session.scalar(select(ResearchEntry.entry_id).where(instrument_run_scope(session, "xlk"))) is None
        assert session.scalar(select(ResearchEntry.entry_id).where(instrument_run_scope(session, "spy"))) == "projection-row"
        assert row.read_context_json["instrument_ids"] == ["spy"]
        assert row.context_json["sources"] == original["sources"]
        session.rollback()
    assert originals() == before
    with factory() as session:
        assert session.scalar(select(ResearchEntry.read_context_json).where(ResearchEntry.entry_id == "projection-row")) == projection
    get_engine().dispose()


@pytest.mark.parametrize("malformed", [[], "invalid scope", None])
def test_malformed_retained_scope_is_preserved_and_does_not_become_public(postgres_watchlist_env, malformed):
    from sqlalchemy.exc import DataError
    from watchlist_app.db.models.workbench import ResearchTopic
    from watchlist_app.services.research_access import topic_portfolio_ids_by_topic
    with get_session_factory()() as session:
        topic = ResearchTopic(topic_id="malformed-scope", title="Malformed retained scope", visibility="team")
        session.add(topic)
        session.flush()
        session.add(ResearchEntry(entry_id="malformed-run", topic_id=topic.topic_id, kind="analysis", title="Retained",
            context_json=malformed))
        session.commit()
        assert session.scalar(select(ResearchEntry.read_context_json).where(ResearchEntry.entry_id == "malformed-run")) == malformed
        with pytest.raises(DataError):
            topic_portfolio_ids_by_topic(session, [topic])


def test_reference_validation_reads_exact_judgment_metadata_without_decoding_retained_sources(postgres_watchlist_env):
    from datetime import datetime
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from watchlist_app.db.models.workbench import ResearchTopic
    from watchlist_app.services.research_activity import research_activity

    iid = postgres_watchlist_env['instrument_id']
    clock = '2026-09-27T00:00:00+00:00'
    source = {'source_id': 'computed:original', 'source_type': 'computed_metric',
        'title': 'Original evidence', 'sources': [{'text': 'unneeded-validation-original' * 10000}]}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id='validation-reference', title='Original judgment', visibility='team'))
        session.flush()
        session.add(ResearchEntry(entry_id='validation-original', topic_id='validation-reference',
            kind='analysis', title='Original judgment', status='completed', completed_at=datetime.fromisoformat(clock),
            context_json={'research_run': True, 'instrument_ids': [iid], 'cutoff': clock,
                'reviews': {iid: {'status': 'completed', 'research': {'version_id': 'original-notebook',
                    'sources': [source], 'forecasts': [{'key': 'capacity', 'version_id': 'original-forecast',
                        'theme_id': 'original-theme', 'claim': 'Retained\u0000judgment', 'horizon': 'Next disclosure',
                        'updated_at': clock, 'source_ids': [source['source_id']]}]}}}}))
        session.commit()

    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        assert 'unneeded-validation-original' not in value
        return json.loads(value)
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={'options': '-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on'})
    try:
        with Session(engine) as session:
            updates = research_activity(session, iid, source_metadata_only=True)['updates']
            original = next(row for row in updates if row['update_id'] == 'research:original-forecast')
            assert original['body'] == 'Retained\u0000judgment' and original['recorded_at'] == clock
            assert original['run_id'] == 'validation-original' and original['theme_ids'] == ['original-theme']
            assert original['reference']['forecast_version_id'] == 'original-forecast'
    finally:
        engine.dispose()
