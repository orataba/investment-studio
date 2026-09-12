import copy
import io
import json
from urllib.error import HTTPError

import pytest
from sqlalchemy import select

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research as service


@pytest.fixture
def submission(client):
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="xlk", instrument_type="etf", detail_view_type="etf",
                                     instrument_name="XLK", metadata_json={}))
        session.commit()
        run, _ = service.begin_run(session, ["xlk"])
        run.context_json = {**run.context_json, "cutoff": "2026-09-07T00:00:00+00:00",
            "catalogue": [{"instrument_id": "xlk", "name": "XLK", "instrument_type": "etf"}],
            "instrument_inputs": [{"instrument_id": "xlk", "name": "XLK", "instrument_type": "etf"}],
            "research_dossiers": [{"instrument_id": "xlk"}],
            "web_evidence": [{"operation": "search", "sources": []}, {"operation": "fetch", "sources": [{
                "source_id": "original", "url": "https://example.com/disclosure", "title": "Disclosure",
                "text": 'The company described its "AI investment" plan.', "published_at": "2026-08-01"}]}]}
        session.commit()
        rid = run.entry_id
    draft = {"reviews": [{"instrument_id": "xlk", "summary": '需要核对 "AI investment" 的回报。',
        "coverage": [], "events": [{"event_key": "ai-investment", "action": "new", "direction": "uncertain",
            "title": "AI投入的回报仍需核实", "body": '公司披露 "AI investment" 计划。',
            "next_watch": "跟进兑现情况", "confidence": "reported", "information_type": "fact",
            "recording_type": "backfill", "published_at": "2026-08-01", "occurred_at": None,
            "source_ids": ["original"]}],
        "research": {"fundamental_view": "资金投入与回报之间仍有不确定性。", "valuation_view": "尚缺估值依据。",
                     "source_ids": ["original"]}}]}
    return rid, draft


def test_submission_retains_structured_quotes_without_publishing(client, submission):
    rid, draft = submission
    response = client.post(f"/api/research/runs/{rid}/sector-draft", json=draft)
    assert response.status_code == 200
    assert response.json()["status"] == "pending_fact_review"
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, rid)
        assert run.context_json["submitted_draft"]["reviews"][0]["summary"] == draft["reviews"][0]["summary"]
        assert run.context_json["submitted_draft"]["reviews"][0]["research"]["source_ids"] == ["original"]
        assert run.status == "queued" and run.body == "" and run.context_json["reviews"] == {}
        assert session.scalars(select(RiskCase)).all() == []
        assert len(session.scalars(select(ResearchEntry)).all()) == 1


@pytest.mark.parametrize("invalid, message", [
    ("scope", "标的范围"), ("event_source", "未取得的来源"),
    ("notebook_source", "原始依据"), ("publication", "发布时间"),
])
def test_invalid_submission_can_be_corrected_without_saving(client, submission, invalid, message):
    rid, draft = submission
    broken = copy.deepcopy(draft)
    review = broken["reviews"][0]
    if invalid == "scope":
        review["instrument_id"] = "xlf"
    elif invalid == "event_source":
        review["events"][0]["source_ids"] = ["missing"]
    elif invalid == "notebook_source":
        review["research"]["source_ids"] = ["missing"]
    else:
        review["events"][0]["published_at"] = "2026-08-02"
    response = client.post(f"/api/research/runs/{rid}/sector-draft", json=broken)
    assert response.status_code == 422 and message in response.json()["detail"]
    with get_session_factory()() as session:
        assert "submitted_draft" not in session.get(ResearchEntry, rid).context_json
        assert session.scalars(select(RiskCase)).all() == []
    assert client.post(f"/api/research/runs/{rid}/sector-draft", json=draft).status_code == 200


def test_finished_run_does_not_accept_a_draft(client, submission):
    rid, draft = submission
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, rid)
        run.status = "failed"
        session.commit()
    assert client.post(f"/api/research/runs/{rid}/sector-draft", json=draft).status_code == 409


