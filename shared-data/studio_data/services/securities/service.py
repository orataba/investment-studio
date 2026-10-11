from __future__ import annotations

from typing import Literal
import os
from pathlib import Path

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
    if catalog_provider == "futu":
        from studio_market.config import MarketSettings
        from studio_market.numeric import NumericStore
        from studio_data.services.futu import materialize_futu_security

        market = NumericStore(MarketSettings.from_environment())
        try:
            candidates = market.latest("futu_security_catalog", symbols=[catalog_symbol], limit=1)["rows"]
            if not candidates or not candidates[0].get("active"):
                raise ValueError("富途本地目录没有此证券，请先同步行情目录。")
            candidate = candidates[0]
            if candidate.get("instrument_type") != instrument_type:
                raise ValueError("富途目录中的证券类型与所选标的不符。")
            return materialize_futu_security(catalog_symbol, catalog_record=candidate, refresh_eod=refresh_eod)
        finally:
            market.close()
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


def sync_security_catalogs(*, client: FmpClient | None = None,
                           prefer_current_futu: bool = False) -> dict[str, object]:
    fmp = client or FmpClient()
    equity = sync_equity_catalog(client=fmp)
    etf = sync_etf_catalog(client=fmp)
    futu = _sync_futu_catalog_if_authorized(prefer_current=prefer_current_futu)
    return {
        "active_count": int(equity["active_count"]) + int(etf["active_count"]) + int(futu.get("active_count", 0)),
        "catalogs": {"equity": equity, "etf": etf, "futu": futu},
    }


def _sync_futu_catalog_if_authorized(*, prefer_current: bool = False) -> dict:
    from studio_market.futu.oauth import DEFAULT_CONFIG_DIR, FutuOAuth

    directory = Path(os.environ.get("INVESTMENT_STUDIO_MARKET_FUTU_CONFIG_DIR", str(DEFAULT_CONFIG_DIR))).expanduser()
    with FutuOAuth(directory) as oauth:
        if not oauth.status()["authenticated"]:
            return {"status": "not_authorized", "active_count": 0}
    from studio_market.config import MarketSettings
    from studio_market.futu.catalog import current_catalog_snapshot, sync_catalog

    settings = MarketSettings.from_environment()
    if prefer_current:
        retained = current_catalog_snapshot(settings)
        if retained is not None:
            return retained
    return sync_catalog(settings)
