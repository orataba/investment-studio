from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.watchlists import (
    InstrumentAttributeDefinition,
    InstrumentAttributeValue,
)


class SQLAlchemyInstrumentAttributeRepository:
    def list_definitions(self, session: Session) -> Sequence[InstrumentAttributeDefinition]:
        return session.scalars(
            select(InstrumentAttributeDefinition).order_by(
                InstrumentAttributeDefinition.label
            )
        ).all()

    def create_definition(
        self,
        session: Session,
        *,
        attribute_key: str,
        label: str,
        description: str | None,
        data_type: str,
        options_json: list[str],
        is_groupable: bool,
        is_filterable: bool,
        is_view_column: bool,
        default_visible: bool,
    ) -> InstrumentAttributeDefinition:
        record = InstrumentAttributeDefinition(
            attribute_key=attribute_key,
            label=label,
            description=description,
            data_type=data_type,
            options_json=options_json,
            is_groupable=is_groupable,
            is_filterable=is_filterable,
            is_view_column=is_view_column,
            default_visible=default_visible,
            created_at=datetime.now(UTC).replace(microsecond=0),
        )
        session.add(record)
        session.flush()
        return record

    def get_values_for_asset(
        self,
        session: Session,
        asset_id: str,
    ) -> Sequence[InstrumentAttributeValue]:
        stmt = (
            select(InstrumentAttributeValue)
            .where(InstrumentAttributeValue.asset_id == asset_id)
            .order_by(
                InstrumentAttributeValue.attribute_key,
                InstrumentAttributeValue.adopted_at.desc(),
                InstrumentAttributeValue.instrument_attribute_value_id.desc(),
            )
        )
        return session.scalars(stmt).all()

    def add_value(
        self,
        session: Session,
        *,
        asset_id: str,
        attribute_key: str,
        value_json: object,
        source_record_id: str | None,
    ) -> InstrumentAttributeValue:
        record = InstrumentAttributeValue(
            asset_id=asset_id,
            attribute_key=attribute_key,
            value_json=value_json,
            effective_from=None,
            adopted_at=datetime.now(UTC).replace(microsecond=0),
            source_record_id=source_record_id,
        )
        session.add(record)
        session.flush()
        return record
