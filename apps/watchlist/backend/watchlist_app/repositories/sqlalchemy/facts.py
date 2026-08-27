from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from watchlist_app.db.models.facts import HoldingPosition, HoldingSnapshot, NavFact


@dataclass(frozen=True)
class HoldingSnapshotWriteResult:
    record: HoldingSnapshot
    created: bool
    became_current: bool


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SQLAlchemyFactsRepository:
    def list_nav_facts(
        self,
        session: Session,
        *,
        instrument_id: str,
        nav_type: str | None = None,
        primary_only: bool = False,
    ) -> Sequence[NavFact]:
        stmt = select(NavFact).where(NavFact.instrument_id == instrument_id)
        if nav_type is not None:
            stmt = stmt.where(NavFact.nav_type == nav_type)
        if primary_only:
            stmt = stmt.where(NavFact.is_primary.is_(True))
        stmt = stmt.order_by(NavFact.as_of_date, NavFact.nav_fact_id)
        return session.scalars(stmt).all()

    def get_current_holding_snapshot(
        self,
        session: Session,
        *,
        instrument_id: str,
    ) -> HoldingSnapshot | None:
        stmt = (
            select(HoldingSnapshot)
            .options(selectinload(HoldingSnapshot.positions))
            .where(HoldingSnapshot.instrument_id == instrument_id, HoldingSnapshot.is_current.is_(True))
            .order_by(HoldingSnapshot.as_of_date.desc(), HoldingSnapshot.calculated_at.desc())
        )
        return session.scalars(stmt).first()

    def append_holding_snapshot(
        self,
        session: Session,
        *,
        holding_snapshot_id: str,
        instrument_id: str,
        as_of_date,
        source_cutoff_at,
        methodology_version: str,
        input_hash: str,
        source_record_id: str | None,
        positions: list[dict[str, object]],
    ) -> HoldingSnapshotWriteResult:
        now = datetime.now(UTC).replace(microsecond=0)
        existing = session.scalar(
            select(HoldingSnapshot)
            .options(selectinload(HoldingSnapshot.positions))
            .where(
                HoldingSnapshot.instrument_id == instrument_id,
                HoldingSnapshot.input_hash == input_hash,
            )
        )
        if existing is not None:
            return HoldingSnapshotWriteResult(
                record=existing,
                created=False,
                became_current=False,
            )

        current = self.get_current_holding_snapshot(
            session,
            instrument_id=instrument_id,
        )
        becomes_current = current is None or (
            as_of_date,
            _as_utc(source_cutoff_at),
        ) >= (
            current.as_of_date,
            _as_utc(current.source_cutoff_at),
        )
        if becomes_current and current is not None:
            current.is_current = False
            current.superseded_at = now
            session.flush()

        record = HoldingSnapshot(
            holding_snapshot_id=holding_snapshot_id,
            instrument_id=instrument_id,
            as_of_date=as_of_date,
            source_cutoff_at=source_cutoff_at,
            methodology_version=methodology_version,
            input_hash=input_hash,
            calculated_at=now,
            superseded_at=None,
            is_current=becomes_current,
            source_record_id=source_record_id,
            positions=[],
        )
        session.add(record)

        for item in positions:
            record.positions.append(
                HoldingPosition(
                    holding_name=str(item["holding_name"]),
                    holding_type=str(item["holding_type"]),
                    security_identifier=item.get("security_identifier"),
                    issuer_name=item.get("issuer_name"),
                    issuer_type=item.get("issuer_type"),
                    portfolio_weight=item.get("portfolio_weight"),
                    market_value=item.get("market_value"),
                    quantity=item.get("quantity"),
                    currency=item.get("currency"),
                    market_price=item.get("market_price"),
                    share_change_pct=item.get("share_change_pct"),
                    maturity_date=item.get("maturity_date"),
                    coupon_rate=item.get("coupon_rate"),
                    credit_rating=item.get("credit_rating"),
                    effective_duration=item.get("effective_duration"),
                    modified_duration=item.get("modified_duration"),
                    yield_to_worst=item.get("yield_to_worst"),
                    sector=item.get("sector"),
                    country_code=item.get("country_code"),
                )
            )
        session.flush()
        return HoldingSnapshotWriteResult(
            record=record,
            created=True,
            became_current=becomes_current,
        )
