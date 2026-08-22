from __future__ import annotations

from datetime import date, datetime
from numbers import Real

from fastapi import APIRouter, Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import (
    FundAttributesUpsertRequest,
    InstrumentAttributeDefinitionCreateRequest,
    InstrumentSettingsUpsertRequest,
)
from watchlist_app.api.presenters import present_attribute_definition, present_attribute_values
from watchlist_app.db.session import get_db_session
from watchlist_app.reference_data.watchlist_fields import build_attribute_field_definition
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.field_registry import SQLAlchemyFieldRegistryRepository
from watchlist_app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from watchlist_app.repositories.sqlalchemy.taxonomy import SQLAlchemyTaxonomyRepository
from watchlist_app.services.canonical_recalc import CanonicalRecalcService
from watchlist_app.reference_data.instrument_taxonomy import INSTRUMENT_TAXONOMY_CODE
from watchlist_app.services.instrument_taxonomy import (
    SUPPORTED_TAXONOMY_INSTRUMENT_TYPES,
    build_taxonomy_context,
    taxonomy_node_supports_instrument,
)
from watchlist_app.services.instrument_resolution import equity_exchange_taxonomy_node


router = APIRouter()
attribute_repository = SQLAlchemyInstrumentAttributeRepository()
field_registry_repository = SQLAlchemyFieldRegistryRepository()
instrument_repository = SQLAlchemyInstrumentRepository()
taxonomy_repository = SQLAlchemyTaxonomyRepository()
canonical_recalc_service = CanonicalRecalcService()


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


