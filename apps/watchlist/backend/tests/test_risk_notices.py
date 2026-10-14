from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from studio_identity import Principal

from watchlist_app.db.models.workbench import ResearchEntry, RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import risk_officer as service
from watchlist_app.services.risk_notices import current_notices, version_notices
from .test_risk_officer import seed, portfolio_access_fixture


def notice(case_id="price", **changes):
    return {"level": "medium", "title": "当前风险事实", "body": "截至2026-09-06，已触发所设损失复核线。",
        "instrument_ids": ["risk-a"], "case_ids": [case_id], "source_ids": [], "holding_ids": [], **changes}


def prepared(client):
    seed(client)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, instrument_id="risk-a")
        run_id = run.entry_id
    service.prepare_run(run_id)
    return run_id


def publish(run_id, items):
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        service.apply_result(session, run, {"notice_items": items, "limitations": []})
        session.commit()
        return deepcopy(run.context_json["risk_notice_items"])


def test_required_selection_is_not_inferred_from_priorities_or_legacy_report(client):
    run_id = prepared(client)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        with pytest.raises(ValueError, match="notice_items"):
            service.apply_result(session, run, {"summary": "旧总评", "priorities": [], "limitations": []})
    publish(run_id, [])
    value = client.get("/api/risk/review?instrument_id=risk-a").json()
    assert value["notice_items"] == [] and value["notice_status"] == "assessed"
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        context = deepcopy(run.context_json)
        context["result"].pop("notice_items")
        context.pop("risk_notice_items")
        run.context_json = context
        session.commit()
    assert client.get("/api/risk/review?instrument_id=risk-a").json()["notice_status"] == "assessment_pending"


def test_selected_quantitative_notice_reaches_home_and_keeps_fact_date(client):
    items = publish(prepared(client), [notice()])
    assert items[0]["as_of"] == "2026-09-06"
    page = client.get("/api/risk/alerts?all=true").json()
    assert [row["title"] for row in page["alerts"]] == ["当前风险事实"]
    assert page["alerts"][0]["case_ids"] == ["price"]
    assert page["notice_status"] == "assessed"
    with get_session_factory()() as session:
        session.get(RiskCase, "price").status = "resolved"
        session.get(RiskCase, "price").trigger_active = False
        session.commit()
    assert client.get("/api/risk/alerts?all=true").json()["alerts"] == []


def test_new_referral_marks_selection_pending_without_removing_old_notice(client):
    publish(prepared(client), [notice()])
    with get_session_factory()() as session:
        session.add(RiskCase(case_id="new-risk", instrument_id="risk-a", signal="sector:new", title="新待核实事实",
            severity="attention", status="recorded", trigger_active=False,
            evidence_json={"event_version_id": "new:1", "risk_assessment": {"status": "pending", "event_version_id": "new:1"}}))
        session.commit()
    value = client.get("/api/risk/review?instrument_id=risk-a").json()
    assert len(value["notice_items"]) == 1 and value["notice_status"] == "assessment_pending"
    assert client.get("/api/risk/alerts?all=true").json()["notice_status"] == "assessment_pending"


def test_severe_facts_survive_model_absence_and_selected_empty(client):
    run_id = prepared(client)
    with get_session_factory()() as session:
        case = session.get(RiskCase, "price")
        case.severity = "severe"
        case.evidence_json = {"mandatory_notice": True, "rule_version": "3", "metric_snapshot_id": "snapshot:1"}
        session.commit()
    value = client.get("/api/risk/review?instrument_id=risk-a").json()
    assert value["notice_items"][0]["mandatory"] and value["notice_items"][0]["level"] is None
    home = client.get("/api/risk/alerts?all=true").json()
    assert home["alerts"][0]["origin"] == "deterministic"
    assert home["alerts"][0]["metric_snapshot_id"] == "snapshot:1"
    # No model selection is needed for facts to remain visible while paused.
    assert service.selected_cases([{"case_id": "severe", "severity": "severe", "signal": "period_loss",
        "status": "open", "trigger_active": True}])["quantitative"]


def test_notice_references_obey_scope_and_do_not_publish_coverage_as_risk(client):
    run_id = prepared(client)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        with pytest.raises(ValueError, match="范围快照"):
            service.apply_result(session, run, {"notice_items": [notice("outside")], "limitations": []})
        with pytest.raises(ValueError, match="重复"):
            service.apply_result(session, run, {"notice_items": [notice(), notice()], "limitations": []})


