from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.research_ratings import InstrumentResearchRating


class ResearchRatingConflictError(Exception):
    def __init__(self, *, expected_revision_id: str | None, actual_revision_id: str | None):
        super().__init__("The current research rating changed before this update was saved.")
        self.expected_revision_id = expected_revision_id
        self.actual_revision_id = actual_revision_id


class SQLAlchemyResearchRatingRepository:
    def get_current(
        self,
        session: Session,
        instrument_id: str,
    ) -> InstrumentResearchRating | None:
        stmt = (
            select(InstrumentResearchRating)
            .where(
                InstrumentResearchRating.instrument_id == instrument_id,
                InstrumentResearchRating.is_current.is_(True),
            )
            .order_by(InstrumentResearchRating.created_at.desc())
        )
        return session.scalars(stmt).first()

    def list_history(
        self,
        session: Session,
        instrument_id: str,
    ) -> Sequence[InstrumentResearchRating]:
        stmt = (
            select(InstrumentResearchRating)
            .where(InstrumentResearchRating.instrument_id == instrument_id)
            .order_by(
                InstrumentResearchRating.revision_number.desc(),
            )
        )
        return session.scalars(stmt).all()

    def append_revision(
        self,
        session: Session,
        *,
        instrument_id: str,
        rating_value: int | None,
        confidence: str,
        rationale: str,
        as_of_date: date,
        next_review_date: date | None,
        author: str,
        expected_current_revision_id: str | None,
    ) -> InstrumentResearchRating:
        # Lock the stable parent row so two first-time ratings for the same
        # instrument cannot both observe an empty current revision.
        session.scalar(
            select(InstrumentDetail.instrument_id)
            .where(InstrumentDetail.instrument_id == instrument_id)
            .with_for_update()
        )
        current = session.scalars(
            select(InstrumentResearchRating)
            .where(
                InstrumentResearchRating.instrument_id == instrument_id,
                InstrumentResearchRating.is_current.is_(True),
            )
            .with_for_update()
        ).first()
        actual_revision_id = current.rating_revision_id if current is not None else None
        if expected_current_revision_id != actual_revision_id:
            raise ResearchRatingConflictError(
                expected_revision_id=expected_current_revision_id,
                actual_revision_id=actual_revision_id,
            )

        now = datetime.now(UTC).replace(microsecond=0)
        if current is not None:
            current.is_current = False
            current.superseded_at = now
            # Make the partial unique slot available before inserting the new
            # current revision, independent of ORM statement ordering.
            session.flush()

        revision = InstrumentResearchRating(
            rating_revision_id=f"rating-{uuid4().hex}",
            previous_rating_revision_id=actual_revision_id,
            instrument_id=instrument_id,
            revision_number=(current.revision_number + 1) if current is not None else 1,
            rating_value=rating_value,
            confidence=confidence,
            as_of_date=as_of_date,
            next_review_date=next_review_date,
            rationale=rationale,
            author=author,
            created_at=now,
            superseded_at=None,
            is_current=True,
        )
        session.add(revision)
        session.flush()
        return revision
