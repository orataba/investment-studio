"""Read only selected retained run fields, with the original access checks."""
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import JSON, select, true

from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.services.research_access import (
    require_entry_access, research_context_projection, research_projection_rows,
)


def load_run_fields(session, run_id, fields, *, summary_only=False):
    names = sorted(set(fields) | {"research_actor", "execution"})
    from watchlist_app.db.research_read_context import EXACT_FIELDS
    json_column = ResearchEntry.read_context_json if summary_only or set(names) <= EXACT_FIELDS else ResearchEntry.context_json
    relation, values = research_context_projection(session, {name: JSON for name in names}, json_column=json_column)
    query = select(ResearchEntry.entry_id, ResearchEntry.topic_id, ResearchEntry.team_id,
        ResearchEntry.kind, ResearchEntry.status, *(values[name].label(name) for name in names)).select_from(ResearchEntry)
    if relation is not None:
        query = query.join(relation, true())
    rows = research_projection_rows(session, query.where(ResearchEntry.entry_id == run_id),
                                   {name: (name,) for name in names}, json_column=json_column)
    if not rows or rows[0].kind != "analysis":
        raise HTTPException(404, "研究运行不存在")
    row = rows[0]
    record = SimpleNamespace(**{key: getattr(row, key) for key in ("entry_id", "topic_id", "team_id", "kind", "status")},
        context_json={name: getattr(row, name) for name in names if getattr(row, name) is not None})
    require_entry_access(session, record)
    return record


def load_shared_history_fields(session, run_id, instrument_id, fields):
    """Prepare a run from narrowly selected shared history, not another run's tools.

    Run credentials remain fenced to their current attempt. The server may bind
    public team research metadata for an instrument in that run; private and
    portfolio history and retained originals never enter this projection.
    """
    from studio_identity import current_principal
    from watchlist_app.db.models.workbench import ResearchTopic
    from watchlist_app.services.research_access import instrument_run_scope, topic_portfolio_ids

    allowed = {"sector_run", "research_run", "instrument_ids", "cutoff",
               "input_snapshot_cutoff", "market_queries", "incremental_trigger"}
    if not set(fields) <= allowed:
        raise ValueError("Unsupported shared research history fields")
    principal = current_principal()
    if principal.resource_scope:
        current = load_run_fields(session, principal.resource_scope.get("id"), {"instrument_ids"}, summary_only=True)
        if instrument_id not in current.context_json.get("instrument_ids", []):
            raise HTTPException(403, "本轮研究不能绑定范围外标的的历史资料")
    names = sorted(set(fields) | {"sector_run", "research_run"})
    relation, values = research_context_projection(session, {name: JSON for name in names},
                                                   json_column=ResearchEntry.read_context_json)
    query = select(ResearchEntry.topic_id, *(values[name].label(name) for name in names)).select_from(
        ResearchEntry).join(ResearchTopic, ResearchTopic.topic_id == ResearchEntry.topic_id)
    if relation is not None:
        query = query.join(relation, true())
    query = query.where(ResearchEntry.entry_id == run_id, ResearchEntry.kind == "analysis",
        ResearchEntry.team_id == principal.team_id, ResearchTopic.team_id == principal.team_id,
        ResearchTopic.visibility == "team", ResearchTopic.portfolio_id.is_(None),
        instrument_run_scope(session, instrument_id))
    rows = research_projection_rows(session, query, {name: (name,) for name in names},
                                    json_column=ResearchEntry.read_context_json)
    if (not rows or not (rows[0].sector_run or rows[0].research_run)
            or topic_portfolio_ids(session, session.get(ResearchTopic, rows[0].topic_id))):
        raise HTTPException(404, "共享标的研究历史不存在")
    return SimpleNamespace(context_json={name: getattr(rows[0], name) for name in fields
                                         if getattr(rows[0], name) is not None})


def load_run_list_item(session, run_id, field, source_id):
    """Select one retained source in SQL, never transport the entire source list."""
    import json
    from sqlalchemy import Text, cast, func
    from watchlist_app.services.research_access import _postgres_projection_context
    if field not in {"computed_metrics", "tool_evidence"}:
        raise ValueError("不是可读取的计算来源分区")
    # Check current run/attempt and its original conversation scope first.
    load_run_fields(session, run_id, set())
    postgres = session.get_bind().dialect.name == "postgresql"
    if postgres:
        affected, safe = _postgres_projection_context()
        values = func.json_array_elements(safe[field]).table_valued("value").lateral("source_item")
        item = cast(values.c.value, JSON)
        query = select(item, affected).select_from(ResearchEntry).join(values, true()).where(
            ResearchEntry.entry_id == run_id, item["source_id"].as_string() == source_id).limit(1)
    else:
        values = func.json_each(ResearchEntry.context_json[field]).table_valued("value")
        item = values.c.value
        query = select(item).select_from(ResearchEntry).join(values, true()).where(
            ResearchEntry.entry_id == run_id, func.json_extract(item, "$.source_id") == source_id).limit(1)
    row = session.execute(query).first()
    if row is None:
        return None
    result = row[0] if postgres else json.loads(row[0])
    if postgres and row[1] and "\ufffd" in json.dumps(result, ensure_ascii=False):
        # Rare historical JSON NUL: preserve the exact original, including a
        # literal U+FFFD. Normal sources never load the complete retained list.
        original = load_run_fields(session, run_id, {field}).context_json.get(field) or []
        return next((value for value in original if value.get("source_id") == source_id), None)
    return result
