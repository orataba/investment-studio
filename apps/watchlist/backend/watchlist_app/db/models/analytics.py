from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, JSON, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from watchlist_app.db.base import Base


class PerformanceSnapshot(Base):
    __tablename__ = "performance_snapshot"
    __table_args__ = (Index("idx_performance_snapshot_current", "asset_id", "is_current"),)

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
    return_ytd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_1w: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_mtd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_1m: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_3m: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_6m: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_1y: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_3y_annualized: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_5y_annualized: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_10y_annualized: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    calmar: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    annualized_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))


class RiskSnapshot(Base):
    __tablename__ = "risk_snapshot"
    __table_args__ = (Index("idx_risk_snapshot_current", "asset_id", "is_current"),)

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
    volatility: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    downside_volatility: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    sharpe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    sortino_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    alpha: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    beta: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    r_squared: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    up_capture: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    down_capture: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    tracking_error: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    information_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))


class ExposureAnalyticsSnapshot(Base):
    __tablename__ = "exposure_analytics_snapshot"
    __table_args__ = (Index("idx_exposure_snapshot_current", "asset_id", "is_current"),)

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
    asset_allocation_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    sector_allocation_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    country_allocation_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    currency_allocation_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    credit_rating_allocation_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    duration_bucket_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    maturity_bucket_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    yield_bucket_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    top10_concentration: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    holding_count: Mapped[int | None]
    bond_count: Mapped[int | None]
    equity_count: Mapped[int | None]
    other_count: Mapped[int | None]
    cash_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    leverage_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    weighted_duration: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    weighted_maturity: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    weighted_yield_to_worst: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    avg_credit_rating: Mapped[str | None]
    reported_turnover: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    style_box_code: Mapped[str | None]
