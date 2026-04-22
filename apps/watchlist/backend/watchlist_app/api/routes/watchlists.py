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
from watchlist_app.db.session import get_db_session
from watchlist_app.repositories.sqlalchemy.assets import SQLAlchemyAssetRepository
from watchlist_app.repositories.sqlalchemy.field_registry import SQLAlchemyFieldRegistryRepository
from watchlist_app.repositories.sqlalchemy.instrument_attributes import (
    SQLAlchemyInstrumentAttributeRepository,
)
from watchlist_app.repositories.sqlalchemy.read_models import SQLAlchemyReadModelRepository
from watchlist_app.repositories.sqlalchemy.watchlists import SQLAlchemyWatchlistRepository
from watchlist_app.services.canonical_recalc import CanonicalRecalcService
from watchlist_app.services.read_models import (
    build_watchlist_row_materialization,
    collapse_latest_attribute_values,
)
from watchlist_app.services.instrument_resolution import resolve_watchlist_instrument
from watchlist_app.services.shared_instrument_registry import (
    SharedInstrumentRegistryError,
    get_shared_instrument,
)


router = APIRouter()
watchlist_repository = SQLAlchemyWatchlistRepository()
field_registry_repository = SQLAlchemyFieldRegistryRepository()
asset_repository = SQLAlchemyAssetRepository()
attribute_repository = SQLAlchemyInstrumentAttributeRepository()
read_model_repository = SQLAlchemyReadModelRepository()
canonical_recalc_service = CanonicalRecalcService()
MAX_WATCHLIST_ID_ATTEMPTS = 10


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


