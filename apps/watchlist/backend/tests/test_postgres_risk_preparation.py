"""Risk preparation must not hydrate unused notebooks or a previous run twice."""
from datetime import UTC, datetime, timedelta
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from studio_identity import Principal, principal_context

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services import risk_officer, sector_research

from .test_postgres_instrument_registry_constraints import postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


@pytest.mark.parametrize("selected_nul", [False, True])
def test_risk_states_project_current_fields_without_changing_scope_or_version(postgres_watchlist_env, selected_nul):
    iid = postgres_watchlist_env["instrument_id"]
    ids = [iid, "modules-only", "empty-notebook", "null-notebook"]
    stamp = datetime.now(UTC)
    direction = "current\x00direction" if selected_nul else r"current literal\u0000"
    large = "UNUSED_NOTEBOOK_ORIGINAL" * 10000 + "\x00"
    current = {"source_run_id": "retained-source-run", "investment_view": {
        "direction": direction, "source_run_id": "view-run", "updated_at": "2026-09-27",
        "versions": [{"direction": large}]},
        "questions": [{"key": "active", "claim": r"literal\u0000", "versions": [large], "sources": [large]},
                      {"key": "closed", "tracking_status": "closed"}, {"key": "null-status", "tracking_status": None},
                      {"key": "another-active", "claim": "Retain source ordering"}],
        "forecasts": [{"key": "open", "status": "active", "review_on": "2026-10-01", "sources": [large]},
                      {"key": "withdrawn", "status": "withdrawn"}, {"key": "null-status", "status": None}],
        "sources": [{"source_id": "full-original", "text": large}], "prior_analysis": large}
    base = {"sector_run": True, "instrument_ids": ids, "cutoff": stamp.isoformat(), "reviews": {
        key: {"status": "completed", "coverage": [], "research": {"investment_view": {"direction": "older " + key}}}
        for key in ids}}
    retained = {**base, "recordkeeping_only": True, "reviews": {
        iid: {"status": "completed", "research": current},
        "modules-only": {"status": "completed", "research": {"modules": [large]}},
        "empty-notebook": {"status": "completed", "research": {}},
        "null-notebook": {"status": "completed", "research": None},
        "outside-request": {"status": "completed", "research": {"sources": [large]}},
    }}
    with get_session_factory()() as session:
        for topic_id, team in (("public-risk", "default"), ("portfolio-history", "default"), ("other-team", "other")):
            session.add(ResearchTopic(topic_id=topic_id, title=topic_id, visibility="team", team_id=team))
        session.flush()
        for key, topic, offset, context, team in (
            ("base", "public-risk", 0, base, "default"),
            ("retained", "public-risk", 1, retained, "default"),
            ("private-newer", "portfolio-history", 2, base, "default"),
            ("other-newer", "other-team", 3, base, "other"),
        ):
            session.add(ResearchEntry(entry_id=key, topic_id=topic, kind="analysis", title=key,
                status="completed", team_id=team, created_at=stamp + timedelta(seconds=offset), context_json=context))
        session.add(ResearchEntry(entry_id="old-private-scope", topic_id="portfolio-history", kind="note",
            title="Historical authorization", context_json={"portfolio_id": "private"}))
        session.commit()

    hydrated_large = []
    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        if "UNUSED_NOTEBOOK_ORIGINAL" in value:
            assert selected_nul, "Unselected originals reached the Python process"
            hydrated_large.append(True)
        return json.loads(value)
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on"})
    try:
        with principal_context(Principal("reader", "Reader", "default")), Session(engine) as session:
            states = sector_research.review_states(session, instrument_ids=ids, for_risk=True)
            for kind in ("latest", "last_completed"):
                assert set(states[kind]) == set(ids)
                assert {row["run_id"] for row in states[kind].values()} == {"base"}
                assert {row["checked_at"] for row in states[kind].values()} == {stamp.isoformat()}
                notebook = states[kind][iid]["current_research"]
                assert notebook == {"investment_view": {"direction": direction, "source_run_id": "view-run", "updated_at": "2026-09-27"},
                    "source_run_id": "retained-source-run", "questions": [{"key": "active", "claim": r"literal\u0000"},
                        {"key": "another-active", "claim": "Retain source ordering"}],
                    "forecasts": [{"key": "open", "status": "active", "review_on": "2026-10-01"}]}
                assert states[kind]["modules-only"]["current_summary"] == ""
                for key in ("empty-notebook", "null-notebook"):
                    assert states[kind][key]["current_summary"] == "older " + key
            assert not session.identity_map
            assert bool(hydrated_large) is selected_nul
    finally:
        engine.dispose()
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "retained").context_json == retained
        full = sector_research.review_states(session, instrument_ids=[iid])["latest"][iid]["current_research"]
        assert full == current
        for key, status, context in (
            ("queued-null", "queued", {**base, "reviews": None, "instrument_ids": [iid]}),
            ("queued-empty", "queued", {**base, "reviews": {iid: {}}, "instrument_ids": [iid]}),
            ("failed-missing", "failed", {key: value for key, value in base.items() if key != "reviews"}),
        ):
            session.add(ResearchEntry(entry_id=key, topic_id="public-risk", kind="analysis", title=key,
                status=status, body="The current check failed" if status == "failed" else "",
                created_at=stamp + timedelta(seconds=4 if status == "queued" else 5), context_json=context))
        session.flush()
        queued = sector_research.review_states(session, instrument_ids=[iid], for_risk=True)
        assert queued["latest"][iid]["status"] == "failed"
        assert queued["latest"][iid]["summary"] == "The current check failed"
        assert queued["latest"][iid]["current_summary"] == direction
        assert queued["last_completed"][iid]["run_id"] == "base"
        session.add(ResearchEntry(entry_id="withdrawn", topic_id="public-risk", kind="analysis", title="Withdrawal",
            status="completed", created_at=stamp + timedelta(seconds=6), context_json={**base, "recordkeeping_only": True,
                "reviews": {iid: {"status": "completed", "research": {"investment_view": {"direction": "", "status": "withdrawn"}}}}}))
        session.flush()
        withdrawn = sector_research.review_states(session, instrument_ids=[iid], for_risk=True)
        assert withdrawn["latest"][iid]["run_id"] == "failed-missing"
        assert withdrawn["last_completed"][iid]["current_summary"] == ""


