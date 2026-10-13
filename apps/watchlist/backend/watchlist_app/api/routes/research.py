from __future__ import annotations

from uuid import uuid4

from studio_identity import current_principal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import (
    InstrumentResearchNoteUpsertRequest,
    InstrumentResearchProfileUpsertRequest,
    InvestmentStanceSelectionInput,
)
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.services.research_records import (
    research_repository,
    serialize_profile_revision, serialize_note_revision,
)
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_identity import research_identity
from watchlist_app.services.research_views import prepare_note_values, read_current_stance, read_research_records, select_current_stance


router = APIRouter()
instrument_repository = SQLAlchemyInstrumentRepository()


def _ensure_instrument_exists(session: Session, instrument_id: str) -> None:
    if instrument_repository.get(session, instrument_id) is None:
        raise HTTPException(status_code=404, detail="Instrument not found")


@router.get("/{instrument_id}/research")
def get_instrument_research(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    return read_research_records(session, instrument_id)


@router.get("/{instrument_id}/research/history")
def get_instrument_research_history(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    from sqlalchemy import select
    from watchlist_app.db.models.research import InstrumentInvestmentStance
    selections = session.scalars(select(InstrumentInvestmentStance).where(
        InstrumentInvestmentStance.instrument_id == instrument_id,
        InstrumentInvestmentStance.team_id == research_identity()["team_id"],
    ).order_by(InstrumentInvestmentStance.selected_at.desc()))
    return {
        "stance_selections": serialize_payload([{key: getattr(row, key) for key in
            ("selection_id", "note_id", "note_revision", "selected_at", "selected_by", "selected_by_name")} for row in selections]),
        "profile_revisions": [
            serialize_profile_revision(record)
            for record in research_repository.list_profile_revisions(
                session,
                instrument_id,
            )
        ],
        "note_revisions": [
            serialize_note_revision(record)
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
    return read_research_records(session, instrument_id)


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
    return read_research_records(session, instrument_id)


@router.put("/{instrument_id}/research/current-stance")
def set_instrument_investment_stance(instrument_id: str, request: InvestmentStanceSelectionInput,
                                    session: Session = Depends(get_db_session)) -> dict:
    _ensure_instrument_exists(session, instrument_id)
    try:
        select_current_stance(session, instrument_id, request.note_id, request.revision_number)
    except (ValueError, LookupError) as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    return read_research_records(session, instrument_id)


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
    return read_research_records(session, instrument_id)


@router.delete("/{instrument_id}/research/notes/{note_id}")
def delete_instrument_research_note(
    instrument_id: str,
    note_id: str,
    deleted_by: str | None = None,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_instrument_exists(session, instrument_id)
    from sqlalchemy import select
    from watchlist_app.db.models.instruments import InstrumentDetail
    session.execute(select(InstrumentDetail.instrument_id).where(
        InstrumentDetail.instrument_id == instrument_id).with_for_update())
    record = research_repository.get_note(session, instrument_id, note_id, for_update=True)
    if record is None:
        raise HTTPException(status_code=404, detail="Research note not found")
    actor = research_identity()
    if not current_principal().local_unrestricted and (record.team_id != actor["team_id"] or (record.author_user_id != actor["user_id"] and actor["team_role"] != "admin")):
        raise HTTPException(403, "不能删除其他投资经理的观点")
    stance = read_current_stance(session, instrument_id, actor=actor)
    if stance and stance["note"]["note_id"] == note_id:
        select_current_stance(session, instrument_id, None, None, actor=actor)
    research_repository.delete_note(
        session,
        record,
        deleted_by=actor["user_id"],
    )
    from watchlist_app.services.risk_workbench import refresh_risk_cases
    refresh_risk_cases(session, [instrument_id])
    session.commit()
    return read_research_records(session, instrument_id)
