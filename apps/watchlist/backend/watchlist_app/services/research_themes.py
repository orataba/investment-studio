"""Team continuing themes reference canonical, individually authored PM views."""
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from studio_identity import current_principal

from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_identity import research_identity


ACTIVE_THEME_LIMIT = 10
ThemeKind = Literal["fundamental", "event", "quantitative", "valuation", "risk", "other"]
ThemePriority = Literal["core", "important"]


class ThemeReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    research_update_id: str | None = None
    event_case_id: str | None = None
    event_version_id: str | None = None
    notebook_version_id: str | None = None
    investment_view_version_id: str | None = None
    theme_version_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class ThemeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    question: str = Field(default="", max_length=4000)
    background: str = Field(default="", max_length=6000)
    kind: ThemeKind = "fundamental"
    priority: ThemePriority = "important"
    priority_reason: str = Field(default="", max_length=2000)
    pinned: bool = False
    synthesis: str = Field(default="", max_length=6000)
    latest_development: str = Field(default="", max_length=3000)
    next_check: str = Field(default="", max_length=2000)
    figure_source_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    reference: ThemeReference | None = None
    status: str = Field(default="active", pattern="^(active|paused|closed)$")
    responsible_user_id: str | None = None

    @field_validator("title")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("请输入关注主题及研究问题")
        return value.strip()


class ThemePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=300)
    question: str | None = Field(default=None, max_length=4000)
    background: str | None = Field(default=None, max_length=6000)
    status: str | None = Field(default=None, pattern="^(active|paused|closed)$")
    responsible_user_id: str | None = None
    close_reason: str | None = Field(default=None, max_length=2000)
    kind: ThemeKind | None = None
    priority: ThemePriority | None = None
    priority_reason: str | None = Field(default=None, max_length=2000)
    pinned: bool | None = None
    synthesis: str | None = Field(default=None, max_length=6000)
    latest_development: str | None = Field(default=None, max_length=3000)
    next_check: str | None = Field(default=None, max_length=2000)
    figure_source_ids: list[str] | None = None
    source_ids: list[str] | None = None
    reference: ThemeReference | None = None


class AnalystThemeUpdate(BaseModel):
    """A sparse, reviewed update to a researcher-owned continuing question."""
    model_config = ConfigDict(extra="forbid")
    theme_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    theme_id: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    question: str | None = Field(default=None, min_length=1, max_length=4000)
    background: str | None = Field(default=None, max_length=6000)
    status: Literal["active", "paused", "closed"] | None = None
    close_reason: str | None = Field(default=None, max_length=2000)
    source_ids: list[str] = Field(default_factory=list)
    kind: ThemeKind | None = None
    priority: ThemePriority | None = None
    priority_reason: str | None = Field(default=None, max_length=2000)
    synthesis: str | None = Field(default=None, max_length=6000)
    latest_development: str | None = Field(default=None, max_length=3000)
    next_check: str | None = Field(default=None, max_length=2000)
    figure_source_ids: list[str] = Field(default_factory=list)


