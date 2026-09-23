"""Retained scope indexing must not rewrite evidence or infer mutable topic scope."""
import json

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import JSON, Text, create_engine, event, select, text, true
from sqlalchemy.orm import Session

from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.research_scope import RESEARCH_SCOPE_INDEX
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services.research_access import (
    instrument_run_scope, research_context_projection, research_projection_rows,
)

from .test_postgres_instrument_registry_constraints import BACKEND_ROOT, postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


def test_monitoring_scope_streams_only_matching_history_and_preserves_originals(postgres_watchlist_env):
    from datetime import UTC, datetime, timedelta
    from watchlist_app.db.models.workbench import ResearchTopic
    from watchlist_app.services.research_triggers import _monitoring_context
    iid = postgres_watchlist_env["instrument_id"]
    stamp = datetime.now(UTC)
    current = {"research_run": True, "instrument_ids": [iid], "cutoff": stamp.isoformat(),
        "market_queries": [{"instrument_id": iid, "query": "Exact issuer disclosure", "entities": []}],
        "source": "original\x00text"}
    with get_session_factory()() as session:
        for topic in ("monitor-public", "monitor-private", "monitor-other"):
            session.add(ResearchTopic(topic_id=topic, title=topic, visibility="team"))
        session.flush()
        fixtures = [
            ("old-poison", "monitor-public", -1, {**current, "source": "unneeded-monitor-history"}),
            ("current-scope", "monitor-public", 0, current),
            ("newer-private", "monitor-private", 1, {**current, "market_queries": [
                {"instrument_id": iid, "query": "Private retained query", "entities": []}]}),
            ("newer-unrelated", "monitor-other", 2, {**current, "instrument_ids": [iid + "-other"],
                "source": "unneeded-monitor-history"}),
        ]
        for key, topic, offset, context in fixtures:
            session.add(ResearchEntry(entry_id=key, topic_id=topic, kind="analysis", title=key,
                created_at=stamp + timedelta(seconds=offset), context_json=context))
        session.add(ResearchEntry(entry_id="original-private-boundary", topic_id="monitor-private", kind="note",
            title="Retained portfolio", context_json={"portfolio_id": "original-private-portfolio"}))
        session.commit()

    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        assert "unneeded-monitor-history" not in value, "Unrelated or unneeded older run was decoded"
        return json.loads(value)
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on"})
    cursors = []
    event.listen(engine, "after_cursor_execute", lambda _conn, cursor, *_: cursors.append(cursor) if getattr(cursor, "name", None) else None)
    try:
        with Session(engine) as session:
            actual = _monitoring_context(session, iid, {"instrument_ids": [iid], "research_actor": {"team_id": "default"}})
            assert actual == current
            assert cursors and all(cursor.closed for cursor in cursors)
    finally:
        engine.dispose()


def test_streamed_current_states_and_notebook_keep_exact_sources_and_private_history(postgres_watchlist_env):
    from datetime import UTC, datetime, timedelta
    from watchlist_app.db.models.workbench import ResearchTopic
    from watchlist_app.services.research_dossier import _notebooks
    from watchlist_app.services.sector_research import review_states

    iid = postgres_watchlist_env["instrument_id"]
    stamp = datetime.now(UTC)
    with get_session_factory()() as session:
        for topic_id in ("current-public", "retained-private"):
            session.add(ResearchTopic(topic_id=topic_id, title=topic_id, visibility="team"))
        session.flush()
        for topic_id, offset in (("current-public", 0), ("retained-private", 1)):
            session.add(ResearchEntry(entry_id=topic_id, topic_id=topic_id, kind="analysis", title=topic_id,
                status="completed", created_at=stamp + timedelta(seconds=offset),
                context_json={"research_run": True, "instrument_ids": [iid], "cutoff": stamp.isoformat(),
                    "reviews": {iid: {"status": "completed", "research": {
                        "investment_view": {"direction": "exact\x00direction"},
                        "sources": [{"source_id": "original", "text": "exact\x00source"}]}}}}))
        session.add(ResearchEntry(entry_id="original-private-scope", topic_id="retained-private",
            kind="note", title="Old permission", context_json={"portfolio_id": "private"}))
        session.commit()
    with get_session_factory()() as session:
        state = review_states(session, instrument_ids=[iid])["latest"][iid]
        assert state["run_id"] == "current-public"
        assert state["current_summary"] == "exact\x00direction"
        notebook, history = _notebooks(session, iid, include_history=False)
        assert notebook["run_id"] == "current-public"
        assert notebook["sources"][0]["text"] == "exact\x00source"
        assert history == []
        assert not session.identity_map