@pytest.mark.parametrize("selected_nul", [False, True])
def test_prepare_only_loads_latest_completed_risk_inputs(postgres_watchlist_env, monkeypatch, selected_nul):
    stamp = datetime.now(UTC)
    prior_inputs = {"original": "exact\x00value" if selected_nul else r"literal\u0000"}
    current_inputs = {"scope_available": True, "instrument_ids": [], "instruments": []}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="risk-prepare", title="Risk", visibility="team"))
        session.flush()
        for key, offset, status, context in (
            ("older", 0, "completed", {"risk_inputs": {"older": True}}),
            ("prior", 1, "completed", {"risk_inputs": prior_inputs,
                "prior_inputs": {"unused": "UNUSED_PRIOR_HISTORY" * 10000 + "\x00"},
                "risk_review": {"summary": "UNUSED_PRIOR_HISTORY" * 10000}}),
            ("newer-failed", 2, "failed", {"risk_inputs": {"failed": True}}),
            ("prepare", 3, "running", {"risk_run": True, "risk_scope": {"instrument_id": "example"}}),
        ):
            session.add(ResearchEntry(entry_id=key, topic_id="risk-prepare", kind="analysis", title=key,
                status=status, created_at=stamp + timedelta(seconds=offset), context_json=context))
        session.commit()
    from watchlist_app.services import risk_review_state
    monkeypatch.setattr(risk_officer, "read_snapshot", lambda *args, **kwargs: current_inputs)
    monkeypatch.setattr(risk_review_state, "current_scope", lambda *args: {})
    monkeypatch.setattr(risk_review_state, "input_version", lambda *args: "unchanged-version")
    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        if not selected_nul:
            assert "UNUSED_PRIOR_HISTORY" not in value
        return json.loads(value)
    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public"})
    try:
        monkeypatch.setattr(risk_officer, "get_session_factory", lambda: sessionmaker(engine))
        risk_officer.prepare_run("prepare")
        with Session(engine) as session:
            context = session.scalar(select(ResearchEntry.context_json).where(ResearchEntry.entry_id == "prepare"))
            assert context["prior_inputs"] == prior_inputs
            assert context["risk_inputs"] == current_inputs
            assert context["risk_input_version"] == "unchanged-version"
            assert context["cutoff"] == context["prepared_at"] == context["input_snapshot_cutoff"]
    finally:
        engine.dispose()
