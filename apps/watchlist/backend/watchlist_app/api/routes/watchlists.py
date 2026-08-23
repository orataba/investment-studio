from __future__ import annotations

from datetime import datetime
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from watchlist_app.api.contracts import (
    WatchlistCreateRequest,
    WatchlistItemsCopyRequest,
    WatchlistItemsCreateRequest,
    WatchlistItemsDeleteRequest,
    WatchlistItemsMoveRequest,
    WatchlistReorderRequest,
    WatchlistViewCreateRequest,
)
from watchlist_app.api.presenters import (
    present_default_filter_summary,
    present_group_by_options,
    present_watchlist,
    present_watchlist_view,
)
from watchlist_app.db.models.watchlists import Watchlist
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.instruments import SQLAlchemyInstrumentRepository
from watchlist_app.repositories.sqlalchemy.field_registry import SQLAlchemyFieldRegistryRepository
from watchlist_app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.taxonomy import SQLAlchemyTaxonomyRepository
from watchlist_app.repositories.sqlalchemy.watchlists import (
    SYSTEM_WATCHLIST_BY_ID,
    SYSTEM_WATCHLIST_IDS,
    SYSTEM_WATCHLIST_SPECS,
    SQLAlchemyWatchlistRepository,
    SystemWatchlistSpec,
)
from watchlist_app.services.canonical_recalc import CanonicalRecalcService
from watchlist_app.services.instrument_taxonomy import (
    build_taxonomy_context,
    merge_taxonomy_attributes,
)
from watchlist_app.services.read_models import (
    build_watchlist_row_materialization,
    collapse_latest_attribute_values,
)
from watchlist_app.services.instrument_resolution import (
    local_detail_view_type,
    resolve_watchlist_instrument,
    sync_local_instrument,
)
from watchlist_app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
    get_shared_instrument,
    list_shared_instruments,
)
from watchlist_app.services.watchlist_query_contract import (
    WatchlistQueryContractError,
    validate_watchlist_query_contract,
)


router = APIRouter()
watchlist_repository = SQLAlchemyWatchlistRepository()
field_registry_repository = SQLAlchemyFieldRegistryRepository()
instrument_repository = SQLAlchemyInstrumentRepository()
attribute_repository = SQLAlchemyInstrumentAttributeRepository()
read_model_repository = SQLAlchemyReadModelRepository()
taxonomy_repository = SQLAlchemyTaxonomyRepository()
canonical_recalc_service = CanonicalRecalcService()
MAX_WATCHLIST_ID_ATTEMPTS = 10


def _is_system_watchlist(watchlist_id: str) -> bool:
    return watchlist_id in SYSTEM_WATCHLIST_IDS


def _slugify_watchlist_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    slug = slug.strip("-")
    return slug or "watchlist"


def _slugify_watchlist_view_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    slug = slug.strip("-")
    return slug or "view"


