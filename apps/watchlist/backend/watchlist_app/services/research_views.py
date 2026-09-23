"""Preserve PM ownership and the original judgment referenced by a later review."""
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from studio_identity import current_principal

from watchlist_app.db.models.research import InstrumentResearchNoteRevision
from watchlist_app.services.research_identity import research_identity
from watchlist_app.services.research_themes import get_theme


def _stance_selection(session, instrument_id, actor):
    from watchlist_app.db.models.research import InstrumentInvestmentStance
    return session.scalar(select(InstrumentInvestmentStance).where(
        InstrumentInvestmentStance.instrument_id == instrument_id,
        InstrumentInvestmentStance.team_id == actor["team_id"],
    ).order_by(InstrumentInvestmentStance.selected_at.desc(), InstrumentInvestmentStance.selection_id.desc()).limit(1))


def read_current_stance(session, instrument_id, *, actor=None):
    actor = actor or research_identity()
    selection = _stance_selection(session, instrument_id, actor)
    if selection is None or selection.note_id is None:
        return None
    from watchlist_app.api.routes.research import _serialize_note_revision
    from watchlist_app.services.read_models import serialize_payload
    from watchlist_app.db.models.research import InstrumentResearchNote
    revision = note_version(session, instrument_id, selection.note_id, selection.note_revision, actor=actor)
    current = session.get(InstrumentResearchNote, (instrument_id, selection.note_id))
    if current is None or current.deleted_at:
        return None
    return serialize_payload({"selection_id": selection.selection_id, "selected_at": selection.selected_at,
        "selected_by": selection.selected_by, "selected_by_name": selection.selected_by_name,
        "note": {**_serialize_note_revision(revision), "created_at": current.created_at,
                 "updated_at": revision.recorded_at, "updated_by": revision.recorded_by},
        "has_later_revision": current.revision_number != selection.note_revision})


def select_current_stance(session, instrument_id, note_id, revision, *, actor=None):
    from watchlist_app.db.models.research import InstrumentInvestmentStance, InstrumentResearchNote
    from watchlist_app.db.models.instruments import InstrumentDetail
    from watchlist_app.services.research_access import require_team_write
    require_team_write()
    actor = actor or research_identity()
    if bool(note_id) != bool(revision):
        raise ValueError("选择总体观点须同时指定观点和版本")
    # Serialize selection with deletion and other selections for this instrument.
    session.execute(select(InstrumentDetail.instrument_id).where(
        InstrumentDetail.instrument_id == instrument_id).with_for_update())
    if note_id:
        original = note_version(session, instrument_id, note_id, revision, actor=actor)
        current = session.get(InstrumentResearchNote, (instrument_id, note_id), populate_existing=True)
        if current is None or current.deleted_at or original.change_type == "delete" or original.team_id != actor["team_id"]:
            raise ValueError("只能选择当前团队未删除的投资观点")
        if original.note_type == "review" or (original.research_context or {}).get("relationship") in {"review", "lesson"}:
            raise ValueError("复盘和经验不能作为当前总体观点；请另存你的当前判断")
    previous = _stance_selection(session, instrument_id, actor)
    if previous and (previous.note_id, previous.note_revision) == (note_id, revision):
        return read_current_stance(session, instrument_id, actor=actor)
    session.add(InstrumentInvestmentStance(selection_id=uuid4().hex, instrument_id=instrument_id,
        team_id=actor["team_id"], note_id=note_id, note_revision=revision, selected_at=datetime.now(UTC),
        selected_by=actor["user_id"], selected_by_name=actor["display_name"]))
    session.flush()
    return read_current_stance(session, instrument_id, actor=actor)


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


