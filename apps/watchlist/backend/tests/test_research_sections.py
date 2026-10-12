"""Specialist evidence, execution boundaries and human workspace controls."""
from copy import deepcopy
from dataclasses import replace
from studio_identity import current_principal, principal_context
from types import SimpleNamespace

import pytest

from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_sections as sections
from watchlist_app.services import sector_research
from .test_sector_research import seed_sector, add_sources, bind_policy_theme
from .test_research_themes import research_client
from .test_research_activity import activity_client, publish, event


def test_specialists_bind_evidence_and_reuse_only_same_frozen_input(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = sector_research.begin_run(session, ["xlk"])
        add_sources(run)
        run.status = "running"
        cutoff = run.context_json["cutoff"]
        run.context_json = {**run.context_json, "input_snapshot_cutoff": cutoff, "section_results": {},
            "execution": {"attempt": 1, "research_section": "recent_updates"}}
        session.commit()
        with principal_context(replace(current_principal(), resource_scope={"kind": "run", "id": run.entry_id, "attempt": "1"})):
            payload = sections.SectionSubmission(instrument_id="xlk", section="recent_updates",
                status="completed", summary="原文发生变化，影响仍待量化验证", source_ids=["web-one"])
            with pytest.raises(ValueError, match="原始依据"):
                sections.save_section_result(session, run, payload.model_copy(update={"source_ids": ["invented"]}))
            assert not run.context_json.get("section_results")
            saved = sections.save_section_result(session, run, payload)
            assert saved["attempt"] == 1 and saved["input_snapshot_cutoff"] == cutoff
            assert sections.section_complete(run.context_json, "xlk", "recent_updates")
            retry = {**run.context_json, "execution": {"attempt": 2}}
            assert sections.section_complete(retry, "xlk", "recent_updates")
            assert not sections.section_complete({**retry, "input_snapshot_cutoff": "changed"}, "xlk", "recent_updates")
            with pytest.raises(ValueError, match="对应专员"):
                sections.save_section_result(session, run, payload.model_copy(update={"section": "quantitative"}))


def test_synthesis_requires_all_specialists_and_overview_owner():
    context = {"sector_run": True, "instrument_ids": ["xlk"], "input_snapshot_cutoff": "bound",
        "execution": {"research_section": "overview"}, "section_results": {"xlk": {}}}
    run = SimpleNamespace(context_json=context)
    with pytest.raises(ValueError, match="三个专员"):
        sections.require_section_results(run)

    for name in sections.SECTION_ORDER:
        context["section_results"]["xlk"][name] = {"input_snapshot_cutoff": "bound"}
    sections.require_section_results(run)
    context["execution"]["research_section"] = "quantitative"
    with pytest.raises(ValueError, match="总览综合"):
        sections.require_section_results(run)


@pytest.mark.parametrize("defect,message", [
    ("theme_figure", "主题图表必须绑定真实留存的数值来源"),
    ("missing_event", "更新或解除的事件没有原跟进记录"),
    ("event_figure", "市场反应必须引用当前标的留存的事件窗口计算"),
])
def test_specialist_and_final_share_source_and_event_action_contract(client, monkeypatch, defect, message):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = sector_research.begin_run(session, ["xlk"])
        theme = bind_policy_theme(session, run)
        add_sources(run)
        section = "themes" if defect == "theme_figure" else "recent_updates"
        run.status = "running"
        run.context_json = {**run.context_json, "input_snapshot_cutoff": run.context_json["cutoff"],
            "section_results": {}, "execution": {"attempt": 1, "research_section": section}}
        session.commit()
        values = {"instrument_id": "xlk", "section": section, "status": "completed",
            "summary": "有来源的专员提案仍须满足发布契约。", "source_ids": ["web-one"]}
        if defect == "theme_figure":
            values["themes"] = [{"theme_id": theme["theme_id"], "theme_key": theme["theme_key"],
                "figure_source_ids": ["web-one"]}]
        else:
            values["events"] = [event(source_ids=["web-one"], published_at=run.context_json["cutoff"],
                occurred_at=None, follow_up="none", action="updated" if defect == "missing_event" else "new",
                **({"market_reaction": {"status": "available", "figure_source_ids": ["web-one"]}}
                   if defect == "event_figure" else {}))]
        payload = sections.SectionSubmission.model_validate(values)
        with principal_context(replace(current_principal(), resource_scope={"kind": "run", "id": run.entry_id, "attempt": "1"})):
            with pytest.raises(ValueError, match=message):
                sections.save_section_result(session, run, payload)
            assert not run.context_json["section_results"]
        final = sector_research.ReviewResult(reviews=[sector_research.SectorReview(
            instrument_id="xlk", events=payload.events, themes=payload.themes)])
        with pytest.raises(ValueError, match=message):
            sector_research.validate_result(session, run, final)

        # The specialist can repair its own proposal without starting synthesis.
        if defect == "theme_figure":
            values["themes"][0].update(figure_source_ids=[], source_ids=["web-one"])
        else:
            values["events"][0].update(action="new", market_reaction=None)
        repaired = sections.SectionSubmission.model_validate(values)
        with principal_context(replace(current_principal(), resource_scope={"kind": "run", "id": run.entry_id, "attempt": "1"})):
            assert sections.save_section_result(session, run, repaired)["section"] == section


def test_specialists_receive_distinct_context_without_whole_notebook_history():
    context = {"instrument_ids": ["xlk"], "cutoff": "bound", "input_snapshot_cutoff": "bound",
        "research_dossiers": [{"instrument_id": "xlk", "instrument_type": "etf",
            "mandate": {"registration": {"name": "XLK", "instrument_type": "etf"}},
            "themes": [{"theme_id": "theme-1", "status": "active", "question": "订单是否兑现？"}],
            "notebook": {"investment_view": {"direction": "综合判断"},
                "modules": [{"key": "market-quantitative", "summary": "价格变化"}], "versions": ["UNRELATED ARCHIVE"]},
            "current_stance": {"body": "投资经理观点"}}],
        "prior_events": [{"instrument_id": "xlk", "event_key": "event", "follow_up": "watch", "title": "外部动态"}],
        "section_results": {"xlk": {"recent_updates": {"events": [{"event_key": "event", "importance_score": 4}]}}}}
    news = sections.section_context(context, "xlk", "recent_updates")
    themes = sections.section_context(context, "xlk", "themes")
    quant = sections.section_context(context, "xlk", "quantitative")
    overview = sections.section_context(context, "xlk", "overview")
    assert news["tracked_events"] and "active_themes" not in news and "prior_judgment" not in news
    assert themes["active_themes"] and themes["related_updates"] and "numeric_tools" not in themes
    assert quant["prior_quantitative_assessments"] and "tracked_events" not in quant and "active_themes" not in quant
    assert overview["section_results"] and overview["prior_judgment"] and overview["current_stance"]
    assert len({value["skill"] for value in (news, themes, quant, overview)}) == 4
    assert all("UNRELATED ARCHIVE" not in str(value) for value in (news, themes, quant, overview))


@pytest.mark.parametrize("assigned", sections.SECTION_ORDER)
def test_specialist_context_keeps_its_assignment_but_synthesis_and_assistant_can_read(assigned):
    context = {"sector_run": True, "instrument_ids": ["xlk"],
        "execution": {"research_section": assigned}}
    assert sections.section_context(context, "xlk", assigned)["section"] == assigned
    for other in (*sections.SECTION_ORDER, "overview"):
        if other == assigned:
            continue
        with pytest.raises(ValueError, match=f"当前分工为 {assigned}"):
            sections.section_context(context, "xlk", other)
    for allowed in ({**context, "execution": {"research_section": "overview"}},
                    {**context, "sector_run": False}):
        for other in (*sections.SECTION_ORDER, "overview"):
            assert sections.section_context(allowed, "xlk", other)["section"] == other


def test_section_read_tool_advertises_only_assigned_section_without_mutating_assistant_schema(monkeypatch):
    import asyncio
    from watchlist_app import research_mcp
    def section_enum():
        tool = next(tool for tool in asyncio.run(research_mcp.mcp.list_tools()) if tool.name == "read_research_section")
        return tool.input_schema["properties"]["section"]["enum"]
    monkeypatch.setenv("INVESTMENT_STUDIO_WATCHLIST_HARNESS_MODE", "sector")
    for assigned in sections.SECTION_ORDER:
        monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_SECTION", assigned)
        assert section_enum() == [assigned]
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_SECTION", "overview")
    assert set(section_enum()) == {*sections.SECTION_ORDER, "overview"}
    monkeypatch.setenv("INVESTMENT_STUDIO_WATCHLIST_HARNESS_MODE", "conversation")
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_SECTION", "themes")
    assert set(section_enum()) == {*sections.SECTION_ORDER, "overview"}


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


def test_automatic_overview_is_only_scope_and_assigned_section_directory():
    from watchlist_app.services.research_tool_projection import read_research_context
    context = {"sector_run": True, "run_id": "run", "instrument_ids": ["xlk"], "cutoff": "frozen",
        "execution": {"research_section": "quantitative"},
        "research_working_sets": {"xlk": {"unrelated": "hidden"}}, "section_results": {"xlk": {"themes": "hidden"}}}
    view = read_research_context(lambda suffix: context)
    assert view["section_reads"] == [{"tool": "read_research_section", "instrument_id": "xlk", "section": "quantitative"}]
    assert "hidden" not in str(view) and "research_working_sets" not in view and "section_results" not in view
