from __future__ import annotations

from fastapi import APIRouter, HTTPException

from platform_app.api.contracts import PlatformFxRateRecord, PlatformFxRatesResponse, PlatformFxRateUpsertRequest
from platform_app.services.fx_rates import list_fx_rates, maintained_fx_pairs, supported_fx_currencies, upsert_fx_rate


router = APIRouter()


@router.get("", response_model=PlatformFxRatesResponse)
def list_platform_fx_rates() -> PlatformFxRatesResponse:
    return PlatformFxRatesResponse(
        supported_currencies=supported_fx_currencies(),
        maintained_pairs=maintained_fx_pairs(),
        rates=[PlatformFxRateRecord.model_validate(item) for item in list_fx_rates()],
    )


@router.post("", response_model=PlatformFxRateRecord)
def upsert_platform_fx_rate(payload: PlatformFxRateUpsertRequest) -> PlatformFxRateRecord:
    try:
        record = upsert_fx_rate(
            base_currency=payload.base_currency,
            quote_currency=payload.quote_currency,
            rate=payload.rate,
            as_of_date=payload.as_of_date,
            provider=payload.provider,
            status=payload.status,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return PlatformFxRateRecord.model_validate(record)
