from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from watchlist_app.db.base import Base
from watchlist_app.db.models.common import TimestampMixin


class InstrumentResearchProfile(TimestampMixin, Base):
    __tablename__ = "instrument_research_profile"
    __table_args__ = (
        CheckConstraint(
            "manual_rating IS NULL OR manual_rating BETWEEN 1 AND 5",
            name="manual_rating_range",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    research_stage: Mapped[str] = mapped_column(Text, nullable=False, default="watching", server_default="watching")
    thesis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_view: Mapped[str] = mapped_column(Text, nullable=False, default="")
    why_now: Mapped[str] = mapped_column(Text, nullable=False, default="")
    edge_assessment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    valuation_framework: Mapped[str] = mapped_column(Text, nullable=False, default="")
    catalysts: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_risks: Mapped[str] = mapped_column(Text, nullable=False, default="")
    disconfirming_evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    open_questions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    monitoring_plan: Mapped[str] = mapped_column(Text, nullable=False, default="")
    people_assessment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    portfolio_role: Mapped[str] = mapped_column(Text, nullable=False, default="")
    time_horizon: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decision_rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    primary_analyst: Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_review_date: Mapped[date | None] = mapped_column(Date)
    dd_status: Mapped[str] = mapped_column(Text, nullable=False, default="")
    odd_status: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ic_status: Mapped[str] = mapped_column(Text, nullable=False, default="")
    manual_rating: Mapped[int | None] = mapped_column(Integer)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str | None]


class InstrumentResearchProfileRevision(Base):
    __tablename__ = "instrument_research_profile_revision"
    __table_args__ = (
        CheckConstraint(
            "manual_rating IS NULL OR manual_rating BETWEEN 1 AND 5",
            name="manual_rating_range",
        ),
        Index(
            "idx_instrument_research_profile_revision_instrument_time",
            "instrument_id",
            "recorded_at",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    research_stage: Mapped[str] = mapped_column(Text, nullable=False, default="watching", server_default="watching")
    thesis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_view: Mapped[str] = mapped_column(Text, nullable=False, default="")
    why_now: Mapped[str] = mapped_column(Text, nullable=False, default="")
    edge_assessment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    valuation_framework: Mapped[str] = mapped_column(Text, nullable=False, default="")
    catalysts: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_risks: Mapped[str] = mapped_column(Text, nullable=False, default="")
    disconfirming_evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    open_questions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    monitoring_plan: Mapped[str] = mapped_column(Text, nullable=False, default="")
    people_assessment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    portfolio_role: Mapped[str] = mapped_column(Text, nullable=False, default="")
    time_horizon: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decision_rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    primary_analyst: Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_review_date: Mapped[date | None] = mapped_column(Date)
    dd_status: Mapped[str] = mapped_column(Text, nullable=False, default="")
    odd_status: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ic_status: Mapped[str] = mapped_column(Text, nullable=False, default="")
    manual_rating: Mapped[int | None] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by: Mapped[str | None]


class InstrumentInvestmentStance(Base):
    """Explicit, append-only choices of the team's current overall view."""
    __tablename__ = "instrument_investment_stance"
    __table_args__ = (
        CheckConstraint("(note_id IS NULL) = (note_revision IS NULL)", name="stance_reference_pair"),
        ForeignKeyConstraint(["instrument_id", "note_id", "note_revision"],
            ["instrument_research_note_revision.instrument_id", "instrument_research_note_revision.note_id", "instrument_research_note_revision.revision_number"]),
        Index("idx_investment_stance_scope_time", "instrument_id", "team_id", "selected_at"),
    )

    selection_id: Mapped[str] = mapped_column(primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"))
    team_id: Mapped[str] = mapped_column(Text, nullable=False)
    note_id: Mapped[str | None] = mapped_column(Text)
    note_revision: Mapped[int | None] = mapped_column(Integer)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    selected_by: Mapped[str] = mapped_column(Text, nullable=False)
    selected_by_name: Mapped[str] = mapped_column(Text, nullable=False)


class InstrumentResearchNote(Base):
    __tablename__ = "instrument_research_note"
    __table_args__ = (
        CheckConstraint(
            "note_type IN ('research_update', 'thesis_update', 'evidence', "
            "'meeting', 'event', 'risk', 'decision', 'review')",
            name="note_type",
        ),
        CheckConstraint(
            "importance IN ('low', 'medium', 'high')",
            name="importance",
        ),
        Index(
            "idx_instrument_research_note_instrument_date",
            "instrument_id",
            "note_date",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    note_id: Mapped[str] = mapped_column(primary_key=True)
    note_date: Mapped[date] = mapped_column(Date, nullable=False)
    note_type: Mapped[str] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    importance: Mapped[str] = mapped_column(nullable=False, default="medium")
    tags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_refs: Mapped[str] = mapped_column(Text, nullable=False, default="")
    people: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author_user_id: Mapped[str | None] = mapped_column(Text)
    team_id: Mapped[str] = mapped_column(Text, nullable=False, default="default", server_default="default")
    research_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    follow_up_date: Mapped[date | None] = mapped_column(Date)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str | None]
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[str | None]


class InstrumentResearchNoteRevision(Base):
    __tablename__ = "instrument_research_note_revision"
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('create', 'update', 'delete')",
            name="change_type",
        ),
        CheckConstraint(
            "note_type IN ('research_update', 'thesis_update', 'evidence', "
            "'meeting', 'event', 'risk', 'decision', 'review')",
            name="note_type",
        ),
        CheckConstraint(
            "importance IN ('low', 'medium', 'high')",
            name="importance",
        ),
        Index(
            "idx_instrument_research_note_revision_instrument_time",
            "instrument_id",
            "recorded_at",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    note_id: Mapped[str] = mapped_column(primary_key=True)
    revision_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_type: Mapped[str] = mapped_column(nullable=False)
    note_date: Mapped[date] = mapped_column(Date, nullable=False)
    note_type: Mapped[str] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    importance: Mapped[str] = mapped_column(nullable=False, default="medium")
    tags_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_refs: Mapped[str] = mapped_column(Text, nullable=False, default="")
    people: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author_user_id: Mapped[str | None] = mapped_column(Text)
    team_id: Mapped[str] = mapped_column(Text, nullable=False, default="default", server_default="default")
    research_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    follow_up_date: Mapped[date | None] = mapped_column(Date)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by: Mapped[str | None]
