from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from typing import Annotated

from platform_app.api.contracts import (
    PlatformInstrumentCreateRequest,
    PlatformInstrumentDetail,
    PlatformLifecycleTransitionRequest,
    PlatformInstrumentRecord,
    PlatformInstrumentsResponse,
    PlatformMarketDataUpsertRequest,
    PlatformNavImportFileRequest,
    PlatformNavImportPreviewRequest,
    PlatformNavImportPreviewResponse,
    PlatformNavImportRequest,
    PlatformRefreshTriggerRequest,
    PlatformSourceSettingsUpdateRequest,
)
from platform_app.services.market_data_ops import (
    import_nav_file,
    import_nav_text,
    preview_nav_import,
    refresh_market_data,
)
from platform_app.services.instrument_store import (
    archive_instrument,
    create_instrument,
    find_instrument_by_identifier,
    get_instrument,
    instrument_registry_name,
    list_instruments,
    restore_instrument,
    upsert_market_data,
    upsert_source_settings,
)
from platform_app.services.portfolio_notifications import queue_portfolio_daily_snapshot_refresh


router = APIRouter()


@router.get("", response_model=PlatformInstrumentsResponse)
def list_instrument_records(
    search: Annotated[str | None, Query(min_length=1)] = None,
    asset_type: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    include_inactive: bool = False,
) -> PlatformInstrumentsResponse:
    return PlatformInstrumentsResponse(
        registry_name=instrument_registry_name(),
        instruments=list_instruments(
            search=search,
            asset_type=asset_type,
            limit=limit,
            include_inactive=include_inactive,
        ),
    )


@router.get("/resolve", response_model=PlatformInstrumentDetail)
def resolve_instrument_record(
    identifier_value: Annotated[str, Query(min_length=1)],
    identifier_type: str | None = None,
    include_inactive: bool = False,
) -> PlatformInstrumentDetail:
    record = find_instrument_by_identifier(
        identifier_value=identifier_value,
        identifier_type=identifier_type,
        include_inactive=include_inactive,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentDetail.model_validate(record)


@router.post("", response_model=PlatformInstrumentRecord)
def create_instrument_record(
    payload: PlatformInstrumentCreateRequest,
) -> PlatformInstrumentRecord:
    if not payload.identifiers:
        raise HTTPException(status_code=400, detail="At least one identifier is required.")
    if not any(item.is_primary for item in payload.identifiers):
        raise HTTPException(status_code=400, detail="One identifier must be primary.")

    try:
        record = create_instrument(
            asset_name=payload.asset_name,
            asset_type=payload.asset_type,
            currency=payload.currency,
            identifiers=[item.model_dump() for item in payload.identifiers],
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return PlatformInstrumentRecord.model_validate(record)


@router.get("/{asset_id}", response_model=PlatformInstrumentDetail)
def get_instrument_record(asset_id: str) -> PlatformInstrumentDetail:
    record = get_instrument(asset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentDetail.model_validate(record)


@router.post("/{asset_id}/market-data", response_model=PlatformInstrumentRecord)
def upsert_instrument_market_data(
    asset_id: str,
    payload: PlatformMarketDataUpsertRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    record = upsert_market_data(
        asset_id=asset_id,
        metric_family=payload.metric_family,
        quote_basis=payload.quote_basis,
        as_of_date=payload.as_of_date,
        value=str(payload.value),
        currency=payload.currency,
        provider=payload.provider,
        status=payload.status,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    queue_portfolio_daily_snapshot_refresh(
        background_tasks,
        asset_ids=[asset_id],
        dirty_from=payload.as_of_date,
        refresh_all=payload.metric_family.strip().lower() == "fx" or asset_id.startswith("fx-"),
    )
    return PlatformInstrumentRecord.model_validate(record)


@router.put("/{asset_id}/source-settings", response_model=PlatformInstrumentRecord)
def update_instrument_source_settings(
    asset_id: str,
    payload: PlatformSourceSettingsUpdateRequest,
) -> PlatformInstrumentRecord:
    record = upsert_source_settings(
        asset_id=asset_id,
        source_mode=payload.source_mode,
        source_email=payload.source_email,
        source_location=payload.source_location,
        source_api_profile=payload.source_api_profile,
        source_email_rules=(
            [item.model_dump() for item in payload.source_email_rules]
            if payload.source_email_rules is not None
            else None
        ),
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentRecord.model_validate(record)


@router.post("/{asset_id}/refresh", response_model=PlatformInstrumentRecord)
def refresh_instrument_market_data(
    asset_id: str,
    payload: PlatformRefreshTriggerRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    record = refresh_market_data(
        asset_id=asset_id,
        updated_by=payload.updated_by,
        full_history=payload.full_history,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    queue_portfolio_daily_snapshot_refresh(
        background_tasks,
        asset_ids=[asset_id],
        refresh_all=asset_id.startswith("fx-"),
    )
    return PlatformInstrumentRecord.model_validate(record)


@router.post("/{asset_id}/archive", response_model=PlatformInstrumentRecord)
def archive_instrument_record(
    asset_id: str,
    payload: PlatformLifecycleTransitionRequest,
) -> PlatformInstrumentRecord:
    record = archive_instrument(asset_id=asset_id, updated_by=payload.updated_by)
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentRecord.model_validate(record)


@router.post("/{asset_id}/restore", response_model=PlatformInstrumentRecord)
def restore_instrument_record(
    asset_id: str,
    payload: PlatformLifecycleTransitionRequest,
) -> PlatformInstrumentRecord:
    record = restore_instrument(asset_id=asset_id, updated_by=payload.updated_by)
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentRecord.model_validate(record)


@router.post("/{asset_id}/nav-import", response_model=PlatformInstrumentRecord)
def import_instrument_nav_history(
    asset_id: str,
    payload: PlatformNavImportRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    try:
        record = import_nav_text(
            asset_id=asset_id,
            raw_text=payload.raw_text,
            provider=payload.provider,
            status=payload.status,
            updated_by=payload.updated_by,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    queue_portfolio_daily_snapshot_refresh(background_tasks, asset_ids=[asset_id])
    return PlatformInstrumentRecord.model_validate(record)


@router.post(
    "/{asset_id}/nav-import/preview",
    response_model=PlatformNavImportPreviewResponse,
)
def preview_instrument_nav_history(
    asset_id: str,
    payload: PlatformNavImportPreviewRequest,
) -> PlatformNavImportPreviewResponse:
    try:
        rows = preview_nav_import(
            asset_id=asset_id,
            raw_text=payload.raw_text,
            file_name=payload.file_name,
            file_bytes=payload.decoded_bytes(),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if rows is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformNavImportPreviewResponse(
        row_count=len(rows),
        rows=rows,
    )


@router.post("/{asset_id}/nav-import/file", response_model=PlatformInstrumentRecord)
def import_instrument_nav_history_file(
    asset_id: str,
    payload: PlatformNavImportFileRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    try:
        record = import_nav_file(
            asset_id=asset_id,
            file_name=payload.file_name,
            file_bytes=payload.decoded_bytes(),
            provider=payload.provider,
            status=payload.status,
            updated_by=payload.updated_by,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    queue_portfolio_daily_snapshot_refresh(background_tasks, asset_ids=[asset_id])
    return PlatformInstrumentRecord.model_validate(record)