def _validated_attribute_value(definition, value: object) -> object:
    if value is None:
        return None
    data_type = str(definition.data_type).strip().lower()
    options = [str(item) for item in definition.options_json or []]
    if data_type == "single_select":
        if not isinstance(value, str) or value not in options:
            raise ValueError(f"must be one of: {', '.join(options)}")
        return value
    if data_type == "multi_select":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("must be a list of strings")
        if len(set(value)) != len(value):
            raise ValueError("must not contain duplicate values")
        invalid = [item for item in value if item not in options]
        if invalid:
            raise ValueError(f"contains invalid values: {', '.join(invalid)}")
        return value
    if data_type == "number":
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError("must be a number")
        return value
    if data_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("must be true or false")
        return value
    if data_type in {"text", "string"}:
        if not isinstance(value, str):
            raise ValueError("must be text")
        return value
    if data_type == "date":
        if not isinstance(value, str):
            raise ValueError("must be an ISO date")
        date.fromisoformat(value)
        return value
    if data_type == "datetime":
        if not isinstance(value, str):
            raise ValueError("must be an ISO datetime")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("must include a timezone")
        return value
    raise ValueError(f"uses unsupported data type {data_type!r}")


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
        instrument_scope_json=payload_data.get(
            "instrument_scope_json", ["public_fund", "private_fund"]
        ),
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
    instrument = _require_asset(session, instrument_id)
    definitions = {
        item.attribute_key: item for item in attribute_repository.list_definitions(session)
    }
    keys = [item.attribute_key for item in payload.values]
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=422, detail="Each attribute may be updated only once per request.")
    values = {item.attribute_key: item.value for item in payload.values}
    unknown_keys = [key for key in values if key not in definitions]
    if unknown_keys:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown instrument attribute keys: {', '.join(sorted(unknown_keys))}.",
        )
    for key, value in values.items():
        definition = definitions[key]
        instrument_scope = {
            str(item).strip().lower() for item in definition.instrument_scope_json or []
        }
        if instrument_scope and str(instrument.instrument_type).strip().lower() not in instrument_scope:
            raise HTTPException(
                status_code=422,
                detail=f"Attribute {key!r} does not apply to {instrument.instrument_type} instruments.",
            )
        try:
            validated_value = _validated_attribute_value(definition, value)
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid value for attribute {key!r}: {error}.",
            ) from error
        attribute_repository.add_value(
            session,
            instrument_id=instrument_id,
            attribute_key=key,
            value_json=validated_value,
            effective_from=payload.effective_from,
            source_record_id=str(payload.source_record_id or "api").strip() or "api",
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
    execution = canonical_recalc_service.execute_recalc(
        session,
        instrument_id=instrument_id,
        job_type="performance",
        trigger_type="instrument_attribute_update",
        trigger_ref_type="instrument_attribute_value",
        trigger_ref_id=payload.source_record_id,
    )
    session.commit()
    return current_values | {
        "updated": True,
        "taxonomy": taxonomy_context,
        "recalculated": True,
        "execution": execution,
    }


@router.put("/instruments/{instrument_id}/settings")
def update_instrument_settings(
    instrument_id: str,
    payload: InstrumentSettingsUpsertRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    instrument = _require_asset(session, instrument_id)
    instrument_type = str(instrument.instrument_type or "").strip().lower()
    if instrument_type not in SUPPORTED_TAXONOMY_INSTRUMENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Settings are only available for public funds, private funds, ETFs, equities, and indexes.",
        )

    node_id = str(payload.taxonomy_node_id or "").strip() or None
    if instrument_type == "equity" and node_id != equity_exchange_taxonomy_node(instrument):
        raise HTTPException(
            status_code=422,
            detail="Equity taxonomy is maintained from the Registry exchange identity.",
        )
    node = taxonomy_repository.get_node(session, node_id=node_id) if node_id else None
    if node_id and node is None:
        raise HTTPException(status_code=404, detail="Instrument taxonomy node not found")
    if node is not None:
        if node.taxonomy_code != INSTRUMENT_TAXONOMY_CODE:
            raise HTTPException(status_code=400, detail="Invalid taxonomy node")
        if not taxonomy_node_supports_instrument(
            instrument_type=instrument_type,
            node=node,
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{instrument_type} instruments cannot be assigned to a "
                    f"{node.instrument_type} taxonomy node"
                ),
            )

    definitions = {
        item.attribute_key: item for item in attribute_repository.list_definitions(session)
    }
    status_definition = definitions.get("coverage_status")
    if status_definition is None:
        raise HTTPException(status_code=500, detail="Investment status definition is missing")
    instrument_scope = {
        str(item).strip().lower()
        for item in status_definition.instrument_scope_json or []
    }
    if instrument_scope and instrument_type not in instrument_scope:
        raise HTTPException(
            status_code=422,
            detail=f"Investment status does not apply to {instrument_type} instruments.",
        )
    try:
        coverage_status = _validated_attribute_value(
            status_definition,
            payload.coverage_status,
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid investment status: {error}.",
        ) from error

    current_assignment = taxonomy_repository.get_assignment(
        session,
        instrument_id=instrument_id,
    )
    current_values = present_attribute_values(
        instrument_id,
        list(definitions.values()),
        attribute_repository.get_values_for_asset(session, instrument_id),
    )
    taxonomy_changed = (current_assignment.node_id if current_assignment else None) != node_id
    status_changed = current_values["values"].get("coverage_status") != coverage_status
    source_record_id = str(payload.updated_by or "terminal_ui").strip() or "terminal_ui"

    if taxonomy_changed:
        taxonomy_repository.upsert_assignment(
            session,
            instrument_id=instrument_id,
            taxonomy_code=INSTRUMENT_TAXONOMY_CODE,
            node_id=node_id,
            source_record_id=source_record_id,
        )
    if status_changed:
        attribute_repository.add_value(
            session,
            instrument_id=instrument_id,
            attribute_key="coverage_status",
            value_json=coverage_status,
            effective_from=None,
            source_record_id=source_record_id,
        )

    execution = None
    if taxonomy_changed or status_changed:
        execution = canonical_recalc_service.execute_recalc(
            session,
            instrument_id=instrument_id,
            job_type="performance",
            trigger_type="instrument_settings_update",
            trigger_ref_type="instrument_settings",
            trigger_ref_id=source_record_id,
        )

    next_values = present_attribute_values(
        instrument_id,
        list(definitions.values()),
        attribute_repository.get_values_for_asset(session, instrument_id),
    )
    taxonomy_context = _taxonomy_context_for_asset(
        session,
        instrument_id=instrument_id,
    )
    session.commit()
    return next_values | {
        "updated": taxonomy_changed or status_changed,
        "taxonomy_updated": taxonomy_changed,
        "status_updated": status_changed,
        "taxonomy": taxonomy_context,
        "recalculated": execution is not None,
        "execution": execution,
    }
