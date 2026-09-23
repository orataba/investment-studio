"""Real PostgreSQL index, original-text and historical ACL contracts."""
import json

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.research_scope import RESEARCH_PORTFOLIO_SCOPE_INDEX, research_portfolio_scope_expression
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services.research_access import topic_portfolio_ids_by_topic

from .test_postgres_instrument_registry_constraints import BACKEND_ROOT, postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


@pytest.mark.parametrize("postgres_watchlist_env", ["20260923_0062"], indirect=True)
def test_portfolio_scope_migration_preserves_originals_and_generic_index_plan(postgres_watchlist_env):
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    factory = get_session_factory()
    contexts = {
        "plain": {"portfolio_id": "old", "source": "Original"},
        "risk": {"risk_scope": {"portfolio_id": "risk-old"}, "source": "original\x00text"},
        "literal": {"portfolio_id": r"literal\u0000", "source": r"literal\u0000"},
        "nul-id": {"portfolio_id": "scope\x00id"},
        "public": {"source": "large\x00original", "risk_scope": {"watchlist_id": "public"}},
        "empty": {"portfolio_id": "", "risk_scope": {"portfolio_id": None}},
    }
    with factory() as session:
        session.add(ResearchTopic(topic_id="portfolio-scope", title="Scope", visibility="team", portfolio_id="current"))
        session.flush()
        for key, context in contexts.items():
            session.add(ResearchEntry(entry_id=key, topic_id="portfolio-scope", kind="note", title=key,
                context_json=context))
        session.commit()

    def originals():
        with factory() as session:
            return session.execute(text(
                "SELECT entry_id,context_json::text FROM research_entry WHERE topic_id='portfolio-scope' ORDER BY entry_id"
            )).all()

    before = originals()
    get_engine().dispose()
    command.upgrade(config, "20260923_0063")
    assert originals() == before
    with factory() as session:
        assert session.execute(text(
            "SELECT provolatile,proparallel FROM pg_proc "
            "WHERE oid='watchlist.research_entry_has_retained_portfolio_scope(json)'::regprocedure"
        )).one() == ("i", "s")
        definition = session.scalar(text(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='watchlist' AND indexname=:name"
        ), {"name": RESEARCH_PORTFOLIO_SCOPE_INDEX})
        assert "USING btree (topic_id)" in definition
        assert "WHERE watchlist.research_entry_has_retained_portfolio_scope(context_json)" in definition or (
            "WHERE research_entry_has_retained_portfolio_scope(context_json)" in definition)

        # Exclude only an unambiguous absence of scope. Invalid structures are
        # candidates, so the existing exact reader still decides or fails closed.
        empty = [{}, {"portfolio_id": None}, {"portfolio_id": ""},
                 {"risk_scope": None}, {"risk_scope": {}}, {"risk_scope": {"portfolio_id": None}},
                 {"risk_scope": {"portfolio_id": ""}}, {"source": "kept\x00original"}]
        candidates = [None, [], "not-an-object", {"portfolio_id": False}, {"portfolio_id": 0},
                      {"portfolio_id": {}}, {"portfolio_id": []}, {"portfolio_id": " "},
                      {"risk_scope": ""}, {"risk_scope": False}, {"risk_scope": []},
                      {"risk_scope": {"portfolio_id": 0}}, {"risk_scope": {"portfolio_id": False}},
                      {"risk_scope": {"portfolio_id": []}}, {"risk_scope": {"portfolio_id": {}}},
                      {"portfolio_id": "\x00"}, {"portfolio_id": r"\u0000"}]
        for context in empty + candidates:
            actual = session.scalar(text(
                "SELECT watchlist.research_entry_has_retained_portfolio_scope(CAST(:context AS json))"
            ), {"context": json.dumps(context, ensure_ascii=True)})
            assert actual is (context in candidates), context

        topic = session.get(ResearchTopic, "portfolio-scope")
        assert topic_portfolio_ids_by_topic(session, [topic]) == {
            "portfolio-scope": {"current", "old", "risk-old", r"literal\u0000", "scope\x00id"},
        }
        session.execute(text("SET LOCAL plan_cache_mode=force_generic_plan"))
        session.execute(text("SET LOCAL enable_seqscan=off"))  # Force an eligible index for this tiny fixture.
        session.execute(text(
            "PREPARE portfolio_scope_lookup(text) AS SELECT entry_id FROM watchlist.research_entry "
            "WHERE topic_id=$1 AND watchlist.research_entry_has_retained_portfolio_scope(context_json)"
        ))
        for _ in range(7):
            assert set(session.scalars(text("EXECUTE portfolio_scope_lookup('portfolio-scope')"))) == {
                "plain", "risk", "literal", "nul-id",
            }
        plan = session.scalar(text("EXPLAIN (FORMAT JSON) EXECUTE portfolio_scope_lookup('portfolio-scope')"))
        assert RESEARCH_PORTFOLIO_SCOPE_INDEX in json.dumps(plan)
        session.execute(text("DEALLOCATE portfolio_scope_lookup"))

    # The database maintains membership on writes; no application cache or
    # secondary scope record needs invalidation or synchronization.
    with factory() as session, session.begin():
        row = session.get(ResearchEntry, "public")
        original = row.context_json
        row.context_json = {"risk_scope": {"portfolio_id": "new-boundary"}}
        session.flush()
        selected = select(ResearchEntry.entry_id).where(
            ResearchEntry.entry_id == "public", research_portfolio_scope_expression(ResearchEntry.context_json))
        assert session.scalar(selected) == "public"
        row.context_json = original
        session.flush()
        assert session.scalar(selected) is None
    assert originals() == before
    get_engine().dispose()
    command.downgrade(config, "20260923_0062")
    assert originals() == before
    with factory() as session:
        assert session.scalar(text("SELECT to_regclass('watchlist.ix_research_entry_retained_portfolio_topic')")) is None
        assert session.scalar(text(
            "SELECT to_regprocedure('watchlist.research_entry_has_retained_portfolio_scope(json)')"
        )) is None
    command.upgrade(config, "head")
    assert originals() == before


