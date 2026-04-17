from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models.facts import HoldingPosition, HoldingSnapshot, NavFact


class SQLAlchemyFactsRepository:
    def list_nav_facts(
        self,
        session: Session,
        *,
        asset_id: str,
        nav_type: str | None = None,
        primary_only: bool = False,
    ) -> Sequence[NavFact]:
        stmt = select(NavFact).where(NavFact.asset_id == asset_id)
        if nav_type is not None:
            stmt = stmt.where(NavFact.nav_type == nav_type)
        if primary_only:
            stmt = stmt.where(NavFact.is_primary.is_(True))
        stmt = stmt.order_by(NavFact.as_of_date, NavFact.nav_fact_id)
        return session.scalars(stmt).all()

    def upsert_nav_fact(
        self,
        session: Session,
        *,
        asset_id: str,
        as_of_date,
        nav_type: str,
        value: Decimal,
        currency: str,
        frequency: str | None,
        is_primary: bool,
        source_record_id: str | None,
        observation_id: str | None,
    ) -> NavFact:
        stmt = select(NavFact).where(
            NavFact.asset_id == asset_id,
            NavFact.as_of_date == as_of_date,
            NavFact.nav_type == nav_type,
            NavFact.currency == currency,
        )
        record = session.scalars(stmt).first()
        if record is None:
            record = NavFact(
                asset_id=asset_id,
                as_of_date=as_of_date,
                nav_type=nav_type,
                value=value,
                currency=currency,
                frequency=frequency,
                is_primary=is_primary,
                source_record_id=source_record_id,
                observation_id=observation_id,
                adopted_at=datetime.now(UTC).replace(microsecond=0),
            )
            session.add(record)
            session.flush()
            return record
        record.value = value
        record.frequency = frequency
        record.is_primary = is_primary
        record.source_record_id = source_record_id
        record.observation_id = observation_id
        record.adopted_at = datetime.now(UTC).replace(microsecond=0)
        session.flush()
        return record

    def delete_nav_facts_for_date(
        self,
        session: Session,
        *,
        asset_id: str,
        as_of_date,
        primary_only: bool = True,
    ) -> int:
        stmt = select(NavFact).where(
            NavFact.asset_id == asset_id,
            NavFact.as_of_date == as_of_date,
        )
        if primary_only:
            stmt = stmt.where(NavFact.is_primary.is_(True))
        records = list(session.scalars(stmt).all())
        for record in records:
            session.delete(record)
        session.flush()
        return len(records)

    def get_current_holding_snapshot(
        self,
        session: Session,
        *,
        asset_id: str,
    ) -> HoldingSnapshot | None:
        stmt = (
            select(HoldingSnapshot)
            .options(selectinload(HoldingSnapshot.positions))
            .where(HoldingSnapshot.asset_id == asset_id, HoldingSnapshot.is_current.is_(True))
            .order_by(HoldingSnapshot.as_of_date.desc(), HoldingSnapshot.calculated_at.desc())
        )
        return session.scalars(stmt).first()

    def replace_current_holding_snapshot(
        self,
        session: Session,
        *,
        holding_snapshot_id: str,
        asset_id: str,
        as_of_date,
        source_cutoff_at,
        methodology_version: str,
        input_hash: str,
        source_record_id: str | None,
        positions: list[dict[str, object]],
    ) -> HoldingSnapshot:
        now = datetime.now(UTC).replace(microsecond=0)
        for current in session.scalars(
            select(HoldingSnapshot).where(
                HoldingSnapshot.asset_id == asset_id,
                HoldingSnapshot.is_current.is_(True),
            )
        ):
            current.is_current = False
            current.superseded_at = now

        record = session.get(HoldingSnapshot, holding_snapshot_id)
        if record is None:
            record = HoldingSnapshot(
                holding_snapshot_id=holding_snapshot_id,
                asset_id=asset_id,
                as_of_date=as_of_date,
                source_cutoff_at=source_cutoff_at,
                methodology_version=methodology_version,
                input_hash=input_hash,
                calculated_at=now,
                superseded_at=None,
                is_current=True,
                source_record_id=source_record_id,
                positions=[],
            )
            session.add(record)
            session.flush()
        else:
            record.as_of_date = as_of_date
            record.source_cutoff_at = source_cutoff_at
            record.methodology_version = methodology_version
            record.input_hash = input_hash
            record.calculated_at = now
            record.superseded_at = None
            record.is_current = True
            record.source_record_id = source_record_id
            for existing in list(record.positions):
                session.delete(existing)
            session.flush()

        for item in positions:
            session.add(
                HoldingPosition(
                    holding_snapshot_id=record.holding_snapshot_id,
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
        session.refresh(record)
        return self.get_current_holding_snapshot(session, asset_id=asset_id) or record
