"""Portfolio-oriented research and persistent risk follow-up."""
from datetime import date, datetime
from typing import Any
from sqlalchemy import JSON, Date, DateTime, ForeignKey, Float, Text
from sqlalchemy.orm import Mapped, mapped_column
from watchlist_app.db.base import Base
from watchlist_app.db.models.common import TimestampMixin

class ResearchTopic(TimestampMixin, Base):
    __tablename__ = "research_topic"
    topic_id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str]
    question: Mapped[str] = mapped_column(Text, default="")
    instrument_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    portfolio_id: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="active")
    conclusion: Mapped[str] = mapped_column(Text, default="")
    next_review_date: Mapped[date | None] = mapped_column(Date)

class ResearchEntry(TimestampMixin, Base):
    __tablename__ = "research_entry"
    entry_id: Mapped[str] = mapped_column(primary_key=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("research_topic.topic_id", ondelete="CASCADE"), index=True)
    kind: Mapped[str]
    title: Mapped[str]
    body: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text, default="")
    follow_up_date: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The run retains the exact analytical inputs and evidence supplied to the assistant.
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(default="recorded")

class RiskReviewRule(TimestampMixin, Base):
    __tablename__ = "risk_review_rule"
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"), primary_key=True)
    drawdown_limit: Mapped[float | None] = mapped_column(Float)
    period_limits_json: Mapped[dict[str, float | None]] = mapped_column(JSON, default=dict)
    calibration_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

class RiskCase(TimestampMixin, Base):
    __tablename__ = "risk_case"
    case_id: Mapped[str] = mapped_column(primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"), index=True)
    signal: Mapped[str]
    title: Mapped[str]
    body: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(default="attention")
    trigger_active: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(default="open")
    observed_on: Mapped[date | None] = mapped_column(Date)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    follow_up_date: Mapped[date | None] = mapped_column(Date)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    history_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
