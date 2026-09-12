"""Preserve PM ownership and the original judgment referenced by a later review."""
from copy import deepcopy
from datetime import UTC, datetime

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


def note_sources(session, instrument_id, context):
    """Resolve only the sources explicitly cited in this PM revision."""
    from watchlist_app.services.market_evidence import hydrate_source
    from watchlist_app.services.research_notebook import research_sources, _original_source
    refs = set(context.get("source_ids", []))
    if not refs:
        return []
    cutoff = datetime.fromisoformat(context["information_cutoff"]) if context.get("information_cutoff") else None
    retained = context.get("sources")
    if retained is None and context.get("source_run_id"):
        # Historical PM records kept explicit IDs and the source run, but did not
        # freeze their own evidence list. Read that retained run, never today's dossier.
        from watchlist_app.db.models.workbench import ResearchEntry
        from watchlist_app.services.research_access import require_team_publication_scope
        run = session.get(ResearchEntry, context["source_run_id"])
        if run and (current_principal().local_unrestricted or run.team_id == current_principal().team_id):
            require_team_publication_scope(session, run)
            retained = [{**source, "pm_binding_note": "旧观点未单独冻结来源；此项来自原运行保留的明确引用。"}
                        for sid, source in research_sources(run.context_json, run.entry_id).items() if sid in refs]
    return [source for row in retained or [] if row.get("source_id") in refs
            and (source := hydrate_source(row, cutoff=cutoff)) and _original_source(source, instrument_id, cutoff)
            and (source.get("instrument_id") in {None, instrument_id}
                 or (source.get("source_type") == "computed_metric" and source.get("scope") == "public_market"))]


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
    from watchlist_app.services.research_activity import resolve_research_update, resolve_event_reference
    linked = None
    if context.get("research_update_id"):
        linked = resolve_research_update(session, instrument_id, context["research_update_id"], actor=actor)
    if context.get("event_case_id") or context.get("event_version_id"):
        event = resolve_event_reference(session, instrument_id, context.get("event_case_id"), context.get("event_version_id"))
        if linked and linked["reference"].get("event_version_id") != event["reference"]["event_version_id"]:
            raise ValueError("引用的研究更新与事件版本不一致")
        linked = linked or event
    if linked:
        if record is not None and datetime.fromisoformat(linked["recorded_at"]) > record.created_at.replace(tzinfo=record.created_at.tzinfo or UTC):
            raise ValueError("不能将后来形成的研究更新写成原观点的事前依据")
        context["research_update_title"] = linked["title"]
        context["research_update_recorded_at"] = linked["recorded_at"]
    if theme_id:
        get_theme(session, instrument_id, theme_id, actor=actor)
    related_id, revision = context.get("related_note_id"), context.get("related_revision")
    if bool(related_id) != bool(revision):
        raise ValueError("关联原观点时须同时指定观点和当时的版本")
    if context.get("relationship", "initial") != "initial" and not related_id and not linked:
        raise ValueError("观点更新、复盘和经验需要关联一条原始观点或研究更新")
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
    if payload.research_context is not None and "source_ids" in payload.research_context.model_fields_set and "sources" not in (provenance or {}):
        from watchlist_app.services.research_dossier import read_dossier
        from watchlist_app.services.research_notebook import ResearchNotebook, research_sources, validate_notebook
        from watchlist_app.services.market_evidence import source_reference
        saved_context = values["research_context"]
        cutoff = saved_context.get("information_cutoff") or (record.created_at.replace(tzinfo=record.created_at.tzinfo or UTC).isoformat() if record else datetime.now(UTC).isoformat())
        refs = saved_context.get("source_ids", [])
        available = research_sources({"cutoff": cutoff, "research_dossiers": [read_dossier(session, instrument_id, actor=actor)]}, "pm-editor") if refs else {}
        # Editing prose must not rebind an unchanged citation to a later material
        # that happens to have the same catalogue ID.
        if record is not None:
            available.update({source["source_id"]: source for source in note_sources(session, instrument_id, old_context)})
        validate_notebook(ResearchNotebook(source_ids=refs), instrument_id, available)
        saved_context.update(information_cutoff=cutoff, sources=[deepcopy(source_reference(available[sid])) for sid in dict.fromkeys(refs)])
    return values
