from __future__ import annotations

from typing import Literal

from studio_data.services.fmp.client import FmpClient
from studio_data.services.fmp.exchanges import (
    FmpExchange,
    FmpQuoteContract,
    profile_quote_contract,
    resolve_exchange,
)


def listing_quote_contract(
    *,
    client: FmpClient,
    symbol: str,
    exchange: FmpExchange,
    instrument_type: Literal["equity", "etf"],
) -> FmpQuoteContract:
    profile = client.profile(symbol)
    profile_exchange = resolve_exchange(
        profile.get("exchangeShortName"),
        profile.get("exchange"),
    )
    if profile_exchange != exchange:
        raise ValueError(
            f"FMP profile exchange does not match the local catalog for {symbol}."
        )
    is_etf = profile.get("isEtf") is True
    if is_etf != (instrument_type == "etf"):
        raise ValueError(
            f"FMP profile instrument type does not match the local catalog for {symbol}."
        )
    return profile_quote_contract(profile)
