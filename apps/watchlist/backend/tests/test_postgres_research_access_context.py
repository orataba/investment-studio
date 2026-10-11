"""Exact ACL projection: real backfill, large history and locked reauthorization."""
from dataclasses import replace
import json
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session, defer
from studio_identity import Principal, principal_context

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.research_access_context import derive_access_context
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services import research_access as access
from .test_postgres_instrument_registry_constraints import BACKEND_ROOT, postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


@pytest.mark.parametrize("postgres_watchlist_env", ["20261009_0068"], indirect=True)
def test_access_backfill_preserves_both_contexts_and_timestamps_and_downgrades(postgres_watchlist_env):
    contexts = {
        "large": {"portfolio_id": "A\x00exact", "risk_scope": {"portfolio_id": r"literal\u0000"},
                  "research_actor": {"user_id": "pm\x00one"}, "execution": {"attempt": 2},
                  "risk_inputs": {"evidence_projection": "risk_decision_v1", "text": "RETAINED" * 150000}},
        "public": {"unrelated": "private\x00original"},
        "empty": {}, "null": None, "array": [], "scalar": "invalid retained scope",
    }
    factory = get_session_factory()
    with factory() as session, session.begin():
        session.add(ResearchTopic(topic_id="access-backfill", title="Backfill", visibility="team"))
        session.flush()
        for key, context in contexts.items():
            # Seed the actual previous schema; existing read projections contain
            # all four access fields but may still have large analytical inputs.
            session.execute(text("INSERT INTO research_entry "
                "(entry_id,topic_id,kind,title,body,source,context_json,read_context_json,status,team_id,created_at,updated_at) "
                "VALUES (:id,'access-backfill','analysis',:id,'','',CAST(:context AS json),CAST(:context AS json),"
                "'completed','default',now(),now())"), {"id": key, "context": json.dumps(context)})

    def retained():
        with factory() as session:
            return session.execute(text("SELECT entry_id,context_json::text,read_context_json::text,created_at,updated_at "
                "FROM research_entry WHERE topic_id='access-backfill' ORDER BY entry_id")).all()

    before = retained()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    statements = []
    def trace(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)
    from sqlalchemy.engine import Engine
    event.listen(Engine, "before_cursor_execute", trace)
    try:
        command.upgrade(config, "head")
    finally:
        event.remove(Engine, "before_cursor_execute", trace)
    assert not any("research_entry.context_json" in sql for sql in statements)
    assert retained() == before
    with factory() as session:
        actual = dict(session.execute(select(ResearchEntry.entry_id, ResearchEntry.access_context_json)).all())
        assert actual == {key: derive_access_context(value) for key, value in contexts.items()}
        assert len(json.dumps(actual["large"])) < 250
        definition = session.scalar(text("SELECT indexdef FROM pg_indexes WHERE schemaname='watchlist' "
            "AND indexname='ix_research_entry_retained_portfolio_topic'"))
        assert "access_context_json" in definition
        # Malformed history cannot disappear into an empty/public projection.
        with pytest.raises(DataError):
            access.topic_portfolio_ids(session, session.get(ResearchTopic, "access-backfill"))
    command.downgrade(config, "20261009_0068")
    assert retained() == before
    with factory() as session:
        definition = session.scalar(text("SELECT indexdef FROM pg_indexes WHERE schemaname='watchlist' "
            "AND indexname='ix_research_entry_retained_portfolio_topic'"))
        assert "read_context_json" in definition and "access_context_json" not in definition
    command.upgrade(config, "head")
    assert retained() == before


