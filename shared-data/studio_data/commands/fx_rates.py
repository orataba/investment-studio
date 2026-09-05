from __future__ import annotations
from studio_data.contracts import (
    StudioFxRateRecord,
    StudioFxRatesResponse,
    StudioFxRateUpsertRequest,
)
from studio_data.services.fx_rates import (
    list_fx_rates,
    maintained_fx_pairs,
    supported_fx_currencies,
    upsert_fx_rate,
)
from studio_data.services.downstream_notifications import (
    notify_market_data_downstream_refresh,
)


def list_shared_fx_rates() -> StudioFxRatesResponse:
    return StudioFxRatesResponse(
        supported_currencies=supported_fx_currencies(),
        maintained_pairs=maintained_fx_pairs(),
        rates=[StudioFxRateRecord.model_validate(item) for item in list_fx_rates()],
    )


def upsert_shared_fx_rate(
    payload: StudioFxRateUpsertRequest,
) -> StudioFxRateRecord:
    record = upsert_fx_rate(
        base_currency=payload.base_currency,
        quote_currency=payload.quote_currency,
        rate=payload.rate,
        as_of_date=payload.as_of_date,
        provider=payload.provider,
        status=payload.status,
    )
    notify_market_data_downstream_refresh(
        dirty_from=payload.as_of_date,
        refresh_all_portfolios=True,
        refresh_watchlist=False,
        raise_on_error=True,
    )
    return StudioFxRateRecord.model_validate(record)
