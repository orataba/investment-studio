from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import (
    FundAttributesUpsertRequest,
    InstrumentAttributeDefinitionCreateRequest,
)
from watchlist_app.api.presenters import present_attribute_definition, present_attribute_values
from watchlist_app.db.session import get_db_session
from watchlist_app.reference_data.watchlist_fields import build_attribute_field_definition
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.field_registry import SQLAlchemyFieldRegistryRepository
from watchlist_app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.taxonomy import SQLAlchemyTaxonomyRepository
from watchlist_app.services.fund_taxonomy import (
    build_taxonomy_context,
    merge_taxonomy_attributes,
)


router = APIRouter()
attribute_repository = SQLAlchemyInstrumentAttributeRepository()
field_registry_repository = SQLAlchemyFieldRegistryRepository()
instrument_repository = SQLAlchemyInstrumentRepository()
read_model_repository = SQLAlchemyReadModelRepository()
taxonomy_repository = SQLAlchemyTaxonomyRepository()


def _require_asset(session: Session, instrument_id: str):
    record = instrument_repository.get(session, instrument_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return record


def _taxonomy_context_for_asset(
    session: Session,
    *,
    instrument_id: str,
) -> dict[str, object]:
    assignment = taxonomy_repository.get_assignment(session, instrument_id=instrument_id)
    node = (
        taxonomy_repository.get_node(session, node_id=str(assignment.node_id))
        if assignment is not None and assignment.node_id
        else None
    )
    return build_taxonomy_context(node)


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
        domain_code=payload_data["domain_code"],
        group_code=payload_data["group_code"],
        display_order=payload_data.get("display_order", 999),
        options_json=payload_data.get("options", []),
        instrument_scope_json=payload_data.get("instrument_scope_json", ["fund"]),
        applicability_json=payload_data.get("applicability_json", {}),
        rubric_json=payload_data.get("rubric_json", {}),
        is_groupable=payload_data.get("is_groupable", True),
        is_filterable=payload_data.get("is_filterable", True),
        is_view_column=payload_data.get("is_view_column", True),
        default_visible=payload_data.get("default_visible", False),
        required_for_monitoring=payload_data.get("required_for_monitoring", False),
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
            instrument_scope_json=field_payload["instrument_scope_json"],
            product_scope_json=field_payload["product_scope_json"],
            availability_rule_json=field_payload["availability_rule_json"],
            source_domain=field_payload["source_domain"],
            source_metric_code=field_payload["source_metric_code"],
            default_width=field_payload["default_width"],
            default_visible=field_payload["default_visible"],
        )
    session.commit()
    return present_attribute_definition(record)


@router.get("/instruments/{instrument_id}")
def get_instrument_attribute_values(
    instrument_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_asset(session, instrument_id)
    payload = present_attribute_values(
        instrument_id,
        attribute_repository.list_definitions(session),
        attribute_repository.get_values_for_asset(session, instrument_id),
    )
    payload["taxonomy"] = _taxonomy_context_for_asset(
        session,
        instrument_id=instrument_id,
    )
    return payload


@router.post("/instruments/{instrument_id}")
def upsert_instrument_attribute_values(
    instrument_id: str,
    payload: FundAttributesUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_asset(session, instrument_id)
    definitions = {
        item.attribute_key: item for item in attribute_repository.list_definitions(session)
    }
    values = {item.attribute_key: item.value for item in payload.values}
    unknown_keys = [key for key in values if key not in definitions]
    if unknown_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown instrument attribute keys: {', '.join(sorted(unknown_keys))}.",
        )
    for key, value in values.items():
        attribute_repository.add_value(
            session,
            instrument_id=instrument_id,
            attribute_key=key,
            value_json=value,
            source_record_id="api",
        )
    current_values = present_attribute_values(
        instrument_id,
        list(definitions.values()),
        attribute_repository.get_values_for_asset(session, instrument_id),
    )
    taxonomy_context = _taxonomy_context_for_asset(
        session,
        instrument_id=instrument_id,
    )
    read_model_repository.set_attributes_for_asset(
        session,
        instrument_id=instrument_id,
        attributes=merge_taxonomy_attributes(
            taxonomy_context=taxonomy_context,
            instrument_attributes=current_values["values"],
        ),
        touched_at=datetime.now(UTC).replace(microsecond=0),
    )
    session.commit()
    return current_values | {"updated": True, "taxonomy": taxonomy_context}
