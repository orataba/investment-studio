"""Freeze relevant current research, while immutable history remains addressable.

This selection is not an authorization shortcut and not an evidence-read receipt.
The full current notebook is still merged under its aggregate publication CAS.
"""
from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy import Boolean, String, func, or_, select, true
from sqlalchemy.orm import defer

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic, RiskCase


def current_state(value):
    """History is read by exact version, never copied recursively into a new task."""
    if isinstance(value, list):
        return [current_state(item) for item in value]
    if isinstance(value, dict):
        return {key: deepcopy(item) if key == "sources" else current_state(item) for key, item in value.items()
                if key not in {"versions", "history", "updates", "research_progress", "notebook_history"}}
    return deepcopy(value)


def working_notebook(notebook, selected_themes):
    value = current_state(notebook)
    if not value:
        return value
    for key in ("changes", "forecast_reviews", "lessons", "prior_analysis"):
        value.pop(key, None)
    for key, field, active in (("questions", "tracking_status", "active"), ("forecasts", "status", "active"), ("catalysts", "status", "scheduled")):
        value[key] = [row for row in value.get(key, []) if row.get("theme_id") in selected_themes
                      and row.get(field, active) == active]
    value["modules"] = [{**{key: item for key, item in row.items() if key != "analysis"},
        **({"body_read": {"tool": "read_research_dossier", "version_id": row["version_id"]},
            "body_note": "仅当前摘要；完整已保存分析按原版本读取。"} if row.get("version_id") else
           {"body_note": "旧记录未提供独立版本标识，可按底稿原版本读取，未补造时点。"})}
        for row in value.get("modules", [])]
    return value


def current_events(session, instrument_id):
    from watchlist_app.services.sector_research import event_record
    # The current snapshot, scalar history length and explicitly watched status
    # suffice; old event snapshot bodies never cross the database boundary.
    from watchlist_app.services.research_access import research_context_projection
    relation, fields = research_context_projection(session,
        {"follow_up": String, "withdrawn": Boolean}, json_column=RiskCase.evidence_json)
    query = select(RiskCase, func.json_array_length(RiskCase.history_json)).options(defer(RiskCase.history_json))
    if relation is not None:
        query = query.join(relation, true())
    rows = session.execute(query.where(RiskCase.instrument_id == instrument_id, RiskCase.signal.like("sector:%"),
        fields["withdrawn"].is_not(True), or_(fields["follow_up"] == "watch", RiskCase.trigger_active.is_(True))))
    output = []
    for case, history_length in rows:
        evidence = case.evidence_json or {}
        if evidence.get("withdrawn") or not (evidence.get("follow_up") == "watch" or case.trigger_active):
            continue
        value = SimpleNamespace(**{key: getattr(case, key) for key in (
            "case_id", "instrument_id", "signal", "title", "body", "status", "trigger_active", "created_at", "updated_at")},
            evidence_json=evidence, history_json=[])
        event = event_record(value)
        event["event_version_id"] = evidence.get("event_version_id") or f"{case.case_id}:{max(1, history_length or 0)}"
        event.pop("history", None)
        # Unknown legacy follow-up is not silently declared watched.
        event["follow_up"] = evidence.get("follow_up")
        event["evidence"] = current_state(evidence)
        output.append(event)
    return output


