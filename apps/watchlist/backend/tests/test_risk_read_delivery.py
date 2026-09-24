"""Risk submission requires delivered bound pages, not model-declared coverage."""
import copy
import json
from dataclasses import replace
from io import BytesIO
from urllib.error import HTTPError

import pytest
from sqlalchemy import event

from watchlist_app import research_mcp as mcp
from watchlist_app.api.routes import risk_officer as route
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import risk_officer as service
from watchlist_app.services.risk_read_projection import missing_required_reads, required_detail_reads
from .test_risk_officer import seed
from .test_account_research_access import accounts, as_user


def bound_context():
    instruments = [{"instrument_id": iid, "name": iid,
        "research_context": {"records": [{"kind": "pm_view", "source_id": f"{iid}-{i}",
            "value": {"body": "原始判断" * 1500}} for i in range(4)]}}
        for iid in ("risk-a", "risk-b")]
    snapshot = {"instrument_ids": [row["instrument_id"] for row in instruments], "instruments": instruments,
                "research": [], "quantitative": [], "coverage": [], "limitations": []}
    prior = copy.deepcopy(snapshot)
    snapshot["instruments"][1]["research_context"]["records"] = []  # Previous-only evidence remains required.
    snapshot["research"] = [{"instrument_id": "risk-a", "case_id": f"case-{i}", "body": "风险原文" * 1200} for i in range(4)]
    return {"risk_run": True, "risk_scope": {"watchlist_id": "risk-list"},
            "cutoff": "2026-09-20T00:00:00+00:00", "prepared_at": "2026-09-20T00:00:01+00:00",
            "risk_inputs": snapshot, "prior_inputs": prior, "risk_delivered_pages": []}


def prepare_fixture(client, context=None):
    seed(client)
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, watchlist_id="risk-list")
        run.status = "running"
        run.context_json = {**run.context_json, **(context or bound_context())}
        session.commit()
        return run.entry_id


def state(run_id):
    with get_session_factory()() as session:
        return copy.deepcopy(session.get(ResearchEntry, run_id).context_json)


def read(client, run_id, instruction):
    return client.post(f"/api/research/runs/{run_id}/risk-read",
                       json={key: value for key, value in instruction.items() if key != "tool"})


RESULT = {"summary": "保留范围与证据限制。", "priorities": [], "limitations": []}


def test_missing_page_blocks_submission_until_exact_bound_next_pages_delivered(client):
    rid = prepare_fixture(client)
    context = state(rid)
    reads = missing_required_reads(context)
    assert len([r for r in reads if r["section"] == "research_context"]) > 2
    # An empty current side does not hide the retained previous-only PM records.
    assert any(r["instrument_id"] == "risk-b" and r["section"] == "research_context" for r in reads)
    first = next(r for r in reads if r["section"] == "research_context")
    packet = read(client, rid, first).json()
    assert packet["next_offset"] is not None
    # Reading the end, a subset offset, or the first page twice is not closure.
    read(client, rid, first)
    read(client, rid, {**first, "offset": packet["total"]})
    reply = client.post(f"/api/research/runs/{rid}/risk-draft", json=RESULT)
    assert reply.status_code == 422
    detail = reply.json()["detail"]
    assert detail["error"] == "risk_reads_incomplete"
    assert {**first, "offset": packet["next_offset"]} in detail["missing_reads"]
    assert first not in detail["missing_reads"]
    assert "submitted_risk_review" not in state(rid)
    for instruction in reversed(detail["missing_reads"]):
        response = read(client, rid, instruction)
        assert response.status_code == 200, response.text
        assert len(response.content) <= 48000
    assert missing_required_reads(state(rid)) == []
    assert client.post(f"/api/research/runs/{rid}/risk-draft", json=RESULT).status_code == 200
    assert state(rid)["submitted_risk_review"] == {**RESULT, "case_assessments": []}


