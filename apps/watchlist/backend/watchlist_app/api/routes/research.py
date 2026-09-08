from __future__ import annotations

from uuid import uuid4

from studio_identity import current_principal

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
from watchlist_app.services.research_identity import research_identity
from watchlist_app.services.research_views import prepare_note_values


router = APIRouter()
instrument_repository = SQLAlchemyInstrumentRepository()
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


def _research_response(session: Session, instrument_id: str) -> dict[str, object]:
    return {
        "profile": _serialize_profile(research_repository.get_profile(session, instrument_id)),
        "notes": [
            _serialize_note(note)
            for note in research_repository.list_notes(session, instrument_id)
            if current_principal().local_unrestricted or note.team_id == research_identity()['team_id']
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
            "author_user_id": record.author_user_id,
            "team_id": record.team_id,
            "research_context": record.research_context or {},
            "follow_up_date": record.follow_up_date,
            "completed_at": record.completed_at,
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
            if current_principal().local_unrestricted or record.team_id == research_identity()['team_id']
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
        updated_by=research_identity()["user_id"],
    )
    from watchlist_app.services.risk_workbench import refresh_risk_cases
    refresh_risk_cases(session, [instrument_id])
    session.commit()
    return _research_response(session, instrument_id)


@router.post("/{instrument_id}/research/notes")
def create_instrument_research_note(
    instrument_id: str,
    request: InstrumentResearchNoteUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    actor = research_identity()
    try:
        provenance = None
        if request.source_entry_id:
            from watchlist_app.db.models.workbench import ResearchEntry
            from watchlist_app.services.research_identity import run_identity
            source = session.get(ResearchEntry, request.source_entry_id)
            if (source is None or not source.context_json.get("research_run")
                    or source.context_json.get("sector_run") or source.context_json.get("risk_run")
                    or source.status != "draft" or instrument_id not in source.context_json.get("instrument_ids", [])
                    or (not current_principal().local_unrestricted and run_identity(source.context_json)["user_id"] != actor["user_id"])):
                raise ValueError("请选择当前标的已完成的助手对话")
            from watchlist_app.services.research_access import require_entry_access
            require_entry_access(session, source)
            from watchlist_app.services.research_access import require_team_publication_scope
            require_team_publication_scope(session, source)
            bound = next((item for item in source.context_json.get("research_dossiers", [])
                          if item["instrument_id"] == instrument_id), {})
            notebook = bound.get("notebook") or {}
            theme_id = request.note.research_context.theme_id if request.note.research_context else None
            theme = next((item for item in bound.get("themes", []) if item["theme_id"] == theme_id), {})
            provenance = {"recorded_via": "assistant_adopted", "source_run_id": source.entry_id,
                          "source_quote": source.title, "information_cutoff": source.context_json.get("cutoff"),
                          "research_snapshot": {"notebook_version_id": notebook.get("version_id", notebook.get("run_id")),
                              "mandate_version_id": (bound.get("mandate") or {}).get("version_id"),
                              "theme_revision": theme.get("revision_number")}}
        values = prepare_note_values(session, instrument_id, request.note, actor=actor, provenance=provenance)
    except (ValueError, LookupError) as error:
        raise HTTPException(422, str(error)) from error
    research_repository.create_note(
        session,
        instrument_id=instrument_id,
        note_id=uuid4().hex,
        values=values,
        author_user_id=actor["user_id"],
        team_id=actor["team_id"],
        updated_by=actor["user_id"],
    )
    from watchlist_app.services.risk_workbench import refresh_risk_cases
    refresh_risk_cases(session, [instrument_id])
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
    record = research_repository.get_note(session, instrument_id, note_id, for_update=True)
    if record is None:
        raise HTTPException(status_code=404, detail="Research note not found")
    actor = research_identity()
    try:
        values = prepare_note_values(session, instrument_id, request.note, actor=actor, record=record)
    except (ValueError, LookupError) as error:
        raise HTTPException(422, str(error)) from error
    research_repository.update_note(
        session,
        record=record,
        values=values,
        updated_by=actor["user_id"],
    )
    from watchlist_app.services.risk_workbench import refresh_risk_cases
    refresh_risk_cases(session, [instrument_id])
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
    record = research_repository.get_note(session, instrument_id, note_id, for_update=True)
    if record is None:
        raise HTTPException(status_code=404, detail="Research note not found")
    actor = research_identity()
    if not current_principal().local_unrestricted and (record.team_id != actor["team_id"] or (record.author_user_id != actor["user_id"] and actor["team_role"] != "admin")):
        raise HTTPException(403, "不能删除其他投资经理的观点")
    research_repository.delete_note(
        session,
        record,
        deleted_by=actor["user_id"],
    )
    from watchlist_app.services.risk_workbench import refresh_risk_cases
    refresh_risk_cases(session, [instrument_id])
    session.commit()
    return _research_response(session, instrument_id)
