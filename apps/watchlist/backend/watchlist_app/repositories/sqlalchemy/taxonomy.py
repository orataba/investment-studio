from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchlist_app.db.models.watchlists import (
    InstrumentTaxonomyAssignment,
    InstrumentTaxonomyNode,
)
from watchlist_app.reference_data.fund_taxonomy import FUND_TAXONOMY_CODE


class SQLAlchemyTaxonomyRepository:
    def list_nodes(
        self,
        session: Session,
        *,
        taxonomy_code: str = FUND_TAXONOMY_CODE,
    ) -> Sequence[InstrumentTaxonomyNode]:
        stmt = (
            select(InstrumentTaxonomyNode)
            .where(InstrumentTaxonomyNode.taxonomy_code == taxonomy_code)
            .order_by(
                InstrumentTaxonomyNode.level_index,
                InstrumentTaxonomyNode.display_order,
                InstrumentTaxonomyNode.label,
            )
        )
        return session.scalars(stmt).all()

    def get_node(
        self,
        session: Session,
        *,
        node_id: str,
    ) -> InstrumentTaxonomyNode | None:
        return session.get(InstrumentTaxonomyNode, node_id)

    def get_assignment(
        self,
        session: Session,
        *,
        asset_id: str,
        taxonomy_code: str = FUND_TAXONOMY_CODE,
    ) -> InstrumentTaxonomyAssignment | None:
        stmt = select(InstrumentTaxonomyAssignment).where(
            InstrumentTaxonomyAssignment.asset_id == asset_id,
            InstrumentTaxonomyAssignment.taxonomy_code == taxonomy_code,
        )
        return session.scalars(stmt).first()

    def list_assignments(
        self,
        session: Session,
        *,
        taxonomy_code: str = FUND_TAXONOMY_CODE,
    ) -> Sequence[InstrumentTaxonomyAssignment]:
        stmt = (
            select(InstrumentTaxonomyAssignment)
            .where(InstrumentTaxonomyAssignment.taxonomy_code == taxonomy_code)
            .order_by(InstrumentTaxonomyAssignment.asset_id)
        )
        return session.scalars(stmt).all()

    def upsert_assignment(
        self,
        session: Session,
        *,
        asset_id: str,
        taxonomy_code: str = FUND_TAXONOMY_CODE,
        node_id: str | None,
        source_record_id: str | None,
    ) -> InstrumentTaxonomyAssignment:
        record = self.get_assignment(
            session,
            asset_id=asset_id,
            taxonomy_code=taxonomy_code,
        )
        now = datetime.now(UTC).replace(microsecond=0)
        if record is None:
            record = InstrumentTaxonomyAssignment(
                asset_id=asset_id,
                taxonomy_code=taxonomy_code,
                node_id=node_id,
                assigned_at=now,
                source_record_id=source_record_id,
            )
            session.add(record)
            session.flush()
            return record

        record.node_id = node_id
        record.assigned_at = now
        record.source_record_id = source_record_id
        session.flush()
        return record