def test_notice_version_and_fact_date_do_not_advance_on_plain_refresh():
    case = {"case_id": "price", "instrument_id": "risk-a", "observed_on": "2026-09-06", "evidence_json": {}}
    initial = version_notices([notice()], previous=[], run_id="one", reviewed_at="one", cases={"price": case})
    case["observed_on"] = "2026-10-14"
    second = version_notices([notice()], previous=initial, run_id="two", reviewed_at="two", cases={"price": case})
    assert initial[0]["notice_id"] == second[0]["notice_id"]
    assert second[0]["notice_version"] == 1 and second[0]["as_of"] == "2026-09-06"
    revised = version_notices([notice(level="high")], previous=second, run_id="three", reviewed_at="three", cases={"price": case})
    assert revised[0]["notice_version"] == 2


@pytest.mark.parametrize("withdrawn", [False, True])
def test_partial_case_resolution_keeps_independent_states_and_marks_old_prose(withdrawn):
    items = [{**notice(), "notice_id": "n", "notice_version": 1, "case_ids": ["a", "b"]}]
    cases = [{"case_id": cid, "instrument_id": "risk-a", "title": cid, "evidence_json": {},
        "status": "open" if cid == "b" else "resolved", "trigger_active": cid == "b"} for cid in ("a", "b")]
    if withdrawn:
        cases[0].update(status="open", trigger_active=True, evidence_json={"withdrawn": True, "risk_assessment": {"status": "pending"}})
    result = current_notices({"notice_items": items}, cases)
    assert len(result) == 1 and result[0]["resolved_case_ids"] == ["a"]
    assert result[0]["alert_state"] == "assessment_pending" and result[0]["status_note"]


def test_notice_rejects_new_trigger_episode_during_review(client):
    run_id = prepared(client)
    with get_session_factory()() as session:
        case = session.get(RiskCase, "price")
        case.evidence_json = {**case.evidence_json, "trigger_version": "price:2"}
        session.commit()
    with get_session_factory()() as session:
        with pytest.raises(ValueError, match="触发事项已有新版本"):
            service.apply_result(session, session.get(ResearchEntry, run_id), {"notice_items": [notice()], "limitations": []})


def test_correction_preserves_original_and_does_not_change_cases(client):
    run_id = prepared(client)
    original = publish(run_id, [notice()])[0]
    response = client.patch(f"/api/risk/notices/{run_id}/{original['notice_id']}", json={
        "expected_notice_version": 1, "level": "high", "title": "更正标题", "body": "修正后的风险措辞。", "reason": "原表述不准确"})
    assert response.status_code == 200, response.text
    assert response.json()["notice_version"] == 2
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        assert run.context_json["risk_notice_corrections"][0]["before"] == original
        assert session.get(RiskCase, "price").status == "open"
    stale = client.patch(f"/api/risk/notices/{run_id}/{original['notice_id']}", json={
        "expected_notice_version": 1, "level": "low", "title": "旧请求", "body": "旧内容。", "reason": "旧"})
    assert stale.status_code == 409


def test_receipts_and_silence_are_personal_version_bound_and_do_not_hide_facts(client, monkeypatch):
    run_id = prepared(client)
    original = publish(run_id, [notice()])[0]
    import watchlist_app.main as main
    monkeypatch.setattr(main, "resolve_request", lambda *a, **k: Principal("member", "Member", "default", team_role="member"))
    body = {"notice_id": original["notice_id"], "notice_version": 1, "run_id": run_id, "action": "mute",
        "muted_until": (datetime.now(UTC) + timedelta(hours=24)).isoformat()}
    response = client.put("/api/risk/notices/receipt", json=body)
    assert response.status_code == 200, response.text
    item = client.get("/api/risk/alerts?all=true").json()["alerts"][0]
    assert item["receipt"]["muted"] and not item["receipt"]["unread"]
    assert item["body"] == original["body"]
    changed = client.patch(f"/api/risk/notices/{run_id}/{original['notice_id']}", json={
        "expected_notice_version": 1, "level": "high", "title": "新增重要变化", "body": "新事实应再次提醒。", "reason": "新资料"})
    assert changed.status_code == 200, changed.text
    item = client.get("/api/risk/alerts?all=true").json()["alerts"][0]
    assert item["receipt"]["unread"] and not item["receipt"]["muted"]
    assert client.put("/api/risk/notices/receipt", json=body).status_code == 409
    monkeypatch.setattr(main, "resolve_request", lambda *a, **k: Principal("foreign", "Foreign", "other", team_role="member"))
    assert client.put("/api/risk/notices/receipt", json={**body, "notice_version": 2, "action": "read"}).status_code == 404