def _generate_watchlist_id(session: Session, name: str) -> str:
    base = _slugify_watchlist_name(name)
    candidate = base
    suffix = 2
    while watchlist_repository.get(session, candidate) is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _generate_watchlist_view_id(
    session: Session,
    *,
    watchlist_id: str,
    name: str,
) -> str:
    base = _slugify_watchlist_view_name(name)
    candidate = base
    suffix = 2
    while (
        watchlist_repository.get_view(
            session,
            watchlist_id=watchlist_id,
            view_id=candidate,
        )
        is not None
    ):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _require_watchlist(session: Session, watchlist_id: str):
    record = watchlist_repository.get(session, watchlist_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return record


def _field_supports_instrument_types(
    instrument_scope: list[str] | None,
    instrument_types: set[str],
    *,
    require_all: bool,
) -> bool:
    normalized_scope = {
        str(value).strip().lower()
        for value in (instrument_scope or [])
        if str(value).strip()
    }
    if not normalized_scope:
        return True
    if not instrument_types:
        return False
    return normalized_scope.issuperset(instrument_types) if require_all else bool(
        normalized_scope.intersection(instrument_types)
    )


def _primary_shared_identifier(
    shared_instrument: dict[str, object],
) -> tuple[str | None, str | None]:
    identifiers = shared_instrument.get("identifiers")
    if not isinstance(identifiers, list):
        return None, None

    primary = next(
        (
            item
            for item in identifiers
            if isinstance(item, dict) and bool(item.get("is_primary"))
        ),
        None,
    )
    fallback = next((item for item in identifiers if isinstance(item, dict)), None)
    candidate = primary or fallback
    if not isinstance(candidate, dict):
        return None, None

    identifier_value = str(candidate.get("identifier_value") or "").strip() or None
    identifier_type = str(candidate.get("identifier_type") or "").strip() or None
    return identifier_type, identifier_value


def _supports_local_detail(shared_instrument: dict[str, object]) -> bool:
    instrument_type = str(shared_instrument.get("instrument_type") or "")
    return local_detail_view_type(instrument_type) is not None


def _ensure_local_instrument_detail(
    session: Session,
    shared_instrument: dict[str, object],
) -> str | None:
    if not _supports_local_detail(shared_instrument):
        return None

    instrument_registry_id = str(shared_instrument.get("instrument_id") or "").strip()
    if not instrument_registry_id:
        return None

    detail_view_type = local_detail_view_type(
        str(shared_instrument.get("instrument_type") or "")
    )
    if detail_view_type is None:
        return None

    sync_local_instrument(session, shared_instrument)
    return instrument_registry_id


def _ensure_required_columns(columns: list[dict[str, object]]) -> list[dict[str, object]]:
    required = ["instrument_name"]
    existing = [str(item.get("field_key")) for item in columns]
    merged: list[dict[str, object]] = []
    display_order = 0
    for field_key in required:
        if field_key not in existing:
            merged.append(
                {
                    "field_key": field_key,
                    "display_order": display_order,
                    "width": 320,
                    "is_visible": True,
                }
            )
            display_order += 1
    for item in columns:
        merged.append(
            {
                "field_key": str(item.get("field_key")),
                "display_order": display_order,
                "width": item.get("width"),
                "is_visible": bool(item.get("is_visible", True)),
                "pin_side": item.get("pin_side"),
            }
        )
        display_order += 1
    return merged


def _validate_saved_view_contract(
    session: Session,
    payload: WatchlistViewCreateRequest,
) -> None:
    fields = {item.field_key: item for item in field_registry_repository.list_fields(session)}
    try:
        validate_watchlist_query_contract(
            fields,
            selected_fields=[item.field_key for item in payload.columns],
            filters=payload.default_filters,
            sort_rules=[item.model_dump() for item in payload.default_sort],
            group_by=payload.default_group_by,
            advanced_filters=(
                payload.default_advanced_filters.model_dump()
                if payload.default_advanced_filters
                else None
            ),
        )
    except WatchlistQueryContractError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _normalize_instrument_ids(instrument_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    for instrument_id in instrument_ids:
        candidate = instrument_id.strip()
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _assert_source_membership(
    *,
    source_watchlist: object,
    instrument_ids: list[str],
) -> None:
    source_instrument_ids = {
        str(item.instrument_id).strip()
        for item in getattr(source_watchlist, "items", [])
        if str(item.instrument_id).strip()
    }
    missing_instrument_ids = [
        instrument_id for instrument_id in instrument_ids if instrument_id not in source_instrument_ids
    ]
    if missing_instrument_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                "Instruments not found in source watchlist: "
                f"{', '.join(missing_instrument_ids)}."
            ),
        )


def _assert_mutable_watchlist(watchlist_id: str) -> None:
    if _is_system_watchlist(watchlist_id):
        spec = SYSTEM_WATCHLIST_BY_ID[watchlist_id]
        raise HTTPException(
            status_code=400,
            detail=f"{spec.name} is system-maintained and cannot be manually edited.",
        )


