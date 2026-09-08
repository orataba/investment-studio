"""Preserve PM ownership and the original judgment referenced by a later review."""
from copy import deepcopy
from datetime import UTC

from sqlalchemy import select
from studio_identity import current_principal

from watchlist_app.db.models.research import InstrumentResearchNoteRevision
from watchlist_app.services.research_identity import research_identity
from watchlist_app.services.research_themes import get_theme


def note_version(session, instrument_id, note_id, revision, *, actor=None):
    actor = actor or research_identity()
    record = session.scalar(select(InstrumentResearchNoteRevision).where(
        InstrumentResearchNoteRevision.instrument_id == instrument_id,
        InstrumentResearchNoteRevision.note_id == note_id,
        InstrumentResearchNoteRevision.revision_number == revision))
    if record is None or (not current_principal().local_unrestricted and record.team_id != actor["team_id"]):
        raise ValueError("找不到当前团队标的的原始观点版本")
    return record


def prepare_note_values(session, instrument_id, payload, *, actor=None, record=None, provenance=None):
    actor = actor or research_identity()
    from watchlist_app.services.research_access import require_team_write
    principal = require_team_write()
    if record is not None and not principal.local_unrestricted and (record.team_id != actor["team_id"] or record.author_user_id != actor["user_id"]):
        raise ValueError("不能修改其他投资经理的观点")
    values = payload.model_dump()
    old_context = deepcopy(record.research_context or {}) if record is not None else {}
    if payload.research_context is None:
        context = old_context
    else:
        context = {**old_context, **payload.research_context.model_dump(exclude_unset=True)}
    theme_id = context.get("theme_id")
    if theme_id:
        get_theme(session, instrument_id, theme_id, actor=actor)
    related_id, revision = context.get("related_note_id"), context.get("related_revision")
    if bool(related_id) != bool(revision):
        raise ValueError("关联原观点时须同时指定观点和当时的版本")
    if context.get("relationship", "initial") != "initial" and not related_id:
        raise ValueError("观点更新、复盘和经验需要关联一条原始观点")
    if related_id:
        if record is not None and related_id == record.note_id:
            raise ValueError("新的判断或复盘请追加记录，不能关联自身")
        previous = note_version(session, instrument_id, related_id, revision, actor=actor)
        if record is not None and (previous.recorded_at.replace(tzinfo=previous.recorded_at.tzinfo or UTC)
                > record.created_at.replace(tzinfo=record.created_at.tzinfo or UTC)):
            raise ValueError("只能关联这条记录形成前已有的观点版本")
        previous_theme = (previous.research_context or {}).get("theme_id")
        if theme_id and previous_theme and theme_id != previous_theme:
            raise ValueError("复盘主题须与原观点一致")
    if record is None:
        from watchlist_app.services.research_dossier import read_mandate, _notebooks
        notebook, _ = _notebooks(session, instrument_id, False)
        context["research_snapshot"] = {
            "notebook_version_id": (notebook or {}).get("version_id", (notebook or {}).get("run_id")),
            "mandate_version_id": read_mandate(session, instrument_id).get("version_id"),
            "theme_revision": get_theme(session, instrument_id, theme_id, actor=actor).context_json["revision_number"] if theme_id else None,
        }
    values["author"] = record.author if record is not None else actor["display_name"]
    values["research_context"] = {**context, **({"author_role": "user", "recorded_via": "editor"}
        if record is None else {}), **(provenance or {})}
    return values
