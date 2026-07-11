from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchlist_app.db.models.analytics import (
    PerformanceSnapshot,
    ExposureAnalyticsSnapshot,
    RiskSnapshot,
)
from watchlist_app.db.models.scoring import InstrumentScoreSnapshot


class SQLAlchemySnapshotRepository:
    def get_current_performance(
        self,
        session: Session,
        instrument_id: str,
    ) -> PerformanceSnapshot | None:
        stmt = (
            select(PerformanceSnapshot)
            .where(
                PerformanceSnapshot.instrument_id == instrument_id,
                PerformanceSnapshot.is_current.is_(True),
            )
            .order_by(PerformanceSnapshot.as_of_date.desc())
        )
        return session.scalars(stmt).first()

    def list_current_performance(
        self,
        session: Session,
        instrument_ids: Sequence[str] | None = None,
    ) -> Sequence[PerformanceSnapshot]:
        stmt = select(PerformanceSnapshot).where(PerformanceSnapshot.is_current.is_(True))
        if instrument_ids is not None:
            if not instrument_ids:
                return []
            stmt = stmt.where(PerformanceSnapshot.instrument_id.in_(instrument_ids))
        stmt = stmt.order_by(PerformanceSnapshot.instrument_id, PerformanceSnapshot.as_of_date.desc())
        return session.scalars(stmt).all()

    def get_current_risk(self, session: Session, instrument_id: str) -> RiskSnapshot | None:
        stmt = (
            select(RiskSnapshot)
            .where(RiskSnapshot.instrument_id == instrument_id, RiskSnapshot.is_current.is_(True))
            .order_by(RiskSnapshot.as_of_date.desc())
        )
        return session.scalars(stmt).first()

    def list_current_risk(
        self,
        session: Session,
        instrument_ids: Sequence[str] | None = None,
    ) -> Sequence[RiskSnapshot]:
        stmt = select(RiskSnapshot).where(RiskSnapshot.is_current.is_(True))
        if instrument_ids is not None:
            if not instrument_ids:
                return []
            stmt = stmt.where(RiskSnapshot.instrument_id.in_(instrument_ids))
        stmt = stmt.order_by(RiskSnapshot.instrument_id, RiskSnapshot.as_of_date.desc())
        return session.scalars(stmt).all()

    def get_current_exposure(
        self,
        session: Session,
        instrument_id: str,
    ) -> ExposureAnalyticsSnapshot | None:
        stmt = (
            select(ExposureAnalyticsSnapshot)
            .where(
                ExposureAnalyticsSnapshot.instrument_id == instrument_id,
                ExposureAnalyticsSnapshot.is_current.is_(True),
            )
            .order_by(ExposureAnalyticsSnapshot.as_of_date.desc())
        )
        return session.scalars(stmt).first()

    def get_current_score(
        self,
        session: Session,
        instrument_id: str,
    ) -> InstrumentScoreSnapshot | None:
        stmt = (
            select(InstrumentScoreSnapshot)
            .where(InstrumentScoreSnapshot.instrument_id == instrument_id, InstrumentScoreSnapshot.is_current.is_(True))
            .order_by(InstrumentScoreSnapshot.as_of_date.desc())
        )
        return session.scalars(stmt).first()

    def _replace_current(self, session: Session, model_class, instrument_id: str) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        for current in session.scalars(
            select(model_class).where(
                model_class.instrument_id == instrument_id, model_class.is_current.is_(True)
            )
        ):
            current.is_current = False
            current.superseded_at = now
        session.flush()

    def replace_performance(
        self,
        session: Session,
        *,
        snapshot_id: str,
        instrument_id: str,
        data: dict[str, Any],
    ) -> PerformanceSnapshot:
        self._replace_current(session, PerformanceSnapshot, instrument_id)
        record = PerformanceSnapshot(snapshot_id=snapshot_id, instrument_id=instrument_id, **data)
        session.add(record)
        session.flush()
        return record

    def clear_performance(self, session: Session, *, instrument_id: str) -> None:
        self._replace_current(session, PerformanceSnapshot, instrument_id)

    def replace_risk(
        self,
        session: Session,
        *,
        snapshot_id: str,
        instrument_id: str,
        data: dict[str, Any],
    ) -> RiskSnapshot:
        self._replace_current(session, RiskSnapshot, instrument_id)
        record = RiskSnapshot(snapshot_id=snapshot_id, instrument_id=instrument_id, **data)
        session.add(record)
        session.flush()
        return record

    def clear_risk(self, session: Session, *, instrument_id: str) -> None:
        self._replace_current(session, RiskSnapshot, instrument_id)

    def replace_exposure(
        self,
        session: Session,
        *,
        snapshot_id: str,
        instrument_id: str,
        data: dict[str, Any],
    ) -> ExposureAnalyticsSnapshot:
        self._replace_current(session, ExposureAnalyticsSnapshot, instrument_id)
        record = ExposureAnalyticsSnapshot(snapshot_id=snapshot_id, instrument_id=instrument_id, **data)
        session.add(record)
        session.flush()
        return record

    def replace_score(
        self,
        session: Session,
        *,
        snapshot_id: str,
        instrument_id: str,
        data: dict[str, Any],
    ) -> InstrumentScoreSnapshot:
        self._replace_current(session, InstrumentScoreSnapshot, instrument_id)
        record = InstrumentScoreSnapshot(snapshot_id=snapshot_id, instrument_id=instrument_id, **data)
        session.add(record)
        session.flush()
        return record
