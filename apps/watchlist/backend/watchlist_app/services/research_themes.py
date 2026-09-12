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


class ThemeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=1, max_length=4000)
    background: str = Field(default="", max_length=6000)
    status: str = Field(default="active", pattern="^(active|paused|closed)$")
    responsible_user_id: str | None = None

    @field_validator("title", "question")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("请输入关注主题及研究问题")
        return value.strip()


class ThemePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=300)
    question: str | None = Field(default=None, min_length=1, max_length=4000)
    background: str | None = Field(default=None, max_length=6000)
    status: str | None = Field(default=None, pattern="^(active|paused|closed)$")
    responsible_user_id: str | None = None


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
        "updated_by": context.get("updated_by", context.get("author", "")),
        "updated_by_user_id": context.get("updated_by_user_id"),
        "updated_by_role": context.get("updated_by_role", "user"),
        "recorded_via": context.get("recorded_via"), "source_run_id": context.get("source_run_id"),
        "source_quote": context.get("source_quote", "")})


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
        value = ThemeInput.model_validate({**{key: old[key] for key in ThemeInput.model_fields}, **updates})
        if all(old[key] == item for key, item in value.model_dump().items()) and old.get("managed_by", "user") == "user":
            return old
        context = deepcopy(entry.context_json)
        context["versions"] = [*context.get("versions", []), {key: item for key, item in old.items() if key != "versions"}]
        context["revision_number"] = context.get("revision_number", 1) + 1
    else:
        value = ThemeInput.model_validate(payload.model_dump())
        entry = ResearchEntry(entry_id=uuid4().hex, topic_id=topic.topic_id, kind="note", status="recorded", created_at=timestamp,
                              team_id=actor["team_id"], author_user_id=actor["user_id"], responsible_user_id=actor["user_id"])
        session.add(entry)
        context = {"role": "research_theme", "instrument_id": instrument_id,
                   "author": actor["display_name"], "revision_number": 1, "versions": []}
    if "responsible_user_id" in payload.model_fields_set:
        if value.responsible_user_id:
            from studio_identity import team_directory
            if not any(member["user_id"] == value.responsible_user_id and member["active"] for member in team_directory(principal)):
                raise ValueError("负责人须为当前团队的有效成员")
        entry.responsible_user_id = value.responsible_user_id
    entry.title, entry.body = value.title, value.question
    entry.context_json = {**context, "background": value.background, "theme_status": value.status,
                          "origin": context.get("origin", "user"), "managed_by": "user",
                          "recorded_via": "editor", "updated_by_user_id": actor["user_id"],
                          "updated_by": actor["display_name"], "updated_by_role": "user",
                          "source_run_id": None, "source_quote": "", **(provenance or {})}
    entry.updated_at = topic.updated_at = timestamp
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
    if previous and previous.get("managed_by", "user") != "researcher":
        raise ValueError("人工维护的关注主题不能由自动研究改写；研究判断请记录为主题进展")
    values = {key: previous.get(key, default) for key, default in
              (("title", ""), ("question", ""), ("background", ""), ("status", "active"), ("close_reason", ""), ("source_ids", []))}
    values.update(update.model_dump(exclude_unset=True, exclude={"theme_id", "theme_key"}))
    if any(values[key] is None for key in ("title", "question", "background", "status", "close_reason")):
        raise ValueError("主题内容不能设为null；清空可选说明请使用空字符串")
    validated = ThemeInput.model_validate({key: values[key] for key in ("title", "question", "background", "status")})
    values.update(title=validated.title, question=validated.question)
    if values["status"] == "closed" and not values["close_reason"].strip():
        raise ValueError("结束研究员主题需要说明结论或不再跟进的原因")
    if "status" in update.model_fields_set and values["status"] != "closed" and "close_reason" not in update.model_fields_set:
        values["close_reason"] = ""
    return values


def save_analyst_theme(session, instrument_id, update, *, actor=None, provenance=None):
    """Publish inside the caller's locked research transaction; no separate commit."""
    from watchlist_app.services.research_dossier import dossier_topic
    from watchlist_app.services.research_access import require_team_write
    require_team_write()
    actor = actor or research_identity()
    previous = analyst_theme_target(session, instrument_id, update, actor=actor)
    values = analyst_theme_values(update, previous)
    timestamp = datetime.now(UTC)
    topic = dossier_topic(session, instrument_id)
    if previous:
        entry = get_theme(session, instrument_id, previous["theme_id"], actor=actor)
        session.refresh(entry, with_for_update=True)
        previous = theme_record(entry)
        values = analyst_theme_values(update, previous)
        if all(previous.get(key) == value for key, value in values.items()):
            return previous
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
                          "recorded_via": "research", "updated_by": "研究员", "updated_by_role": "researcher",
                          "updated_by_user_id": None, **(provenance or {})}
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
    from watchlist_app.services.research_activity import research_activity
    updates = research_activity(session, instrument_id, actor=actor)["updates"]
    for theme in themes:
        theme["notes"] = [_serialize_note(note) for note in notes if (current_principal().local_unrestricted or note.team_id == actor["team_id"])
                          and (note.research_context or {}).get("theme_id") == theme["theme_id"]]
        theme["research_progress"] = research_progress(session, instrument_id, theme_id=theme["theme_id"], actor=actor)
        theme["updates"] = [row for row in updates if theme["theme_id"] in row["theme_ids"]]
        current = next((row for row in theme["updates"] if row["kind"] == "question"
                        and row["reference"].get("theme_id") == theme["theme_id"]
                        and not row.get("superseded")), None)
        theme["current_assessment"] = ({"assessment": current.get("body", ""),
            "next_check": current.get("next_check", ""), "status": current.get("status"),
            "updated_at": current.get("recorded_at"),
            "source_ids": [source["source_id"] for source in current.get("sources", [])]} if current else None)
    return {"identity": actor, "themes": themes}
