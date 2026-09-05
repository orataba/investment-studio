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