def source_ids(value):
    result = set()
    if isinstance(value, list):
        for item in value:
            result.update(source_ids(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"source_ids", "figure_source_ids"} and isinstance(item, list):
                result.update(sid for sid in item if isinstance(sid, str))
            elif key not in {"sources", "versions", "history"}:
                result.update(source_ids(item))
    return result


def bind_history_read(session, run_id, instrument_id, selector, value):
    """An explicit authorized read adds exact references, never the whole archive."""
    from datetime import UTC, datetime
    from fastapi import HTTPException
    from watchlist_app.services.research_access import require_entry_access
    from watchlist_app.services.market_evidence import source_reference
    run = session.get(ResearchEntry, run_id, with_for_update=True, populate_existing=True)
    require_entry_access(session, run, tool_write=True)
    context = dict(run.context_json or {})
    if run.status not in {"queued", "running"}:
        raise HTTPException(409, "本轮研究已结束，不能增加历史引用")
    if instrument_id not in {row["instrument_id"] for row in context.get("research_dossiers", [])}:
        raise ValueError("历史引用超出本轮绑定标的")
    stamp = value.get("recorded_at") or (value.get("value") or {}).get("recorded_at") or (value.get("value") or {}).get("updated_at")
    if stamp:
        recorded = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if recorded.replace(tzinfo=recorded.tzinfo or UTC) > datetime.fromisoformat(context["cutoff"]):
            raise ValueError("该历史版本形成于本轮截止时间之后")
    key = f"{instrument_id}:{selector}"
    bound = dict(context.get("research_historical_reads") or {})
    if key in bound:
        return
    # The exact full version remains in its original owner. These identifiers
    # suffice for citation/forecast/PM link validation and independent retrieval.
    record = value.get("value") or {}
    if value.get("kind") == "event":
        record = {**record, "case_id": record.get("case_id") or value["version_id"].removeprefix("event:").rsplit(":", 1)[0]}
    bound[key] = {"instrument_id": instrument_id, "selector": selector,
        **{name: value.get(name) for name in ("kind", "version_id", "information_cutoff", "recorded_at")},
        "value": {name: deepcopy(record[name]) for name in (
            "key", "version_id", "theme_id", "note_id", "revision_number", "event_key", "event_version_id", "case_id",
            "recorded_at", "updated_at", "created_at", "reference") if name in record},
        "sources": [source_reference(source) for source in value.get("sources", record.get("sources", []))]}
    run.context_json = {**context, "research_historical_reads": bound}
    session.commit()


def build_working_set(dossier, events, context):
    iid = dossier["instrument_id"]
    notebook = dossier.get("notebook") or {}
    themes = [theme for theme in dossier.get("themes", [])
              if theme.get("status") == "active" and theme.get("priority") in {"important", "core"}]
    tracked = [event for event in events if event.get("follow_up") == "watch"]
    trigger = (context.get("incremental_trigger") or {}).get(iid, {})
    mode = "initial" if not context.get("last_successful_review_cutoff") or context.get("initialization") else "incremental" if context.get("scheduled") else "explicit"
    affected_themes, affected_events = set(), set()
    if mode in {"initial", "explicit"}:
        affected_themes.update(theme["theme_id"] for theme in themes)
        affected_events.update(event["case_id"] for event in tracked)
    dependencies = {}
    for kind, rows, identity in (("theme", themes, "theme_id"), ("event", events, "case_id")):
        for row in rows:
            for sid in source_ids(row):
                dependencies.setdefault(sid, []).append({"kind": kind, "id": row[identity]})
    for sid in source_ids(notebook):
        dependencies.setdefault(sid, []).append({"kind": "notebook", "id": notebook.get("version_id")})
    for reason in trigger.get("reasons", []):
        for row in reason.get("sources", []):
            for source_id in {*row.get("previous_source_ids", []), row.get("previous_source_id")}:
                for dependent in dependencies.get(source_id, []):
                    if dependent["kind"] == "theme":
                        affected_themes.add(dependent["id"])
                    elif dependent["kind"] == "event":
                        affected_events.add(dependent["id"])
        for row in [*reason.get("items", []), *reason.get("themes", [])]:
            if row.get("theme_id"):
                affected_themes.add(row["theme_id"])
            if row.get("case_id"):
                affected_events.add(row["case_id"])
    selected = {theme["theme_id"]: theme["source_version_id"] for theme in themes}
    return {"schema_version": 1, "mode": mode, "cutoff": context["cutoff"],
        "baseline": {"notebook_version": notebook.get("version_id"), "mandate_version": (dossier.get("mandate") or {}).get("version_id")},
        "tracked_event_versions": {row["case_id"]: row["event_version_id"] for row in tracked},
        "selected_theme_versions": selected, "selected_theme_ids": sorted(selected), "reviewed_theme_ids": [],
        "affected_event_ids": sorted(affected_events), "affected_theme_ids": sorted(affected_themes & selected.keys()),
        "reasons": deepcopy(trigger.get("reasons", [])), "source_dependencies": dependencies,
        "archive_read": {"tool": "read_research_dossier", "instrument_id": iid,
            "selectors": ["version_id", "update_id"],
            "note": "历史按具体版本或更新标识读取；未选中不等于已复核、已解除或不存在。"}}


def read_working_dossier(session, instrument_id, context, *, actor):
    from watchlist_app.services.research_dossier import (
        _notebooks, read_mandate, plan_for_mandate, material_record, material_index,
        require_instrument, method_library,
    )
    from watchlist_app.services.research_applicability import research_applicability
    from watchlist_app.services.research_themes import theme_summaries
    from watchlist_app.services.research_views import read_current_stance
    instrument = require_instrument(session, instrument_id)
    mandate = read_mandate(session, instrument_id)
    notebook, _ = _notebooks(session, instrument_id, False)
    themes = [current_state(theme) for theme in theme_summaries(session, instrument_id, actor=actor, original_sources=True)["themes"]
              if theme["status"] == "active" and theme.get("priority") in {"core", "important"}]
    events = current_events(session, instrument_id)
    selected = {theme["theme_id"] for theme in themes}
    notebook = working_notebook(notebook, selected)
    dossier = {"instrument_id": instrument_id, "name": instrument.instrument_name,
        "instrument_type": instrument.instrument_type, "mandate": current_state(mandate),
        "research_plan": plan_for_mandate(mandate["registration"], mandate),
        "research_applicability": research_applicability(mandate["registration"]),
        "notebook": notebook, "notebook_history": [], "themes": themes, "pm_views": [],
        "frameworks": method_library()["frameworks"], "available_modules": [],
        "historical_cases": [], "historical_case_limitations": [],
        "current_stance": read_current_stance(session, instrument_id, actor=actor),
        "material_index": material_index(session, instrument_id, actor=actor)}
    working = build_working_set(dossier, events, context)
    wanted = set(working["source_dependencies"])
    # Only originals directly supporting the selected current state are bound.
    sources = [*(notebook or {}).get("sources", []),
        *(source for theme in themes for source in theme.get("sources", [])),
        *(source for event in events for source in event["evidence"].get("sources", []))]
    dossier["prior_sources"] = list({row["source_id"]: row for row in sources if row.get("source_id") in wanted}.values())
    if notebook:
        notebook["sources"] = [row for row in notebook.get("sources", []) if row.get("source_id") in wanted]
    material_ids = {row["source_id"].removeprefix("material:") for row in dossier["material_index"]
        if row.get("source_id") and (working["mode"] != "incremental" or row["source_id"] in wanted)}
    for reason in working["reasons"]:
        material_ids.update(row["entry_id"] for row in reason.get("materials", []) if row.get("entry_id"))
    entries = session.scalars(select(ResearchEntry).join(ResearchTopic).where(
        ResearchEntry.entry_id.in_(material_ids), ResearchEntry.kind == "evidence",
        ResearchTopic.topic_id == f"dossier:{instrument_id}", ResearchTopic.visibility == "team",
        ResearchTopic.team_id == actor["team_id"]))
    dossier["materials"] = [material_record(entry, instrument_id) for entry in entries]
    changed_documents = any(row.get("key") == "registered_material_directory"
        for reason in working["reasons"] for row in reason.get("materials", []))
    if working["mode"] != "incremental" or changed_documents:
        # This is the existing registered-file extractor, restricted to current
        # documents. A directory alone is not an original or a citation identity.
        from watchlist_app.services.research_dossier import _document_materials
        dossier["materials"].extend(_document_materials(session, instrument_id))
    dossier["working_set"] = working
    dossier["review_agenda"] = {"focus_themes": [{"theme_id": theme["theme_id"], "theme_key": theme.get("theme_key"),
        "required_review": theme["theme_id"] in working["affected_theme_ids"]} for theme in themes],
        "pending_events": [{key: event.get(key) for key in ("case_id", "event_key", "event_version_id", "title", "next_watch", "follow_up_until")}
                           for event in events if event.get("follow_up") == "watch"],
        "instruction": "只复核本轮受影响事项及主题。其他候选主题仅提供当前认识；可依据本轮新来源提出相关稀疏更新，不提交的主题没有本轮复核结论。历史及原件按精确版本读取，不遍历全部历史。"}
    from watchlist_app.services.read_models import serialize_payload
    return serialize_payload(dossier), events