@pytest.mark.parametrize("tool", ["portfolio", "risk_review"])
def test_instrument_research_does_not_gain_private_portfolio_scope(client, submission, tool):
    rid, _ = submission
    response = client.post(f"/api/research/runs/{rid}/tools", json={"tool": tool, "instrument_ids": ["xlk"]})
    assert response.status_code == 422
    assert "已绑定" in response.json()["detail"]


@pytest.mark.parametrize("tool", ["instruments", "dossier"])
def test_both_entrances_read_the_bound_snapshot_with_common_tools(client, submission, tool):
    rid, _ = submission
    response = client.post(f"/api/research/runs/{rid}/tools", json={"tool": tool, "instrument_ids": ["xlk"]})
    assert response.status_code == 200
    result = response.json()["result"]
    assert (result["assets"][0] if tool == "instruments" else result)["instrument_id"] == "xlk"


def test_quiet_submission_does_not_require_a_working_paper(client, submission):
    rid, _ = submission
    response = client.post(f"/api/research/runs/{rid}/sector-draft", json={"reviews": [
        {"instrument_id": "xlk", "change_kind": "none"}]})
    assert response.status_code == 200
    with get_session_factory()() as session:
        row = session.get(ResearchEntry, rid).context_json["submitted_draft"]["reviews"][0]
        assert row["summary"] == "" and row["events"] == [] and row["research"] is None


def test_mandate_draft_is_persisted_only_with_accepted_research(client, submission):
    from watchlist_app.services.research_dossier import read_mandate
    rid, draft = submission
    update = {"title": "XLK专属研究任务", "background": "工作假设：投入回报需核实。", "focus": ["跟踪投入回报"],
              "mechanisms": [], "research_approach": [], "source_plan": ["公司正式披露"], "gaps": ["回报证据尚缺"]}
    draft["reviews"][0]["research"]["mandate_update"] = update
    assert client.post(f"/api/research/runs/{rid}/sector-draft", json=draft).status_code == 200
    with get_session_factory()() as session:
        assert read_mandate(session, "xlk")["entry_id"] is None
        run = session.get(ResearchEntry, rid)
        service.apply_result(session, run, json.dumps(draft))
        session.commit()
        assert read_mandate(session, "xlk")["focus"] == update["focus"]
        assert run.context_json["reviews"]["xlk"]["research"]["mandate_update"]["title"] == update["title"]


def test_daily_context_is_read_per_instrument_without_copying_other_instrument_material(monkeypatch):
    from watchlist_app import research_mcp as mcp
    context = {"sector_run": True, "run_id": "run", "cutoff": "2026-09-07T00:00:00+00:00", "instrument_ids": ["gold", "equity"],
        "catalogue": [{"instrument_id": "gold"}, {"instrument_id": "equity"}], "instrument_inputs": [
            {"instrument_id": "gold", "benchmark": "Au99.99"}, {"instrument_id": "equity", "name": "公司"}],
        "research_dossiers": [{"instrument_id": "gold", "mandate": {"focus": ["实际利率与国内基差"]}},
                              {"instrument_id": "equity", "materials": [{"body": "无关长资料" * 20000}]}],
        "web_evidence": [{"text": "已抓原文" * 30000}]}
    monkeypatch.setattr(mcp, "request", lambda _: context)
    index = mcp.read_research_context()
    assert index["instrument_ids"] == ["gold", "equity"]
    assert "instrument_inputs" not in index and "web_evidence" not in index
    packet = mcp.read_research_instrument("gold")
    assert packet["instrument_inputs"][0]["benchmark"] == "Au99.99"
    assert packet["research_dossier"]["mandate"]["focus"] == ["实际利率与国内基差"]
    assert "无关长资料" not in str(packet)
    context["instrument_inputs"][0].update(risk_cases=[{"body": "重复正文" * 30000}], reference_data={"sections": {
        "profile": {"name": "黄金"}, "financials": [{"revenue": 100, "date": "2026-06-30"}]}})
    context["prior_events"] = [{"instrument_id": "gold", "withdrawn": True, "body": "已撤回的错误分析", "event_key": "old", "withdrawal_reason": "事实错误"}]
    packet = mcp.read_research_instrument("gold")
    assert "重复正文" not in str(packet) and "已撤回的错误分析" not in str(packet)
    assert packet["prior_events"][0]["withdrawal_reason"] == "事实错误"
    assert packet["reference_sections"] == ["financials"]
    assert mcp.read_research_instrument("gold", "financials")["reference_data"]["data"] == [{"revenue": 100, "date": "2026-06-30"}]
    with pytest.raises(ValueError, match="本轮研究范围"):
        mcp.read_research_instrument("outside")