def test_history_authorization_uses_only_narrow_exact_fields(postgres_watchlist_env, monkeypatch):
    factory = get_session_factory()
    principal = Principal("pm\x00one", "PM", "default", resource_scope={"kind": "run", "id": "current", "attempt": "2"})
    scopes = {"current": {"portfolio_id": "A"}, "old-note": {"portfolio_id": "B\x00exact"},
              "old-risk": {"risk_scope": {"portfolio_id": r"literal\u0000"}}}
    with factory() as session, session.begin():
        session.add(ResearchTopic(topic_id="history", title="History", visibility="team", portfolio_id="A"))
        session.flush()
        for key, scope in scopes.items():
            session.add(ResearchEntry(entry_id=key, topic_id="history", kind="analysis" if key == "current" else "note",
                title=key, status="running" if key == "current" else "recorded", context_json={**scope,
                    "research_actor": {"kind": "user", "user_id": principal.user_id}, "execution": {"attempt": 2},
                    "risk_run": True, "risk_inputs": {"evidence_projection": "risk_decision_v1",
                        "retained": "DO_NOT_PARSE_RETAINED_HISTORY" * 50000}}))

    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        assert "DO_NOT_PARSE_RETAINED_HISTORY" not in value
        return json.loads(value)
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on"})
    statements, checked = [], []
    event.listen(engine, "before_cursor_execute", lambda _c, _cu, sql, _p, _co, _m: statements.append(sql))
    monkeypatch.setattr(access, "require_portfolio", lambda value: checked.append(value))
    request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/research/runs/current/risk-read"))
    try:
        with principal_context(principal), Session(engine) as session:
            access.enforce_request(request, session)
        assert set(checked) == {"A", "B\x00exact", r"literal\u0000"}
        assert not any("research_entry.read_context_json" in sql or "research_entry.context_json" in sql for sql in statements)
        assert any("research_entry.access_context_json" in sql for sql in statements)
        with principal_context(replace(principal, user_id="wrong-actor")), Session(engine) as session:
            with pytest.raises(HTTPException) as error:
                access.enforce_request(request, session)
            assert error.value.status_code == 403
        def denied(value):
            if value == "B\x00exact":
                raise HTTPException(404, "No historical portfolio access")
        monkeypatch.setattr(access, "require_portfolio", denied)
        with principal_context(principal), Session(engine) as session:
            with pytest.raises(HTTPException) as error:
                access.enforce_request(request, session)
            assert error.value.status_code == 404
    finally:
        engine.dispose()


@pytest.mark.parametrize("change,expected_status", [("attempt", 409), ("actor", 403), ("portfolio", 404)])
def test_locked_risk_check_reauthorizes_after_middleware(postgres_watchlist_env, monkeypatch, change, expected_status):
    from watchlist_app.api.routes.risk_officer import _running_risk_run
    factory = get_session_factory()
    principal = Principal("pm-one", "PM", "default", resource_scope={"kind": "run", "id": "race", "attempt": "1"})
    denied = False
    def portfolio_access(_):
        if denied:
            raise HTTPException(404, "Portfolio permission revoked")
    monkeypatch.setattr(access, "require_portfolio", portfolio_access)
    with factory() as session, session.begin():
        session.add(ResearchTopic(topic_id="race-topic", title="Race", visibility="team", portfolio_id="A"))
        session.flush()
        session.add(ResearchEntry(entry_id="race", topic_id="race-topic", kind="analysis", title="Race", status="running",
            context_json={"research_actor": {"kind": "user", "user_id": "pm-one"}, "execution": {"attempt": 1},
                          "risk_run": True, "risk_scope": {"portfolio_id": "A"}, "risk_inputs": {"instrument_ids": []}}))
    with principal_context(principal), factory() as session:
        cached = session.get(ResearchEntry, "race")
        access.enforce_request(SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/research/runs/race/risk-read")), session)
        if change == "portfolio":
            denied = True
        else:
            with factory() as writer, writer.begin():
                row = writer.get(ResearchEntry, "race")
                row.context_json = {**row.context_json, **({"execution": {"attempt": 2}} if change == "attempt" else {
                    "research_actor": {"kind": "user", "user_id": "another"}})}
            with factory() as reader:
                current = reader.scalar(select(ResearchEntry.access_context_json).where(ResearchEntry.entry_id == "race"))
                assert current["execution"]["attempt"] == (2 if change == "attempt" else 1)
                assert current["research_actor"]["user_id"] == ("another" if change == "actor" else "pm-one")
                with pytest.raises(HTTPException) as error:
                    access.enforce_request(SimpleNamespace(method="POST", url=SimpleNamespace(
                        path="/api/research/runs/race/risk-read")), reader)
                assert error.value.status_code == expected_status
        with pytest.raises(HTTPException) as error:
            _running_risk_run(session, "race")
        assert error.value.status_code == expected_status
        assert cached.entry_id == "race"  # The locked read refreshed the identity-map entry.
    with factory() as session:
        row = session.scalar(select(ResearchEntry).options(defer(ResearchEntry.context_json)).where(ResearchEntry.entry_id == "race"))
        statements = []
        event.listen(session, "do_orm_execute", lambda state: statements.append(state.statement) if state.is_select else None)
        row.status = "failed"
        session.flush()
        assert statements == []  # Status-only recovery does not hydrate/rebuild either projection.
        session.rollback()
