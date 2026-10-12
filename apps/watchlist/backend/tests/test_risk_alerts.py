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


def test_assessed_major_survives_revision_and_pending_review_without_lending_old_approval(client):
    from watchlist_app.services.risk_case_state import advance_event_risk, assessed_risk_fields
    with get_session_factory()() as session:
        seed_alerts(session)
        case = session.get(RiskCase, "major")
        for version, material in (("editorial", False), ("material", True)):
            snapshot = {**case.evidence_json, "event_version_id": version,
                "title": "NEW_UNREVIEWED_TITLE", "body": "NEW_UNREVIEWED_BODY", "recorded_at": "2026-10-12T02:00:00Z"}
            changes = advance_event_risk(case, snapshot, material_change=material,
                sources=[{"source_type": "public_source"}])
            case.title, case.body = snapshot["title"], snapshot["body"]
            case.evidence_json = {**snapshot, **changes}
            session.commit()
            alert = next(row for row in client.get("/api/risk/alerts?limit=10").json()["alerts"] if row["case_id"] == "major")
            assert alert["title"] == "major"
            assert alert["event_version_id"] == "current-version"
            assert alert["alert_state"] == "assessment_pending"
            assert alert["pending_revision"]["title"] == "NEW_UNREVIEWED_TITLE"
            assert alert["pending_revision"]["event_version_id"] == version
            assert "NEW_UNREVIEWED_BODY" not in str(alert)
        decision = {"status": "pending", "event_version_id": "material", "reason": "等待独立原件",
            "reviewed_at": "2026-10-12T03:00:00Z", "run_id": "risk-review"}
        case.evidence_json = {**case.evidence_json, **assessed_risk_fields(case, decision), "risk_assessment": decision}
        session.commit()
        assert any(row["case_id"] == "major" for row in client.get("/api/risk/alerts?limit=10").json()["alerts"])
        # Explicit evidence withdrawal hides it, without deleting historical proof.
        case.evidence_json = {**case.evidence_json, "withdrawn": True, "withdrawal_reason": "原件被正式撤回"}
        session.commit()
        assert not any(row["case_id"] == "major" for row in client.get("/api/risk/alerts?limit=10").json()["alerts"])


def test_credible_urgent_pending_has_separate_state_and_never_claims_old_confirmation(client):
    from watchlist_app.services.risk_case_state import advance_event_risk, assessed_risk_fields
    with get_session_factory()() as session:
        seed_alerts(session)
        case = session.get(RiskCase, "pending")
        case.trigger_active, case.status = False, "recorded"
        snapshot = {**case.evidence_json, "title": "限时但待独立核实", "information_type": "fact",
            "direction": "risk", "confidence": "reported", "urgency": "immediate", "impact_level": "major",
            "action_condition": "明日资金到期前核实赎回通知", "sources": [{"source_type": "public_source",
                "document_id": "d", "version_id": "v", "published_at": "2026-10-12"}]}
        case.evidence_json = {**snapshot, **advance_event_risk(case, snapshot, material_change=True, sources=snapshot["sources"])}
        session.commit()
        alert = next(row for row in client.get("/api/risk/alerts?limit=10").json()["alerts"] if row["case_id"] == "pending")
        assert alert["alert_state"] == "unverified_urgent"
        assert alert["last_assessed_snapshot"] is None
        assert alert["reviewed_at"] is None
        assert alert["confidence"] == "reported"
        assert alert["pending_revision"]["urgent_review_required"]
        # An old resolved case can acquire a NEW credible urgent revision.
        case.status = "resolved"
        session.commit()
        assert any(row["case_id"] == "pending" for row in client.get("/api/risk/alerts?limit=10").json()["alerts"])
        decision = {"status": "resolved", "event_version_id": snapshot["event_version_id"], "reason": "新事项已核实解除"}
        case.evidence_json = {**case.evidence_json, **assessed_risk_fields(case, decision), "risk_assessment": decision}
        session.commit()
        assert not any(row["case_id"] == "pending" for row in client.get("/api/risk/alerts?limit=10").json()["alerts"])


def test_numeric_refresh_neither_adds_pending_badge_nor_hides_existing_urgent_referral(client):
    from watchlist_app.services.risk_case_state import advance_event_risk
    with get_session_factory()() as session:
        seed_alerts(session)
        for case_id in ("major", "pending"):
            case = session.get(RiskCase, case_id)
            if case_id == "pending":
                case.trigger_active, case.status = False, "recorded"
                original = {**case.evidence_json, "title": "明确期限待核实", "information_type": "fact",
                    "urgency": "immediate", "action_condition": "明日到期前核实",
                    "sources": [{"source_type": "public_source", "document_id": "notice",
                                 "version_id": "original", "published_at": "2026-10-12"}]}
                case.evidence_json = {**original, **advance_event_risk(case, original, material_change=True, sources=original["sources"])}
            numeric = {**case.evidence_json, "title": "NEW_NUMBER_TITLE", "body": "净值更新",
                       "event_version_id": "number-version", "sources": [{"source_type": "computed_metric"}]}
            state = advance_event_risk(case, numeric, material_change=True, sources=numeric["sources"])
            case.title, case.body = numeric["title"], numeric["body"]
            case.evidence_json = {**numeric, **state}
        session.commit()
    rows = {row["case_id"]: row for row in client.get("/api/risk/alerts?limit=10").json()["alerts"]}
    assert rows["major"]["alert_state"] == "assessed" and rows["major"]["pending_revision"] is None
    assert rows["major"]["title"] == "major" and rows["major"]["event_version_id"] == "current-version"
    assert rows["pending"]["alert_state"] == "unverified_urgent"
    assert rows["pending"]["title"] == "明确期限待核实"
    assert rows["pending"]["pending_revision"]["event_version_id"] == "current-version"
    assert rows["pending"]["last_assessed_snapshot"] is None