def test_public_source_tools_retain_sector_evidence_when_used_in_daily_research(monkeypatch):
    from watchlist_app import research_mcp as mcp
    monkeypatch.setattr(mcp, "request", lambda _: {"sector_run": True})
    monkeypatch.setattr(mcp, "read_sector_source", lambda url: {"operation": "fetch", "url": url})
    monkeypatch.setattr(mcp, "search_sector_information", lambda query: {"operation": "search", "query": query})
    assert mcp.read_public_source("https://example.com") == {"operation": "fetch", "url": "https://example.com"}
    assert mcp.search_public_information("public company") == {"operation": "search", "query": "public company"}


def test_submission_tool_uses_structured_payload_and_returns_validation_detail(monkeypatch):
    from watchlist_app import research_mcp
    parsed = service.ReviewResult(reviews=[service.SectorReview(instrument_id="xlk", summary='关于 "AI" 的判断')])
    calls = []
    monkeypatch.setattr(research_mcp, "request", lambda suffix, payload: calls.append((suffix, payload)) or
                        {"status": "pending_fact_review"})
    assert research_mcp.submit_research_review(parsed)["status"] == "pending_fact_review"
    assert calls == [("sector-draft", {"reviews": [{
        "instrument_id": "xlk", "summary": '关于 "AI" 的判断', "change_kind": "none",
        "coverage": [], "events": [], "research": None,
    }]})]

    def rejected(*args):
        raise HTTPError("http://localhost/api", 422, "Unprocessable Entity", {},
                        io.BytesIO(json.dumps({"detail": "事件引用了未取得的来源"}).encode()))
    monkeypatch.setattr(research_mcp, "request", rejected)
    with pytest.raises(ValueError, match="未取得的来源"):
        research_mcp.submit_research_review(parsed)


def test_mcp_stdio_submission_without_database_credentials(client, submission):
    import asyncio
    import sys
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from threading import Thread
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    rid, draft = submission

    class LocalAPI(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers["Authorization"] == "Bearer fixture-run"
            response = client.post(self.path, json=payload, headers={"Authorization": self.headers["Authorization"]})
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *args):
            pass

    # Expose only the test application's routes and disposable database to the real MCP subprocess.
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAPI)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def smoke():
        params = StdioServerParameters(command=sys.executable, args=["-m", "watchlist_app.research_mcp"],
            cwd=str(Path(__file__).resolve().parents[1]), env={
                "INVESTMENT_STUDIO_RESEARCH_API_BASE_URL": f"http://127.0.0.1:{server.server_port}/api",
                "INVESTMENT_STUDIO_RESEARCH_RUN_ID": rid, "INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN": "fixture-run", "DEEPSEEK_API_KEY": "unused-smoke-key"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                tool = next(t for t in listed.tools if t.name == "submit_research_review")
                wire = tool.model_dump(by_alias=True)
                assert wire["annotations"]["readOnlyHint"] is False
                assert "result" in wire["inputSchema"]["properties"]
                response = await session.call_tool(tool.name, {"result": draft})
                result = response.model_dump(by_alias=True)
                assert not result["isError"]
                payload = json.loads(next(part["text"] for part in result["content"] if part["type"] == "text"))
                assert payload["status"] == "pending_fact_review"

    try:
        asyncio.run(smoke())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, rid)
        assert run.context_json["submitted_draft"]["reviews"][0]["summary"] == draft["reviews"][0]["summary"]
        assert run.status == "queued" and run.context_json["reviews"] == {}
        assert session.scalars(select(RiskCase)).all() == []
