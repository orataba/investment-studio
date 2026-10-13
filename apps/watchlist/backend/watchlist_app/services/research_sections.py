"""Specialists share one frozen research run; only synthesis is published."""
from copy import deepcopy
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SECTION_ORDER = ("recent_updates", "themes", "quantitative")
from watchlist_app.services.research_skills import SECTION_SKILLS, SectionSkills

class SectionRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    explanation: str = Field(min_length=1, max_length=3000)
    severity: Literal["important", "critical"] = "important"
    source_ids: list[str] = Field(min_length=1)


# Existing section payloads remain the only write contracts. Specialists retain
# drafts here; final synthesis and the independent reviewer validate publication.
from watchlist_app.services.sector_research import SectorEvent
from watchlist_app.services.research_themes import AnalystThemeUpdate


class SectionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str
    section: Literal["recent_updates", "themes", "quantitative"]
    status: Literal["completed", "limited", "not_applicable"]
    summary: str = Field(min_length=1, max_length=8000)
    source_ids: list[str] = Field(default_factory=list)
    risks: list[SectionRisk] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    events: list[SectorEvent] = Field(default_factory=list)
    themes: list[AnalystThemeUpdate] = Field(default_factory=list)

    @model_validator(mode="after")
    def section_contract(self):
        if self.events and self.section != "recent_updates":
            raise ValueError("事件更新由标的动态专员提交")
        if self.themes and self.section != "themes":
            raise ValueError("主题更新由主题专员提交")
        if self.status != "completed" and not any(item.strip() for item in self.limitations):
            raise ValueError("资料不足或不适用须说明具体原因")
        return self


def section_complete(context, instrument_id, section):
    result = (context.get("section_results") or {}).get(instrument_id, {}).get(section)
    return bool(result and context.get("input_snapshot_cutoff")
        and result.get("input_snapshot_cutoff") == context["input_snapshot_cutoff"])


def require_section_results(run):
    context = run.context_json or {}
    if not context.get("sector_run"):
        return
    if (context.get("execution") or {}).get("research_section") != "overview":
        raise ValueError("最终研究草稿只由总览综合阶段提交")
    missing = [f"{iid}/{section}" for iid in context.get("instrument_ids", []) for section in SECTION_ORDER
               if not section_complete(context, iid, section)]
    if missing:
        raise ValueError("总览综合前须完成三个专员的本轮研究：" + "、".join(missing))


def save_section_result(session, run, payload: SectionSubmission):
    from watchlist_app.services.research_access import require_entry_access
    from watchlist_app.services.research_notebook import ResearchNotebook, research_sources, validate_notebook, notebook_source_ids
    from watchlist_app.services.sector_research import event_evidence_sources, validate_review_updates, SectorReview
    session.refresh(run, with_for_update=True)
    require_entry_access(session, run, tool_write=True)
    context = run.context_json or {}
    execution = context.get("execution") or {}
    if (run.status != "running" or not context.get("sector_run")
            or execution.get("research_section") != payload.section):
        raise ValueError("仅当前运行中的对应专员可提交本节研究")
    if payload.instrument_id not in context.get("instrument_ids", []) or not context.get("input_snapshot_cutoff"):
        raise ValueError("专员结果不属于本轮冻结的研究范围")
    sources = research_sources(context, run.entry_id, session=session)
    refs = notebook_source_ids(payload.model_dump(mode="json"))
    validate_notebook(ResearchNotebook(source_ids=sorted(refs)), payload.instrument_id, sources)
    event_sources = event_evidence_sources(context, run.entry_id, notebook_evidence=sources)
    validate_review_updates(session, run, SectorReview(instrument_id=payload.instrument_id,
        events=payload.events, themes=payload.themes), sources, event_sources)
    if payload.status == "completed" and not refs:
        raise ValueError("完成的专员研究须绑定实际读取的依据；资料不足请提交 limited 并说明限制")
    result = {**payload.model_dump(mode="json"), "attempt": execution.get("attempt", 0),
              "input_snapshot_cutoff": context["input_snapshot_cutoff"], "recorded_at": datetime.now(UTC).isoformat()}
    # These are sparse write contracts: omitted fields must not become explicit
    # nulls (or default values) when synthesis incorporates the accepted proposal.
    result["events"] = [event.model_dump(mode="json", exclude_unset=True) for event in payload.events]
    result["themes"] = [theme.model_dump(mode="json", exclude_unset=True) for theme in payload.themes]
    results = deepcopy(context.get("section_results") or {})
    results.setdefault(payload.instrument_id, {})[payload.section] = result
    run.context_json = {**context, "section_results": results}
    session.commit()
    return result


