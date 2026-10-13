"""Overview preserves valid specialist proposals and uses retained read selectors."""
import asyncio
from copy import deepcopy
import json

from watchlist_app import research_mcp as mcp
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_sections as sections
from .test_sector_submission import submission, mcp_http


def test_specialist_handoff_preserves_valid_structures_into_accepted_draft(submission, mcp_http):
    rid, initial = submission
    draft = json.loads(json.dumps(initial).replace('"original"', '"text:retained-version"'))
    review = draft["reviews"][0]
    # Recent updates precede themes, so they cannot link to a theme proposed later.
    review["events"][0]["theme_ids"] = []
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, rid)
        context = deepcopy(run.context_json)
        context["web_evidence"][-1]["sources"][0]["source_id"] = "text:retained-version"
        run.context_json = context
        run.status = "running"
        session.commit()
        handoff = {}
        for section, field in (("recent_updates", "events"), ("themes", "themes")):
            run.context_json = {**run.context_json, "execution": {"attempt": 1, "research_section": "overview"}}
            session.commit()
            task = mcp.request("tasks", {"role": section, "instrument_id": "xlk", "instruction": "明确待验证的投资影响"})
            mcp.request(f"tasks/{task['task_id']}/result", {"result": {
                "status": "completed", "summary": "明确待验证的投资影响。",
                "source_ids": ["text:retained-version"], field: review[field]}})
            session.refresh(run)
            selected = mcp.request("read", {"resource": "context", "section": "task_results", "path": [task["task_id"], "result"]})
            handoff[field] = selected["data"][field]
    review["events"] = deepcopy(handoff["events"])
    review["themes"] = deepcopy(handoff["themes"])
    review["research"]["investment_view"] = {"risk": "投入回报尚待兑现。", "assumptions": ["投入计划持续执行。"],
        "source_ids": ["text:retained-version"]}
    result = asyncio.run(mcp.mcp.call_tool("submit_research_review", {"result": draft}))
    assert json.loads(result.content[0].text)["status"] == "pending_fact_review"
    with get_session_factory()() as session:
        saved = session.get(ResearchEntry, rid).context_json["submitted_draft"]["reviews"][0]
        assert saved["events"] == review["events"]
        assert saved["themes"] == review["themes"]
        assert saved["themes"][0]["question"] == initial["reviews"][0]["themes"][0]["question"]
        assert saved["themes"][0]["source_ids"] == ["text:retained-version"]
        assert "market_reaction" not in saved["events"][0]
        assert "background" not in saved["themes"][0]


def test_overview_comparison_selector_reads_without_starting_a_calculation(monkeypatch):
    from watchlist_app.services.research_numeric_projection import project_comparison
    monkeypatch.setenv("INVESTMENT_STUDIO_WATCHLIST_HARNESS_MODE", "sector")
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_SECTION", "overview")
    tools = {tool.name for tool in asyncio.run(mcp.mcp.list_tools())}
    assert "compare_instruments" in tools and "read_portfolio_holdings" not in tools
    source = {"source_id": "comparison:retained", "source_type": "computed_metric",
        "input_series": {"xlk": {"points": [{"date": "2026-10-09", "value": 100}]}},
        "data": {"dates": ["2026-10-09"], "rows": [{"instrument_id": "xlk", "return_pct": 0}]}}
    original = deepcopy(source)
    calls = []
    def request(suffix, payload):
        calls.append((suffix, payload))
        assert suffix == "computed-read" and payload["source_id"] == source["source_id"] and payload["tool"] == "comparison"
        return project_comparison(source, **{key: payload[key] for key in ("section", "offset", "limit", "path")})
    monkeypatch.setattr(mcp, "request", request)
    context = {"instrument_ids": ["xlk"], "execution": {"research_section": "overview"}, "sector_run": True}
    handoff = sections.section_context(context, "xlk", "overview")
    assert handoff["retained_computations_read"] == {"tool": "read_research_context", "section": "computed_metrics"}
    result = asyncio.run(mcp.mcp.call_tool("compare_instruments", {"source_id": source["source_id"], "section": "input_series"}))
    assert "2026-10-09" in result.content[0].text
    assert len(calls) == 1 and source == original