def _create_watchlist_with_retry(
    session: Session,
    *,
    name: str,
    description: str | None,
    default_view_id: str,
):
    last_error: IntegrityError | None = None
    for _ in range(MAX_WATCHLIST_ID_ATTEMPTS):
        watchlist_id = _generate_watchlist_id(session, name)
        try:
            return watchlist_repository.create(
                session,
                watchlist_id=watchlist_id,
                name=name,
                description=description,
                owner_type="team",
                owner_id="investment-team",
                default_view_id=default_view_id,
            )
        except IntegrityError as error:
            session.rollback()
            last_error = error
    raise HTTPException(
        status_code=409,
        detail="Unable to allocate a unique watchlist id for this name.",
    ) from last_error


def _duplicate_watchlist_with_retry(
    session: Session,
    *,
    source_watchlist_id: str,
    copied_name: str,
):
    last_error: IntegrityError | None = None
    for _ in range(MAX_WATCHLIST_ID_ATTEMPTS):
        copied_id = _generate_watchlist_id(session, copied_name)
        try:
            return watchlist_repository.duplicate(
                session,
                source_watchlist_id=source_watchlist_id,
                watchlist_id=copied_id,
                name=copied_name,
            )
        except IntegrityError as error:
            session.rollback()
            last_error = error
    raise HTTPException(
        status_code=409,
        detail="Unable to allocate a unique watchlist id for this copy.",
    ) from last_error


def _create_watchlist_view_with_retry(
    session: Session,
    *,
    watchlist_id: str,
    name: str,
    description: str | None,
    default_group_by: str | None,
    default_sort: list[dict[str, object]],
    default_filters: dict[str, object],
    default_advanced_filter: dict[str, object],
    columns: list[dict[str, object]],
):
    last_error: IntegrityError | None = None
    for _ in range(MAX_WATCHLIST_ID_ATTEMPTS):
        if any(
            item.name.strip().casefold() == name.casefold()
            for item in watchlist_repository.list_views(session, watchlist_id)
        ):
            raise HTTPException(
                status_code=409,
                detail="Watchlist view name already exists",
            )
        view_id = _generate_watchlist_view_id(
            session,
            watchlist_id=watchlist_id,
            name=name,
        )
        try:
            return watchlist_repository.create_view(
                session,
                watchlist_id=watchlist_id,
                view_id=view_id,
                name=name,
                description=description,
                kind="custom",
                default_group_by=default_group_by,
                default_sort=default_sort,
                default_filters=default_filters,
                default_advanced_filter=default_advanced_filter,
                columns=columns,
            )
        except IntegrityError as error:
            session.rollback()
            last_error = error
    raise HTTPException(
        status_code=409,
        detail="Unable to allocate a unique watchlist view id for this name.",
    ) from last_error


