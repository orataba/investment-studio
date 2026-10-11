"""Private execution control, separate from research evidence and public reads."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from watchlist_app.db.base import Base


class ResearchExecution(Base):
    __tablename__ = "research_execution"
    __table_args__ = (
        CheckConstraint("attempt > 0", name="attempt_positive"),
        CheckConstraint("status IN ('queued', 'running', 'finished')", name="status_allowed"),
        Index("ix_research_execution_queue", "status", "queued_at"),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey("research_entry.entry_id", ondelete="CASCADE"), primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # A run/attempt-scoped Home grant; never serialize this model into API replies.
    # Its original subject and revocation chain remain authoritative at execution.
    delegation_token: Mapped[str | None] = mapped_column(Text, deferred=True)
    owner_id: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
