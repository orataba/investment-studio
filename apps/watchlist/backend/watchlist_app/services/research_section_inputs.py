"""Read only the frozen inputs needed by a research role and its selected page."""
from types import SimpleNamespace

from sqlalchemy import JSON, case, cast, func, select, true, type_coerce
from sqlalchemy.dialects.postgresql import aggregate_order_by

from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.services.research_run_context import load_run_fields


# Dependencies of section_context's public branches, not a second saved context.
BASE = {
    key: {("dossier", "mandate"), ("research_guidance",)}
    for key in ("skill", "guidance", "instrument", "team_guidance", "instrument_supplement")
}
BRANCHES = {
    "recent_updates": {
        "tracked_events": {("prior_events",)},
        "required_event_ids": {("working", "affected_event_ids")},
        "incremental_trigger": {("incremental_trigger",)},
        "fmp_inputs": {("fmp_inputs",)},
        "analyst_estimate_changes": {("sector_estimate_evidence",)},
        "acquisition": {(key,) for key in ("initialization", "last_successful_review_cutoff", "market_coverage")},
    },
    "themes": {
        "active_themes": {("dossier", "themes")},
        "required_theme_ids": {("working", "affected_theme_ids")},
        "related_updates": {("dossier", "themes"), ("research_tasks",)},
        "prior_theme_assessments": {("dossier", "themes"), ("dossier", "notebook", "questions")},
    },
    "quantitative": {
        "numeric_tools": {("dossier", "instrument_type"), ("dossier", "mandate")},
        "prior_module_index": {("dossier", "notebook", "modules")},
        "prior_quantitative_assessments": {("dossier", "notebook", "modules")},
        "data_gaps": {("data_gaps",)},
    },
    "overview": {
        "task_results": {("research_tasks",)},
        "prior_judgment": {("dossier", "notebook", "investment_view")},
        "current_stance": {("dossier", "current_stance")},
        "pm_views": {("dossier", "pm_views")},
        "publication_requirements": {("working", "affected_event_ids"), ("working", "affected_theme_ids")},
    },
}
THEME_OMISSIONS = {"versions", "sources", "updates", "research_progress"}


def _object_fields(value, fields):
    if not isinstance(value, dict):
        return value
    return {key: _object_fields(item, children) if children is not None else item
            for key, item in value.items() if key in fields for children in (fields[key],)}


def _sql_object(value, fields, name, *, exclude=False):
    """Keep selected object members, preserving their order, nulls and exact JSON."""
    is_object = func.json_typeof(value) == "object"
    rows = func.json_each(case((is_object, value), else_=cast({}, JSON))).table_valued(
        "key", "value", with_ordinality="ordinality").lateral(name)
    member = type_coerce(rows.c.value, JSON)
    replacements = [(rows.c.key == key, _sql_object(member, children, f"{name}_{key}"))
                    for key, children in fields.items() if children is not None]
    projected = case(*replacements, else_=member) if replacements else member
    predicate = rows.c.key.not_in(fields) if exclude else rows.c.key.in_(fields)
    packed = select(func.json_object_agg(rows.c.key, aggregate_order_by(projected, rows.c.ordinality))).select_from(rows).where(predicate).scalar_subquery()
    return type_coerce(case((is_object, func.coalesce(packed, cast({}, JSON))), else_=value), JSON)


def _dossier_fields(dependencies):
    fields = {"instrument_id": None}
    for path in dependencies:
        if path[0] != "dossier":
            continue
        if len(path) == 2:
            fields[path[1]] = None
        else:
            fields.setdefault(path[1], {})[path[2]] = None
    return fields


def _compact_dossier(value, fields, instrument_id):
    candidates = value if isinstance(value, list) else [value]
    dossier = next((row for row in candidates if isinstance(row, dict) and row.get("instrument_id") == instrument_id), None)
    if dossier is None:
        return []
    result = _object_fields(dossier, fields)
    if isinstance(result.get("themes"), list):
        result["themes"] = [{key: item for key, item in row.items() if key not in THEME_OMISSIONS}
                            for row in result["themes"] if row.get("status") == "active"]
    return [result]


