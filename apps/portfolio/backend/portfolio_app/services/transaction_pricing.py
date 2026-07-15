from __future__ import annotations

from collections.abc import Mapping

from portfolio_ops_instrument_core import canonical_price_contract


def transaction_price_scale(instrument_ref: Mapping[str, object]) -> float:
    instrument_type = str(instrument_ref.get("instrument_type") or "").strip().lower()
    if not instrument_type:
        raise ValueError("Transaction pricing requires a canonical instrument type.")
    quote_basis = "dirty_price" if instrument_type == "bond" else "close"
    _, price_scale = canonical_price_contract(
        instrument_type=instrument_type,
        metric_family="price",
        quote_basis=quote_basis,
    )
    return float(price_scale)
