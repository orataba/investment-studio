from __future__ import annotations

from typing import Literal

from studio_data.services.equities import (
    materialize_equity,
    refresh_equity_eod,
    sync_equity_catalog,
)
from studio_data.services.etfs import (
    materialize_etf,
    refresh_etf_eod,
    sync_etf_catalog,
)
from studio_data.services.fmp.client import FmpClient
from studio_data.services.instrument_store import get_instrument


MaterializableSecurityType = Literal["equity", "etf"]


def search_securities(
    query: str,
    *,
    limit: int = 10,
) -> tuple[list[dict[str, object]], dict[MaterializableSecurityType, str]]:
    from investment_studio_instrument_core.security_directory import search_directory
    from studio_data.db.session import get_session_factory
    return search_directory(get_session_factory(), query, limit)


def materialize_security(
    instrument_type: MaterializableSecurityType,
    catalog_provider: str,
    catalog_symbol: str,
    *,
    refresh_eod: bool = True,
    client: FmpClient | None = None,
) -> dict[str, object]:
    if catalog_provider != "fmp":
        raise ValueError(f"Unsupported security catalog provider: {catalog_provider}")
    if instrument_type == "equity":
        return materialize_equity(
            catalog_symbol,
            refresh_eod=refresh_eod,
            client=client,
        )
    return materialize_etf(
        catalog_symbol,
        refresh_eod=refresh_eod,
        client=client,
    )


def refresh_security_eod(
    instrument_id: str,
    *,
    full_history: bool = False,
    store=None,
) -> dict[str, object]:
    instrument = get_instrument(instrument_id)
    if instrument is None:
        raise ValueError(f"Registry instrument not found: {instrument_id}")
    instrument_type = str(instrument.get("instrument_type") or "")
    if instrument_type == "equity":
        return refresh_equity_eod(
            instrument_id,
            full_history=full_history,
            store=store,
        )
    if instrument_type == "etf":
        return refresh_etf_eod(
            instrument_id,
            full_history=full_history,
            store=store,
        )
    if instrument_type == "index":
        source_profile = str(
            dict(instrument.get("source_settings") or {}).get("source_api_profile") or ""
        ).strip().lower()
        if source_profile == "fmp":
            from studio_data.services.fmp.eod import refresh_fmp_eod

            return refresh_fmp_eod(
                instrument_id,
                instrument_type="index",
                full_history=full_history,
                store=store,
            )
        if source_profile in {"tushare", "tushare_pro", "tushare-pro"}:
            from studio_data.services.market_data_ops import refresh_market_data

            refreshed = refresh_market_data(
                instrument_id=instrument_id,
                updated_by="tushare_index_sync",
                full_history=full_history,
                source="tushare",
            )
            if refreshed is None:
                raise RuntimeError(f"Registry index disappeared during refresh: {instrument_id}")
            return refreshed
        raise ValueError(f"Index {instrument_id} has no supported primary API source.")
    raise ValueError("Security refresh only supports equity, ETF, and index instruments.")


def sync_security_catalogs(*, client: FmpClient | None = None) -> dict[str, object]:
    fmp = client or FmpClient()
    equity = sync_equity_catalog(client=fmp)
    etf = sync_etf_catalog(client=fmp)
    return {
        "active_count": int(equity["active_count"]) + int(etf["active_count"]),
        "catalogs": {"equity": equity, "etf": etf},
    }