def _materialize_watchlist_rows(
    session: Session,
    *,
    watchlist_id: str,
    instrument_ids: list[str],
) -> None:
    for requested_instrument_id in instrument_ids:
        instrument = resolve_watchlist_instrument(session, instrument_id=requested_instrument_id)
        canonical_instrument_id = (
            str(instrument.get("canonical_instrument_id") or requested_instrument_id).strip()
            if isinstance(instrument, dict)
            else requested_instrument_id
        )
        instrument_type = (
            str(instrument.get("instrument_type") or "other")
            if isinstance(instrument, dict)
            else "other"
        )
        if (
            read_model_repository.get_watchlist_row(
                session,
                watchlist_id=watchlist_id,
                instrument_id=canonical_instrument_id,
            )
            is not None
        ):
            continue

        source_row = read_model_repository.find_any_watchlist_row_for_asset(
            session,
            canonical_instrument_id,
        )
        summary_record = read_model_repository.get_summary(session, canonical_instrument_id)
        summary_payload = summary_record.payload_json if summary_record is not None else {}
        risk_record = read_model_repository.get_risk(session, canonical_instrument_id)
        risk_payload = risk_record.payload_json if risk_record is not None else {}
        freshness = summary_payload.get("freshness", {})
        instrument = instrument_repository.get(session, canonical_instrument_id)
        attributes = collapse_latest_attribute_values(
            attribute_repository.get_values_for_asset(session, canonical_instrument_id)
        )
        assignment = taxonomy_repository.get_assignment(session, instrument_id=canonical_instrument_id)
        node = (
            taxonomy_repository.get_node(session, node_id=str(assignment.node_id))
            if assignment is not None and assignment.node_id
            else None
        )
        taxonomy_context = build_taxonomy_context(node)
        row_attributes = merge_taxonomy_attributes(
            taxonomy_context=taxonomy_context,
            instrument_attributes=attributes,
        )
        if isinstance(risk_payload, dict) and risk_payload.get("current_drawdown") is not None:
            row_attributes["current_drawdown"] = risk_payload["current_drawdown"]
        display_name = (
            instrument.instrument_name
            if instrument is not None
            else summary_payload.get("instrument_name")
            or (
                str(instrument.get("instrument_name") or canonical_instrument_id)
                if isinstance(instrument, dict)
                else canonical_instrument_id
            )
        )
        read_model_repository.upsert_watchlist_row(
            session,
            watchlist_id=watchlist_id,
            instrument_id=canonical_instrument_id,
            data=build_watchlist_row_materialization(
                watchlist_id=watchlist_id,
                instrument_id=canonical_instrument_id,
                instrument_type=instrument_type,
                source_row=source_row,
                display_name=display_name,
                share_class=None,
                ticker_or_isin=(
                    instrument.primary_identifier_value
                    if instrument is not None
                    else (
                        str(instrument.get("primary_identifier") or "").strip() or None
                        if isinstance(instrument, dict)
                        else None
                    )
                ),
                management_firm_name=None,
                attributes=row_attributes,
                freshness_status=str(
                    (
                        freshness.get("data_freshness_status")
                        if isinstance(freshness, dict)
                        else None
                    )
                    or (source_row.data_freshness_status if source_row is not None else None)
                    or "pending_recalc"
                ),
                last_fact_update_at=(
                    _parse_datetime(freshness.get("last_fact_update_at"))
                    if isinstance(freshness, dict)
                    else None
                ),
                last_recalculated_at=(
                    _parse_datetime(freshness.get("last_recalculated_at"))
                    if isinstance(freshness, dict)
                    else None
                )
                or (source_row.last_recalculated_at if source_row is not None else None),
                last_successful_snapshot_at=(
                    _parse_datetime(freshness.get("last_successful_snapshot_at"))
                    if isinstance(freshness, dict)
                    else None
                )
                or (
                    source_row.last_successful_snapshot_at
                    if source_row is not None
                    else None
                ),
                staleness_reason=(
                    source_row.staleness_reason
                    if source_row is not None
                    else "Watchlist membership added; derived columns pending recalculation."
                ),
            ),
        )


