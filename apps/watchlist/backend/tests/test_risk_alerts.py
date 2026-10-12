from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import event
from sqlalchemy.orm import Session
from studio_identity import Principal

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase
from watchlist_app.db.session import get_engine, get_session_factory


def seed_alerts(session, iid="sxv264"):
    if session.get(InstrumentDetail, iid) is None:
        session.add(InstrumentDetail(instrument_id=iid, instrument_type="public_fund",
            detail_view_type="public_fund", instrument_name="Risk fixture", metadata_json={}))
    session.add_all([
        ResearchTopic(topic_id="alert-topic", title="Private answer", visibility="private"),
        ResearchTopic(topic_id="foreign-alert-topic", title="Other team", team_id="foreign", visibility="team")])
    session.flush()
    session.add_all([
        ResearchEntry(entry_id="alert-run", topic_id="alert-topic", kind="analysis", title="Private answer",
            status="draft", body="DO_NOT_READ_PRIVATE", context_json={"huge_original": "UNSELECTED" * 100000}),
        ResearchEntry(entry_id="foreign-alert-run", topic_id="foreign-alert-topic", kind="analysis", title="Other team",
            team_id="foreign", status="completed", context_json={})])
    session.flush()
    base = {"run_id": "alert-run", "direction": "risk", "event_version_id": "current-version",
        "impact_level": "major", "urgency": "monitor", "confidence": "reported",
        "checked_at": "2026-10-11T21:00:00Z", "risk_assessment": {"status": "active",
            "event_version_id": "current-version", "reviewed_at": "2026-10-12T00:00:00Z"},
        "huge_original": "UNSELECTED" * 100000 + "\x00"}
    changes = {
        "major": {}, "urgent": {"impact_level": "material", "urgency": "immediate"},
        "both": {"urgency": "immediate"}, "ordinary": {"impact_level": "material"},
        "pending": {"risk_assessment": {**base["risk_assessment"], "status": "pending"}},
        "superseded": {"risk_assessment": {**base["risk_assessment"], "event_version_id": "old"}},
        "withdrawn": {"withdrawn": True}, "opportunity-active": {"direction": "opportunity"},
        "pure-opportunity": {"direction": "opportunity", "risk_assessment": {"status": "not_submitted"}},
        "foreign": {"run_id": "foreign-alert-run"}, "handled": {}, "cleared": {}, "coverage": {},
    }
    for key, change in changes.items():
        session.add(RiskCase(case_id=key, instrument_id=iid, signal="sector:" + key, title=key,
            body="DO_NOT_READ_BODY", severity="coverage" if key == "coverage" else "attention",
            status="handled" if key == "handled" else "open", trigger_active=key != "cleared",
            observed_on=datetime(2026, 10, 11).date(), updated_at=datetime(2026, 10, 12, tzinfo=UTC),
            evidence_json={**deepcopy(base), **change}, history_json=[{"original": "UNSELECTED" * 1000}]))
    session.commit()


def test_home_alerts_use_current_assessed_scope_and_paginate_without_originals(client):
    with get_session_factory()() as session:
        seed_alerts(session)
    loaded, queries = [], []
    def on_load(_session, value):
        if isinstance(value, (RiskCase, ResearchEntry, ResearchTopic, InstrumentDetail)):
            loaded.append(type(value).__name__)
    def on_sql(_connection, _cursor, statement, *_args):
        queries.append(statement)
    event.listen(Session, "loaded_as_persistent", on_load)
    event.listen(get_engine(), "before_cursor_execute", on_sql)
    try:
        first = client.get("/api/risk/alerts?limit=2")
        assert first.status_code == 200, first.text
        assert first.json()["total"] == 4 and first.json()["has_more"]
        assert [item["case_id"] for item in first.json()["alerts"]] == ["both", "urgent"]
        assert loaded == []
        assert len(queries) == 2
        assert all("context_json" not in query and "history_json" not in query for query in queries)
        second = client.get("/api/risk/alerts?limit=2&offset=2").json()
        assert second["total"] == 4 and not second["has_more"]
        assert [item["case_id"] for item in second["alerts"]] == ["major", "opportunity-active"]
        alert = second["alerts"][0]
        assert alert["case_id"] == "major" and alert["confidence"] == "reported"
        assert alert["as_of"] == "2026-10-11T21:00:00Z"
        assert alert["reviewed_at"] == "2026-10-12T00:00:00Z"
        assert alert["target"] == {"workspace": "watchlist", "path": "/instruments/sxv264?tab=risk"}
        assert "UNSELECTED" not in first.text and "DO_NOT_READ" not in first.text
        assert "不表示没有风险" in second["selection_note"]
        assert client.get("/api/risk/alerts?offset=99").json()["alerts"] == []
        assert client.get("/api/risk/alerts?limit=11").status_code == 422
        assert client.get("/api/risk/alerts?offset=-1").status_code == 422
    finally:
        event.remove(Session, "loaded_as_persistent", on_load)
        event.remove(get_engine(), "before_cursor_execute", on_sql)


def test_run_scoped_and_unprivileged_service_cannot_read_home_alerts(client, monkeypatch):
    import watchlist_app.main as main
    for principal in (
        Principal("reader", "Reader", "default", resource_scope={"kind": "run", "id": "other", "attempt": "1"}),
        Principal(None, "Service", "default", kind="service", service_id="other", scopes=[]),
    ):
        monkeypatch.setattr(main, "resolve_request", lambda *a, **kw: principal)
        assert client.get("/api/risk/alerts").status_code == 403
