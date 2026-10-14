"""Product section skills and scoped input directories for dynamic research."""
from copy import deepcopy

from watchlist_app.services.research_skills import SECTION_SKILLS

RESEARCH_ROLES = ("recent_updates", "themes", "quantitative")


def _event_context(event):
    return {key: deepcopy(event.get(key)) for key in (
        "case_id", "event_key", "event_version_id", "title", "body", "direction", "impact_level",
        "follow_up", "next_watch", "follow_up_until", "next_observation_on", "theme_ids", "published_at",
        "occurred_at", "importance_score", "source_ids") if event.get(key) is not None}


def section_context(context, instrument_id, section):
    """Role-specific inputs: shared storage does not mean shared prompts/history."""
    if section not in (*RESEARCH_ROLES, "overview"):
        raise ValueError("未知研究部分")
    if instrument_id not in context.get("instrument_ids", []):
        raise ValueError("标的不在本轮研究范围")
    dossier = next((item for item in context.get("research_dossiers", []) if item["instrument_id"] == instrument_id), {})
    mandate = dossier.get("mandate") or {}
    skills = {**SECTION_SKILLS, **mandate.get("section_skills", {})}
    from watchlist_app.services.research_guidance import role_guidance
    team_guidance = role_guidance(context, section)
    notebook = dossier.get("notebook") or {}
    working = (context.get("research_working_sets") or {}).get(instrument_id) or {}
    from watchlist_app.services.research_tasks import current_tasks, task_directory
    tasks = {key: task for key, task in current_tasks(context).items() if task["instrument_id"] == instrument_id}
    registration = mandate.get("registration") or {}
    base = {"instrument_id": instrument_id, "section": section, "skill": skills[section],
        "guidance": mandate.get("guidance", ""),
        "instrument": {key: deepcopy(registration.get(key)) for key in ("name", "identifier", "instrument_type", "currency", "exchange")},
        "cutoff": context.get("cutoff"), "input_snapshot_cutoff": context.get("input_snapshot_cutoff"),
        "instruction": "只读取本角色所需的目录与选定原件。历史按具体问题和版本读取，不遍历全档。专员结论是分析，最终引用仍使用原 source_id。"}
    if team_guidance is not None:
        base.update(skill=team_guidance["skill"], team_guidance={key: value for key, value in team_guidance.items() if key != "skill"},
            instrument_supplement={"revision": mandate.get("revision"), "version_id": mandate.get("version_id"),
                "skill": mandate.get("section_skills", {}).get(section, "")})
    if section == "recent_updates":
        base.update(
            tracked_events=[_event_context(row) for row in context.get("prior_events", [])
                if row.get("instrument_id") == instrument_id and row.get("follow_up") == "watch"],
            required_event_ids=working.get("affected_event_ids", []),
            incremental_trigger=deepcopy((context.get("incremental_trigger") or {}).get(instrument_id)),
            acquisition={"initialization": deepcopy(context.get("initialization")),
                "last_successful_review_cutoff": context.get("last_successful_review_cutoff"),
                "market_coverage": deepcopy(context.get("market_coverage")),
                "search_tool": "search_market_information", "original_tool": "read_market_source",
                "transcript_coverage": "当前未接入FMP业绩会转录采集；共享文本可能有相关纪要，需核实覆盖。缺失时按具体需要读取发行人正式披露或公开原文，不能声称已读完整转录。"})
    elif section == "themes":
        themes = [{key: deepcopy(value) for key, value in row.items()
                   if key not in {"versions", "sources", "updates", "research_progress"}}
                  for row in dossier.get("themes", []) if row.get("status") == "active"]
        for theme in themes:
            if theme.get("manual_note_count", 0) > len(theme.get("manual_notes", [])):
                theme["manual_notes_read"] = {"tool": "read_research_dossier", "instrument_id": instrument_id,
                    "version_id": theme["source_version_id"], "note": "主题摘要仅列最新人工记录；按需读取本版本的完整人工记录。"}
        theme_ids = {row["theme_id"] for row in themes}
        candidates = [row for task in tasks.values() if task["role"] == "recent_updates"
            for row in (task.get("result") or {}).get("events", [])
            if theme_ids.intersection(row.get("theme_ids", [])) or (row.get("importance_score") or 0) >= 3]
        base.update(active_themes=themes, required_theme_ids=working.get("affected_theme_ids", []),
            related_updates=deepcopy(candidates),
            prior_theme_assessments=[deepcopy(row) for row in notebook.get("questions", []) if row.get("theme_id") in theme_ids],
            theme_read={"tool": "read_research_dossier", "instrument_id": instrument_id, "section": "themes"})
    elif section == "quantitative":
        from watchlist_app.services.research_applicability import numeric_tool_capabilities
        kind = dossier.get("instrument_type") or registration.get("instrument_type")
        base.update(numeric_tools=numeric_tool_capabilities(kind),
            prior_module_index=[{**{key: row.get(key) for key in ("key", "title", "coverage")},
                "read": {"tool": "read_research_dossier", "instrument_id": instrument_id,
                    **({"version_id": row["version_id"]} if row.get("version_id") else
                       {"section": "modules", "path": [index]})}}
                for index, row in enumerate(notebook.get("modules", []))],
            prior_quantitative_assessments=[deepcopy(row) for row in notebook.get("modules", [])
                if row.get("key") in {"market-quantitative", "financial-analysis", "valuation", "earnings-expectations"}],
            quantitative_reads=[{"tool": "read_research_instrument", "instrument_id": instrument_id,
                "section": name} for name in ("performance", "sector_context", "financials")],
            retained_computations_read={"tool": "read_research_context", "section": "computed_metrics"},
            data_gaps=deepcopy(context.get("data_gaps") or []))
    else:
        base.update(retained_computations_read={"tool": "read_research_context", "section": "computed_metrics"},
            task_results=task_directory(context, instrument_id), prior_judgment=deepcopy(notebook.get("investment_view")),
            current_stance=deepcopy(dossier.get("current_stance")), pm_views=deepcopy(dossier.get("pm_views") or []),
            publication_requirements={
                "required_event_ids": deepcopy(working.get("affected_event_ids", [])),
                "required_theme_ids": deepcopy(working.get("affected_theme_ids", [])),
                "instruction": "按问题决定是否委派专员；无需固定三个任务。读取task_results中的完整结果再综合。失败或资料不足任务是覆盖缺口，不是已核实无变化。专员结果尚未发布。events/themes 已通过结构和来源校验，纳入最终reviews时保留原字段、完整source_ids和新主题question；只修改需核实或协调的具体判断，不凭叙述重建提案。量化发现纳入research.modules/facts并引用原始计算。各专员重要风险须综合到research.investment_view.risks及下一观察条件；不采纳的风险说明依据。不要把内部量化判断伪装成外部动态。风控读取最终investment_view的完整风险判断及外部风险事件。"},
            overview_read={"tool": "read_research_dossier", "instrument_id": instrument_id, "section": "research_state"})
    return base