def theme_record(entry):
    context = entry.context_json
    return serialize_payload({"theme_id": entry.entry_id, "instrument_id": context["instrument_id"],
        "title": entry.title, "question": entry.body, "background": context.get("background", ""),
        "status": context["theme_status"], "author_user_id": entry.author_user_id,
        "responsible_user_id": entry.responsible_user_id, "team_id": entry.team_id,
        "author": context["author"], "created_at": entry.created_at.replace(tzinfo=entry.created_at.tzinfo or UTC),
        "updated_at": entry.updated_at.replace(tzinfo=entry.updated_at.tzinfo or UTC),
        "revision_number": context.get("revision_number", 1), "versions": context.get("versions", []),
        "origin": context.get("origin", "user"), "managed_by": context.get("managed_by", "user"),
        "theme_key": context.get("theme_key"), "close_reason": context.get("close_reason", ""),
        "source_ids": context.get("source_ids", []),
        "source_version_id": f"theme:{entry.entry_id}:{context.get('revision_number', 1)}",
        "updated_by": context.get("updated_by", context.get("author", "")),
        "updated_by_user_id": context.get("updated_by_user_id"),
        "updated_by_role": context.get("updated_by_role", "user"),
        "recorded_via": context.get("recorded_via"), "source_run_id": context.get("source_run_id"),
        "source_quote": context.get("source_quote", ""),
        **{key: context.get(key, default) for key, default in (
            ("kind", "fundamental"), ("priority", "important"), ("priority_reason", ""),
            ("pinned", False), ("synthesis", ""), ("latest_development", ""), ("next_check", ""),
            ("figure_source_ids", []), ("reference", None), ("last_reviewed_at", None),
            ("baseline_status", "pending"), ("baseline_requested_at", None), ("sources", []), ("migration_origin", None))}})


def get_theme(session, instrument_id, theme_id, *, actor=None):
    actor = actor or research_identity()
    entry = session.get(ResearchEntry, theme_id)
    context = (entry.context_json or {}) if entry else {}
    if (not entry or entry.topic_id != f"dossier:{instrument_id}"
            or context.get("role") != "research_theme" or context.get("instrument_id") != instrument_id
            or (not current_principal().local_unrestricted and entry.team_id != actor["team_id"])):
        raise LookupError("当前团队没有这个标的的关注主题")
    return entry


def theme_index(session, instrument_id, *, actor=None, active_only=False):
    actor = actor or research_identity()
    entries = session.scalars(select(ResearchEntry).where(ResearchEntry.topic_id == f"dossier:{instrument_id}")
                              .order_by(ResearchEntry.created_at.desc()))
    return [theme_record(entry) for entry in entries if (entry.context_json or {}).get("role") == "research_theme"
            and (current_principal().local_unrestricted or entry.team_id == actor["team_id"])
            and (not active_only or entry.context_json.get("theme_status") == "active")]


def _validate_capacity(session, instrument_id, value, *, theme_id=None, actor=None):
    if value.status == "active" and sum(row["status"] == "active" and row["theme_id"] != theme_id
            for row in theme_index(session, instrument_id, actor=actor)) >= ACTIVE_THEME_LIMIT:
        raise ValueError("最多同时跟踪10个重点主题；请先合并、暂停或关闭已有主题，再加入新主题。")


def _reference_version(session, instrument_id, reference, *, actor):
    from watchlist_app.services.research_dossier import read_dossier_version
    selected = [(identifier, kind) for identifier, kind in (
        (reference.notebook_version_id, "notebook"),
        (reference.investment_view_version_id, "investment_view"),
        (reference.theme_version_id, "theme")) if identifier]
    if not selected:
        return None
    if len(selected) > 1:
        raise ValueError("主题来源请选择一个明确的研究、投资判断或主题版本")
    identifier, kind = selected[0]
    version = read_dossier_version(session, instrument_id, identifier, actor=actor)
    if version["kind"] != kind:
        raise ValueError("主题引用的版本类型与所选来源不一致")
    return version


def _reference_context(session, instrument_id, value, *, actor):
    """Bind a selected event/data record without inventing an investment assessment."""
    from watchlist_app.services.research_activity import resolve_research_update, resolve_event_reference
    reference = value.reference
    if reference is None:
        return value
    version = _reference_version(session, instrument_id, reference, actor=actor)
    if version and version["kind"] == "theme" and not value.background:
        saved = version["value"]
        value.background = "\n".join(str(saved.get(key) or "") for key in ("title", "question", "synthesis")).strip()
    linked = None
    if reference.research_update_id:
        linked = resolve_research_update(session, instrument_id, reference.research_update_id, actor=actor)
    if reference.event_case_id or reference.event_version_id:
        event = resolve_event_reference(session, instrument_id, reference.event_case_id, reference.event_version_id)
        if linked and linked["reference"].get("event_version_id") != event["reference"]["event_version_id"]:
            raise ValueError("主题引用的事件与研究更新版本不一致")
        linked = linked or event
    if linked:
        value.background = value.background or linked["body"]
        value.source_ids = list(dict.fromkeys([*value.source_ids, *[row["source_id"] for row in linked.get("sources", [])]]))
    value.source_ids = list(dict.fromkeys([*value.source_ids, *reference.source_ids]))
    return value


