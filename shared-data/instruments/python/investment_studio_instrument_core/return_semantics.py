from __future__ import annotations

from collections.abc import Iterable, Mapping


CONFIRMED_TOTAL_RETURN_QUOTE_BASES = frozenset(
    {
        "adjusted_close",
        "total_return_nav",
    }
)
INDEX_LEVEL_QUOTE_BASES = frozenset({"close", "last"})


def _normalized_text(value: object) -> str:
    return str(value or "").strip().lower()


def resolve_quote_return_semantics(
    *,
    instrument_type: object,
    quote_basis: object,
    source_settings: Mapping[str, object] | None = None,
) -> str:
    """Resolve economic return semantics separately from the provider field.

    ``close`` identifies a level field, not whether the level is a price-return
    or total-return index.  Index close/last therefore requires the explicit
    Registry source contract.  Canonical adjusted-close and total-return NAV
    identities remain total return regardless of instrument type.
    """

    normalized_basis = _normalized_text(quote_basis)
    if normalized_basis in CONFIRMED_TOTAL_RETURN_QUOTE_BASES:
        return "total_return"

    normalized_instrument_type = _normalized_text(instrument_type)
    if normalized_instrument_type == "index" and normalized_basis in INDEX_LEVEL_QUOTE_BASES:
        configured_semantics = _normalized_text(
            (source_settings or {}).get("return_semantics")
        )
        if configured_semantics in {"price_return", "total_return"}:
            return configured_semantics
        return "unknown"

    if normalized_instrument_type == "crypto" and normalized_basis in INDEX_LEVEL_QUOTE_BASES:
        return "price_return"
    if normalized_instrument_type == "fx" and normalized_basis == "spot":
        return "price_return"

    return "unknown"


def confirmed_total_return_quote_bases(
    *,
    instrument_type: object,
    quote_bases: Iterable[object],
    source_settings: Mapping[str, object] | None = None,
) -> list[str]:
    """Keep policy order while retaining only confirmed total-return series."""

    confirmed: list[str] = []
    for raw_basis in quote_bases:
        quote_basis = _normalized_text(raw_basis)
        if not quote_basis or quote_basis in confirmed:
            continue
        if (
            resolve_quote_return_semantics(
                instrument_type=instrument_type,
                quote_basis=quote_basis,
                source_settings=source_settings,
            )
            == "total_return"
        ):
            confirmed.append(quote_basis)
    return confirmed


def analytical_return_quote_bases(
    *,
    instrument_type: object,
    quote_bases: Iterable[object],
    source_settings: Mapping[str, object] | None = None,
) -> list[str]:
    """Select economic return inputs, independently of display/valuation policy.

    Canonical adjusted_close is an ingestion contract for multiplicative total
    return, not an arbitrary provider's field bearing that name. Raw listed
    security prices are never an analytical fallback: even price returns need
    split handling. A price index, spot FX and crypto have distinct explicit
    price-return contracts; their price changes do not promise income/carry.
    """
    kind = _normalized_text(instrument_type)
    eligible: list[str] = []
    for raw_basis in quote_bases:
        basis = _normalized_text(raw_basis)
        if not basis or basis in eligible:
            continue
        semantics = resolve_quote_return_semantics(
            instrument_type=kind, quote_basis=basis, source_settings=source_settings,
        )
        if kind in {"public_fund", "private_fund"}:
            allowed = basis == "total_return_nav"
        elif kind in {"equity", "etf"}:
            allowed = basis == "adjusted_close" and semantics == "total_return"
        elif kind == "index":
            allowed = semantics in {"price_return", "total_return"}
        elif kind == "crypto":
            allowed = basis in INDEX_LEVEL_QUOTE_BASES and semantics == "price_return"
        elif kind == "fx":
            allowed = basis == "spot" and semantics == "price_return"
        else:
            allowed = False
        if allowed:
            eligible.append(basis)
    return eligible
