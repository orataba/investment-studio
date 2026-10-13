"""Specialist evidence, execution boundaries and human workspace controls."""
from copy import deepcopy

import pytest

from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_sections as sections
from .test_research_themes import research_client
from .test_research_activity import activity_client, publish, event


def test_specialists_receive_distinct_context_without_whole_notebook_history():
    context = {"instrument_ids": ["xlk"], "cutoff": "bound", "input_snapshot_cutoff": "bound",
        "research_dossiers": [{"instrument_id": "xlk", "instrument_type": "etf",
            "mandate": {"registration": {"name": "XLK", "instrument_type": "etf"}},
            "themes": [{"theme_id": "theme-1", "status": "active", "question": "订单是否兑现？"}],
            "notebook": {"investment_view": {"direction": "综合判断"},
                "modules": [{"key": "market-quantitative", "summary": "价格变化"}], "versions": ["UNRELATED ARCHIVE"]},
            "current_stance": {"body": "投资经理观点"}}],
        "prior_events": [{"instrument_id": "xlk", "event_key": "event", "follow_up": "watch", "title": "外部动态"}],
        "research_tasks": {"news": {"task_id": "news", "instrument_id": "xlk", "role": "recent_updates",
            "input_snapshot_cutoff": "bound", "result": {"events": [{"event_key": "event", "importance_score": 4}]}}}}
    news = sections.section_context(context, "xlk", "recent_updates")
    themes = sections.section_context(context, "xlk", "themes")
    quant = sections.section_context(context, "xlk", "quantitative")
    overview = sections.section_context(context, "xlk", "overview")
    assert news["tracked_events"] and "active_themes" not in news and "prior_judgment" not in news
    assert themes["active_themes"] and themes["related_updates"] and "numeric_tools" not in themes
    assert quant["prior_quantitative_assessments"] and "tracked_events" not in quant and "active_themes" not in quant
    assert overview["task_results"] and overview["prior_judgment"] and overview["current_stance"]
    assert len({value["skill"] for value in (news, themes, quant, overview)}) == 4
    assert all("UNRELATED ARCHIVE" not in str(value) for value in (news, themes, quant, overview))


def test_skills_are_editable_with_revision_and_not_overwritten_by_analyst(research_client):
    client = research_client
    from watchlist_app.services.research_dossier import ResearchMandateInput, effective_mandate
    path = "/api/research/instruments/fund-us-agg/dossier"
    current = client.get(path).json()["mandate"]
    skills = {**current["section_skills"], "themes": "优先检查下一季订单兑现；不建立泛化主题。"}
    response = client.put(path + "/mandate", json={"guidance": "以现有资料为准", "section_skills": skills,
        "expected_revision": current["revision"]})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["section_skills"] == skills
    previous = {key: saved[key] for key in ResearchMandateInput.model_fields}
    altered = deepcopy(previous)
    altered["section_skills"]["themes"] = "研究员企图改写用户方法"
    assert effective_mandate(ResearchMandateInput.model_validate(altered), previous, origin="research")["section_skills"] == skills
    assert client.put(path + "/mandate", json={"guidance": "old", "section_skills": skills,
        "expected_revision": current["revision"]}).status_code == 409


def test_theme_manual_notes_and_deletion_check_revision(research_client):
    client = research_client
    path = "/api/research/instruments/fund-us-agg/themes"
    theme = client.post(path, json={"title": "订单兑现", "question": "下季订单是否落地？"}).json()
    endpoint = path + "/" + theme["theme_id"]
    response = client.post(endpoint + "/notes", json={"body": "人工访谈记录：尚未得到订单确认。", "expected_revision": 1})
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["revision_number"] == 2 and saved["manual_notes"][0]["author_user_id"]
    assert client.get(endpoint).json()["manual_notes"] == saved["manual_notes"]
    assert client.delete(endpoint + "?expected_revision=1").status_code == 409
    assert client.delete(endpoint + "?expected_revision=2").status_code == 204
    assert client.get(endpoint).status_code == 404


def test_private_strategy_keeps_automatic_risk_but_not_investment_research(research_client, monkeypatch):
    from datetime import UTC, datetime
    from watchlist_app.core.settings import get_settings
    from watchlist_app.db.models import InstrumentAttributeValue
    from watchlist_app.services import research_policy
    monkeypatch.setattr(get_settings(), "research_automatic_enabled", True)
    with get_session_factory()() as session:
        session.add(InstrumentAttributeValue(instrument_id="sxv264", attribute_key="coverage_status", value_json="Invested", adopted_at=datetime.now(UTC)))
        session.commit()
        assert "sxv264" not in research_policy.automatic_research_ids(session)
        assert "sxv264" in research_policy.automatic_risk_ids(session)
        research_policy.require_instrument_research(session, ["sxv264"], scheduled=True, risk=True)


def test_event_category_filters_before_pagination_and_late_arrivals(activity_client):
    from watchlist_app.services.research_activity import event_page
    with get_session_factory()() as session:
        for number, category in enumerate(("news", "earnings", "earnings")):
            publish(session, events=[event(event_key=f"development-{number}", title=f"外部披露{number}",
                body=f"不同外部披露事实{number}", event_category=category, follow_up="none")])
        page = event_page(session, "xlk", scope="history", event_category="earnings", limit=1)
        assert page["total"] == 2 and page["has_more"] and page["next_offset"] == 1
        assert len(page["events"]) == 1 and page["events"][0]["event_category"] == "earnings"
        following = event_page(session, "xlk", scope="history", event_category="earnings", limit=1, offset=1)
        assert following["events"][0]["event_key"] != page["events"][0]["event_key"]
        recent = event_page(session, "xlk", event_category="earnings")
        assert recent["late_arrival_count"] == 2
        assert all(row["event_category"] == "earnings" for row in recent["late_arrivals"])


@pytest.mark.parametrize("kind,node,supported", [
    ("etf", "etf-equity-sector", True), ("index", "index-equity-broad-market", True),
    ("public_fund", "fund-public-equity-active", True), ("public_fund", "fund-public-fixed-income-aggregate", False),
    ("etf", "etf-cash", False), ("etf", "etf-tactical-volatility", False),
    ("etf", "etf-multi-asset-allocation", False), ("index", "index-commodity", False),
])
def test_registered_asset_scope_controls_research(kind, node, supported):
    from watchlist_app.services.research_applicability import investment_research_supported
    assert investment_research_supported({"instrument_type": kind, "taxonomy": {"assigned_node_id": node}}) is supported


def test_automatic_overview_keeps_task_directory_out_of_initial_prompt():
    from watchlist_app.services.research_tool_projection import read_research_context
    context = {"sector_run": True, "run_id": "run", "instrument_ids": ["xlk"], "cutoff": "frozen",
        "execution": {"research_section": "overview"},
        "research_working_sets": {"xlk": {"unrelated": "hidden"}}, "research_tasks": {"task": {"result": "hidden"}}}
    view = read_research_context(lambda suffix: context)
    assert view["section_reads"] == [{"tool": "read_research_section", "instrument_id": "xlk", "section": "overview"}]
    assert view["sections"]["task_results"]["count"] == 1
    assert "hidden" not in str(view) and "research_working_sets" not in view