def _sync_system_watchlist(
    session: Session,
    *,
    spec: SystemWatchlistSpec,
    existing_record: Watchlist | None = None,
) -> Watchlist:
    record = (
        existing_record
        if existing_record is not None
        else watchlist_repository.get(session, spec.watchlist_id)
    )
    if record is None:
        record = watchlist_repository.ensure_system_watchlist(session, spec)

    try:
        shared_instruments = list_shared_instruments(
            instrument_type=spec.instrument_type,
            limit=None,
        )
    except SharedInstrumentRegistryError:
        return record

    try:
        with session.begin_nested():
            active_instrument_ids: list[str] = []
            for shared_instrument in shared_instruments:
                detail_instrument_id = _ensure_local_instrument_detail(
                    session,
                    shared_instrument,
                )
                if (
                    detail_instrument_id
                    and detail_instrument_id not in active_instrument_ids
                ):
                    active_instrument_ids.append(detail_instrument_id)

            refreshed_record = (
                watchlist_repository.get(session, spec.watchlist_id)
                or record
            )
            existing_instrument_ids = {
                str(item.instrument_id).strip()
                for item in refreshed_record.items
                if str(item.instrument_id).strip()
            }
            active_instrument_id_set = set(active_instrument_ids)
            stale_instrument_ids = [
                instrument_id
                for instrument_id in existing_instrument_ids
                if instrument_id not in active_instrument_id_set
            ]
            if stale_instrument_ids:
                watchlist_repository.delete_items(
                    session,
                    watchlist_id=spec.watchlist_id,
                    instrument_ids=stale_instrument_ids,
                )
                read_model_repository.delete_watchlist_rows(
                    session,
                    watchlist_id=spec.watchlist_id,
                    instrument_ids=stale_instrument_ids,
                )

            created = watchlist_repository.add_items(
                session,
                watchlist_id=spec.watchlist_id,
                instrument_ids=active_instrument_ids,
                added_by="system",
            )
            existing_rows = {
                row.instrument_id
                for row in read_model_repository.list_watchlist_rows(
                    session,
                    spec.watchlist_id,
                )
            }
            created_instrument_ids = {item.instrument_id for item in created}
            materialize_instrument_ids = [
                instrument_id
                for instrument_id in active_instrument_ids
                if instrument_id not in existing_rows
                or instrument_id in created_instrument_ids
            ]
            _materialize_watchlist_rows(
                session,
                watchlist_id=spec.watchlist_id,
                instrument_ids=materialize_instrument_ids,
            )
    except SharedInstrumentRegistryError:
        # Roll back the savepoint as one unit. In particular, never persist a
        # membership addition/deletion without its matching read-model row;
        # otherwise the ID probe would consider the broken state reconciled.
        session.expire(record, ["items"])
        return record
    # The session deliberately keeps objects alive across commits. Expire the
    # loaded collection after bulk membership reconciliation so the response
    # cannot report a stale item count after additions or archival removals.
    session.expire(refreshed_record, ["items"])
    return refreshed_record


@router.get("")
def list_watchlists(session: Session = Depends(get_db_session)) -> list[dict[str, object]]:
    records = list(watchlist_repository.list(session))
    by_id = {record.watchlist_id: record for record in records}
    for spec in SYSTEM_WATCHLIST_SPECS:
        _sync_system_watchlist(
            session,
            spec=spec,
            existing_record=by_id.get(spec.watchlist_id),
        )
    session.commit()
    records = list(watchlist_repository.list(session))
    return [present_watchlist(item) for item in records]