def _retained_theme_sources(session, instrument_id, source_ids, *, actor, reference=None, previous=None):
    if not source_ids:
        return []
    from watchlist_app.services.research_dossier import read_dossier
    from watchlist_app.services.research_notebook import research_sources, validate_notebook, ResearchNotebook
    from watchlist_app.services.market_evidence import source_reference, hydrate_source
    sources = research_sources({"cutoff": datetime.now(UTC).isoformat(),
        "research_dossiers": [read_dossier(session, instrument_id, actor=actor)]}, "theme-editor")
    # Unchanged references keep the exact original, not a later material using
    # the same logical source ID. Event/update summaries are not full evidence.
    sources.update({row["source_id"]: row for row in previous or []})
    previous_ids = {row["source_id"] for row in previous or []}
    version = _reference_version(session, instrument_id, reference, actor=actor) if reference else None
    if version:
        selected_ids = set(reference.source_ids) | (set(source_ids) - previous_ids)
        sources.update({row["source_id"]: row for row in version.get("sources", []) if row["source_id"] in selected_ids})
        if any(sid not in {row["source_id"] for row in version.get("sources", [])} for sid in selected_ids):
            raise ValueError("主题引用的原始依据不属于所选研究版本")
    if reference and reference.research_update_id:
        from watchlist_app.services.research_activity import resolve_research_update
        from watchlist_app.services.research_dossier import read_dossier_version
        update = resolve_research_update(session, instrument_id, reference.research_update_id, actor=actor)
        reference = ThemeReference.model_validate({**reference.model_dump(), **{
            key: value for key, value in update["reference"].items() if key in {"event_case_id", "event_version_id"}}})
        if update["reference"].get("notebook_version_id"):
            version = read_dossier_version(session, instrument_id, update["reference"]["notebook_version_id"], actor=actor)
            sources.update({row["source_id"]: row for row in version.get("sources", [])})
    if reference and reference.event_case_id:
        from watchlist_app.db.models.workbench import RiskCase
        case = session.get(RiskCase, reference.event_case_id)
        if case is None or case.instrument_id != instrument_id:
            raise ValueError("主题引用的事件不属于当前标的")
        from watchlist_app.services.sector_research import event_version_id
        snapshots = [{**case.evidence_json, "event_version_id": case.evidence_json.get("event_version_id") or event_version_id(case.case_id, max(1, len(case.history_json or [])))},
            *[{**row.get("snapshot", {}), "event_version_id": row.get("snapshot", {}).get("event_version_id") or event_version_id(case.case_id, index)}
              for index, row in enumerate(case.history_json or [], 1) if row.get("snapshot")]]
        snapshot = next((row for row in snapshots if row.get("event_version_id") == reference.event_version_id), None)
        if snapshot is None:
            raise ValueError("找不到主题所引用事件的原始证据版本")
        sources.update({row["source_id"]: row for row in snapshot.get("sources", [])})
    sources = {sid: hydrate_source(source) for sid, source in sources.items()}
    validate_notebook(ResearchNotebook(source_ids=source_ids), instrument_id, sources)
    return [source_reference(sources[sid]) for sid in dict.fromkeys(source_ids)]


