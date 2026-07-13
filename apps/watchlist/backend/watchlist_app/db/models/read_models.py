from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from watchlist_app.db.base import Base
from watchlist_app.db.models.common import PayloadReadModelMixin


class WatchlistRowReadModel(Base):
    __tablename__ = "watchlist_row_read_model"

    watchlist_id: Mapped[str] = mapped_column(
        ForeignKey("watchlist.watchlist_id", ondelete="CASCADE"),
        primary_key=True,
    )
    instrument_id: Mapped[str] = mapped_column(primary_key=True)
    instrument_type: Mapped[str] = mapped_column(nullable=False, default="fund")
    instrument_name: Mapped[str] = mapped_column(nullable=False)
    share_class: Mapped[str | None]
    ticker_or_isin: Mapped[str | None]
    management_firm_name: Mapped[str | None]
    research_rating: Mapped[int | None]
    research_rating_as_of: Mapped[date | None] = mapped_column(Date)
    research_rating_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    return_ytd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_1w: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_mtd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_1m: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_1y: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    annualized_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_3y: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_5y: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    volatility: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    sharpe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_nav_date: Mapped[date | None] = mapped_column(Date)
    data_freshness_status: Mapped[str] = mapped_column(nullable=False)
    market_data_input_watermark_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_recalculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_snapshot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    staleness_reason: Mapped[str | None]


class InstrumentSummaryReadModel(PayloadReadModelMixin, Base):
    __tablename__ = "instrument_summary_read_model"

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )


class InstrumentChartReadModel(PayloadReadModelMixin, Base):
    __tablename__ = "instrument_chart_read_model"

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )


class InstrumentPerformanceReadModel(PayloadReadModelMixin, Base):
    __tablename__ = "instrument_performance_read_model"

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )


class InstrumentRiskReadModel(PayloadReadModelMixin, Base):
    __tablename__ = "instrument_risk_read_model"

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