def test_batch_overview_delivers_every_member_and_repeat_does_not_rewrite_context(client):
    rid = prepare_fixture(client)
    updates = []
    engine = get_session_factory().kw["bind"]
    def trace(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("UPDATE"):
            updates.append(statement)
    event.listen(engine, "before_cursor_execute", trace)
    try:
        request = {"section": "instrument_overviews", "offset": 0}
        first = read(client, rid, request)
        assert first.status_code == 200
        assert {r["instrument_id"] for r in first.json()["instruments"]} == {"risk-a", "risk-b"}
        assert len(updates) == 1
        assert read(client, rid, request).json() == first.json()
        assert len(updates) == 1
    finally:
        event.remove(engine, "before_cursor_execute", trace)
    assert sorted(state(rid)["risk_delivered_pages"]) == [["risk-a", "overview", 0], ["risk-b", "overview", 0]]


@pytest.mark.parametrize("bad", [
    {"instrument_id": "outside", "section": "cases"},
    {"instrument_id": "risk-a", "section": "cases", "offset": -1},
    {"instrument_id": "risk-a", "section": "cases", "offset": True},
    {"instrument_id": "risk-a", "section": "cases", "offset": 99},
    {"instrument_id": "risk-a", "section": "instrument_overviews"},
    {"instrument_id": "risk-a", "section": "overview", "risk_delivered_pages": [["risk-a", "cases", 0]]},
])
def test_invalid_reads_cannot_claim_delivery(client, bad):
    rid = prepare_fixture(client)
    before = state(rid)
    assert read(client, rid, bad).status_code == 422
    assert state(rid) == before


def test_oversized_or_unserializable_projection_never_records_delivery(client, monkeypatch):
    context = bound_context()
    context["risk_inputs"]["research"][0]["body"] = "过大原文" * 20000
    rid = prepare_fixture(client, context)
    before = state(rid)
    assert required_detail_reads(before)  # Scope plan stays available with an explicit unreadable page.
    assert read(client, rid, {"instrument_id": "risk-a", "section": "cases"}).status_code == 422
    assert state(rid) == before
    monkeypatch.setattr(route, "project_risk_read", lambda *a, **k: {"bad": object()})
    assert read(client, rid, {"instrument_id": "risk-a", "section": "overview"}).status_code == 422
    assert state(rid) == before


def test_schema_errors_precede_coverage_and_empty_sections_need_no_read(client):
    context = bound_context()
    context["prior_inputs"] = None
    context["risk_inputs"]["research"] = []
    for instrument in context["risk_inputs"]["instruments"]:
        instrument["research_context"]["records"] = []
    rid = prepare_fixture(client, context)
    invalid = client.post(f"/api/research/runs/{rid}/risk-draft", json={"limitations": []})
    assert invalid.status_code == 422 and isinstance(invalid.json()["detail"], list)
    assert required_detail_reads(state(rid)) == []
    assert read(client, rid, {"section": "instrument_overviews"}).status_code == 200
    assert client.post(f"/api/research/runs/{rid}/risk-draft", json=RESULT).status_code == 200


def test_prepare_resets_delivery_and_accepted_draft_and_terminal_history_is_immutable(client, monkeypatch):
    rid = prepare_fixture(client)
    for instruction in missing_required_reads(state(rid)):
        assert read(client, rid, instruction).status_code == 200
    assert client.post(f"/api/research/runs/{rid}/risk-draft", json=RESULT).status_code == 200
    monkeypatch.setattr(service, "read_snapshot", lambda *a, **k: {**bound_context()["risk_inputs"], "scope_available": True})
    service.prepare_run(rid)
    assert state(rid)["risk_delivered_pages"] == []
    assert "submitted_risk_review" not in state(rid)
    assert client.post(f"/api/research/runs/{rid}/risk-draft", json=RESULT).status_code == 422
    with get_session_factory()() as session:
        session.get(ResearchEntry, rid).status = "completed"
        session.commit()
    before = state(rid)
    assert read(client, rid, {"section": "instrument_overviews"}).status_code == 409
    assert client.post(f"/api/research/runs/{rid}/risk-draft", json=RESULT).status_code == 409
    with pytest.raises(ValueError, match="已结束"):
        service.prepare_run(rid)
    assert state(rid) == before


def test_mcp_passes_exact_missing_read_diagnostic_without_echoing_rejected_result(monkeypatch):
    detail = {"error": "risk_reads_incomplete", "missing_reads": [{"tool": "read_risk_instrument", "instrument_id": "a", "section": "research_context", "offset": 2}],
              "next_action": "补读后重交完整结果。", "private_body": "DO_NOT_EXPOSE"}
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "run")
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN", "fixture")
    def missing(*a, **k):
        raise HTTPError("https://example.test", 422, "Invalid", {}, BytesIO(json.dumps({"detail": detail}).encode()))
    monkeypatch.setattr(mcp, "urlopen", missing)
    with pytest.raises(ValueError) as error:
        mcp.submit_risk_review(RESULT)
    returned = json.loads(str(error.value))
    assert returned == {key: detail[key] for key in ("error", "missing_reads", "next_action")}
    assert "DO_NOT_EXPOSE" not in str(error.value)


def test_risk_pages_require_matching_run_token_and_recheck_portfolio_revocation(accounts):
    client, principals, grants = accounts
    as_user(client, "alice")
    from studio_identity import principal_context
    with principal_context(principals["alice"]), get_session_factory()() as session:
        run, _ = service.begin_run(session, portfolio_id="A")
        run.status = "running"
        run.context_json = {**run.context_json, **bound_context(), "risk_scope": {"portfolio_id": "A"}}
        session.commit()
        rid = run.entry_id
    path = f"/api/research/runs/{rid}/risk-read"
    # Ordinary login is insufficient for a model-only delivery write.
    assert client.post(path, json={"section": "instrument_overviews"}).status_code == 403
    principals["run"] = replace(principals["alice"], credential="run", resource_scope={"kind": "run", "id": rid})
    as_user(client, "run")
    assert client.post(path, json={"section": "instrument_overviews"}).status_code == 200
    before = state(rid)
    assert client.post(path.replace(rid, "other"), json={"section": "instrument_overviews"}).status_code == 403
    grants["alice"].clear()
    assert client.post(path, json={"instrument_id": "risk-a", "section": "research_context"}).status_code == 404
    assert state(rid) == before
