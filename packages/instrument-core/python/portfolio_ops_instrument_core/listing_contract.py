from __future__ import annotations


LISTED_INSTRUMENT_TYPES = frozenset({"equity", "etf"})

# Canonical MICs used by the shared Registry. Provider-specific exchange names
# are translated at the Platform boundary before an instrument is created.
SUPPORTED_LISTING_EXCHANGES: tuple[str, ...] = (
    "XNAS",
    "XNYS",
    "XASE",
    "ARCX",
    "BATS",
    "XHKG",
    "XSHG",
    "XSHE",
    "XLON",
    "XETR",
    "XPAR",
    "XAMS",
    "XMIL",
    "XSWX",
)


def validate_listing_identity(
    *,
    instrument_type: object,
    exchange_code: object,
) -> str | None:
    normalized_type = str(instrument_type or "").strip().lower()
    normalized_exchange = str(exchange_code or "").strip().upper() or None
    if normalized_type in LISTED_INSTRUMENT_TYPES:
        if normalized_exchange is None:
            raise ValueError("Listed equity and ETF instruments require an exchange_code")
        if normalized_exchange not in SUPPORTED_LISTING_EXCHANGES:
            raise ValueError(f'Unsupported listed exchange_code "{normalized_exchange}"')
        return normalized_exchange
    if normalized_exchange is not None:
        raise ValueError("exchange_code is reserved for listed equity and ETF instruments")
    return None
