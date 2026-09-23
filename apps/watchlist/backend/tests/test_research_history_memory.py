"""Current reads must not decode all retained historical input snapshots."""
from datetime import UTC, datetime, timedelta
import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from watchlist_app.db.models import InstrumentDetail, Watchlist
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services import research_dossier, research_runner, risk_officer, sector_research


IID = "memory-fixture"


def seed_history(*, topic_id=None, newest_status="completed", poisoned="old"):
    topic_id = topic_id or f"instrument-events:{IID}"
    now = datetime.now(UTC)
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=IID, instrument_type="etf", detail_view_type="etf",
            instrument_name="Memory fixture", metadata_json={}))
        session.add(Watchlist(watchlist_id="memory-list", name="Memory list", owner_type="team", owner_id="default"))
        session.add(ResearchTopic(topic_id=topic_id, title="Retained history", visibility="team", instrument_ids=[IID]))
        session.flush()
        for label, stamp in (("old", now - timedelta(days=1)), ("current", now)):
            research = {"version_id": "unchanged-version", "investment_view": {"direction": label},
                        "sources": [{"source_id": label, "text": "poisoned-history-must-not-decode" if label == poisoned else "Current source"}]}
            session.add(ResearchEntry(entry_id=f"memory-{label}", topic_id=topic_id, kind="analysis", title=label,
                status=newest_status if label == "current" else "completed", created_at=stamp, completed_at=stamp,
                context_json={"sector_run": True, "instrument_ids": [IID], "cutoff": stamp.isoformat(),
                    "research_dates": {IID: now.date().isoformat()}, "retained_input": "Original data " * 80000,
                    "reviews": {IID: {"status": "completed", "research": research}}}))
        session.commit()


def guarded_engine():
    def deserialize(value):
        assert "poisoned-history-must-not-decode" not in value, "Unneeded historical notebook was decoded"
        return json.loads(value)
    return create_engine(get_engine().url, json_deserializer=deserialize)


@pytest.mark.parametrize("consumer", ["review_states", "current_notebook"])
def test_current_research_reads_stop_before_historical_json(client, consumer):
    seed_history()
    engine = guarded_engine()
    try:
        with Session(engine) as session:
            if consumer == "review_states":
                result = sector_research.review_states(session, instrument_ids=[IID])
                assert result["latest"][IID]["run_id"] == "memory-current"
                assert result["last_completed"][IID]["current_summary"] == "current"
            else:
                notebook, history = research_dossier._notebooks(session, IID, include_history=False)
                assert notebook["investment_view"]["direction"] == "current"
                assert history == []
    finally:
        engine.dispose()


def test_saved_version_reads_oldest_original_without_decoding_later_copies(client):
    seed_history(poisoned="current")
    engine = guarded_engine()
    try:
        with Session(engine) as session:
            version = research_dossier.read_dossier_version(session, IID, "unchanged-version")
            assert version["value"]["investment_view"]["direction"] == "old"
    finally:
        engine.dispose()


@pytest.mark.parametrize("scheduled", [False, True])
def test_sector_enqueue_decodes_only_latest_matching_run(client, monkeypatch, scheduled):
    seed_history(newest_status="failed")
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="unrelated-active", title="Other instrument", visibility="team"))
        session.flush()
        session.add(ResearchEntry(entry_id="unrelated-active", topic_id="unrelated-active", kind="analysis",
            title="Other instrument", status="running", context_json={"sector_run": True,
                "instrument_ids": ["other-instrument"], "retained_input": "Unrelated input " * 80000}))
        session.commit()
    monkeypatch.setattr(research_dossier, "read_dossier", lambda *_: {})
    monkeypatch.setattr(sector_research, "_research_dates",
        lambda _session, ids, now: {iid: now.date().isoformat() for iid in ids})
    from watchlist_app.services import research_triggers
    monkeypatch.setattr(research_triggers, "research_trigger", lambda *args, **kwargs: None)
    loaded = []
    def loaded_entry(record, _context):
        loaded.append(record.entry_id)
        assert record.entry_id == "memory-current", "Enqueue loaded historical or unrelated input snapshots"
    event.listen(ResearchEntry, "load", loaded_entry)
    try:
        with get_session_factory()() as session:
            run, created = sector_research.begin_run(session, [IID], scheduled=scheduled)
            assert created == (not scheduled)
            assert run.entry_id == "memory-current" if scheduled else run.status == "queued"
    finally:
        event.remove(ResearchEntry, "load", loaded_entry)
    assert loaded == ["memory-current"]


def test_risk_enqueue_decodes_only_latest_run(client):
    seed_history(topic_id="risk-officer:watchlist:memory-list", newest_status="failed")
    loaded = []
    def loaded_entry(record, _context):
        loaded.append(record.entry_id)
        assert record.entry_id != "memory-old", "Risk enqueue loaded historical input snapshots"
    event.listen(ResearchEntry, "load", loaded_entry)
    try:
        with get_session_factory()() as session:
            _, created = risk_officer.begin_run(session, watchlist_id="memory-list")
            assert created
    finally:
        event.remove(ResearchEntry, "load", loaded_entry)
    assert loaded == ["memory-current"]


def test_risk_workspace_stops_after_the_latest_published_result(client, monkeypatch):
    seed_history(topic_id="risk-officer:watchlist:memory-list")
    snapshot = {"scope": {"kind": "watchlist", "id": "memory-list"}, "scope_available": True,
        "input_as_of": None, "instruments": [], "research": [], "quantitative": [], "coverage": [],
        "portfolio": None, "limitations": []}
    with get_session_factory()() as session:
        current = session.get(ResearchEntry, "memory-current")
        current.context_json = {**current.context_json, "result": {"summary": "Current result"}, "risk_inputs": snapshot}
        session.commit()
    monkeypatch.setattr(risk_officer, "read_snapshot", lambda *args, **kwargs: snapshot)
    monkeypatch.setattr(risk_officer, "evidence_sources", lambda *_: {})
    engine = guarded_engine()
    try:
        with Session(engine) as session:
            workspace = risk_officer.review_workspace(session, watchlist_id="memory-list")
            assert workspace["latest_completed"]["run_id"] == "memory-current"
            assert workspace["latest_run"]["run_id"] == "memory-current"
    finally:
        engine.dispose()


def test_startup_recovery_updates_status_without_loading_retained_inputs(client):
    seed_history(newest_status="running")
    def unexpected_load(*_):
        pytest.fail("Startup recovery must not decode retained input snapshots")
    event.listen(ResearchEntry, "load", unexpected_load)
    try:
        research_runner.interrupt_incomplete_runs()
    finally:
        event.remove(ResearchEntry, "load", unexpected_load)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "memory-current")
        assert run.status == "failed" and run.completed_at is not None
        assert run.context_json["retained_input"] == "Original data " * 80000
        assert session.get(ResearchEntry, "memory-old").status == "completed"
