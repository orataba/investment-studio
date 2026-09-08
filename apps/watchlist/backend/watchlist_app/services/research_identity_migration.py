"""Explicit initial-operator mapping, called by the Studio migration command.

The caller verifies the destination member. Dry-run is the default, and unassigned
private conversations require a separate affirmative option. Unknown PM authors
remain unknown; no identity is inferred from the person running this command.
"""
from sqlalchemy import select
from watchlist_app.db.models.research import InstrumentResearchNote, InstrumentResearchNoteRevision
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic


def claim_research_identity(session, *, user_id: str, display_name: str, team_id: str,
                            from_user_id: str = "local-investor", claim_unassigned_conversations=False,
                            dry_run=True) -> dict:
    if not user_id or not team_id or user_id == from_user_id:
        raise ValueError("须明确指定真实团队成员 ID，不能继续使用本机临时身份")
    counts = {"notes": 0, "note_revisions": 0, "entries": 0, "conversations": 0, "run_attributions": 0}
    for name, model in (("notes", InstrumentResearchNote), ("note_revisions", InstrumentResearchNoteRevision)):
        for record in session.scalars(select(model).where(model.team_id == team_id, model.author_user_id == from_user_id)):
            counts[name] += 1
            if not dry_run:
                record.author_user_id = user_id
    claimed = set()
    if claim_unassigned_conversations:
        for topic in session.scalars(select(ResearchTopic).where(ResearchTopic.team_id == team_id,
                ResearchTopic.visibility == "private", ResearchTopic.created_by_user_id.is_(None))):
            counts["conversations"] += 1
            claimed.add(topic.topic_id)
            if not dry_run:
                topic.created_by_user_id = user_id
    for entry in session.scalars(select(ResearchEntry).where(ResearchEntry.team_id == team_id)):
        if entry.author_user_id == from_user_id or (entry.topic_id in claimed and entry.kind != "analysis" and entry.author_user_id is None):
            counts["entries"] += 1
            if not dry_run:
                entry.author_user_id = user_id
        if not dry_run and entry.responsible_user_id == from_user_id:
            entry.responsible_user_id = user_id
        context = entry.context_json or {}
        actor = context.get("research_actor") or {}
        if actor.get("user_id") == from_user_id:
            counts["run_attributions"] += 1
            if not dry_run:
                entry.context_json = {**context, "legacy_research_actor": actor, "research_actor": {
                    "user_id": user_id, "display_name": display_name, "team_id": team_id,
                    "kind": "user", "mode": "account"}}
    if not dry_run:
        session.flush()
    return {"dry_run": dry_run, "user_id": user_id, "team_id": team_id, **counts}
