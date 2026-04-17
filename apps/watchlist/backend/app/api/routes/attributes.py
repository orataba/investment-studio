from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.contracts import (
    FundAttributesUpsertRequest,
    InstrumentAttributeDefinitionCreateRequest,
)
from app.api.presenters import present_attribute_definition, present_attribute_values
from app.db.session import get_db_session
from app.reference_data.watchlist_fields import build_attribute_field_definition
from app.repositories.sqlalchemy.assets import SQLAlchemyAssetRepository
from app.repositories.sqlalchemy.field_registry import SQLAlchemyFieldRegistryRepository
from app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository


router = APIRouter()
attribute_repository = SQLAlchemyInstrumentAttributeRepository()
field_registry_repository = SQLAlchemyFieldRegistryRepository()
asset_repository = SQLAlchemyAssetRepository()
read_model_repository = SQLAlchemyReadModelRepository()


def _require_asset(session: Session, asset_id: str):
    record = asset_repository.get(session, asset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return record


@router.get("/definitions")
def get_instrument_attribute_definitions(
    session: Session = Depends(get_db_session),
) -> list[dict[str, object]]:
    return [
        present_attribute_definition(item)
        for item in attribute_repository.list_definitions(session)
    ]


@router.post("/definitions")
def create_attribute_definition(
    payload: InstrumentAttributeDefinitionCreateRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    payload_data = payload.model_dump()
    record = attribute_repository.create_definition(
        session,
        attribute_key=payload_data["attribute_key"],
        label=payload_data["label"],
        description=payload_data.get("description"),
        data_type=payload_data["data_type"],
        options_json=payload_data.get("options", []),
        is_groupable=payload_data.get("is_groupable", True),
        is_filterable=payload_data.get("is_filterable", True),
        is_view_column=payload_data.get("is_view_column", True),
        default_visible=payload_data.get("default_visible", False),
    )
    field_payload = build_attribute_field_definition(payload_data)
    if field_registry_repository.get(session, field_payload["field_key"]) is None:
        field_registry_repository.create(
            session,
            field_key=field_payload["field_key"],
            label=field_payload["label"],
            description=field_payload["description"],
            category_code=field_payload["category_code"],
            data_type=field_payload["data_type"],
            formatter_code=field_payload["formatter_code"],
            sort_mode=field_payload["sort_mode"],
            filter_mode=field_payload["filter_mode"],
            group_mode=field_payload["group_mode"],
            asset_scope_json=field_payload["asset_scope_json"],
            product_scope_json=field_payload["product_scope_json"],
            availability_rule_json=field_payload["availability_rule_json"],
            source_domain=field_payload["source_domain"],
            source_metric_code=field_payload["source_metric_code"],
            default_width=field_payload["default_width"],
            default_visible=field_payload["default_visible"],
        )
    session.commit()
    return present_attribute_definition(record)


@router.get("/assets/{asset_id}")
def get_asset_attribute_values(
    asset_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_asset(session, asset_id)
    return present_attribute_values(
        asset_id,
        attribute_repository.list_definitions(session),
        attribute_repository.get_values_for_asset(session, asset_id),
    )


@router.post("/assets/{asset_id}")
def upsert_asset_attribute_values(
    asset_id: str,
    payload: FundAttributesUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_asset(session, asset_id)
    values = {item.attribute_key: item.value for item in payload.values}
    for key, value in values.items():
        attribute_repository.add_value(
            session,
            asset_id=asset_id,
            attribute_key=key,
            value_json=value,
            source_record_id="api",
        )
    current_values = present_attribute_values(
        asset_id,
        attribute_repository.list_definitions(session),
        attribute_repository.get_values_for_asset(session, asset_id),
    )
    read_model_repository.set_attributes_for_asset(
        session,
        asset_id=asset_id,
        attributes=current_values["values"],
        touched_at=datetime.now(UTC).replace(microsecond=0),
    )
    session.commit()
    return current_values | {"updated": True}
