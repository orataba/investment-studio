from __future__ import annotations

from typing import Literal

from studio_data.services.equities import (
    EquityCatalogEmptyError,
    materialize_equity,
    refresh_equity_eod,
    search_equities,
    sync_equity_catalog,
)
from studio_data.services.etfs import (
    EtfCatalogEmptyError,
    materialize_etf,
    refresh_etf_eod,
    search_etfs,
    sync_etf_catalog,
)
from studio_data.services.fmp import FmpClient, refresh_fmp_eod
from studio_data.services.instrument_store import get_instrument


MaterializableSecurityType = Literal["equity", "etf"]


def _search_rank(item: dict[str, object], query: str) -> tuple[object, ...]:
    normalized = query.casefold()
    symbol = str(item.get("symbol") or "").casefold()
    catalog_symbol = str(item.get("catalog_symbol") or "").casefold()
    name = str(item.get("name") or "").casefold()
    return (
        0 if normalized in {symbol, catalog_symbol} else 1,
        0 if symbol.startswith(normalized) or catalog_symbol.startswith(normalized) else 1,
        0 if normalized in name else 1,
        name,
        str(item.get("instrument_type") or ""),
        symbol,
    )


def search_securities(
    query: str,
    *,
    limit: int = 10,
) -> tuple[list[dict[str, object]], dict[MaterializableSecurityType, str]]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Security search query must not be blank.")
    bounded_limit = max(1, min(limit, 25))
    results: list[dict[str, object]] = []
    errors: dict[MaterializableSecurityType, str] = {}
    try:
        results.extend(search_equities(normalized_query, limit=bounded_limit))
    except EquityCatalogEmptyError as error:
        errors["equity"] = str(error)
    try:
        results.extend(search_etfs(normalized_query, limit=bounded_limit))
    except EtfCatalogEmptyError as error:
        errors["etf"] = str(error)
    return sorted(results, key=lambda item: _search_rank(item, normalized_query))[
        :bounded_limit
    ], errors


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
