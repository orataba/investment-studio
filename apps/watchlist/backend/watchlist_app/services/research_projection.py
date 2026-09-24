from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session
from watchlist_app.db.models.workbench import RiskCase

from watchlist_app.db.models.research import InstrumentResearchNote, InstrumentResearchNoteRevision, InstrumentInvestmentStance
from watchlist_app.repositories.sqlalchemy.research import (
    SQLAlchemyInstrumentResearchRepository,
)


RESEARCH_WATCHLIST_FIELD_KEYS = {
    "attr.research_current_view",
    "attr.research_stage",
    "attr.risk_attention",
    "attr.manual_rating",
    "attr.primary_analyst",
    "attr.research_next_review_date",
    "attr.research_note_count",
    "attr.research_next_follow_up_date",
    "attr.research_updated_at",
}


def build_risk_watchlist_attribute_overrides(
    session: Session,
    *,
    instrument_ids: list[str],
) -> dict[str, dict[str, object]]:
    normalized_ids = list(dict.fromkeys(value for value in instrument_ids if value))
    if not normalized_ids:
        return {}
    overrides = {
        instrument_id: {"risk_attention": "no_trigger"}
        for instrument_id in normalized_ids
    }
    for case in session.scalars(select(RiskCase).where(RiskCase.instrument_id.in_(normalized_ids), RiskCase.trigger_active.is_(True))):
        if case.status in {"handled", "resolved"} or ((case.evidence_json or {}).get("direction") == "opportunity"
                and not (case.evidence_json or {}).get("risk_assessment")):
            continue
        values = overrides[case.instrument_id]
        if case.severity == "attention":
            values["risk_attention"] = "attention"
        elif case.severity == "coverage" and values["risk_attention"] != "attention":
            values["risk_attention"] = "limited"
    return overrides


def build_research_watchlist_attribute_overrides(
    session: Session,
    *,
    instrument_ids: list[str],
) -> dict[str, dict[str, object]]:
    normalized_ids = list(dict.fromkeys(value for value in instrument_ids if value))
    if not normalized_ids:
        return {}

    repository = SQLAlchemyInstrumentResearchRepository()
    overrides = {
        instrument_id: {"research_note_count": 0, "research_stage": "watching", "research_current_view": None}
        for instrument_id in normalized_ids
    }
    for profile in repository.list_profiles(session, normalized_ids):
        values = overrides[profile.instrument_id]
        values["research_stage"] = profile.research_stage
        if profile.manual_rating is not None:
            values["manual_rating"] = profile.manual_rating
        if profile.primary_analyst:
            values["primary_analyst"] = profile.primary_analyst
        if profile.next_review_date is not None:
            values["research_next_review_date"] = profile.next_review_date
        values["research_updated_at"] = profile.updated_at

    from watchlist_app.services.research_identity import research_identity
    selections = select(InstrumentInvestmentStance.instrument_id, InstrumentInvestmentStance.note_id,
        InstrumentInvestmentStance.note_revision, func.row_number().over(
            partition_by=InstrumentInvestmentStance.instrument_id,
            order_by=(InstrumentInvestmentStance.selected_at.desc(), InstrumentInvestmentStance.selection_id.desc())).label("rank"),
        ).where(InstrumentInvestmentStance.instrument_id.in_(normalized_ids),
                InstrumentInvestmentStance.team_id == research_identity()["team_id"]).subquery()
    selected_notes = select(InstrumentResearchNoteRevision).join(selections, and_(
        selections.c.instrument_id == InstrumentResearchNoteRevision.instrument_id,
        selections.c.note_id == InstrumentResearchNoteRevision.note_id,
        selections.c.note_revision == InstrumentResearchNoteRevision.revision_number,
        selections.c.rank == 1))
    for selected in session.scalars(selected_notes):
        overrides[selected.instrument_id]["research_current_view"] = selected.body or selected.summary

    notes_by_instrument: dict[str, list[InstrumentResearchNote]] = defaultdict(list)
    for note in repository.list_notes_for_instruments(session, normalized_ids):
        notes_by_instrument[note.instrument_id].append(note)
    for instrument_id, notes in notes_by_instrument.items():
        values = overrides[instrument_id]
        values["research_note_count"] = len(notes)
        values["research_updated_at"] = max([note.updated_at for note in notes] + ([values["research_updated_at"]] if values.get("research_updated_at") else []))
        follow_up_dates = [
            note.follow_up_date for note in notes if note.follow_up_date is not None and note.completed_at is None
        ]
        if follow_up_dates:
            values["research_next_follow_up_date"] = min(follow_up_dates)
    for instrument_id, values in build_risk_watchlist_attribute_overrides(
        session, instrument_ids=normalized_ids,
    ).items():
        overrides[instrument_id].update(values)
    return overrides
