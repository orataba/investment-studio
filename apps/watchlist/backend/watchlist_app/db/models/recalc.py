from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from watchlist_app.db.base import Base


class RecalcJob(Base):
    __tablename__ = "recalc_job"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_recalc_job_dedupe_key"),
        Index(
            "idx_recalc_job_status_priority",
            "job_status",
            "priority",
            "enqueued_at",
        ),
    )

    recalc_job_id: Mapped[str] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(nullable=False)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("asset_detail.asset_id", ondelete="CASCADE"),
        nullable=False,
    )
    trigger_type: Mapped[str] = mapped_column(nullable=False)
    trigger_ref_type: Mapped[str | None]
    trigger_ref_id: Mapped[str | None]
    job_status: Mapped[str] = mapped_column(nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    dedupe_key: Mapped[str] = mapped_column(nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None]