def _load_inputs(session, run_id, instrument_id, dependencies):
    """PostgreSQL excludes unused originals before transfer or Python decoding."""
    from watchlist_app.services.research_access import require_entry_access, research_context_projection, research_projection_rows
    names = {path[0] for path in dependencies} - {"dossier", "working"}
    dossier_fields = _dossier_fields(dependencies)
    working_fields = {path[1]: None for path in dependencies if path[0] == "working"}
    if len(dossier_fields) > 1:
        names.add("research_dossiers")
    if working_fields:
        names.add("research_working_sets")
    if not names:
        return {}
    names.update(("research_actor", "execution"))
    if session.get_bind().dialect.name == "postgresql":
        relation, values = research_context_projection(session, dict.fromkeys(sorted(names), JSON))
        if "research_dossiers" in names:
            source = values["research_dossiers"]
            entries = func.json_array_elements(case((func.json_typeof(source) == "array", source),
                else_=cast([], JSON))).table_valued("value", with_ordinality="ordinality").lateral("section_dossier")
            dossier = type_coerce(entries.c.value, JSON)
            slim = _sql_object(dossier, dossier_fields, "section_dossier_fields")
            if "themes" in dossier_fields:
                themes = func.json_array_elements(case((func.json_typeof(dossier["themes"]) == "array", dossier["themes"]),
                    else_=cast([], JSON))).table_valued("value", with_ordinality="ordinality").lateral("section_theme")
                theme = type_coerce(themes.c.value, JSON)
                active = select(func.json_agg(aggregate_order_by(_sql_object(theme, dict.fromkeys(THEME_OMISSIONS),
                    "section_theme_fields", exclude=True), themes.c.ordinality))).select_from(themes).where(theme["status"].as_string() == "active").scalar_subquery()
                # Replace only the selected themes member; other dossier values
                # keep the same fixed field projection and original JSON order.
                members = func.json_each(slim).table_valued("key", "value", with_ordinality="ordinality").lateral("section_dossier_members")
                slim = select(func.json_object_agg(members.c.key, aggregate_order_by(case((members.c.key == "themes",
                    func.coalesce(active, cast([], JSON))), else_=type_coerce(members.c.value, JSON)), members.c.ordinality))).select_from(members).scalar_subquery()
            values["research_dossiers"] = type_coerce(select(slim).select_from(entries).where(
                dossier["instrument_id"].as_string() == instrument_id).order_by(entries.c.ordinality).limit(1).scalar_subquery(), JSON)
        if working_fields:
            values["research_working_sets"] = type_coerce(func.json_build_object(instrument_id,
                _sql_object(values["research_working_sets"][instrument_id], working_fields, "section_working")), JSON)
        for key in names & {"incremental_trigger", "fmp_inputs"}:
            values[key] = type_coerce(func.json_build_object(instrument_id, values[key][instrument_id]), JSON)
        identity = ("entry_id", "topic_id", "team_id", "kind", "status")
        query = select(*(getattr(ResearchEntry, key) for key in identity),
            *(values[key].label(key) for key in sorted(names))).select_from(ResearchEntry).join(relation, true()).where(
            ResearchEntry.entry_id == run_id)
        rows = research_projection_rows(session, query, {key: (key,) for key in names})
        if not rows:
            from fastapi import HTTPException
            raise HTTPException(404, "研究运行不存在")
        context = {key: getattr(rows[0], key) for key in names if getattr(rows[0], key) is not None}
        require_entry_access(session, SimpleNamespace(**{key: getattr(rows[0], key) for key in identity}, context_json=context))
    else:
        context = load_run_fields(session, run_id, names).context_json
    # Also compact the rare exact-original restoration for a selected JSON NUL.
    context = dict(context)
    if "research_dossiers" in context:
        context["research_dossiers"] = _compact_dossier(context["research_dossiers"], dossier_fields, instrument_id)
    if "research_working_sets" in context:
        working = context["research_working_sets"] or {}
        context["research_working_sets"] = {instrument_id: _object_fields(working.get(instrument_id), working_fields)}
    return context


def load_section_inputs(session, run_id, *, instrument_id=None, section=None, task_id=None, path=None):
    """Use the existing run/attempt fence before resolving an assigned role."""
    fields = {"instrument_ids", "cutoff", "input_snapshot_cutoff", "sector_run"}
    if task_id is not None:
        fields.add("research_tasks")
    record = load_run_fields(session, run_id, fields)
    context = record.context_json
    if task_id is not None:
        from watchlist_app.services.research_tasks import _task
        task = _task(context, task_id)
        instrument_id, section = task["instrument_id"], task["role"]
    if section not in BRANCHES:
        raise ValueError("未知研究部分")
    if instrument_id not in context.get("instrument_ids", []):
        raise ValueError("标的不在本轮研究范围")
    branches = {**BASE, **BRANCHES[section]}
    selected = path[0] if path else None
    dependencies = set().union(*(branches.values() if selected is None else [branches.get(selected, set())]))
    # Task results belong to this assignment, not the overview's instrument-wide directory.
    if task_id is not None and selected in {"assignment", "task_results", "retained_computations"}:
        dependencies = set()
    context.update(_load_inputs(session, run_id, instrument_id, dependencies))
    if task_id is not None and (selected is None or selected == "retained_computations"):
        from watchlist_app.services.research_run_evidence import attach_computed_sources
        context["computed_metrics"] = load_run_fields(session, run_id, {"computed_metrics"}, summary_only=True).context_json.get("computed_metrics", [])
        context = attach_computed_sources(session, run_id, context, metadata_only=True)
    return context