def _attach_event(session, instrument_id, theme_id, value, *, actor, timestamp):
    reference = value.reference
    if reference is None:
        return
    if reference.research_update_id and not reference.event_case_id:
        from watchlist_app.services.research_activity import resolve_research_update
        update = resolve_research_update(session, instrument_id, reference.research_update_id, actor=actor)
        reference = ThemeReference.model_validate({**reference.model_dump(), **{
            key: item for key, item in update["reference"].items() if key in {"event_case_id", "event_version_id"}}})
    if not reference.event_case_id:
        return
    from watchlist_app.db.models.workbench import RiskCase
    from watchlist_app.services.sector_research import event_record, event_version_id
    case = session.get(RiskCase, reference.event_case_id, with_for_update=True)
    current = event_record(case) if case and case.instrument_id == instrument_id else None
    if current is None:
        raise ValueError("事件不属于当前标的")
    if theme_id in current["theme_ids"]:
        return
    history = list(case.history_json or [])
    if not any(row.get("snapshot") for row in history):
        history.append({"at": current["recorded_at"], "action": "retained", "snapshot": {
            **case.evidence_json, "title": case.title, "body": case.body, "status": case.status,
            "trigger_active": case.trigger_active, "event_version_id": current["event_version_id"], "recorded_at": current["recorded_at"]}})
    snapshot = {**case.evidence_json, "theme_ids": [*current["theme_ids"], theme_id], "follow_up": "watch",
        "next_watch": value.next_check or current["next_watch"] or "建立主题研究基线，明确下一次观察条件。",
        "event_version_id": event_version_id(case.case_id, len(history) + 1), "recorded_at": timestamp.isoformat(),
        "recorded_by": actor["display_name"], "recorded_by_role": "user"}
    case.status = "open"
    case.resolved_at = None
    case.trigger_active = current["direction"] in {"risk", "uncertain"}
    case.evidence_json = snapshot
    case.history_json = [*history, {"at": timestamp.isoformat(), "action": "organized", "detail": "投资经理将该事项纳入重点主题，原事实与判断保留。",
        "snapshot": {**snapshot, "title": case.title, "body": case.body, "status": case.status, "trigger_active": case.trigger_active}}]


