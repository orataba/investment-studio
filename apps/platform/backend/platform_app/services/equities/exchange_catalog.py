from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EquityExchange:
    exchange_code: str
    market: str
    label: str
    currency: str


FMP_CATALOG_EXCHANGES = {
    "NASDAQ": EquityExchange("XNAS", "US", "NASDAQ", "USD"),
    "NYSE": EquityExchange("XNYS", "US", "NYSE", "USD"),
    "AMEX": EquityExchange("XASE", "US", "NYSE American", "USD"),
    "HKSE": EquityExchange("XHKG", "HK", "Hong Kong Exchange", "HKD"),
    "SHH": EquityExchange("XSHG", "CN", "Shanghai Stock Exchange", "CNY"),
    "SHZ": EquityExchange("XSHE", "CN", "Shenzhen Stock Exchange", "CNY"),
}

_EXCHANGES_BY_FMP_CODE = {
    **FMP_CATALOG_EXCHANGES,
    "NASDAQGS": FMP_CATALOG_EXCHANGES["NASDAQ"],
    "NASDAQGM": FMP_CATALOG_EXCHANGES["NASDAQ"],
    "NASDAQCM": FMP_CATALOG_EXCHANGES["NASDAQ"],
    "NYSEAMERICAN": FMP_CATALOG_EXCHANGES["AMEX"],
    "HKG": FMP_CATALOG_EXCHANGES["HKSE"],
    "SSE": FMP_CATALOG_EXCHANGES["SHH"],
    "SZSE": FMP_CATALOG_EXCHANGES["SHZ"],
}

_EXCHANGES_BY_MIC = {
    exchange.exchange_code: exchange
    for exchange in FMP_CATALOG_EXCHANGES.values()
}


def resolve_exchange(*values: object) -> EquityExchange | None:
    for raw_value in values:
        normalized = "".join(
            character
            for character in str(raw_value or "").strip().upper()
            if character.isalnum()
        )
        if normalized in _EXCHANGES_BY_FMP_CODE:
            return _EXCHANGES_BY_FMP_CODE[normalized]
    return None


def exchange_by_code(exchange_code: str) -> EquityExchange | None:
    return _EXCHANGES_BY_MIC.get(exchange_code.strip().upper())


def canonical_exchange_ticker(*, fmp_symbol: str, exchange_code: str) -> str:
    symbol = fmp_symbol.strip().upper()
    if exchange_code == "XSHG" and symbol.endswith(".SS"):
        return symbol[:-3] + ".SH"
    return symbol
