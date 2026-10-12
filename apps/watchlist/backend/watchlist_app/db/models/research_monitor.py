"""A successful cheap scan and a successfully applied analysis are distinct facts."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from watchlist_app.db.base import Base


class ResearchMonitorState(Base):
    __tablename__ = "research_monitor_state"

    team_id: Mapped[str] = mapped_column(Text, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"), primary_key=True)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_cursor_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    pending_changes_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempted_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_run_id: Mapped[str | None] = mapped_column(ForeignKey("research_entry.entry_id", ondelete="SET NULL"))
