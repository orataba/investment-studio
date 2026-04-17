from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssetScoreSnapshot(Base):
    __tablename__ = "asset_score_snapshot"
    __table_args__ = (Index("idx_score_snapshot_current", "asset_id", "is_current"),)

    snapshot_id: Mapped[str] = mapped_column(primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("asset_detail.asset_id", ondelete="CASCADE"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    methodology_version: Mapped[str] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(nullable=False, default=True)
    overall_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    overall_rating: Mapped[int | None]
    analyst_stance: Mapped[str | None]
    people_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    process_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    exposure_score: Mapped[Decimal | None] = mapped_column("exposure_score", Numeric(12, 6))
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    price_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    operations_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    fit_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