def _field_supports_asset_types(
    asset_scope: list[str] | None,
    asset_types: set[str],
    *,
    require_all: bool,
) -> bool:
    normalized_scope = {
        str(value).strip().lower()
        for value in (asset_scope or [])
        if str(value).strip()
    }
    if not normalized_scope:
        return True
    if not asset_types:
        return False
    return normalized_scope.issuperset(asset_types) if require_all else bool(
        normalized_scope.intersection(asset_types)
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
    return str(shared_instrument.get("asset_type") or "").strip().lower() == "fund"


def _ensure_local_asset_detail(
    session: Session,
    shared_instrument: dict[str, object],
) -> str | None:
    if not _supports_local_detail(shared_instrument):
        return None

    shared_asset_id = str(shared_instrument.get("asset_id") or "").strip()
    if not shared_asset_id:
        return None

    asset_repository.upsert_from_shared_instrument(
        session,
        shared_instrument=shared_instrument,
        detail_view_type="fund",
    )
    return shared_asset_id


def _ensure_required_columns(columns: list[dict[str, object]]) -> list[dict[str, object]]:
    required = ["asset_name"]
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


def _normalize_asset_ids(asset_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    for asset_id in asset_ids:
        candidate = asset_id.strip()
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _assert_source_membership(
    *,
    source_watchlist: object,
    asset_ids: list[str],
) -> None:
    source_asset_ids = {
        str(item.asset_id).strip()
        for item in getattr(source_watchlist, "items", [])
        if str(item.asset_id).strip()
    }
    missing_asset_ids = [
        asset_id for asset_id in asset_ids if asset_id not in source_asset_ids
    ]
    if missing_asset_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                "Assets not found in source watchlist: "
                f"{', '.join(missing_asset_ids)}."
            ),
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
    asset_ids: list[str],
) -> None:
    for requested_asset_id in asset_ids:
        instrument = resolve_watchlist_instrument(session, asset_id=requested_asset_id)
        canonical_asset_id = (
            str(instrument.get("canonical_asset_id") or requested_asset_id).strip()
            if isinstance(instrument, dict)
            else requested_asset_id
        )
        asset_type = (
            str(instrument.get("asset_type") or "other")
            if isinstance(instrument, dict)
            else "other"
        )
        if (
            read_model_repository.get_watchlist_row(
                session,
                watchlist_id=watchlist_id,
                asset_id=canonical_asset_id,
            )
            is not None
        ):
            continue

        source_row = read_model_repository.find_any_watchlist_row_for_asset(
            session,
            canonical_asset_id,
        )
        summary_record = read_model_repository.get_summary(session, canonical_asset_id)
        summary_payload = summary_record.payload_json if summary_record is not None else {}
        freshness = summary_payload.get("freshness", {})
        asset = asset_repository.get(session, canonical_asset_id)
        attributes = collapse_latest_attribute_values(
            attribute_repository.get_values_for_asset(session, canonical_asset_id)
        )
        display_name = (
            asset.asset_name
            if asset is not None
            else summary_payload.get("fund_name")
            or (
                str(instrument.get("asset_name") or canonical_asset_id)
                if isinstance(instrument, dict)
                else canonical_asset_id
            )
        )
        read_model_repository.upsert_watchlist_row(
            session,
            watchlist_id=watchlist_id,
            asset_id=canonical_asset_id,
            data=build_watchlist_row_materialization(
                watchlist_id=watchlist_id,
                asset_id=canonical_asset_id,
                asset_type=asset_type,
                source_row=source_row,
                display_name=display_name,
                share_class=None,
                ticker_or_isin=(
                    asset.primary_identifier_value
                    if asset is not None
                    else (
                        str(instrument.get("primary_identifier") or "").strip() or None
                        if isinstance(instrument, dict)
                        else None
                    )
                ),
                management_firm_name=None,
                category_name=summary_payload.get("category_name") or asset_type.replace("_", " ").title(),
                overall_rating=summary_payload.get("overall_rating"),
                analyst_stance=summary_payload.get("analyst_stance"),
                attributes=attributes,
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


@router.get("")
def list_watchlists(session: Session = Depends(get_db_session)) -> list[dict[str, object]]:
    records = watchlist_repository.list(session)
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
    records = watchlist_repository.reorder(
        session,
        watchlist_ids=payload.watchlist_ids,
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
    source_asset_ids = [item.asset_id for item in source.items]
    record = _duplicate_watchlist_with_retry(
        session,
        source_watchlist_id=watchlist_id,
        copied_name=copied_name,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    try:
        _materialize_watchlist_rows(
            session,
            watchlist_id=record.watchlist_id,
            asset_ids=source_asset_ids,
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
    record = _require_watchlist(session, watchlist_id)
    fields = field_registry_repository.list_fields(session)
    watchlist_rows = read_model_repository.list_watchlist_rows(session, watchlist_id)
    active_asset_types = {
        str(row.asset_type or "").strip().lower()
        for row in watchlist_rows
        if str(row.asset_type or "").strip()
    }
    scoped_fields = [
        field
        for field in fields
        if _field_supports_asset_types(
            field.asset_scope_json,
            active_asset_types,
            require_all=True,
        )
    ]
    return {
        **present_watchlist(record),
        "views": [present_watchlist_view(item) for item in watchlist_repository.list_views(session, watchlist_id)],
        "available_group_bys": present_group_by_options(scoped_fields),
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
    canonical_asset_ids: list[str] = []
    missing_asset_ids: list[str] = []
    unsupported_asset_ids: list[str] = []
    resolved_instruments: list[dict[str, object]] = []
    recalculated_asset_ids: list[str] = []
    for asset_id in payload.asset_ids:
        requested_asset_id = asset_id.strip()
        if not requested_asset_id:
            continue
        try:
            shared_instrument = get_shared_instrument(requested_asset_id)
        except SharedInstrumentRegistryError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        if not isinstance(shared_instrument, dict):
            missing_asset_ids.append(requested_asset_id)
            continue
        lifecycle_state = shared_instrument.get("lifecycle_state")
        lifecycle_status = (
            str(lifecycle_state.get("status") or "active").strip().lower()
            if isinstance(lifecycle_state, dict)
            else "active"
        )
        if lifecycle_status == "archived":
            missing_asset_ids.append(requested_asset_id)
            continue
        if not _supports_local_detail(shared_instrument):
            unsupported_asset_ids.append(requested_asset_id)
            continue

        canonical_asset_id = str(
            shared_instrument.get("asset_id") or requested_asset_id
        ).strip()
        resolved_instruments.append(shared_instrument)
        if canonical_asset_id and canonical_asset_id not in canonical_asset_ids:
            canonical_asset_ids.append(canonical_asset_id)

    if missing_asset_ids:
        missing_label = ", ".join(missing_asset_ids)
        raise HTTPException(
            status_code=404,
            detail=(
                f'Instrument not found in shared registry: {missing_label}. '
                "Add the asset in Database Dashboard first."
            ),
        )
    if unsupported_asset_ids:
        unsupported_label = ", ".join(unsupported_asset_ids)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Watchlist currently supports fund instruments only: {unsupported_label}. "
                "Use Database Dashboard for shared master data, then add supported funds here."
            ),
        )

    for shared_instrument in resolved_instruments:
        detail_asset_id = _ensure_local_asset_detail(session, shared_instrument)
        if detail_asset_id and detail_asset_id not in recalculated_asset_ids:
            recalculated_asset_ids.append(detail_asset_id)

    created = watchlist_repository.add_items(
        session,
        watchlist_id=watchlist_id,
        asset_ids=canonical_asset_ids,
        added_by="api",
    )
    try:
        _materialize_watchlist_rows(
            session,
            watchlist_id=watchlist_id,
            asset_ids=[item.asset_id for item in created],
        )
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    for asset_id in recalculated_asset_ids:
        canonical_recalc_service.execute_recalc(
            session,
            asset_id=asset_id,
            job_type="all",
            trigger_type="watchlist_membership_added",
            trigger_ref_type="watchlist",
            trigger_ref_id=watchlist_id,
        )

    session.commit()
    return {
        "watchlist_id": watchlist_id,
        "accepted_count": len(created),
        "pending_recalc_asset_ids": canonical_asset_ids,
        "recalculated_asset_ids": recalculated_asset_ids,
    }


@router.post("/{watchlist_id}/items/delete")
def delete_items_from_watchlist(
    watchlist_id: str,
    payload: WatchlistItemsDeleteRequest,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require_watchlist(session, watchlist_id)
    deleted_count = watchlist_repository.delete_items(
        session,
        watchlist_id=watchlist_id,
        asset_ids=payload.asset_ids,
    )
    read_model_repository.delete_watchlist_rows(
        session,
        watchlist_id=watchlist_id,
        asset_ids=payload.asset_ids,
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
    target_watchlist_id = payload.target_watchlist_id.strip()
    if not target_watchlist_id:
        raise HTTPException(status_code=400, detail="Target watchlist is required.")
    if target_watchlist_id == watchlist_id:
        raise HTTPException(status_code=400, detail="Target watchlist must be different.")
    _require_watchlist(session, target_watchlist_id)

    asset_ids = _normalize_asset_ids(payload.asset_ids)
    if not asset_ids:
        return {
            "source_watchlist_id": watchlist_id,
            "target_watchlist_id": target_watchlist_id,
            "moved_count": 0,
            "added_count": 0,
            "already_present_count": 0,
        }
    _assert_source_membership(source_watchlist=source_watchlist, asset_ids=asset_ids)

    created = watchlist_repository.add_items(
        session,
        watchlist_id=target_watchlist_id,
        asset_ids=asset_ids,
        added_by="api",
    )
    try:
        _materialize_watchlist_rows(
            session,
            watchlist_id=target_watchlist_id,
            asset_ids=[item.asset_id for item in created],
        )
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    deleted_count = watchlist_repository.delete_items(
        session,
        watchlist_id=watchlist_id,
        asset_ids=asset_ids,
    )
    read_model_repository.delete_watchlist_rows(
        session,
        watchlist_id=watchlist_id,
        asset_ids=asset_ids,
    )
    session.commit()
    return {
        "source_watchlist_id": watchlist_id,
        "target_watchlist_id": target_watchlist_id,
        "moved_count": deleted_count,
        "added_count": len(created),
        "already_present_count": len(asset_ids) - len(created),
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

    asset_ids = _normalize_asset_ids(payload.asset_ids)
    if not asset_ids:
        return {
            "source_watchlist_id": watchlist_id,
            "target_watchlist_id": target_watchlist_id,
            "copied_count": 0,
            "added_count": 0,
            "already_present_count": 0,
        }
    _assert_source_membership(source_watchlist=source_watchlist, asset_ids=asset_ids)

    created = watchlist_repository.add_items(
        session,
        watchlist_id=target_watchlist_id,
        asset_ids=asset_ids,
        added_by="api",
    )
    try:
        _materialize_watchlist_rows(
            session,
            watchlist_id=target_watchlist_id,
            asset_ids=[item.asset_id for item in created],
        )
    except SharedInstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    session.commit()
    return {
        "source_watchlist_id": watchlist_id,
        "target_watchlist_id": target_watchlist_id,
        "copied_count": len(asset_ids),
        "added_count": len(created),
        "already_present_count": len(asset_ids) - len(created),
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
