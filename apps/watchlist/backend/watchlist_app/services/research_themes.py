"""Team continuing themes reference canonical, individually authored PM views."""
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

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


def theme_record(entry):
    context = entry.context_json
    return serialize_payload({"theme_id": entry.entry_id, "instrument_id": context["instrument_id"],
        "title": entry.title, "question": entry.body, "background": context.get("background", ""),
        "status": context["theme_status"], "author_user_id": entry.author_user_id,
        "responsible_user_id": entry.responsible_user_id, "team_id": entry.team_id,
        "author": context["author"], "created_at": entry.created_at.replace(tzinfo=entry.created_at.tzinfo or UTC),
        "updated_at": entry.updated_at.replace(tzinfo=entry.updated_at.tzinfo or UTC),
        "revision_number": context.get("revision_number", 1), "versions": context.get("versions", []),
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
        if all(old[key] == item for key, item in value.model_dump().items()):
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
                          "recorded_via": "editor", "updated_by_user_id": actor["user_id"], **(provenance or {})}
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
    for theme in themes:
        theme["notes"] = [_serialize_note(note) for note in notes if (current_principal().local_unrestricted or note.team_id == actor["team_id"])
                          and (note.research_context or {}).get("theme_id") == theme["theme_id"]]
        theme["research_progress"] = research_progress(session, instrument_id, theme_id=theme["theme_id"], actor=actor)
    return {"identity": actor, "themes": themes}
