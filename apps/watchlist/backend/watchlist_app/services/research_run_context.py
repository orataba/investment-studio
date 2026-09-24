"""Read only selected retained run fields, with the original access checks."""
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import JSON, select, true

from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.services.research_access import (
    require_entry_access, research_context_projection, research_projection_rows,
)


def load_run_fields(session, run_id, fields):
    names = sorted(set(fields) | {"research_actor"})
    relation, values = research_context_projection(session, {name: JSON for name in names})
    query = select(ResearchEntry.entry_id, ResearchEntry.topic_id, ResearchEntry.team_id,
        ResearchEntry.kind, ResearchEntry.status, *(values[name].label(name) for name in names)).select_from(ResearchEntry)
    if relation is not None:
        query = query.join(relation, true())
    rows = research_projection_rows(session, query.where(ResearchEntry.entry_id == run_id),
                                   {name: (name,) for name in names})
    if not rows or rows[0].kind != "analysis":
        raise HTTPException(404, "研究运行不存在")
    row = rows[0]
    record = SimpleNamespace(**{key: getattr(row, key) for key in ("entry_id", "topic_id", "team_id", "kind", "status")},
        context_json={name: getattr(row, name) for name in names if getattr(row, name) is not None})
    require_entry_access(session, record)
    return record
