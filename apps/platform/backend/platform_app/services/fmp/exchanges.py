from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FmpExchange:
    exchange_code: str
    market: str
    label: str
    currency: str


FMP_EQUITY_CATALOG_EXCHANGES = {
    "NASDAQ": FmpExchange("XNAS", "US", "NASDAQ", "USD"),
    "NYSE": FmpExchange("XNYS", "US", "NYSE", "USD"),
    "AMEX": FmpExchange("XASE", "US", "NYSE American", "USD"),
    "HKSE": FmpExchange("XHKG", "HK", "Hong Kong Exchange", "HKD"),
    "SHH": FmpExchange("XSHG", "CN", "Shanghai Stock Exchange", "CNY"),
    "SHZ": FmpExchange("XSHE", "CN", "Shenzhen Stock Exchange", "CNY"),
}

FMP_ETF_CATALOG_EXCHANGES = {
    **FMP_EQUITY_CATALOG_EXCHANGES,
    "CBOE": FmpExchange("BATS", "US", "Cboe BZX", "USD"),
}

_EXCHANGES_BY_FMP_CODE = {
    **FMP_ETF_CATALOG_EXCHANGES,
    "NASDAQGS": FMP_EQUITY_CATALOG_EXCHANGES["NASDAQ"],
    "NASDAQGM": FMP_EQUITY_CATALOG_EXCHANGES["NASDAQ"],
    "NASDAQCM": FMP_EQUITY_CATALOG_EXCHANGES["NASDAQ"],
    "NYSEAMERICAN": FMP_EQUITY_CATALOG_EXCHANGES["AMEX"],
    "HKG": FMP_EQUITY_CATALOG_EXCHANGES["HKSE"],
    "SSE": FMP_EQUITY_CATALOG_EXCHANGES["SHH"],
    "SZSE": FMP_EQUITY_CATALOG_EXCHANGES["SHZ"],
    "BATS": FMP_ETF_CATALOG_EXCHANGES["CBOE"],
}

_EXCHANGES_BY_MIC = {
    exchange.exchange_code: exchange
    for exchange in FMP_ETF_CATALOG_EXCHANGES.values()
}


def resolve_exchange(*values: object) -> FmpExchange | None:
    for raw_value in values:
        normalized = "".join(
            character
            for character in str(raw_value or "").strip().upper()
            if character.isalnum()
        )
        if normalized in _EXCHANGES_BY_FMP_CODE:
            return _EXCHANGES_BY_FMP_CODE[normalized]
    return None


def exchange_by_code(exchange_code: str) -> FmpExchange | None:
    return _EXCHANGES_BY_MIC.get(exchange_code.strip().upper())


def canonical_exchange_ticker(*, fmp_symbol: str, exchange_code: str) -> str:
    symbol = fmp_symbol.strip().upper()
    if exchange_code == "XSHG" and symbol.endswith(".SS"):
        return symbol[:-3] + ".SH"
    return symbol