def test_data_unavailable_keeps_previous_notice_and_handling_does_not_hide_severe():
    original = {**notice(), "notice_id": "old", "notice_version": 1, "as_of": "2026-09-06"}
    retained = current_notices({"notice_items": [original]}, [], scope_available=False)
    assert retained[0]["as_of"] == "2026-09-06" and retained[0]["alert_state"] == "assessment_pending"
    severe = {"case_id": "price", "instrument_id": "risk-a", "signal": "period_loss", "title": "严重规则",
        "body": "事实", "trigger_active": True, "status": "handled", "evidence_json": {"mandatory_notice": True}}
    assert current_notices({"notice_items": []}, [severe])[0]["mandatory"]


def test_human_correction_invalidates_an_inflight_review_of_old_notice(client):
    first = prepared(client)
    original = publish(first, [notice()])[0]
    with get_session_factory()() as session:
        second, _ = service.begin_run(session, instrument_id="risk-a")
        second_id = second.entry_id
    service.prepare_run(second_id)
    response = client.patch(f"/api/risk/notices/{first}/{original['notice_id']}", json={
        "expected_notice_version": 1, "level": "high", "title": "人工更正", "body": "更正事实。", "reason": "准确性"})
    assert response.status_code == 200
    with get_session_factory()() as session:
        with pytest.raises(ValueError, match="已经更正"):
            service.apply_result(session, session.get(ResearchEntry, second_id), {"notice_items": [notice()], "limitations": []})


def test_new_severe_episode_overrides_old_model_silence_and_requires_visible_version(client, monkeypatch):
    run_id = prepared(client)
    original = publish(run_id, [notice()])[0]
    import watchlist_app.main as main
    monkeypatch.setattr(main, "resolve_request", lambda *a, **k: Principal("reader", "Reader", "default", team_role="reader"))
    muted = client.put("/api/risk/notices/receipt", json={"notice_id": original["notice_id"], "notice_version": 1,
        "run_id": run_id, "action": "mute", "muted_until": (datetime.now(UTC) + timedelta(hours=24)).isoformat()})
    assert muted.status_code == 200, muted.text
    with get_session_factory()() as session:
        case = session.get(RiskCase, "price")
        case.evidence_json = {"mandatory_notice": True, "rule_version": "2"}
        case.severity = "severe"
        case.history_json = [{"action": "updated"}]
        session.commit()
    value = client.get("/api/risk/alerts?all=true").json()["alerts"][0]
    assert value["receipt"]["unread"] and not value["receipt"]["muted"]
    body = {"notice_id": value["notice_id"], "notice_version": value["notice_version"], "run_id": run_id, "action": "read"}
    assert client.put("/api/risk/notices/receipt", json=body).status_code == 409
    rules = {row["notice_id"]: str(row["notice_version"]) for row in value["current_rule_facts"]}
    response = client.put("/api/risk/notices/receipt", json={**body, "rule_versions": rules})
    assert response.status_code == 200, response.text
    assert not client.get("/api/risk/alerts?all=true").json()["alerts"][0]["receipt"]["unread"]


def test_portfolio_manager_can_correct_but_viewer_and_service_cannot(client, monkeypatch):
    run_id = prepared(client)
    original = publish(run_id, [notice()])[0]
    from watchlist_app.db.models.workbench import ResearchTopic
    from watchlist_app.services import research_access, risk_notice_actions
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        run.context_json = {**run.context_json, "risk_scope": {"portfolio_id": "private-p"}}
        session.get(ResearchTopic, run.topic_id).portfolio_id = "private-p"
        session.commit()
    import watchlist_app.main as main
    access = {"role": "manager", "can_edit": True}
    monkeypatch.setattr(main, "resolve_request", lambda *a, **k: Principal("manager", "Manager", "default", team_role="reader"))
    monkeypatch.setattr(research_access, "require_portfolio", lambda pid: access)
    monkeypatch.setattr(risk_notice_actions, "require_portfolio", lambda pid: access)
    request = {"expected_notice_version": 1, "level": "medium", "title": "组合措辞更正", "body": "更正组合风险。", "reason": "组合经理复核"}
    response = client.patch(f"/api/risk/notices/{run_id}/{original['notice_id']}", json=request)
    assert response.status_code == 200, response.text
    assert client.get("/api/risk/alerts?all=true").json()["alerts"] == []
    access = {"role": "viewer", "can_edit": False}
    assert client.patch(f"/api/risk/notices/{run_id}/{original['notice_id']}", json={**request, "expected_notice_version": 2}).status_code == 403
    monkeypatch.setattr(main, "resolve_request", lambda *a, **k: Principal(None, "Service", "default", kind="service", service_id="research", scopes=["watchlist:research"]))
    assert client.patch(f"/api/risk/notices/{run_id}/{original['notice_id']}", json={**request, "expected_notice_version": 2}).status_code == 403