@pytest.mark.parametrize("postgres_watchlist_env", ["20260920_0059"], indirect=True)
def test_retained_scope_migration_preserves_originals_and_indexes_generic_plans(postgres_watchlist_env):
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    factory = get_session_factory()
    get_engine().dispose()
    contexts = {
        "plain": {"instrument_ids": ["xlk", "spy"], "original": "retained"},
        "nul": {"instrument_ids": ["xlk"], "original": "a\x00b", "selected": "c\x00d"},
        "literal": {"instrument_ids": [r"literal\u0000"], "original": r"literal\u0000"},
        "escaped": {"instrument_ids": ["xlk"], "original": "slash\\\x00 plus \\\\u0000"},
        "empty": {"instrument_ids": []},
        "missing": {"original": "scope absent"},
        "object": {"instrument_ids": {"xlk": True}},
        "string": {"instrument_ids": "xlk"},
        "null": {"instrument_ids": None},
        # Preserve json_array_elements_text's legacy identity conversion; a
        # JSONB ?| filter alone would silently stop matching these old values.
        "legacy": {"instrument_ids": [123, True, None, {"a": 1}, ["x"]]},
    }
    with factory() as session, session.begin():
        session.execute(text("INSERT INTO research_topic (topic_id,title,instrument_ids,portfolio_id,status,created_at,updated_at,question,conclusion,team_id,visibility) "
            "VALUES ('scope-fixture','Fixture','[\"current-topic-only\"]',NULL,'active',now(),now(),'','','default','team')"))
        for identifier, context in contexts.items():
            session.execute(text("INSERT INTO research_entry (entry_id,topic_id,kind,title,body,source,context_json,status,team_id,created_at,updated_at) "
                "VALUES (:id,'scope-fixture','analysis','Fixture','','',CAST(:context AS json),'completed','default',now(),now())"),
                {"id": "scope-" + identifier, "context": json.dumps(context, ensure_ascii=True)})

    def originals():
        with factory() as session:
            return session.execute(text("SELECT entry_id,context_json::text FROM research_entry WHERE topic_id='scope-fixture' ORDER BY entry_id")).all()

    before = originals()
    command.upgrade(config, "20260920_0060")
    assert originals() == before
    with factory() as session:
        assert session.scalar(text("SELECT version_num FROM watchlist.alembic_version")) == "20260920_0060"
        assert session.scalar(text("SELECT indexdef FROM pg_indexes WHERE schemaname='watchlist' AND indexname=:name"),
                              {"name": RESEARCH_SCOPE_INDEX}).endswith("USING gin (research_entry_instrument_scope(context_json))")
        assert session.execute(text("SELECT provolatile,proparallel FROM pg_proc WHERE oid='watchlist.research_entry_instrument_scope(json)'::regprocedure")).one() == ("i", "s")
        assert session.scalars(select(ResearchEntry.entry_id).where(instrument_run_scope(session, "xlk")).order_by(ResearchEntry.entry_id)).all() == [
            "scope-escaped", "scope-nul", "scope-plain"]
        assert session.scalars(select(ResearchEntry.entry_id).where(instrument_run_scope(session, "current-topic-only"))).all() == []
        for identifier in ("123", "true", '{"a": 1}', '["x"]'):
            assert session.scalars(select(ResearchEntry.entry_id).where(instrument_run_scope(session, identifier))).all() == ["scope-legacy"]
        assert session.scalars(select(ResearchEntry.entry_id).where(instrument_run_scope(session, r"literal\u0000"))).all() == ["scope-literal"]
        assert session.scalars(select(ResearchEntry.entry_id).where(instrument_run_scope(session, []))).all() == []
        assert set(session.scalars(select(ResearchEntry.entry_id).where(instrument_run_scope(session, ["spy", r"literal\u0000"])))) == {"scope-plain", "scope-literal"}

        # Selected NUL-bearing data still comes from the exact original JSON.
        relation, values = research_context_projection(session, {"selected": Text, "original": JSON})
        query = select(ResearchEntry.entry_id, values["selected"].label("selected"), values["original"].label("original")).select_from(ResearchEntry).join(relation, true()).where(ResearchEntry.entry_id == "scope-nul")
        restored = research_projection_rows(session, query, {"selected": ("selected",), "original": ("original",)})[0]
        assert restored.selected == "c\x00d"
        assert restored.original == "a\x00b"

        session.execute(text("SET LOCAL plan_cache_mode=force_generic_plan"))
        session.execute(text("SET LOCAL enable_seqscan=off"))  # Tiny fixture; production's natural plan is measured separately.
        session.execute(text("PREPARE scope_lookup(text[]) AS SELECT entry_id FROM watchlist.research_entry "
                             "WHERE watchlist.research_entry_instrument_scope(context_json) && $1"))
        for _ in range(7):
            assert len(session.execute(text("EXECUTE scope_lookup(ARRAY['xlk'])")).all()) == 3
        plan = session.scalar(text("EXPLAIN (FORMAT JSON) EXECUTE scope_lookup(ARRAY['xlk'])"))
        assert RESEARCH_SCOPE_INDEX in json.dumps(plan)
        session.execute(text("DEALLOCATE scope_lookup"))

    with factory() as session, session.begin():
        session.execute(text("UPDATE research_entry SET context_json='{}' WHERE entry_id='scope-plain'"))
        assert "scope-plain" not in session.scalars(select(ResearchEntry.entry_id).where(instrument_run_scope(session, "xlk"))).all()
        session.execute(text("UPDATE research_entry SET context_json=CAST(:context AS json) WHERE entry_id='scope-plain'"), {"context": dict(before)["scope-plain"]})
    assert originals() == before
    get_engine().dispose()
    command.downgrade(config, "20260920_0059")
    assert originals() == before
    with factory() as session:
        assert session.scalar(text("SELECT to_regclass('watchlist.ix_research_entry_instrument_scope')")) is None
        assert session.scalar(text("SELECT to_regprocedure('watchlist.research_entry_instrument_scope(json)')")) is None
    command.upgrade(config, "head")
    assert originals() == before