@router.post("")
def create_watchlist_record(
    payload: WatchlistCreateRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Watchlist name is required.")
    default_view_id = "overview"
    record = _create_watchlist_with_retry(
        session,
        name=name,
        description=payload.description,
        default_view_id=default_view_id,
    )
    session.commit()
    return present_watchlist(
        watchlist_repository.get(session, record.watchlist_id) or record
    )


@router.post("/reorder")
def reorder_watchlist_records(
    payload: WatchlistReorderRequest,
    session: Session = Depends(get_db_session),
) -> list[dict[str, object]]:
    for spec in SYSTEM_WATCHLIST_SPECS:
        _sync_system_watchlist(session, spec=spec)
    ordered_watchlist_ids = [
        *(spec.watchlist_id for spec in SYSTEM_WATCHLIST_SPECS),
        *[
            watchlist_id
            for watchlist_id in payload.watchlist_ids
            if watchlist_id not in SYSTEM_WATCHLIST_IDS
        ],
    ]
    records = watchlist_repository.reorder(
        session,
        watchlist_ids=ordered_watchlist_ids,
    )
    session.commit()
    return [present_watchlist(item) for item in records]


@router.post("/{watchlist_id}/copy")
def copy_watchlist_record(
    watchlist_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    source = _require_watchlist(session, watchlist_id)
    copied_name = f"{source.name} Copy"
    source_instrument_ids = [item.instrument_id for item in source.items]
    record = _duplicate_watchlist_with_retry(
        session,
        source_watchlist_id=watchlist_id,
        copied_name=copied_name,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    if _is_system_watchlist(watchlist_id):
        record.owner_type = "team"
        record.owner_id = "investment-team"
        record.is_default = False
        record.is_shared = False

    try:
        _materialize_watchlist_rows(
            session,
            watchlist_id=record.watchlist_id,
            instrument_ids=source_instrument_ids,
        )
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    session.commit()
    return present_watchlist(watchlist_repository.get(session, record.watchlist_id) or record)


@router.delete("/{watchlist_id}")
def delete_watchlist_record(
    watchlist_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_watchlist(session, watchlist_id)
    _assert_mutable_watchlist(watchlist_id)
    read_model_repository.delete_all_watchlist_rows(
        session,
        watchlist_id=watchlist_id,
    )
    deleted = watchlist_repository.delete(session, watchlist_id=watchlist_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    session.commit()
    return {"watchlist_id": watchlist_id, "deleted": True}


@router.get("/{watchlist_id}")
def get_watchlist(
    watchlist_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    if _is_system_watchlist(watchlist_id):
        _sync_system_watchlist(
            session,
            spec=SYSTEM_WATCHLIST_BY_ID[watchlist_id],
        )
        session.commit()
    record = _require_watchlist(session, watchlist_id)
    fields = field_registry_repository.list_fields(session)
    watchlist_rows = read_model_repository.list_watchlist_rows(session, watchlist_id)
    active_instrument_types = {
        str(row.instrument_type or "").strip().lower()
        for row in watchlist_rows
        if str(row.instrument_type or "").strip()
    }
    scoped_fields = [
        field
        for field in fields
        if _field_supports_instrument_types(
            field.instrument_scope_json,
            active_instrument_types,
            require_all=True,
        )
    ]
    return {
        **present_watchlist(record),
        "instrument_types": sorted(active_instrument_types),
        "views": [present_watchlist_view(item) for item in watchlist_repository.list_views(session, watchlist_id)],
        "available_group_bys": present_group_by_options(
            scoped_fields,
            include_instrument_type=len(active_instrument_types) > 1,
        ),
        "default_filters_summary": present_default_filter_summary(scoped_fields),
    }


@router.get("/{watchlist_id}/views")
def list_watchlist_views_for_watchlist(
    watchlist_id: str,
    session: Session = Depends(get_db_session),
) -> list[dict[str, object]]:
    _require_watchlist(session, watchlist_id)
    return [
        present_watchlist_view(item)
        for item in watchlist_repository.list_views(session, watchlist_id)
    ]


@router.post("/{watchlist_id}/items")
def add_items_to_watchlist(
    watchlist_id: str,
    payload: WatchlistItemsCreateRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_watchlist(session, watchlist_id)
    _assert_mutable_watchlist(watchlist_id)
    canonical_instrument_ids: list[str] = []
    missing_instrument_ids: list[str] = []
    unsupported_instrument_ids: list[str] = []
    resolved_instruments: list[dict[str, object]] = []
    for instrument_id in payload.instrument_ids:
        requested_instrument_id = instrument_id.strip()
        if not requested_instrument_id:
            continue
        try:
            shared_instrument = get_shared_instrument(requested_instrument_id)
        except SharedInstrumentRegistryError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        if not isinstance(shared_instrument, dict):
            missing_instrument_ids.append(requested_instrument_id)
            continue
        lifecycle_state = shared_instrument.get("lifecycle_state")
        lifecycle_status = (
            str(lifecycle_state.get("status") or "active").strip().lower()
            if isinstance(lifecycle_state, dict)
            else "active"
        )
        if lifecycle_status == "archived":
            missing_instrument_ids.append(requested_instrument_id)
            continue
        if not _supports_local_detail(shared_instrument):
            unsupported_instrument_ids.append(requested_instrument_id)
            continue

        canonical_instrument_id = str(
            shared_instrument.get("instrument_id") or requested_instrument_id
        ).strip()
        resolved_instruments.append(shared_instrument)
        if canonical_instrument_id and canonical_instrument_id not in canonical_instrument_ids:
            canonical_instrument_ids.append(canonical_instrument_id)

    if missing_instrument_ids:
        missing_label = ", ".join(missing_instrument_ids)
        raise HTTPException(
            status_code=404,
            detail=(
                f'Instrument not found in shared registry: {missing_label}. '
                "Search and materialize stocks through the equity search; "
                "register other instrument types in Database Dashboard."
            ),
        )
    if unsupported_instrument_ids:
        unsupported_label = ", ".join(unsupported_instrument_ids)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Watchlist currently supports public funds, private funds, ETFs, stocks, and indexes only: {unsupported_label}. "
                "Use Database Dashboard for shared master data, then add supported instruments here."
            ),
        )

    for shared_instrument in resolved_instruments:
        _ensure_local_instrument_detail(session, shared_instrument)

    created = watchlist_repository.add_items(
        session,
        watchlist_id=watchlist_id,
        instrument_ids=canonical_instrument_ids,
        added_by="api",
    )
    created_instrument_ids = [item.instrument_id for item in created]
    try:
        _materialize_watchlist_rows(
            session,
            watchlist_id=watchlist_id,
            instrument_ids=created_instrument_ids,
        )
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    recalculated_instrument_ids: list[str] = []
    for instrument_id in created_instrument_ids:
        canonical_recalc_service.execute_recalc(
            session,
            instrument_id=instrument_id,
            job_type="all",
            trigger_type="watchlist_membership_added",
            trigger_ref_type="watchlist",
            trigger_ref_id=watchlist_id,
        )
        recalculated_instrument_ids.append(instrument_id)

    session.commit()
    return {
        "watchlist_id": watchlist_id,
        "accepted_count": len(created),
        "pending_recalc_instrument_ids": created_instrument_ids,
        "recalculated_instrument_ids": recalculated_instrument_ids,
    }


@router.post("/{watchlist_id}/items/delete")
def delete_items_from_watchlist(
    watchlist_id: str,
    payload: WatchlistItemsDeleteRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_watchlist(session, watchlist_id)
    _assert_mutable_watchlist(watchlist_id)
    deleted_count = watchlist_repository.delete_items(
        session,
        watchlist_id=watchlist_id,
        instrument_ids=payload.instrument_ids,
    )
    read_model_repository.delete_watchlist_rows(
        session,
        watchlist_id=watchlist_id,
        instrument_ids=payload.instrument_ids,
    )
    session.commit()
    return {
        "watchlist_id": watchlist_id,
        "deleted_count": deleted_count,
    }


@router.post("/{watchlist_id}/items/move")
def move_items_to_watchlist(
    watchlist_id: str,
    payload: WatchlistItemsMoveRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    source_watchlist = _require_watchlist(session, watchlist_id)
    _assert_mutable_watchlist(watchlist_id)
    target_watchlist_id = payload.target_watchlist_id.strip()
    if not target_watchlist_id:
        raise HTTPException(status_code=400, detail="Target watchlist is required.")
    if target_watchlist_id == watchlist_id:
        raise HTTPException(status_code=400, detail="Target watchlist must be different.")
    _require_watchlist(session, target_watchlist_id)
    _assert_mutable_watchlist(target_watchlist_id)

    instrument_ids = _normalize_instrument_ids(payload.instrument_ids)
    if not instrument_ids:
        return {
            "source_watchlist_id": watchlist_id,
            "target_watchlist_id": target_watchlist_id,
            "moved_count": 0,
            "added_count": 0,
            "already_present_count": 0,
        }
    _assert_source_membership(source_watchlist=source_watchlist, instrument_ids=instrument_ids)

    created = watchlist_repository.add_items(
        session,
        watchlist_id=target_watchlist_id,
        instrument_ids=instrument_ids,
        added_by="api",
    )
    try:
        _materialize_watchlist_rows(
            session,
            watchlist_id=target_watchlist_id,
            instrument_ids=[item.instrument_id for item in created],
        )
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    deleted_count = watchlist_repository.delete_items(
        session,
        watchlist_id=watchlist_id,
        instrument_ids=instrument_ids,
    )
    read_model_repository.delete_watchlist_rows(
        session,
        watchlist_id=watchlist_id,
        instrument_ids=instrument_ids,
    )
    session.commit()
    return {
        "source_watchlist_id": watchlist_id,
        "target_watchlist_id": target_watchlist_id,
        "moved_count": deleted_count,
        "added_count": len(created),
        "already_present_count": len(instrument_ids) - len(created),
    }


@router.post("/{watchlist_id}/items/copy")
def copy_items_to_watchlist(
    watchlist_id: str,
    payload: WatchlistItemsCopyRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    source_watchlist = _require_watchlist(session, watchlist_id)
    target_watchlist_id = payload.target_watchlist_id.strip()
    if not target_watchlist_id:
        raise HTTPException(status_code=400, detail="Target watchlist is required.")
    if target_watchlist_id == watchlist_id:
        raise HTTPException(status_code=400, detail="Target watchlist must be different.")
    _require_watchlist(session, target_watchlist_id)
    _assert_mutable_watchlist(target_watchlist_id)

    instrument_ids = _normalize_instrument_ids(payload.instrument_ids)
    if not instrument_ids:
        return {
            "source_watchlist_id": watchlist_id,
            "target_watchlist_id": target_watchlist_id,
            "copied_count": 0,
            "added_count": 0,
            "already_present_count": 0,
        }
    _assert_source_membership(source_watchlist=source_watchlist, instrument_ids=instrument_ids)

    created = watchlist_repository.add_items(
        session,
        watchlist_id=target_watchlist_id,
        instrument_ids=instrument_ids,
        added_by="api",
    )
    try:
        _materialize_watchlist_rows(
            session,
            watchlist_id=target_watchlist_id,
            instrument_ids=[item.instrument_id for item in created],
        )
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    session.commit()
    return {
        "source_watchlist_id": watchlist_id,
        "target_watchlist_id": target_watchlist_id,
        "copied_count": len(instrument_ids),
        "added_count": len(created),
        "already_present_count": len(instrument_ids) - len(created),
    }


@router.post("/{watchlist_id}/views")
def create_view_for_watchlist(
    watchlist_id: str,
    payload: WatchlistViewCreateRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_watchlist(session, watchlist_id)
    view_name = payload.name.strip()
    if not view_name:
        raise HTTPException(status_code=400, detail="Watchlist view name is required")
    _validate_saved_view_contract(session, payload)
    columns = _ensure_required_columns([item.model_dump() for item in payload.columns])
    record = _create_watchlist_view_with_retry(
        session,
        watchlist_id=watchlist_id,
        name=view_name,
        description=payload.description,
        default_group_by=payload.default_group_by,
        default_sort=payload.default_sort and [item.model_dump() for item in payload.default_sort] or [],
        default_filters=payload.default_filters,
        default_advanced_filter=payload.default_advanced_filters.model_dump()
        if payload.default_advanced_filters
        else {},
        columns=columns,
    )
    session.commit()
    return present_watchlist_view(record)


@router.put("/{watchlist_id}/views/{view_id}")
def update_view_for_watchlist(
    watchlist_id: str,
    view_id: str,
    payload: WatchlistViewCreateRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_watchlist(session, watchlist_id)
    view_name = payload.name.strip()
    if not view_name:
        raise HTTPException(status_code=400, detail="Watchlist view name is required")
    _validate_saved_view_contract(session, payload)
    existing_view = watchlist_repository.get_view(
        session,
        watchlist_id=watchlist_id,
        view_id=view_id,
    )
    if existing_view is None:
        raise HTTPException(status_code=404, detail="Watchlist view not found")
    if any(
        item.watchlist_view_id != existing_view.watchlist_view_id
        and item.name.strip().casefold() == view_name.casefold()
        for item in watchlist_repository.list_views(session, watchlist_id)
    ):
        raise HTTPException(status_code=409, detail="Watchlist view name already exists")
    try:
        columns = _ensure_required_columns([item.model_dump() for item in payload.columns])
        record = watchlist_repository.update_view(
            session,
            watchlist_id=watchlist_id,
            view_id=view_id,
            name=view_name,
            description=payload.description,
            default_group_by=payload.default_group_by,
            default_sort=payload.default_sort
            and [item.model_dump() for item in payload.default_sort]
            or [],
            default_filters=payload.default_filters,
            default_advanced_filter=payload.default_advanced_filters.model_dump()
            if payload.default_advanced_filters
            else {},
            columns=columns,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()
    return present_watchlist_view(record)