def save_theme(session, instrument_id, payload, *, theme_id=None, actor=None, provenance=None):
    from watchlist_app.services.research_dossier import dossier_topic
    actor = actor or research_identity()
    from watchlist_app.services.research_access import require_team_write
    principal = require_team_write()
    topic = dossier_topic(session, instrument_id)
    timestamp = datetime.now(UTC)
    if theme_id:
        entry = get_theme(session, instrument_id, theme_id, actor=actor)
        session.refresh(entry, with_for_update=True)
        old = theme_record(entry)
        updates = payload.model_dump(exclude_unset=True)
        close_reason = updates.pop("close_reason", old.get("close_reason", ""))
        if close_reason is None:
            raise ValueError("结束原因不能设为null；清空说明请使用空字符串")
        value = ThemeInput.model_validate({**{key: old.get(key) for key in ThemeInput.model_fields}, **updates})
        if "reference" in payload.model_fields_set:
            value = _reference_context(session, instrument_id, value, actor=actor)
        close_reason = close_reason.strip() if value.status == "closed" else ""
        if (all(old[key] == item for key, item in value.model_dump().items())
                and old.get("close_reason", "") == close_reason):
            return old
        context = deepcopy(entry.context_json)
        context["versions"] = [*context.get("versions", []), {key: item for key, item in old.items() if key != "versions"}]
        context["revision_number"] = context.get("revision_number", 1) + 1
    else:
        value = ThemeInput.model_validate(payload.model_dump())
        value = _reference_context(session, instrument_id, value, actor=actor)
        close_reason = ""
        entry = ResearchEntry(entry_id=uuid4().hex, topic_id=topic.topic_id, kind="note", status="recorded", created_at=timestamp,
                              team_id=actor["team_id"], author_user_id=actor["user_id"], responsible_user_id=actor["user_id"])
        session.add(entry)
        context = {"role": "research_theme", "instrument_id": instrument_id,
                   "author": actor["display_name"], "theme_key": f"theme-{entry.entry_id}", "revision_number": 1, "versions": []}
    baseline_requested = theme_id is None or any(key in payload.model_fields_set and old.get(key) != value.model_dump().get(key)
        for key in ("title", "question", "background", "reference"))
    _validate_capacity(session, instrument_id, value, theme_id=theme_id, actor=actor)
    if "responsible_user_id" in payload.model_fields_set:
        if value.responsible_user_id and value.responsible_user_id != principal.user_id:
            from studio_identity import team_directory
            if not any(member["user_id"] == value.responsible_user_id and member["active"] for member in team_directory(principal)):
                raise ValueError("负责人须为当前团队的有效成员")
        entry.responsible_user_id = value.responsible_user_id
    entry.title, entry.body = value.title, value.question
    saved_sources = _retained_theme_sources(session, instrument_id,
        list(dict.fromkeys([*value.source_ids, *value.figure_source_ids])), actor=actor,
        reference=value.reference, previous=context.get("sources", [])) if value.source_ids or value.figure_source_ids else []
    if any(row["source_id"] in value.figure_source_ids and row.get("source_type") not in {"computed_metric", "sector_snapshot", "analyst_estimate_changes"}
            for row in saved_sources):
        raise ValueError("主题图表必须绑定真实留存的数值来源")
    entry.context_json = {**context, **value.model_dump(exclude={"title", "question", "status", "responsible_user_id"}, mode="json"),
                          "theme_status": value.status, "close_reason": close_reason, "sources": saved_sources,
                          "baseline_status": "pending" if baseline_requested else context.get("baseline_status", "pending"),
                          "baseline_requested_at": timestamp.isoformat() if baseline_requested else context.get("baseline_requested_at"),
                          "origin": context.get("origin", "user"), "managed_by": "user" if value.pinned else "researcher",
                          "recorded_via": "editor", "updated_by_user_id": actor["user_id"],
                          "updated_by": actor["display_name"], "updated_by_role": "user",
                          "source_run_id": None, "source_quote": "", **(provenance or {})}
    entry.updated_at = topic.updated_at = timestamp
    if value.reference:
        _attach_event(session, instrument_id, entry.entry_id, value, actor=actor, timestamp=timestamp)
    session.flush()
    return theme_record(entry)


def analyst_theme_target(session, instrument_id, update, *, actor=None):
    """Resolve stable keys inside one instrument/team; never infer a cross-scope match."""
    actor = actor or research_identity()
    themes = [item for item in theme_index(session, instrument_id, actor=actor)
              if item["team_id"] == actor["team_id"]]
    matches = [item for item in themes if item.get("theme_key") == update.theme_key]
    if len(matches) > 1:
        raise ValueError("关注主题标识重复，请先明确需要更新的主题")
    if update.theme_id:
        record = theme_record(get_theme(session, instrument_id, update.theme_id, actor=actor))
        if record["team_id"] != actor["team_id"]:
            raise ValueError("研究主题不属于本轮团队")
        if record.get("theme_key") not in {None, update.theme_key} or (matches and matches[0]["theme_id"] != record["theme_id"]):
            raise ValueError("关注主题标识与已有主题不一致")
        return record
    return matches[0] if matches else None


