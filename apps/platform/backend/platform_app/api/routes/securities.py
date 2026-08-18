from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from platform_app.api.contracts import PlatformInstrumentRecord
from platform_app.services.downstream_notifications import (
    queue_market_data_downstream_refresh,
)
from platform_app.services.equities import EquityNotSupportedError
from platform_app.services.etfs import EtfNotSupportedError
from platform_app.services.fmp import FmpApiError
from platform_app.services.securities import (
    materialize_security,
    refresh_security_eod,
    search_securities,
)
from platform_app.services.securities.contracts import (
    SecurityMaterializeRequest,
    SecuritySearchResponse,
)


router = APIRouter()


def _upstream_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=str(error))


@router.get("/search", response_model=SecuritySearchResponse)
def search_security_records(
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> SecuritySearchResponse:
    results, catalog_errors = search_securities(q, limit=limit)
    return SecuritySearchResponse.model_validate(
        {"results": results, "catalog_errors": catalog_errors}
    )


@router.post("/materialize", response_model=PlatformInstrumentRecord)
def materialize_security_record(
    payload: SecurityMaterializeRequest,
    background_tasks: BackgroundTasks,
) -> PlatformInstrumentRecord:
    try:
        record = materialize_security(
            payload.instrument_type,
            payload.fmp_symbol,
            refresh_eod=payload.refresh_eod,
        )
    except FmpApiError as error:
        raise _upstream_error(error) from error
    except (EquityNotSupportedError, EtfNotSupportedError) as error:
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
def refresh_security_record(
    instrument_id: str,
    background_tasks: BackgroundTasks,
    full_history: bool = False,
) -> PlatformInstrumentRecord:
    try:
        record = refresh_security_eod(instrument_id, full_history=full_history)
    except FmpApiError as error:
        raise _upstream_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    queue_market_data_downstream_refresh(
        background_tasks,
        instrument_ids=[instrument_id],
    )
    return PlatformInstrumentRecord.model_validate(record)