def _event_context(event):
    return {key: deepcopy(event.get(key)) for key in (
        "case_id", "event_key", "event_version_id", "title", "body", "direction", "impact_level",
        "follow_up", "next_watch", "follow_up_until", "next_observation_on", "theme_ids", "published_at",
        "occurred_at", "importance_score", "source_ids") if event.get(key) is not None}


def section_context(context, instrument_id, section):
    """Role-specific inputs: shared storage does not mean shared prompts/history."""
    if section not in (*SECTION_ORDER, "overview"):
        raise ValueError("未知研究部分")
    assigned = (context.get("execution") or {}).get("research_section")
    if context.get("sector_run") and assigned in SECTION_ORDER and section != assigned:
        raise ValueError(f"当前分工为 {assigned} 专员，请读取自己的 section={assigned}；具体论点所需原件仍可按 source_id 或所需资料分区读取。")
    if instrument_id not in context.get("instrument_ids", []):
        raise ValueError("标的不在本轮研究范围")
    dossier = next((item for item in context.get("research_dossiers", []) if item["instrument_id"] == instrument_id), {})
    mandate = dossier.get("mandate") or {}
    skills = {**SECTION_SKILLS, **mandate.get("section_skills", {})}
    notebook = dossier.get("notebook") or {}
    working = (context.get("research_working_sets") or {}).get(instrument_id) or {}
    results = (context.get("section_results") or {}).get(instrument_id, {})
    registration = mandate.get("registration") or {}
    base = {"instrument_id": instrument_id, "section": section, "skill": skills[section],
        "guidance": mandate.get("guidance", ""),
        "instrument": {key: deepcopy(registration.get(key)) for key in ("name", "identifier", "instrument_type", "currency", "exchange")},
        "cutoff": context.get("cutoff"), "input_snapshot_cutoff": context.get("input_snapshot_cutoff"),
        "instruction": "只读取本角色所需的目录与选定原件。历史按具体问题和版本读取，不遍历全档。专员结论是分析，最终引用仍使用原 source_id。"}
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
        candidates = [row for row in (results.get("recent_updates") or {}).get("events", [])
            if theme_ids.intersection(row.get("theme_ids", [])) or (row.get("importance_score") or 0) >= 3]
        base.update(active_themes=themes, required_theme_ids=working.get("affected_theme_ids", []),
            related_updates=deepcopy(candidates),
            prior_theme_assessments=[deepcopy(row) for row in notebook.get("questions", []) if row.get("theme_id") in theme_ids],
            theme_read={"tool": "read_research_dossier", "instrument_id": instrument_id, "section": "themes"})
    elif section == "quantitative":
        from watchlist_app.services.research_applicability import numeric_tool_capabilities
        kind = dossier.get("instrument_type") or registration.get("instrument_type")
        base.update(numeric_tools=numeric_tool_capabilities(kind),
            prior_quantitative_assessments=[deepcopy(row) for row in notebook.get("modules", [])
                if row.get("key") in {"market-quantitative", "financial-analysis", "valuation", "earnings-expectations"}],
            quantitative_reads=[{"tool": "read_research_instrument", "instrument_id": instrument_id,
                "section": name} for name in ("performance", "sector_context", "financials")],
            retained_computations_read={"tool": "read_research_context", "section": "computed_metrics"},
            data_gaps=deepcopy(context.get("data_gaps") or []))
    else:
        base.update(retained_computations_read={"tool": "read_research_context", "section": "computed_metrics"},
            section_results=deepcopy(results), prior_judgment=deepcopy(notebook.get("investment_view")),
            current_stance=deepcopy(dossier.get("current_stance")), pm_views=deepcopy(dossier.get("pm_views") or []),
            publication_requirements={
                "required_event_ids": deepcopy(working.get("affected_event_ids", [])),
                "required_theme_ids": deepcopy(working.get("affected_theme_ids", [])),
                "instruction": "专员结果尚未发布。events/themes 已通过结构和来源校验，纳入最终reviews时保留原字段、完整source_ids和新主题question；只修改需核实或协调的具体判断，不凭叙述重建提案。量化发现纳入research.modules/facts并引用原始计算。各专员重要风险须综合到research.investment_view.risks及下一观察条件；不采纳的风险说明依据。不要把内部量化判断伪装成外部动态。风控读取最终investment_view的完整风险判断及外部风险事件。"},
            overview_read={"tool": "read_research_dossier", "instrument_id": instrument_id, "section": "research_state"})
    return base
