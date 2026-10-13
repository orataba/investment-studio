import asyncio
import copy
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

from fastapi import HTTPException
from mcp.server.mcpserver.exceptions import ToolError
import pytest

from watchlist_app import research_mcp
from watchlist_app.api.routes import risk_officer as route
from watchlist_app.services import risk_officer as service


def bound_run():
    return SimpleNamespace(entry_id="risk-run", read_state=SimpleNamespace(submitted_review=None),
        context_json={"risk_inputs": {
            "scope": {"kind": "instrument", "id": "fund"}, "instrument_ids": ["fund"],
            "instruments": [{"instrument_id": "fund", "name": "Fund", "performance_evidence": {
                "source_id": "risk-performance:fund", "sample_start": "2026-01-01",
                "sample_end": "2026-10-13", "currency": "CNY", "frequency": "daily", "comparisons": []}}],
            "research": [{"case_id": "research-case", "instrument_id": "fund", "signal": "sector:event",
                "evidence_json": {"event_version_id": "event:1", "sources": [
                    {"source_id": "text:case-original", "text": "Retained original body"}]}}],
            "quantitative": [], "coverage": []}})


def review_payload():
    return {"summary": "PRIVATE_SUMMARY", "limitations": [], "priorities": [
        {"title": "Performance", "analysis": "PRIVATE_ANALYSIS", "instrument_ids": ["fund"],
         "source_ids": ["risk-performance:fund"], "next_watch": "Next observation"},
        {"title": "Research event", "analysis": "PRIVATE_EVENT_ANALYSIS", "instrument_ids": ["fund"],
         "case_ids": ["research-case"], "source_ids": ["text:case-original"], "next_watch": "Next disclosure"}]}


@pytest.mark.parametrize("invalid_source", ["text:case-original", "computed:unknown", "risk-performance:other-fund"])
def test_risk_source_diagnostic_keeps_bound_scope_and_identifies_repair(invalid_source):
    run, payload = bound_run(), review_payload()
    before = copy.deepcopy(run.context_json)
    payload["priorities"][1]["source_ids"] = [invalid_source]
    with pytest.raises(ValueError) as error:
        service.validate_result(run, service.RiskReview.model_validate(payload))
    diagnostic = str(error.value)
    assert "priorities[1].source_ids" in diagnostic
    assert invalid_source in diagnostic
    assert '本轮允许的直接 source_ids：["risk-performance:fund"]' in diagnostic
    assert "case_ids" in diagnostic and "完整 result" in diagnostic
    assert "PRIVATE_" not in diagnostic and "Retained original body" not in diagnostic
    assert run.context_json == before

    # The event is already bound through its case; its originals remain intact.
    payload["priorities"][1]["source_ids"] = []
    service.validate_result(run, service.RiskReview.model_validate(payload))
    assert set(service.evidence_sources(run.context_json["risk_inputs"])) == {"risk-performance:fund"}
    payload["priorities"][0]["source_ids"] = []
    with pytest.raises(ValueError, match="独立研判需要引用"):
        service.validate_result(run, service.RiskReview.model_validate(payload))


def test_source_repair_diagnostic_reaches_native_mcp_and_corrected_full_result_saves(monkeypatch):
    run, payload = bound_run(), review_payload()
    commits = []
    session = SimpleNamespace(commit=lambda: commits.append(True))
    monkeypatch.setattr(route, "_running_risk_run", lambda *args: run)
    monkeypatch.setattr(route, "missing_required_reads", lambda context: [])
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", run.entry_id)
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN", "test-only-not-a-secret")

    def local_http(request, timeout):
        try:
            result = route.submit_review(run.entry_id,
                service.RiskReview.model_validate(json.loads(request.data)), session)
        except HTTPException as error:
            raise HTTPError(request.full_url, error.status_code, "Rejected", {},
                io.BytesIO(json.dumps({"detail": error.detail}).encode())) from error
        return io.BytesIO(json.dumps(result).encode())

    monkeypatch.setattr(research_mcp, "urlopen", local_http)
    with pytest.raises(ToolError) as error:
        asyncio.run(research_mcp.mcp.call_tool("submit_risk_review", {"result": payload}))
    diagnostic = json.loads(str(error.value).split(": ", 1)[1])
    assert diagnostic["status"] == 422
    assert "priorities[1].source_ids" in diagnostic["message"]
    assert "text:case-original" in diagnostic["message"]
    assert '本轮允许的直接 source_ids：["risk-performance:fund"]' in diagnostic["message"]
    assert "PRIVATE_" not in str(error.value)
    assert run.read_state.submitted_review is None and commits == []

    payload["priorities"][1]["source_ids"] = []
    accepted = asyncio.run(research_mcp.mcp.call_tool("submit_risk_review", {"result": payload}))
    assert json.loads(accepted.content[0].text) == {"run_id": run.entry_id, "status": "accepted"}
    assert run.read_state.submitted_review == service.RiskReview.model_validate(payload).model_dump(mode="json")
    assert commits == [True]


def test_native_risk_schema_distinguishes_case_and_direct_source_references():
    tool = next(tool for tool in asyncio.run(research_mcp.mcp.list_tools()) if tool.name == "submit_risk_review")
    fields = tool.input_schema["$defs"]["Priority"]["properties"]
    assert "underlying originals" in fields["case_ids"]["description"]
    assert "case_ids" in fields["source_ids"]["description"]
    assert "research_context.records" in fields["source_ids"]["description"]
