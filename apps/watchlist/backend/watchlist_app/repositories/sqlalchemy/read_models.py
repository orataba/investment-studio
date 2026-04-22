from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from watchlist_app.db.models.read_models import (
    AssetChartReadModel,
    AssetPerformanceReadModel,
    AssetExposureHoldingsReadModel,
    AssetExposureReadModel,
    AssetRatingReadModel,
    AssetRiskReadModel,
    AssetSummaryReadModel,
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
            .order_by(WatchlistRowReadModel.asset_name, WatchlistRowReadModel.asset_id)
        )
        return session.scalars(stmt).all()

    def get_watchlist_row(
        self,
        session: Session,
        *,
        watchlist_id: str,
        asset_id: str,
    ) -> WatchlistRowReadModel | None:
        stmt = select(WatchlistRowReadModel).where(
            WatchlistRowReadModel.watchlist_id == watchlist_id,
            WatchlistRowReadModel.asset_id == asset_id,
        )
        return session.scalars(stmt).first()

    def delete_watchlist_rows(
        self,
        session: Session,
        *,
        watchlist_id: str,
        asset_ids: list[str],
    ) -> int:
        if not asset_ids:
            return 0
        stmt = select(WatchlistRowReadModel).where(
            WatchlistRowReadModel.watchlist_id == watchlist_id,
            WatchlistRowReadModel.asset_id.in_(asset_ids),
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
        asset_id: str,
    ) -> WatchlistRowReadModel | None:
        stmt = (
            select(WatchlistRowReadModel)
            .where(WatchlistRowReadModel.asset_id == asset_id)
            .order_by(WatchlistRowReadModel.watchlist_id)
        )
        return session.scalars(stmt).first()

    def list_watchlist_rows_for_asset(
        self,
        session: Session,
        asset_id: str,
    ) -> Sequence[WatchlistRowReadModel]:
        stmt = (
            select(WatchlistRowReadModel)
            .where(WatchlistRowReadModel.asset_id == asset_id)
            .order_by(WatchlistRowReadModel.watchlist_id)
        )
        return session.scalars(stmt).all()

    def upsert_watchlist_row(
        self,
        session: Session,
        *,
        watchlist_id: str,
        asset_id: str,
        data: dict[str, Any],
    ) -> WatchlistRowReadModel:
        record = self.get_watchlist_row(
            session,
            watchlist_id=watchlist_id,
            asset_id=asset_id,
        )
        if record is None:
            record = WatchlistRowReadModel(
                watchlist_id=watchlist_id,
                asset_id=asset_id,
                asset_type=str(data.get("asset_type") or "fund"),
                asset_name=str(data["asset_name"]),
                share_class=data.get("share_class"),
                ticker_or_isin=data.get("ticker_or_isin"),
                management_firm_name=data.get("management_firm_name"),
                category_name=data.get("category_name"),
                overall_rating=data.get("overall_rating"),
                analyst_stance=data.get("analyst_stance"),
                aum=data.get("aum"),
                return_ytd=data.get("return_ytd"),
                return_1w=data.get("return_1w"),
                return_1m=data.get("return_1m"),
                return_1y=data.get("return_1y"),
                annualized_return=data.get("annualized_return"),
                return_3y=data.get("return_3y"),
                return_5y=data.get("return_5y"),
                max_drawdown=data.get("max_drawdown"),
                volatility=data.get("volatility"),
                sharpe_ratio=data.get("sharpe_ratio"),
                duration=data.get("duration"),
                yield_to_worst=data.get("yield_to_worst"),
                avg_credit_rating=data.get("avg_credit_rating"),
                attributes_json=data.get("attributes", {}),
                exposure_updated_at=data.get("exposure_updated_at"),
                last_nav_date=data.get("last_nav_date"),
                data_freshness_status=str(data.get("data_freshness_status") or "unavailable"),
                last_fact_update_at=data.get("last_fact_update_at"),
                last_recalculated_at=data.get("last_recalculated_at"),
                last_successful_snapshot_at=data.get("last_successful_snapshot_at"),
                staleness_reason=data.get("staleness_reason"),
            )
            session.add(record)
            session.flush()
            return record

        record.asset_type = str(data.get("asset_type") or record.asset_type or "fund")
        record.asset_name = str(data["asset_name"])
        record.share_class = data.get("share_class")
        record.ticker_or_isin = data.get("ticker_or_isin")
        record.management_firm_name = data.get("management_firm_name")
        record.category_name = data.get("category_name")
        record.overall_rating = data.get("overall_rating")
        record.analyst_stance = data.get("analyst_stance")
        record.aum = data.get("aum")
        record.return_ytd = data.get("return_ytd")
        record.return_1w = data.get("return_1w")
        record.return_1m = data.get("return_1m")
        record.return_1y = data.get("return_1y")
        record.annualized_return = data.get("annualized_return")
        record.return_3y = data.get("return_3y")
        record.return_5y = data.get("return_5y")
        record.max_drawdown = data.get("max_drawdown")
        record.volatility = data.get("volatility")
        record.sharpe_ratio = data.get("sharpe_ratio")
        record.duration = data.get("duration")
        record.yield_to_worst = data.get("yield_to_worst")
        record.avg_credit_rating = data.get("avg_credit_rating")
        record.attributes_json = data.get("attributes", {})
        record.exposure_updated_at = data.get("exposure_updated_at")
        record.last_nav_date = data.get("last_nav_date")
        record.data_freshness_status = str(data.get("data_freshness_status") or "unavailable")
        record.last_fact_update_at = data.get("last_fact_update_at")
        record.last_recalculated_at = data.get("last_recalculated_at")
        record.last_successful_snapshot_at = data.get("last_successful_snapshot_at")
        record.staleness_reason = data.get("staleness_reason")
        session.flush()
        return record

    def set_attributes_for_asset(
        self,
        session: Session,
        *,
        asset_id: str,
        attributes: dict[str, object],
        touched_at: datetime | None = None,
    ) -> None:
        stmt = select(WatchlistRowReadModel).where(WatchlistRowReadModel.asset_id == asset_id)
        for record in session.scalars(stmt):
            record.attributes_json = attributes
            if touched_at is not None:
                record.last_recalculated_at = touched_at
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

    def get_summary(self, session: Session, asset_id: str) -> AssetSummaryReadModel | None:
        return session.get(AssetSummaryReadModel, asset_id)

    def get_chart(self, session: Session, asset_id: str) -> AssetChartReadModel | None:
        return session.get(AssetChartReadModel, asset_id)

    def list_charts(
        self,
        session: Session,
        asset_ids: Sequence[str],
    ) -> Sequence[AssetChartReadModel]:
        if not asset_ids:
            return []
        stmt = select(AssetChartReadModel).where(AssetChartReadModel.asset_id.in_(asset_ids))
        return session.scalars(stmt).all()

    def get_performance(
        self,
        session: Session,
        asset_id: str,
    ) -> AssetPerformanceReadModel | None:
        return session.get(AssetPerformanceReadModel, asset_id)

    def get_risk(self, session: Session, asset_id: str) -> AssetRiskReadModel | None:
        return session.get(AssetRiskReadModel, asset_id)

    def get_exposure_summary(
        self,
        session: Session,
        asset_id: str,
    ) -> AssetExposureReadModel | None:
        return session.get(AssetExposureReadModel, asset_id)

    def get_exposure_holdings(
        self,
        session: Session,
        asset_id: str,
    ) -> AssetExposureHoldingsReadModel | None:
        return session.get(AssetExposureHoldingsReadModel, asset_id)

    def get_rating(self, session: Session, asset_id: str) -> AssetRatingReadModel | None:
        return session.get(AssetRatingReadModel, asset_id)

    def upsert_payload_read_model(
        self,
        session: Session,
        *,
        model_class,
        asset_id: str,
        payload_json: dict[str, Any],
        data_freshness_status: str,
        last_recalculated_at,
        source_cutoff_at,
    ):
        record = session.get(model_class, asset_id)
        if record is None:
            record = model_class(
                asset_id=asset_id,
                payload_json=payload_json,
                data_freshness_status=data_freshness_status,
                last_recalculated_at=last_recalculated_at,
                source_cutoff_at=source_cutoff_at,
            )
            session.add(record)
            session.flush()
            return record
        record.payload_json = payload_json
        record.data_freshness_status = data_freshness_status
        record.last_recalculated_at = last_recalculated_at
        record.source_cutoff_at = source_cutoff_at
        session.flush()
        return record
