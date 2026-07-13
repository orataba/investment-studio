from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from watchlist_app.db.models.read_models import (
    InstrumentChartReadModel,
    InstrumentPerformanceReadModel,
    InstrumentRiskReadModel,
    InstrumentSummaryReadModel,
    WatchlistRowReadModel,
)
from watchlist_app.db.models.watchlists import WatchlistView


def _scoped_view_id(watchlist_id: str, local_view_id: str) -> str:
    return f"{watchlist_id}::{local_view_id}"


class SQLAlchemyReadModelRepository:
    def list_watchlist_rows(
        self,
        session: Session,
        watchlist_id: str,
    ) -> Sequence[WatchlistRowReadModel]:
        stmt = (
            select(WatchlistRowReadModel)
            .where(WatchlistRowReadModel.watchlist_id == watchlist_id)
            .order_by(WatchlistRowReadModel.instrument_name, WatchlistRowReadModel.instrument_id)
        )
        return session.scalars(stmt).all()

    def get_watchlist_row(
        self,
        session: Session,
        *,
        watchlist_id: str,
        instrument_id: str,
    ) -> WatchlistRowReadModel | None:
        stmt = select(WatchlistRowReadModel).where(
            WatchlistRowReadModel.watchlist_id == watchlist_id,
            WatchlistRowReadModel.instrument_id == instrument_id,
        )
        return session.scalars(stmt).first()

    def delete_watchlist_rows(
        self,
        session: Session,
        *,
        watchlist_id: str,
        instrument_ids: list[str],
    ) -> int:
        if not instrument_ids:
            return 0
        stmt = select(WatchlistRowReadModel).where(
            WatchlistRowReadModel.watchlist_id == watchlist_id,
            WatchlistRowReadModel.instrument_id.in_(instrument_ids),
        )
        rows = session.scalars(stmt).all()
        for row in rows:
            session.delete(row)
        session.flush()
        return len(rows)

    def delete_all_watchlist_rows(
        self,
        session: Session,
        *,
        watchlist_id: str,
    ) -> int:
        stmt = select(WatchlistRowReadModel).where(
            WatchlistRowReadModel.watchlist_id == watchlist_id
        )
        rows = session.scalars(stmt).all()
        for row in rows:
            session.delete(row)
        session.flush()
        return len(rows)

    def find_any_watchlist_row_for_asset(
        self,
        session: Session,
        instrument_id: str,
    ) -> WatchlistRowReadModel | None:
        stmt = (
            select(WatchlistRowReadModel)
            .where(WatchlistRowReadModel.instrument_id == instrument_id)
            .order_by(WatchlistRowReadModel.watchlist_id)
        )
        return session.scalars(stmt).first()

    def list_watchlist_rows_for_instrument(
        self,
        session: Session,
        instrument_id: str,
    ) -> Sequence[WatchlistRowReadModel]:
        stmt = (
            select(WatchlistRowReadModel)
            .where(WatchlistRowReadModel.instrument_id == instrument_id)
            .order_by(WatchlistRowReadModel.watchlist_id)
        )
        return session.scalars(stmt).all()

    def upsert_watchlist_row(
        self,
        session: Session,
        *,
        watchlist_id: str,
        instrument_id: str,
        data: dict[str, Any],
    ) -> WatchlistRowReadModel:
        record = self.get_watchlist_row(
            session,
            watchlist_id=watchlist_id,
            instrument_id=instrument_id,
        )
        if record is None:
            record = WatchlistRowReadModel(
                watchlist_id=watchlist_id,
                instrument_id=instrument_id,
                instrument_type=str(data.get("instrument_type") or "fund"),
                instrument_name=str(data["instrument_name"]),
                share_class=data.get("share_class"),
                ticker_or_isin=data.get("ticker_or_isin"),
                management_firm_name=data.get("management_firm_name"),
                research_rating=data.get("research_rating"),
                research_rating_as_of=data.get("research_rating_as_of"),
                research_rating_updated_at=data.get("research_rating_updated_at"),
                return_ytd=data.get("return_ytd"),
                return_1w=data.get("return_1w"),
                return_mtd=data.get("return_mtd"),
                return_1m=data.get("return_1m"),
                return_1y=data.get("return_1y"),
                annualized_return=data.get("annualized_return"),
                return_3y=data.get("return_3y"),
                return_5y=data.get("return_5y"),
                max_drawdown=data.get("max_drawdown"),
                volatility=data.get("volatility"),
                sharpe_ratio=data.get("sharpe_ratio"),
                attributes_json=data.get("attributes", {}),
                last_nav_date=data.get("last_nav_date"),
                data_freshness_status=str(data.get("data_freshness_status") or "unavailable"),
                market_data_input_watermark_at=data.get(
                    "market_data_input_watermark_at"
                ),
                last_recalculated_at=data.get("last_recalculated_at"),
                last_successful_snapshot_at=data.get("last_successful_snapshot_at"),
                staleness_reason=data.get("staleness_reason"),
            )
            session.add(record)
            session.flush()
            return record

        record.instrument_type = str(data.get("instrument_type") or record.instrument_type or "fund")
        record.instrument_name = str(data["instrument_name"])
        record.share_class = data.get("share_class")
        record.ticker_or_isin = data.get("ticker_or_isin")
        record.management_firm_name = data.get("management_firm_name")
        record.research_rating = data.get("research_rating")
        record.research_rating_as_of = data.get("research_rating_as_of")
        record.research_rating_updated_at = data.get("research_rating_updated_at")
        record.return_ytd = data.get("return_ytd")
        record.return_1w = data.get("return_1w")
        record.return_mtd = data.get("return_mtd")
        record.return_1m = data.get("return_1m")
        record.return_1y = data.get("return_1y")
        record.annualized_return = data.get("annualized_return")
        record.return_3y = data.get("return_3y")
        record.return_5y = data.get("return_5y")
        record.max_drawdown = data.get("max_drawdown")
        record.volatility = data.get("volatility")
        record.sharpe_ratio = data.get("sharpe_ratio")
        record.attributes_json = data.get("attributes", {})
        record.last_nav_date = data.get("last_nav_date")
        record.data_freshness_status = str(data.get("data_freshness_status") or "unavailable")
        record.market_data_input_watermark_at = data.get(
            "market_data_input_watermark_at"
        )
        record.last_recalculated_at = data.get("last_recalculated_at")
        record.last_successful_snapshot_at = data.get("last_successful_snapshot_at")
        record.staleness_reason = data.get("staleness_reason")
        session.flush()
        return record

    def set_attributes_for_asset(
        self,
        session: Session,
        *,
        instrument_id: str,
        attributes: dict[str, object],
        touched_at: datetime | None = None,
    ) -> None:
        stmt = select(WatchlistRowReadModel).where(WatchlistRowReadModel.instrument_id == instrument_id)
        for record in session.scalars(stmt):
            record.attributes_json = attributes
            if touched_at is not None:
                record.last_recalculated_at = touched_at
        session.flush()

    def set_current_research_rating(
        self,
        session: Session,
        *,
        instrument_id: str,
        rating_payload: dict[str, object] | None,
        rating_value: int | None,
        rating_as_of: date | None,
        rating_updated_at: datetime | None,
    ) -> None:
        summary = self.get_summary(session, instrument_id)
        if summary is not None:
            summary.payload_json = {
                **(summary.payload_json or {}),
                "research_rating": rating_payload,
            }

        stmt = select(WatchlistRowReadModel).where(
            WatchlistRowReadModel.instrument_id == instrument_id
        )
        for row in session.scalars(stmt):
            row.research_rating = rating_value
            row.research_rating_as_of = rating_as_of
            row.research_rating_updated_at = rating_updated_at
        session.flush()

    def get_view(
        self,
        session: Session,
        *,
        watchlist_id: str,
        view_id: str,
    ) -> WatchlistView | None:
        stmt = (
            select(WatchlistView)
            .options(selectinload(WatchlistView.columns))
            .where(WatchlistView.watchlist_view_id == _scoped_view_id(watchlist_id, view_id))
        )
        return session.scalars(stmt).first()

    def get_summary(self, session: Session, instrument_id: str) -> InstrumentSummaryReadModel | None:
        return session.get(InstrumentSummaryReadModel, instrument_id)

    def get_chart(self, session: Session, instrument_id: str) -> InstrumentChartReadModel | None:
        return session.get(InstrumentChartReadModel, instrument_id)

    def list_charts(
        self,
        session: Session,
        instrument_ids: Sequence[str],
    ) -> Sequence[InstrumentChartReadModel]:
        if not instrument_ids:
            return []
        stmt = select(InstrumentChartReadModel).where(InstrumentChartReadModel.instrument_id.in_(instrument_ids))
        return session.scalars(stmt).all()

    def get_performance(
        self,
        session: Session,
        instrument_id: str,
    ) -> InstrumentPerformanceReadModel | None:
        return session.get(InstrumentPerformanceReadModel, instrument_id)

    def get_risk(self, session: Session, instrument_id: str) -> InstrumentRiskReadModel | None:
        return session.get(InstrumentRiskReadModel, instrument_id)

    def upsert_payload_read_model(
        self,
        session: Session,
        *,
        model_class,
        instrument_id: str,
        payload_json: dict[str, Any],
        data_freshness_status: str,
        last_recalculated_at,
        market_data_input_watermark_at,
    ):
        record = session.get(model_class, instrument_id)
        if record is None:
            record = model_class(
                instrument_id=instrument_id,
                payload_json=payload_json,
                data_freshness_status=data_freshness_status,
                last_recalculated_at=last_recalculated_at,
                market_data_input_watermark_at=market_data_input_watermark_at,
            )
            session.add(record)
            session.flush()
            return record
        record.payload_json = payload_json
        record.data_freshness_status = data_freshness_status
        record.last_recalculated_at = last_recalculated_at
        record.market_data_input_watermark_at = market_data_input_watermark_at
        session.flush()
        return record
