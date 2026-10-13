"""Research record validation and projections shared by HTTP and agent workflows."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from watchlist_app.db.models.research import (
    InstrumentResearchNote, InstrumentResearchNoteRevision,
    InstrumentResearchProfile, InstrumentResearchProfileRevision,
)
from watchlist_app.repositories.sqlalchemy.research import SQLAlchemyInstrumentResearchRepository
from watchlist_app.services.read_models import serialize_payload

research_repository = SQLAlchemyInstrumentResearchRepository()

PROFILE_FIELDS = (
    "research_stage",
    "thesis",
    "current_view",
    "why_now",
    "edge_assessment",
    "valuation_framework",
    "catalysts",
    "key_risks",
    "disconfirming_evidence",
    "open_questions",
    "monitoring_plan",
    "people_assessment",
    "portfolio_role",
    "time_horizon",
    "decision_rationale",
    "primary_analyst",
    "next_review_date",
    "dd_status",
    "odd_status",
    "ic_status",
    "manual_rating",
)


def serialize_profile(record: InstrumentResearchProfile | None) -> dict[str, object]:
    profile = {
        field: getattr(record, field) if record is not None else None
        for field in PROFILE_FIELDS
    }
    for field in PROFILE_FIELDS:
        if field not in {"next_review_date", "manual_rating"} and profile[field] is None:
            profile[field] = "watching" if field == "research_stage" else ""
    profile.update(
        {
            "created_at": record.created_at if record is not None else None,
            "updated_at": record.updated_at if record is not None else None,
            "updated_by": record.updated_by if record is not None else None,
            "revision_number": record.revision_number if record is not None else 0,
        }
    )
    return serialize_payload(profile)  # type: ignore[return-value]


def serialize_note(record: InstrumentResearchNote) -> dict[str, object]:
    return serialize_payload(
        {
            "note_id": record.note_id,
            "note_date": record.note_date,
            "note_type": record.note_type,
            "title": record.title,
            "summary": record.summary,
            "body": record.body,
            "importance": record.importance,
            "tags": record.tags_json,
            "source_refs": record.source_refs,
            "people": record.people,
            "author": record.author,
            "author_user_id": record.author_user_id,
            "team_id": record.team_id,
            "research_context": record.research_context or {},
            "follow_up_date": record.follow_up_date,
            "completed_at": record.completed_at,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "updated_by": record.updated_by,
            "revision_number": record.revision_number,
        }
    )  # type: ignore[return-value]


def serialize_profile_revision(
    record: InstrumentResearchProfileRevision,
) -> dict[str, object]:
    return serialize_payload(
        {
            **{field: getattr(record, field) for field in PROFILE_FIELDS},
            "revision_number": record.revision_number,
            "recorded_at": record.recorded_at,
            "recorded_by": record.recorded_by,
        }
    )  # type: ignore[return-value]


def serialize_note_revision(
    record: InstrumentResearchNoteRevision,
) -> dict[str, object]:
    return serialize_payload(
        {
            "note_id": record.note_id,
            "revision_number": record.revision_number,
            "change_type": record.change_type,
            "note_date": record.note_date,
            "note_type": record.note_type,
            "title": record.title,
            "summary": record.summary,
            "body": record.body,
            "importance": record.importance,
            "tags": record.tags_json,
            "source_refs": record.source_refs,
            "people": record.people,
            "author": record.author,
            "author_user_id": record.author_user_id,
            "team_id": record.team_id,
            "research_context": record.research_context or {},
            "follow_up_date": record.follow_up_date,
            "completed_at": record.completed_at,
            "recorded_at": record.recorded_at,
            "recorded_by": record.recorded_by,
        }
    )  # type: ignore[return-value]


class InstrumentResearchNoteContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme_id: str | None = None
    research_update_id: str | None = None
    event_case_id: str | None = None
    event_version_id: str | None = None
    notebook_version_id: str | None = None
    theme_version_id: str | None = None
    investment_view_version_id: str | None = None
    related_note_id: str | None = None
    related_revision: int | None = Field(default=None, ge=1)
    relationship: Literal["initial", "update", "review", "lesson"] = "initial"
    background: str = ""
    horizon: str = ""
    verification: str = ""
    invalidation: str = ""
    outcome: str = ""
    mechanism_assessment: str = ""
    lesson: str = ""
    applicability: str = ""
    limitations: str = ""
    alternative_explanations: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class InstrumentResearchNoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed_at: datetime | None = None
    note_date: date
    note_type: Literal[
        "research_update",
        "thesis_update",
        "evidence",
        "meeting",
        "event",
        "risk",
        "decision",
        "review",
    ] = "research_update"
    title: str = Field(min_length=1)
    summary: str = ""
    body: str = ""
    importance: Literal["low", "medium", "high"] = "medium"
    tags: list[str] = Field(default_factory=list)
    source_refs: str = ""
    people: str = ""
    author: str = ""
    follow_up_date: date | None = None
    research_context: InstrumentResearchNoteContextInput | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be empty")
        return normalized