def _linked_sources(session, instrument_id, linked, actor):
    """Timeline cards contain source indexes; freeze the full retained originals."""
    refs = {s["source_id"] for s in linked.get("sources", [])}
    if not refs:
        return []
    from watchlist_app.services.research_dossier import read_dossier_version
    version_id = linked.get("reference", {}).get("theme_version_id") or linked.get("reference", {}).get("notebook_version_id")
    if version_id:
        return [s for s in read_dossier_version(session, instrument_id, version_id, actor=actor)["sources"]
                if s["source_id"] in refs]
    from watchlist_app.db.models.workbench import ResearchEntry
    from watchlist_app.services.research_access import require_team_publication_scope
    from watchlist_app.services.research_notebook import research_sources
    run = session.get(ResearchEntry, linked.get("run_id")) if linked.get("run_id") else None
    if run:
        require_team_publication_scope(session, run)
        if run.team_id != actor["team_id"] and not current_principal().local_unrestricted:
            raise ValueError("引用的资料不属于当前团队")
        return [s for sid, s in research_sources(run.context_json, run.entry_id).items() if sid in refs]
    from watchlist_app.services.market_evidence import hydrate_source
    return [hydrate_source(s) for s in linked.get("sources", [])]


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
    selectors = ("theme_id", "research_update_id", "event_case_id", "event_version_id", "notebook_version_id", "investment_view_version_id", "theme_version_id")
    reference_changed = any(context.get(key) != old_context.get(key) for key in selectors)
    source_reference_changed = any(context.get(key) != old_context.get(key) for key in selectors if key != "theme_id")
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
    theme = get_theme(session, instrument_id, theme_id, actor=actor) if theme_id else None
    bound_versions = {}
    from watchlist_app.services.research_dossier import read_dossier_version
    for field, expected in (("notebook_version_id", "notebook"), ("investment_view_version_id", "investment_view"), ("theme_version_id", "theme")):
        if context.get(field):
            version = read_dossier_version(session, instrument_id, context[field], actor=actor)
            if version["kind"] != expected:
                raise ValueError("研究背景引用的版本类型不一致")
            recorded_at = version.get("recorded_at") or version.get("information_cutoff")
            if record is not None and recorded_at and datetime.fromisoformat(recorded_at) > record.created_at.replace(tzinfo=record.created_at.tzinfo or UTC):
                raise ValueError("不能将后来形成的研究写成原观点的事前依据")
            if linked and linked["reference"].get(field) and linked["reference"][field] != context[field]:
                raise ValueError("研究更新与背景版本不一致")
            bound_versions[field] = version
    notebook_version = bound_versions.get("notebook_version_id")
    view_version = bound_versions.get("investment_view_version_id")
    theme_version = bound_versions.get("theme_version_id")
    if theme_version and theme_version["value"].get("theme_id") != theme_id:
        raise ValueError("主题版本与引用主题不一致")
    if theme_version and (notebook_version or view_version):
        raise ValueError("请使用主题自身的资料版本，不能同时替换为另一份研究背景")
    if notebook_version and view_version:
        notebook_view = notebook_version["value"].get("investment_view") or {}
        if view_version["version_id"] not in {row.get("version_id") for row in [notebook_view, *notebook_view.get("versions", [])]}:
            raise ValueError("投资判断版本不属于引用的研究背景版本")
    bound_version = theme_version or notebook_version or view_version
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
    if record is None or reference_changed:
        from watchlist_app.services.research_dossier import read_mandate, _notebooks
        from watchlist_app.services.research_themes import theme_record
        theme_snapshot = theme_version["value"] if theme_version else theme_record(theme) if theme else None
        notebook, _ = _notebooks(session, instrument_id, False)
        context["research_snapshot"] = {
            "notebook_version_id": context.get("notebook_version_id") or (linked or {}).get("reference", {}).get("notebook_version_id") or (notebook or {}).get("version_id", (notebook or {}).get("run_id")),
            "mandate_version_id": read_mandate(session, instrument_id).get("version_id"),
            "theme_revision": theme_snapshot.get("revision_number") if theme_snapshot else None,
        }
        if not str(context.get("background") or "").strip():
            if linked:
                context["background"] = f"{linked['title']}\n{linked['body']}\n研究记录时间：{linked['recorded_at']}"
            elif theme_snapshot:
                context["background"] = "\n".join(str(theme_snapshot.get(key) or "") for key in ("title", "question", "background", "synthesis")).strip()
            elif related_id:
                context["background"] = f"原观点：{previous.title}\n{previous.body or previous.summary}\n原记录时间：{previous.recorded_at.isoformat()}"
            else:
                raise ValueError("请填写形成投资观点时的背景，或关联具体主题、事件或研究记录")
        if record is None or (payload.research_context and "background" in payload.research_context.model_fields_set):
            context["background_origin"] = "user" if (payload.research_context and payload.research_context.background.strip()) else "research_snapshot"
        if linked:
            context["linked_research_snapshot"] = {key: deepcopy(linked.get(key)) for key in
                ("title", "body", "recorded_at", "reference", "details")}
        else:
            context.pop("linked_research_snapshot", None)
        if theme_snapshot:
            context["theme_snapshot"] = {key: deepcopy(theme_snapshot.get(key)) for key in
                ("theme_id", "title", "question", "background", "synthesis", "revision_number")}
        else:
            context.pop("theme_snapshot", None)
    values["author"] = record.author if record is not None else actor["display_name"]
    values["research_context"] = {**context, **({"author_role": "user", "recorded_via": "editor"}
        if record is None else {}), **(provenance or {})}
    explicit_sources = payload.research_context is not None and "source_ids" in payload.research_context.model_fields_set
    if (explicit_sources or source_reference_changed or (record is None and (linked or bound_version))) and "sources" not in (provenance or {}):
        from watchlist_app.services.research_dossier import read_dossier
        from watchlist_app.services.research_notebook import ResearchNotebook, research_sources, validate_notebook
        from watchlist_app.services.market_evidence import source_reference
        saved_context = values["research_context"]
        cutoff = ((bound_version or {}).get("information_cutoff") if source_reference_changed else saved_context.get("information_cutoff")) or (record.created_at.replace(tzinfo=record.created_at.tzinfo or UTC).isoformat() if record else datetime.now(UTC).isoformat())
        frozen = (bound_version or {}).get("sources") if bound_version else _linked_sources(session, instrument_id, linked, actor) if linked else None
        refs = saved_context.get("source_ids", []) if explicit_sources else [s["source_id"] for s in frozen or []]
        available = ({source["source_id"]: source for source in frozen} if frozen is not None else
            research_sources({"cutoff": cutoff, "research_dossiers": [read_dossier(session, instrument_id, actor=actor)]}, "pm-editor") if refs else {})
        # Editing prose must not rebind an unchanged citation to a later material
        # that happens to have the same catalogue ID.
        if record is not None and not source_reference_changed:
            available.update({source["source_id"]: source for source in note_sources(session, instrument_id, old_context)})
        validate_notebook(ResearchNotebook(source_ids=refs), instrument_id, available)
        saved_context.update(information_cutoff=cutoff, source_ids=refs,
            sources=[deepcopy(source_reference(available[sid])) for sid in dict.fromkeys(refs)])
    return values