def analyst_theme_values(update, previous=None):
    previous = previous or {}
    protected = {"title", "question", "background", "kind", "priority", "priority_reason", "status", "close_reason"}
    if previous.get("pinned") and any(key in update.model_fields_set and getattr(update, key) != previous.get(key)
                                      for key in protected):
        raise ValueError("已固定主题的核心问题、优先级和生命周期由投资经理维护；研究员仍可更新研究结论与进展")
    values = {key: previous.get(key, default) for key, default in
              (("title", ""), ("question", ""), ("background", ""), ("status", "active"), ("close_reason", ""), ("source_ids", []),
               ("kind", "fundamental"), ("priority", "important"), ("priority_reason", ""), ("synthesis", ""),
               ("latest_development", ""), ("next_check", ""), ("figure_source_ids", []))}
    values.update(update.model_dump(exclude_unset=True, exclude={"theme_id", "theme_key"}))
    if any(values[key] is None for key in ("title", "question", "background", "status", "close_reason")):
        raise ValueError("主题内容不能设为null；清空可选说明请使用空字符串")
    validated = ThemeInput.model_validate({key: value for key, value in values.items() if key != "close_reason"})
    values.update(title=validated.title, question=validated.question)
    if not previous and not values["question"].strip():
        raise ValueError("研究员新建主题须明确要验证的研究问题")
    if not previous and not values["priority_reason"].strip():
        raise ValueError("研究员新建重点主题须说明它为什么值得占用持续跟踪名额")
    if values["status"] == "closed" and not values["close_reason"].strip():
        raise ValueError("结束研究员主题需要说明结论或不再跟进的原因")
    if "status" in update.model_fields_set and values["status"] != "closed" and "close_reason" not in update.model_fields_set:
        values["close_reason"] = ""
    return values


def save_analyst_theme(session, instrument_id, update, *, actor=None, provenance=None, sources=None):
    """Publish inside the caller's locked research transaction; no separate commit."""
    from watchlist_app.services.research_dossier import dossier_topic
    from watchlist_app.services.research_access import require_team_write
    require_team_write()
    actor = actor or research_identity()
    previous = analyst_theme_target(session, instrument_id, update, actor=actor)
    values = analyst_theme_values(update, previous)
    timestamp = datetime.now(UTC)
    topic = dossier_topic(session, instrument_id)
    _validate_capacity(session, instrument_id, ThemeInput.model_validate({key: value for key, value in values.items()
        if key != "close_reason"}), theme_id=previous["theme_id"] if previous else None, actor=actor)
    if previous:
        entry = get_theme(session, instrument_id, previous["theme_id"], actor=actor)
        session.refresh(entry, with_for_update=True)
        previous = theme_record(entry)
        values = analyst_theme_values(update, previous)
        if all(previous.get(key) == value for key, value in values.items()):
            entry.context_json = {**entry.context_json, "last_reviewed_at": timestamp.isoformat(),
                                  "baseline_status": "ready" if previous.get("synthesis") else "pending"}
            # TimestampMixin has an SQL onupdate; a receipt must explicitly keep
            # the judgment clock rather than let that default claim new progress.
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(entry, "updated_at")
            session.flush()
            return theme_record(entry)
        context = deepcopy(entry.context_json)
        context["versions"] = [*context.get("versions", []), {key: value for key, value in previous.items() if key != "versions"}]
        context["revision_number"] = context.get("revision_number", 1) + 1
    else:
        entry = ResearchEntry(entry_id=uuid4().hex, topic_id=topic.topic_id, kind="note", status="recorded", created_at=timestamp,
                              team_id=actor["team_id"], author_user_id=None, responsible_user_id=None)
        session.add(entry)
        context = {"role": "research_theme", "instrument_id": instrument_id, "author": "研究员",
                   "origin": "researcher", "managed_by": "researcher", "theme_key": update.theme_key,
                   "revision_number": 1, "versions": []}
    entry.title, entry.body = values.pop("title").strip(), values.pop("question").strip()
    status = values.pop("status")
    entry.context_json = {**context, **values, "theme_status": status,
                          "pinned": context.get("pinned", False),
                          "last_reviewed_at": timestamp.isoformat(),
                          "baseline_status": "ready" if values.get("synthesis") else "pending",
                          "recorded_via": "research", "updated_by": "研究员", "updated_by_role": "researcher",
                          "updated_by_user_id": None, **(provenance or {})}
    if sources is not None:
        from watchlist_app.services.market_evidence import source_reference
        refs = list(dict.fromkeys([*values.get("source_ids", []), *values.get("figure_source_ids", [])]))
        entry.context_json = {**entry.context_json, "sources": [source_reference(sources[sid]) for sid in refs]}
    entry.updated_at = topic.updated_at = timestamp
    session.flush()
    return theme_record(entry)


