from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class FmpExchange:
    exchange_code: str
    market: str
    label: str
    currency: str
    requires_profile_currency: bool = False


@dataclass(frozen=True)
class FmpQuoteContract:
    provider_currency: str
    currency: str
    price_multiplier: Decimal


FMP_EQUITY_CATALOG_EXCHANGES = {
    "NASDAQ": FmpExchange("XNAS", "US", "NASDAQ", "USD"),
    "NYSE": FmpExchange("XNYS", "US", "NYSE", "USD"),
    "AMEX": FmpExchange("XASE", "US", "NYSE American", "USD"),
    "HKSE": FmpExchange("XHKG", "HK", "Hong Kong Exchange", "HKD"),
    "SHH": FmpExchange("XSHG", "CN", "Shanghai Stock Exchange", "CNY"),
    "SHZ": FmpExchange("XSHE", "CN", "Shenzhen Stock Exchange", "CNY"),
    "LSE": FmpExchange("XLON", "EU", "London Stock Exchange", "GBP", True),
    "XETRA": FmpExchange("XETR", "EU", "Deutsche Börse Xetra", "EUR", True),
    "PAR": FmpExchange("XPAR", "EU", "Euronext Paris", "EUR", True),
    "AMS": FmpExchange("XAMS", "EU", "Euronext Amsterdam", "EUR", True),
    "MIL": FmpExchange("XMIL", "EU", "Borsa Italiana", "EUR", True),
    "SIX": FmpExchange("XSWX", "EU", "SIX Swiss Exchange", "CHF", True),
}

NYSE_ARCA = FmpExchange("ARCX", "US", "NYSE Arca", "USD")

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
    "NYSEARCA": NYSE_ARCA,
    "NEWYORKSTOCKEXCHANGEARCA": NYSE_ARCA,
    "ARCA": NYSE_ARCA,
    "HKG": FMP_EQUITY_CATALOG_EXCHANGES["HKSE"],
    "SSE": FMP_EQUITY_CATALOG_EXCHANGES["SHH"],
    "SZSE": FMP_EQUITY_CATALOG_EXCHANGES["SHZ"],
    "BATS": FMP_ETF_CATALOG_EXCHANGES["CBOE"],
}

_EXCHANGES_BY_MIC = {
    exchange.exchange_code: exchange
    for exchange in FMP_ETF_CATALOG_EXCHANGES.values()
}
_EXCHANGES_BY_MIC[NYSE_ARCA.exchange_code] = NYSE_ARCA


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


def profile_quote_contract(profile: dict[str, object]) -> FmpQuoteContract:
    provider_currency = str(profile.get("currency") or "").strip()
    if not provider_currency:
        raise ValueError("FMP profile is missing its quote currency.")
    if provider_currency == "GBp" or provider_currency.upper() == "GBX":
        return FmpQuoteContract(
            provider_currency=provider_currency,
            currency="GBP",
            price_multiplier=Decimal("0.01"),
        )
    currency = provider_currency.upper()
    if not currency.isalpha() or not 3 <= len(currency) <= 8:
        raise ValueError(f'FMP profile has invalid quote currency "{provider_currency}".')
    return FmpQuoteContract(
        provider_currency=provider_currency,
        currency=currency,
        price_multiplier=Decimal("1"),
    )
