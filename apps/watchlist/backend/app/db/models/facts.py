from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class NavFact(Base):
    __tablename__ = "nav_fact"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "as_of_date",
            "nav_type",
            "currency",
            name="uq_nav_fact_asset_date_type_currency",
        ),
        Index("idx_nav_fact_asset_date", "asset_id", "as_of_date"),
    )

    nav_fact_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("asset_detail.asset_id", ondelete="CASCADE"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    nav_type: Mapped[str] = mapped_column(nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    currency: Mapped[str] = mapped_column(nullable=False)
    frequency: Mapped[str | None]
    is_primary: Mapped[bool] = mapped_column(nullable=False, default=False)
    source_record_id: Mapped[str | None]
    observation_id: Mapped[str | None]
    adopted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HoldingSnapshot(Base):
    __tablename__ = "holding_snapshot"
    __table_args__ = (Index("idx_holding_snapshot_current", "asset_id", "is_current"),)

    holding_snapshot_id: Mapped[str] = mapped_column(primary_key=True)
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
    source_record_id: Mapped[str | None]

    positions: Mapped[list["HoldingPosition"]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )


class HoldingPosition(Base):
    __tablename__ = "holding_position"

    holding_position_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    holding_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("holding_snapshot.holding_snapshot_id", ondelete="CASCADE"),
        nullable=False,
    )
    holding_name: Mapped[str] = mapped_column(nullable=False)
    holding_type: Mapped[str] = mapped_column(nullable=False)
    security_identifier: Mapped[str | None]
    issuer_name: Mapped[str | None]
    issuer_type: Mapped[str | None]
    portfolio_weight: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    market_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    currency: Mapped[str | None]
    market_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    share_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    maturity_date: Mapped[date | None] = mapped_column(Date)
    coupon_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    credit_rating: Mapped[str | None]
    effective_duration: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    modified_duration: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    yield_to_worst: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    sector: Mapped[str | None]
    country_code: Mapped[str | None]

    snapshot: Mapped[HoldingSnapshot] = relationship(back_populates="positions")