def research_progress(session, instrument_id, *, theme_id=None, note_id=None, actor=None):
    from watchlist_app.services.research_dossier import _research_records
    actor = actor or research_identity()
    progress, last = [], {}
    # A theme's research assessment is already stored in the analyst notebook.
    # Project its material revisions; do not copy them into the PM's own view.
    for run, notebook in reversed(list(_research_records(session, instrument_id))):
        if not current_principal().local_unrestricted and run.team_id != actor["team_id"]:
            continue
        for question in notebook.get("questions", []):
            if theme_id and question.get("theme_id") != theme_id:
                continue
            if note_id and question.get("pm_note_id") != note_id:
                continue
            if not theme_id and not note_id:
                continue
            value = {key: question.get(key) for key in ("key", "assessment", "next_check", "status", "source_ids",
                "evidence_for", "evidence_against", "pm_note_id", "pm_note_revision")}
            if value == last.get(question["key"]):
                continue
            last[question["key"]] = value
            progress.append({**value, "run_id": run.entry_id, "recorded_at": run.context_json.get("cutoff"),
                             "author_role": "researcher"})
    return progress


def themes_view(session, instrument_id, *, actor=None):
    from watchlist_app.api.routes.research import _serialize_note
    from watchlist_app.repositories.sqlalchemy.research import SQLAlchemyInstrumentResearchRepository
    actor = actor or research_identity()
    notes = SQLAlchemyInstrumentResearchRepository().list_notes(session, instrument_id)
    themes = theme_index(session, instrument_id, actor=actor)
    from watchlist_app.services.research_activity import research_activity, review_receipts, judgment_changed_at, judgment_review_receipt
    updates = research_activity(session, instrument_id, actor=actor)["updates"]
    receipts = review_receipts(session, instrument_id) if themes else {}
    for theme in themes:
        theme["notes"] = [_serialize_note(note) for note in notes if (current_principal().local_unrestricted or note.team_id == actor["team_id"])
                          and (note.research_context or {}).get("theme_id") == theme["theme_id"]]
        theme["research_progress"] = research_progress(session, instrument_id, theme_id=theme["theme_id"], actor=actor)
        theme["updates"] = [row for row in updates if theme["theme_id"] in row["theme_ids"]]
        current = [row for row in theme["updates"] if row["kind"] == "question"
                        and row["reference"].get("theme_id") == theme["theme_id"]
                        and not row.get("superseded") and not row.get("withdrawn")
                        and (row.get("tracking_status") or "active") == "active" and theme["status"] == "active"]
        progress = [row for row in theme["updates"] if row["kind"] != "theme"
                    and not row.get("superseded") and not row.get("withdrawn")]
        theme["last_changed_at"] = max((judgment_changed_at(row) for row in progress), default=None)
        theme["current_questions"] = [{**row, "tracking_status": "active", "last_changed_at": judgment_changed_at(row),
            "last_reviewed_at": None, **judgment_review_receipt(row, receipts)} for row in current]
        if (theme.get("reference") or {}).get("research_update_id"):
            reference_id = theme["reference"]["research_update_id"]
            original = next((row for row in updates if row["update_id"] == reference_id), None)
            if original and original["update_id"] not in {row["update_id"] for row in theme["updates"]}:
                theme["updates"].append(original)
                theme["updates"].sort(key=lambda row: row["recorded_at"], reverse=True)
    themes.sort(key=lambda row: (row["status"] != "active", row["priority"] != "core",
        row["title"], row["theme_id"]))
    return {"identity": actor, "themes": themes, "active_limit": ACTIVE_THEME_LIMIT, "target_count": 5}
