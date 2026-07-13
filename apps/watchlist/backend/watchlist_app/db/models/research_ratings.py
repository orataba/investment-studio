from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from watchlist_app.db.base import Base


class InstrumentResearchRating(Base):
    """A manually authored, append-only revision of the internal research rating."""

    __tablename__ = "instrument_research_rating"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "revision_number",
            name="uq_instrument_research_rating_instrument_revision",
        ),
        CheckConstraint(
            "rating_value IS NULL OR (rating_value >= 1 AND rating_value <= 5)",
            name="rating_value_range",
        ),
        CheckConstraint(
            "next_review_date IS NULL OR next_review_date >= as_of_date",
            name="next_review_not_before_as_of",
        ),
        CheckConstraint(
            "confidence IN ('low', 'medium', 'high', 'unassessed')",
            name="confidence_value",
        ),
        Index(
            "idx_research_rating_instrument_history",
            "instrument_id",
            "created_at",
        ),
        Index(
            "uq_research_rating_current_instrument",
            "instrument_id",
            unique=True,
            sqlite_where=text("is_current = 1"),
            postgresql_where=text("is_current IS TRUE"),
        ),
    )

    rating_revision_id: Mapped[str] = mapped_column(primary_key=True)
    previous_rating_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "instrument_research_rating.rating_revision_id",
            name="fk_research_rating_previous_revision",
            ondelete="RESTRICT",
        )
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_value: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[str] = mapped_column(nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    next_review_date: Mapped[date | None] = mapped_column(Date)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
