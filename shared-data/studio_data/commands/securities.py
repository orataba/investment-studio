from __future__ import annotations
from studio_data.contracts import StudioInstrumentRecord
from studio_data.services.downstream_notifications import (
    notify_market_data_downstream_refresh,
)
from studio_data.services.equities import EquityNotSupportedError
from studio_data.services.etfs import EtfNotSupportedError
from studio_data.services.fmp import FmpApiError
from studio_data.services.securities import (
    materialize_security,
    refresh_security_eod,
)
from studio_data.services.securities.contracts import SecurityMaterializeRequest


def materialize_security_record(
    payload: SecurityMaterializeRequest,
) -> StudioInstrumentRecord:
    try:
        record = materialize_security(
            payload.instrument_type,
            payload.catalog_provider,
            payload.catalog_symbol,
            refresh_eod=payload.refresh_eod,
        )
    except (FmpApiError, EquityNotSupportedError, EtfNotSupportedError) as error:
        raise ValueError(str(error)) from error
    instrument_id = str(record["instrument_id"])
    notify_market_data_downstream_refresh(
        instrument_ids=[instrument_id], raise_on_error=True
    )
    return StudioInstrumentRecord.model_validate(record)


def refresh_security_record(
    instrument_id: str, full_history: bool = False
) -> StudioInstrumentRecord:
    try:
        record = refresh_security_eod(instrument_id, full_history=full_history)
    except FmpApiError as error:
        raise ValueError(str(error)) from error
    notify_market_data_downstream_refresh(
        instrument_ids=[instrument_id], raise_on_error=True
    )
    return StudioInstrumentRecord.model_validate(record)
