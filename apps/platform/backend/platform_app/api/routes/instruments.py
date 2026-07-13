from __future__ import annotations

from datetime import date
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from typing import Annotated

from platform_app.api.contracts import (
    PlatformBulkRefreshRequest,
    PlatformBulkRefreshResponse,
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
    PlatformQuoteSelectionPolicyUpdateRequest,
    PlatformQuoteObservationRevisionsResponse,
    PlatformRefreshTriggerRequest,
    PlatformSourceSettingsUpdateRequest,
)
from platform_app.services.market_data_ops import (
    import_nav_file,
    import_nav_text,
    preview_nav_import,
    refresh_market_data,
    refresh_market_data_batch,
)
from platform_app.services.instrument_store import (
    archive_instrument,
    create_instrument,
    find_instrument_by_identifier,
    get_instrument,
    instrument_exists,
    instrument_registry_name,
    list_quote_observation_revisions,
    list_instruments,
    restore_instrument,
    upsert_market_data,
    upsert_quote_selection_policy,
    upsert_source_settings,
)
from platform_app.services.downstream_notifications import queue_market_data_downstream_refresh


router = APIRouter()


@router.get("", response_model=PlatformInstrumentsResponse)
def list_instrument_records(
    search: Annotated[str | None, Query(min_length=1)] = None,
    instrument_type: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    include_inactive: bool = False,
) -> PlatformInstrumentsResponse:
    return PlatformInstrumentsResponse(
        registry_name=instrument_registry_name(),
        instruments=list_instruments(
            search=search,
            instrument_type=instrument_type,
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


@router.post("/refresh", response_model=PlatformBulkRefreshResponse)
def refresh_instrument_market_data_batch(
    payload: PlatformBulkRefreshRequest,
    background_tasks: BackgroundTasks,
) -> PlatformBulkRefreshResponse:
    response = refresh_market_data_batch(
        source=payload.source,
        updated_by=payload.updated_by,
        full_history=payload.full_history,
        include_inactive=payload.include_inactive,
    )
    refreshed_ids = [
        item["instrument_id"]
        for item in response["results"]
        if item.get("status") in {"imported", "refreshed"}
    ]
    if refreshed_ids:
        queue_market_data_downstream_refresh(
            background_tasks,
            instrument_ids=refreshed_ids,
        )
    return PlatformBulkRefreshResponse.model_validate(response)


@router.post("", response_model=PlatformInstrumentRecord)
def create_instrument_record(
    payload: PlatformInstrumentCreateRequest,
) -> PlatformInstrumentRecord:
    try:
        record = create_instrument(
            instrument_name=payload.instrument_name,
            instrument_type=payload.instrument_type,
            currency=payload.currency,
            identifiers=[item.model_dump() for item in payload.identifiers],
            quote_selection_policy=(
                payload.quote_selection_policy.model_dump()
                if payload.quote_selection_policy is not None
                else None
            ),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return PlatformInstrumentRecord.model_validate(record)


@router.put(
    "/{instrument_id}/quote-selection-policy",
    response_model=PlatformInstrumentRecord,
)
def update_instrument_quote_selection_policy(
    instrument_id: str,
    payload: PlatformQuoteSelectionPolicyUpdateRequest,
) -> PlatformInstrumentRecord:
    try:
        record = upsert_quote_selection_policy(
            instrument_id=instrument_id,
            quote_selection_policy=payload.quote_selection_policy.model_dump(),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentRecord.model_validate(record)


@router.get("/{instrument_id}", response_model=PlatformInstrumentDetail)
def get_instrument_record(instrument_id: str) -> PlatformInstrumentDetail:
    record = get_instrument(instrument_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentDetail.model_validate(record)


@router.get(
    "/{instrument_id}/quote-revisions",
    response_model=PlatformQuoteObservationRevisionsResponse,
)
def list_instrument_quote_revisions(
    instrument_id: str,
    quote_series_id: str | None = None,
    as_of_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> PlatformQuoteObservationRevisionsResponse:
    if not instrument_exists(instrument_id):
        raise HTTPException(status_code=404, detail="Instrument not found")
    revisions = list_quote_observation_revisions(
        instrument_id=instrument_id,
        quote_series_id=quote_series_id,
        as_of_date=as_of_date,
        limit=limit + 1,
    )
    return PlatformQuoteObservationRevisionsResponse(
        instrument_id=instrument_id,
        limit=limit,
        truncated=len(revisions) > limit,
        revisions=revisions[:limit],
    )


@router.post("/{instrument_id}/market-data", response_model=PlatformInstrumentRecord)
def upsert_instrument_market_data(
    instrument_id: str,
    payload: PlatformMarketDataUpsertRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    record = upsert_market_data(
        instrument_id=instrument_id,
        metric_family=payload.metric_family,
        quote_basis=payload.quote_basis,
        as_of_date=payload.as_of_date,
        value=str(payload.value),
        currency=payload.currency,
        source_ref=payload.source_ref,
        status=payload.status,
        source_published_at=payload.source_published_at,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    queue_market_data_downstream_refresh(
        background_tasks,
        instrument_ids=[instrument_id],
        dirty_from=payload.as_of_date,
        refresh_all_portfolios=payload.metric_family.strip().lower() == "fx" or instrument_id.startswith("fx-"),
    )
    return PlatformInstrumentRecord.model_validate(record)


@router.put("/{instrument_id}/source-settings", response_model=PlatformInstrumentRecord)
def update_instrument_source_settings(
    instrument_id: str,
    payload: PlatformSourceSettingsUpdateRequest,
) -> PlatformInstrumentRecord:
    record = upsert_source_settings(
        instrument_id=instrument_id,
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


@router.post("/{instrument_id}/refresh", response_model=PlatformInstrumentRecord)
def refresh_instrument_market_data(
    instrument_id: str,
    payload: PlatformRefreshTriggerRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    record = refresh_market_data(
        instrument_id=instrument_id,
        updated_by=payload.updated_by,
        full_history=payload.full_history,
        source=payload.source,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    queue_market_data_downstream_refresh(
        background_tasks,
        instrument_ids=[instrument_id],
        refresh_all_portfolios=instrument_id.startswith("fx-"),
    )
    return PlatformInstrumentRecord.model_validate(record)


@router.post("/{instrument_id}/archive", response_model=PlatformInstrumentRecord)
def archive_instrument_record(
    instrument_id: str,
    payload: PlatformLifecycleTransitionRequest,
) -> PlatformInstrumentRecord:
    record = archive_instrument(instrument_id=instrument_id, updated_by=payload.updated_by)
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentRecord.model_validate(record)


@router.post("/{instrument_id}/restore", response_model=PlatformInstrumentRecord)
def restore_instrument_record(
    instrument_id: str,
    payload: PlatformLifecycleTransitionRequest,
) -> PlatformInstrumentRecord:
    record = restore_instrument(instrument_id=instrument_id, updated_by=payload.updated_by)
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    return PlatformInstrumentRecord.model_validate(record)


@router.post("/{instrument_id}/nav-import", response_model=PlatformInstrumentRecord)
def import_instrument_nav_history(
    instrument_id: str,
    payload: PlatformNavImportRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    try:
        record = import_nav_text(
            instrument_id=instrument_id,
            raw_text=payload.raw_text,
            source_ref=payload.source_ref,
            status=payload.status,
            updated_by=payload.updated_by,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    queue_market_data_downstream_refresh(background_tasks, instrument_ids=[instrument_id])
    return PlatformInstrumentRecord.model_validate(record)


@router.post(
    "/{instrument_id}/nav-import/preview",
    response_model=PlatformNavImportPreviewResponse,
)
def preview_instrument_nav_history(
    instrument_id: str,
    payload: PlatformNavImportPreviewRequest,
) -> PlatformNavImportPreviewResponse:
    try:
        rows = preview_nav_import(
            instrument_id=instrument_id,
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


@router.post("/{instrument_id}/nav-import/file", response_model=PlatformInstrumentRecord)
def import_instrument_nav_history_file(
    instrument_id: str,
    payload: PlatformNavImportFileRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    try:
        record = import_nav_file(
            instrument_id=instrument_id,
            file_name=payload.file_name,
            file_bytes=payload.decoded_bytes(),
            source_ref=payload.source_ref,
            status=payload.status,
            updated_by=payload.updated_by,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Instrument not found")
    queue_market_data_downstream_refresh(background_tasks, instrument_ids=[instrument_id])
    return PlatformInstrumentRecord.model_validate(record)
