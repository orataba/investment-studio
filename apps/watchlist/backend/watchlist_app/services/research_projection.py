from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from watchlist_app.db.models.research import InstrumentResearchNote
from watchlist_app.repositories.sqlalchemy.research import (
    SQLAlchemyInstrumentResearchRepository,
)


RESEARCH_WATCHLIST_FIELD_KEYS = {
    "attr.research_current_view",
    "attr.manual_rating",
    "attr.primary_analyst",
    "attr.research_next_review_date",
    "attr.research_note_count",
    "attr.research_next_follow_up_date",
    "attr.research_updated_at",
}


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
        instrument_id: {"research_note_count": 0}
        for instrument_id in normalized_ids
    }
    for profile in repository.list_profiles(session, normalized_ids):
        values = overrides[profile.instrument_id]
        if profile.current_view:
            values["research_current_view"] = profile.current_view
        if profile.manual_rating is not None:
            values["manual_rating"] = profile.manual_rating
        if profile.primary_analyst:
            values["primary_analyst"] = profile.primary_analyst
        if profile.next_review_date is not None:
            values["research_next_review_date"] = profile.next_review_date
        values["research_updated_at"] = profile.updated_at

    notes_by_instrument: dict[str, list[InstrumentResearchNote]] = defaultdict(list)
    for note in repository.list_notes_for_instruments(session, normalized_ids):
        notes_by_instrument[note.instrument_id].append(note)
    for instrument_id, notes in notes_by_instrument.items():
        values = overrides[instrument_id]
        values["research_note_count"] = len(notes)
        follow_up_dates = [
            note.follow_up_date for note in notes if note.follow_up_date is not None
        ]
        if follow_up_dates:
            values["research_next_follow_up_date"] = min(follow_up_dates)
    return overrides
