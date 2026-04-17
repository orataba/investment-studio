from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.contracts import SharedFxRateRecord, SharedFxRatesResponse
from app.services.instrument_registry import InstrumentRegistryError, get_platform_fx_rates
from app.services.portfolio_store import get_portfolio


router = APIRouter()


@router.get("/{portfolio_id}/fx-rates", response_model=SharedFxRatesResponse)
def get_shared_fx_rates(portfolio_id: str) -> SharedFxRatesResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        payload = get_platform_fx_rates()
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    rates = payload.get("rates", [])
    return SharedFxRatesResponse(
        portfolio_id=portfolio_id,
        supported_currencies=list(payload.get("supported_currencies", [])),
        maintained_pairs=list(payload.get("maintained_pairs", [])),
        rates=[
            SharedFxRateRecord.model_validate(
                {
                    **dict(item),
                    "rate": float(item.get("rate") or 0.0),
                }
            )
            for item in rates
            if isinstance(item, dict)
        ],
    )
