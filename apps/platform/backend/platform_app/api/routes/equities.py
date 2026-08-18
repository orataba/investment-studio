from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from platform_app.api.contracts import PlatformInstrumentRecord
from platform_app.services.downstream_notifications import (
    queue_market_data_downstream_refresh,
)
from platform_app.services.equities import (
    EquityCatalogEmptyError,
    EquityNotSupportedError,
    materialize_equity,
    refresh_equity_eod,
    search_equities,
)
from platform_app.services.equities.contracts import (
    EquityMaterializeRequest,
    EquitySearchResponse,
)
from platform_app.services.equities.fmp_client import FmpApiError


router = APIRouter()


def _upstream_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=str(error))


@router.get("/search", response_model=EquitySearchResponse)
def search_equity_records(
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> EquitySearchResponse:
    try:
        results = search_equities(q, limit=limit)
    except EquityCatalogEmptyError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return EquitySearchResponse.model_validate({"results": results})


@router.post("/materialize", response_model=PlatformInstrumentRecord)
def materialize_equity_record(
    payload: EquityMaterializeRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    try:
        record = materialize_equity(
            payload.fmp_symbol,
            refresh_eod=payload.refresh_eod,
        )
    except FmpApiError as error:
        raise _upstream_error(error) from error
    except EquityNotSupportedError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    instrument_id = str(record["instrument_id"])
    queue_market_data_downstream_refresh(
        background_tasks,
        instrument_ids=[instrument_id],
    )
    return PlatformInstrumentRecord.model_validate(record)


@router.post("/{instrument_id}/refresh", response_model=PlatformInstrumentRecord)
def refresh_equity_record(
    instrument_id: str,
    background_tasks: BackgroundTasks,
    full_history: bool = False,
) -> PlatformInstrumentRecord:
    try:
        record = refresh_equity_eod(instrument_id, full_history=full_history)
    except FmpApiError as error:
        raise _upstream_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    queue_market_data_downstream_refresh(
        background_tasks,
        instrument_ids=[instrument_id],
    )
    return PlatformInstrumentRecord.model_validate(record)
