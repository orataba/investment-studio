from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class FxInstrumentIdentity:
    instrument_id: str
    base_currency: str
    quote_currency: str


@dataclass(frozen=True, slots=True)
class ValidatedFxSpot:
    identity: FxInstrumentIdentity
    rate: Decimal
    status: str


FX_INSTRUMENT_IDENTITIES: tuple[FxInstrumentIdentity, ...] = (
    FxInstrumentIdentity(
        instrument_id="fx-usd-hkd",
        base_currency="USD",
        quote_currency="HKD",
    ),
    FxInstrumentIdentity(
        instrument_id="fx-usd-cny",
        base_currency="USD",
        quote_currency="CNY",
    ),
)
SUPPORTED_FX_CURRENCIES: tuple[str, ...] = tuple(
    dict.fromkeys(
        currency
        for identity in FX_INSTRUMENT_IDENTITIES
        for currency in (identity.base_currency, identity.quote_currency)
    )
)
PIVOT_CURRENCY = "USD"
VALID_FX_DATA_STATUSES = frozenset({"complete", "partial", "unavailable"})

_FX_IDENTITY_BY_INSTRUMENT_ID = {
    identity.instrument_id: identity for identity in FX_INSTRUMENT_IDENTITIES
}
_FX_IDENTITY_BY_PAIR = {
    (identity.base_currency, identity.quote_currency): identity
    for identity in FX_INSTRUMENT_IDENTITIES
}


def normalize_fx_currency(value: object) -> str:
    return str(value or "").strip().upper()


def fx_instrument_identity(instrument_id: object) -> FxInstrumentIdentity | None:
    return _FX_IDENTITY_BY_INSTRUMENT_ID.get(str(instrument_id or "").strip())


def fx_instrument_identity_for_pair(
    base_currency: object,
    quote_currency: object,
) -> FxInstrumentIdentity | None:
    return _FX_IDENTITY_BY_PAIR.get(
        (
            normalize_fx_currency(base_currency),
            normalize_fx_currency(quote_currency),
        )
    )


def parse_positive_fx_rate(value: object) -> Decimal:
    try:
        rate = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("FX spot rate must be a finite positive decimal.") from error
    if not rate.is_finite() or rate <= 0:
        raise ValueError("FX spot rate must be a finite positive decimal.")
    return rate


def validate_fx_market_data_contract(
    *,
    instrument_id: object,
    instrument_type: object,
    instrument_currency: object,
    metric_family: object,
    quote_basis: object,
    point_currency: object,
    value: object,
    status: object,
) -> ValidatedFxSpot | None:
    normalized_instrument_type = str(instrument_type or "").strip().lower()
    normalized_metric_family = str(metric_family or "").strip().lower()
    normalized_quote_basis = str(quote_basis or "").strip().lower()
    is_fx_contract = (
        normalized_instrument_type == "fx"
        or normalized_metric_family == "fx"
        or normalized_quote_basis == "spot"
    )
    if not is_fx_contract:
        return None
    if (
        normalized_instrument_type != "fx"
        or normalized_metric_family != "fx"
        or normalized_quote_basis != "spot"
    ):
        raise ValueError(
            "FX market data requires an fx instrument with metric_family \"fx\" "
            "and quote_basis \"spot\"."
        )

    identity = fx_instrument_identity(instrument_id)
    if identity is None:
        raise ValueError(
            f'FX spot instrument "{str(instrument_id or "").strip()}" has no maintained identity.'
        )

    normalized_instrument_currency = normalize_fx_currency(instrument_currency)
    if normalized_instrument_currency != identity.quote_currency:
        raise ValueError(
            f'FX instrument "{identity.instrument_id}" requires master currency '
            f'"{identity.quote_currency}", not "{normalized_instrument_currency}".'
        )

    normalized_point_currency = normalize_fx_currency(point_currency)
    if normalized_point_currency != identity.quote_currency:
        raise ValueError(
            f'FX spot point for "{identity.instrument_id}" requires currency '
            f'"{identity.quote_currency}", not "{normalized_point_currency}".'
        )

    rate = parse_positive_fx_rate(value)
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in VALID_FX_DATA_STATUSES:
        raise ValueError(
            f'FX spot point has unsupported status "{normalized_status}".'
        )
    return ValidatedFxSpot(
        identity=identity,
        rate=rate,
        status=normalized_status,
    )
