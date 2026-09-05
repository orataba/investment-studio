from __future__ import annotations

from collections.abc import Mapping

from investment_studio_instrument_core import canonical_price_contract


def transaction_price_scale(
    *,
    instrument_ref: Mapping[str, object] | None,
    derivative_contract: Mapping[str, object] | None,
) -> float:
    if derivative_contract is not None:
        contract_type = str(
            derivative_contract.get("contract_type") or ""
        ).strip().lower()
        if contract_type == "fcn":
            return 1.0
        if contract_type != "option":
            raise ValueError("Unsupported derivative contract type.")
        option_terms = derivative_contract.get("terms")
        if not isinstance(option_terms, Mapping):
            raise ValueError("Option transaction pricing requires contract terms.")
        multiplier = float(option_terms.get("contract_multiplier") or 0)
        if multiplier <= 0:
            raise ValueError("Option contract multiplier must be positive.")
        return multiplier
    if instrument_ref is None:
        raise ValueError("Transaction pricing requires an asset reference.")
    instrument_type = str(instrument_ref.get("instrument_type") or "").strip().lower()
    if not instrument_type:
        raise ValueError("Transaction pricing requires a canonical instrument type.")
    _, price_scale = canonical_price_contract(
        instrument_type=instrument_type,
        metric_family="price",
        quote_basis="close",
    )
    return float(price_scale)
