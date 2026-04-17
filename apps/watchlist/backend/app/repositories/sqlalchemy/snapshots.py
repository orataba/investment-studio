from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.analytics import (
    PerformanceSnapshot,
    ExposureAnalyticsSnapshot,
    RiskSnapshot,
)
from app.db.models.scoring import AssetScoreSnapshot


class SQLAlchemySnapshotRepository:
    def get_current_performance(
        self,
        session: Session,
        asset_id: str,
    ) -> PerformanceSnapshot | None:
        stmt = (
            select(PerformanceSnapshot)
            .where(
                PerformanceSnapshot.asset_id == asset_id,
                PerformanceSnapshot.is_current.is_(True),
            )
            .order_by(PerformanceSnapshot.as_of_date.desc())
        )
        return session.scalars(stmt).first()

    def get_current_risk(self, session: Session, asset_id: str) -> RiskSnapshot | None:
        stmt = (
            select(RiskSnapshot)
            .where(RiskSnapshot.asset_id == asset_id, RiskSnapshot.is_current.is_(True))
            .order_by(RiskSnapshot.as_of_date.desc())
        )
        return session.scalars(stmt).first()

    def get_current_exposure(
        self,
        session: Session,
        asset_id: str,
    ) -> ExposureAnalyticsSnapshot | None:
        stmt = (
            select(ExposureAnalyticsSnapshot)
            .where(
                ExposureAnalyticsSnapshot.asset_id == asset_id,
                ExposureAnalyticsSnapshot.is_current.is_(True),
            )
            .order_by(ExposureAnalyticsSnapshot.as_of_date.desc())
        )
        return session.scalars(stmt).first()

    def get_current_score(
        self,
        session: Session,
        asset_id: str,
    ) -> AssetScoreSnapshot | None:
        stmt = (
            select(AssetScoreSnapshot)
            .where(AssetScoreSnapshot.asset_id == asset_id, AssetScoreSnapshot.is_current.is_(True))
            .order_by(AssetScoreSnapshot.as_of_date.desc())
        )
        return session.scalars(stmt).first()

    def _replace_current(self, session: Session, model_class, asset_id: str) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        for current in session.scalars(
            select(model_class).where(
                model_class.asset_id == asset_id, model_class.is_current.is_(True)
            )
        ):
            current.is_current = False
            current.superseded_at = now

    def replace_performance(
        self,
        session: Session,
        *,
        snapshot_id: str,
        asset_id: str,
        data: dict[str, Any],
    ) -> PerformanceSnapshot:
        self._replace_current(session, PerformanceSnapshot, asset_id)
        record = PerformanceSnapshot(snapshot_id=snapshot_id, asset_id=asset_id, **data)
        session.add(record)
        session.flush()
        return record

    def replace_risk(
        self,
        session: Session,
        *,
        snapshot_id: str,
        asset_id: str,
        data: dict[str, Any],
    ) -> RiskSnapshot:
        self._replace_current(session, RiskSnapshot, asset_id)
        record = RiskSnapshot(snapshot_id=snapshot_id, asset_id=asset_id, **data)
        session.add(record)
        session.flush()
        return record

    def replace_exposure(
        self,
        session: Session,
        *,
        snapshot_id: str,
        asset_id: str,
        data: dict[str, Any],
    ) -> ExposureAnalyticsSnapshot:
        self._replace_current(session, ExposureAnalyticsSnapshot, asset_id)
        record = ExposureAnalyticsSnapshot(snapshot_id=snapshot_id, asset_id=asset_id, **data)
        session.add(record)
        session.flush()
        return record

    def replace_score(
        self,
        session: Session,
        *,
        snapshot_id: str,
        asset_id: str,
        data: dict[str, Any],
    ) -> AssetScoreSnapshot:
        self._replace_current(session, AssetScoreSnapshot, asset_id)
        record = AssetScoreSnapshot(snapshot_id=snapshot_id, asset_id=asset_id, **data)
        session.add(record)
        session.flush()
        return record
