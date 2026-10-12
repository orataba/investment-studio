"""Specialist sessions hand off retained evidence without sharing transcripts."""
import asyncio
from datetime import UTC, datetime

from studio_identity import current_principal
from watchlist_app import research_mcp
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner as runner


def running_run():
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="specialists", title="Specialists", instrument_ids=["xlk"],
            created_by_user_id=current_principal().user_id))
        session.flush()
        session.add(ResearchEntry(entry_id="specialists", topic_id="specialists", title="Specialists", kind="analysis", status="running",
            context_json={"sector_run": True, "instrument_ids": ["xlk"], "research_actor": current_principal().to_dict(),
                "input_snapshot_cutoff": datetime.now(UTC).isoformat(), "execution": {"attempt": 1}}))
        session.commit()


def complete(section):
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "specialists")
        results = dict(run.context_json.get("section_results", {}))
        results["xlk"] = {**results.get("xlk", {}), section: {
            "summary": "Retained finding", "input_snapshot_cutoff": run.context_json["input_snapshot_cutoff"], "attempt": 1}}
        run.context_json = {**run.context_json, "section_results": results}
        session.commit()


def test_specialist_order_reuses_completed_input_and_only_then_enables_synthesis(client, monkeypatch):
    running_run()
    complete("recent_updates")
    launched = []
    def wait(section, **kwargs):
        complete(section)
        assert kwargs["accepted"]()
        return "ok", "", 0
    monkeypatch.setattr(runner, "_wait_harness", wait)
    def launch(section):
        launched.append(section)
        return section
    assert runner._run_specialists("specialists", 1, launch) is None
    assert launched == ["themes", "quantitative"]
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "specialists").context_json["execution"]["research_section"] == "overview"


def test_missing_section_submission_stops_without_starting_other_roles(client, monkeypatch):
    running_run()
    launched = []
    monkeypatch.setattr(runner, "_wait_harness", lambda *args, **kwargs: ("fluent answer", "", 0))
    def launch(section):
        launched.append(section)
        return section
    result = runner._run_specialists("specialists", 1, launch)
    assert result[2] == 1
    assert launched == ["recent_updates"]
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "specialists").context_json["execution"]["research_section"] != "overview"


def test_recovery_with_all_sections_complete_enters_synthesis_without_another_call(client):
    running_run()
    for section in ("recent_updates", "themes", "quantitative"):
        complete(section)
    def unexpected_launch(section):
        raise AssertionError(f"Already retained section launched: {section}")
    assert runner._run_specialists("specialists", 1, unexpected_launch) is None
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "specialists").context_json["execution"]["research_section"] == "overview"


def test_specialist_tools_are_distinct_and_cannot_publish_or_modify_pm_views(monkeypatch):
    monkeypatch.setenv("INVESTMENT_STUDIO_WATCHLIST_HARNESS_MODE", "sector")
    tools = {}
    for section in ("recent_updates", "themes", "quantitative", "overview"):
        monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_SECTION", section)
        tools[section] = {tool.name for tool in asyncio.run(research_mcp.mcp.list_tools())}
        assert not tools[section] & {"record_investment_view", "manage_research_theme", "calculate_portfolio_allocation"}
    assert "search_market_information" in tools["recent_updates"]
    assert "run_quant_analysis" not in tools["recent_updates"]
    assert "run_quant_analysis" in tools["quantitative"]
    assert "search_market_information" not in tools["quantitative"]
    assert "submit_research_review" in tools["overview"]
    assert all("submit_research_review" not in tools[role] for role in ("recent_updates", "themes", "quantitative"))
