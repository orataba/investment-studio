"""Immutable calculation originals, addressed independently of run orchestration."""
from datetime import datetime
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from watchlist_app.db.base import Base


class ResearchRunEvidence(Base):
    __tablename__ = "research_run_evidence"
    run_id: Mapped[str] = mapped_column(ForeignKey("research_entry.entry_id", ondelete="CASCADE"), primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    source_json: Mapped[dict] = mapped_column(JSON, nullable=False, deferred=True)
    index_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
