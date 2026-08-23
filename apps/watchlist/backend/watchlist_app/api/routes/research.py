from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import (
    InstrumentResearchNoteUpsertRequest,
    InstrumentResearchProfileUpsertRequest,
)
from watchlist_app.db.models.research import (
    InstrumentResearchNote,
    InstrumentResearchNoteRevision,
    InstrumentResearchProfile,
    InstrumentResearchProfileRevision,
)
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.research import SQLAlchemyInstrumentResearchRepository
from watchlist_app.services.read_models import serialize_payload


router = APIRouter()
instrument_repository = SQLAlchemyInstrumentRepository()
research_repository = SQLAlchemyInstrumentResearchRepository()

PROFILE_FIELDS = (
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


def _ensure_instrument_exists(session: Session, instrument_id: str) -> None:
    if instrument_repository.get(session, instrument_id) is None:
        raise HTTPException(status_code=404, detail="Instrument not found")


def _serialize_profile(record: InstrumentResearchProfile | None) -> dict[str, object]:
    profile = {
        field: getattr(record, field) if record is not None else None
        for field in PROFILE_FIELDS
    }
    for field in PROFILE_FIELDS:
        if field not in {"next_review_date", "manual_rating"} and profile[field] is None:
            profile[field] = ""
    profile.update(
        {
            "created_at": record.created_at if record is not None else None,
            "updated_at": record.updated_at if record is not None else None,
            "updated_by": record.updated_by if record is not None else None,
            "revision_number": record.revision_number if record is not None else 0,
        }
    )
    return serialize_payload(profile)  # type: ignore[return-value]


def _serialize_note(record: InstrumentResearchNote) -> dict[str, object]:
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
            "follow_up_date": record.follow_up_date,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "updated_by": record.updated_by,
            "revision_number": record.revision_number,
        }
    )  # type: ignore[return-value]


def _research_response(session: Session, instrument_id: str) -> dict[str, object]:
    return {
        "profile": _serialize_profile(research_repository.get_profile(session, instrument_id)),
        "notes": [
            _serialize_note(note)
            for note in research_repository.list_notes(session, instrument_id)
        ],
    }


def _serialize_profile_revision(
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


def _serialize_note_revision(
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
            "follow_up_date": record.follow_up_date,
            "recorded_at": record.recorded_at,
            "recorded_by": record.recorded_by,
        }
    )  # type: ignore[return-value]


@router.get("/{instrument_id}/research")
def get_instrument_research(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    return _research_response(session, instrument_id)


@router.get("/{instrument_id}/research/history")
def get_instrument_research_history(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    return {
        "profile_revisions": [
            _serialize_profile_revision(record)
            for record in research_repository.list_profile_revisions(
                session,
                instrument_id,
            )
        ],
        "note_revisions": [
            _serialize_note_revision(record)
            for record in research_repository.list_note_revisions(
                session,
                instrument_id,
            )
        ],
    }


@router.put("/{instrument_id}/research")
def upsert_instrument_research_profile(
    instrument_id: str,
    request: InstrumentResearchProfileUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    research_repository.upsert_profile(
        session,
        instrument_id=instrument_id,
        values=request.profile.model_dump(),
        updated_by=request.updated_by,
    )
    session.commit()
    return _research_response(session, instrument_id)


@router.post("/{instrument_id}/research/notes")
def create_instrument_research_note(
    instrument_id: str,
    request: InstrumentResearchNoteUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    research_repository.create_note(
        session,
        instrument_id=instrument_id,
        note_id=uuid4().hex,
        values=request.note.model_dump(),
        updated_by=request.updated_by,
    )
    session.commit()
    return _research_response(session, instrument_id)


@router.put("/{instrument_id}/research/notes/{note_id}")
def update_instrument_research_note(
    instrument_id: str,
    note_id: str,
    request: InstrumentResearchNoteUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = research_repository.get_note(session, instrument_id, note_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Research note not found")
    research_repository.update_note(
        session,
        record=record,
        values=request.note.model_dump(),
        updated_by=request.updated_by,
    )
    session.commit()
    return _research_response(session, instrument_id)


@router.delete("/{instrument_id}/research/notes/{note_id}")
def delete_instrument_research_note(
    instrument_id: str,
    note_id: str,
    deleted_by: str | None = None,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    record = research_repository.get_note(session, instrument_id, note_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Research note not found")
    research_repository.delete_note(
        session,
        record,
        deleted_by=deleted_by,
    )
    session.commit()
    return _research_response(session, instrument_id)
