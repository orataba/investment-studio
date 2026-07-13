from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from watchlist_app.db.models.research_ratings import InstrumentResearchRating
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository


def serialize_research_rating(
    record: InstrumentResearchRating | None,
) -> dict[str, object] | None:
    """Serialize the single canonical shape shared by summary and rating APIs."""

    if record is None:
        return None
    as_of_date = getattr(record, "as_of_date", None)
    next_review_date = getattr(record, "next_review_date", None)
    created_at = getattr(record, "created_at", None)
    superseded_at = getattr(record, "superseded_at", None)
    return {
        "instrument_id": getattr(record, "instrument_id", None),
        "rating_revision_id": getattr(record, "rating_revision_id", None),
        "revision_number": getattr(record, "revision_number", None),
        "previous_rating_revision_id": getattr(
            record, "previous_rating_revision_id", None
        ),
        "rating": getattr(record, "rating_value", None),
        "confidence": getattr(record, "confidence", None),
        "as_of_date": as_of_date.isoformat() if isinstance(as_of_date, date) else None,
        "next_review_date": (
            next_review_date.isoformat() if isinstance(next_review_date, date) else None
        ),
        "rationale": getattr(record, "rationale", None),
        "author": getattr(record, "author", None),
        "created_at": (
            created_at.isoformat().replace("+00:00", "Z")
            if isinstance(created_at, datetime)
            else None
        ),
        "superseded_at": (
            superseded_at.isoformat().replace("+00:00", "Z")
            if isinstance(superseded_at, datetime)
            else None
        ),
        "is_current": bool(getattr(record, "is_current", False)),
    }


def empty_research_rating(instrument_id: str) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "rating_revision_id": None,
        "revision_number": None,
        "previous_rating_revision_id": None,
        "rating": None,
        "confidence": None,
        "as_of_date": None,
        "next_review_date": None,
        "rationale": None,
        "author": None,
        "created_at": None,
        "superseded_at": None,
        "is_current": False,
    }


def materialize_research_rating(
    session: Session,
    *,
    record: InstrumentResearchRating,
    read_model_repository: SQLAlchemyReadModelRepository,
) -> dict[str, object]:
    payload = serialize_research_rating(record)
    if payload is None:
        raise ValueError("A persisted research rating revision is required.")
    read_model_repository.set_current_research_rating(
        session,
        instrument_id=str(payload["instrument_id"]),
        rating_payload=payload,
        rating_value=getattr(record, "rating_value", None),
        rating_as_of=getattr(record, "as_of_date", None),
        rating_updated_at=getattr(record, "created_at", None),
    )
    return payload