def test_indexed_acl_does_not_decode_unscoped_originals(postgres_watchlist_env, monkeypatch):
    from fastapi import HTTPException
    from watchlist_app.services import research_access
    with get_session_factory()() as session:
        session.add_all([ResearchTopic(topic_id=tid, title=tid, visibility="team") for tid in ("shared", "other")])
        session.flush()
        for index in range(8):
            session.add(ResearchEntry(entry_id=f"large-{index}", topic_id="shared", kind="analysis", title="Public",
                context_json={"instrument_ids": [f"instrument-{index}"], "original": "never-decode-public\x00" + "x" * 10000}))
        session.add(ResearchEntry(entry_id="old-permission", topic_id="shared", kind="evidence", title="History",
            status="failed", context_json={"risk_scope": {"portfolio_id": "old-portfolio"}, "original": "retained\x00"}))
        session.add(ResearchEntry(entry_id="other-permission", topic_id="other", kind="note", title="Other",
            context_json={"portfolio_id": "unrelated-portfolio"}))
        session.commit()

    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        assert "never-decode-public" not in value
        return json.loads(value)

    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on"})
    try:
        with Session(engine) as session:
            topic = session.get(ResearchTopic, "shared")
            assert topic_portfolio_ids_by_topic(session, [topic]) == {"shared": {"old-portfolio"}}

            def deny(portfolio_id):
                assert portfolio_id == "old-portfolio"
                raise HTTPException(404, "No access to the historical portfolio")
            monkeypatch.setattr(research_access, "require_portfolio", deny)
            with pytest.raises(HTTPException) as denied:
                research_access.require_topic_access(session, topic)
            assert denied.value.status_code == 404
    finally:
        engine.dispose()
