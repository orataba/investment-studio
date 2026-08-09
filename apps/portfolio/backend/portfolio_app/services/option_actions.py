"""Canonical option side/open-close semantics for the current transaction schema."""

from __future__ import annotations

from typing import Literal, Mapping


OptionAction = Literal[
    "buy_to_open",
    "sell_to_close",
    "sell_to_open",
    "buy_to_close",
]


def _value(value: object, key: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _contract_type_from_ref(derivative_contract: object | None) -> str | None:
    """Return a normalized local contract type from dict or Pydantic objects."""

    raw = _value(derivative_contract, "contract_type")
    if raw is None:
        return None
    normalized = str(raw).strip().lower()
    return normalized or None


def resolve_option_action(
    transaction_type: object | Mapping[str, object],
    derivative_contract: object | None = None,
    *,
    contract_type: object | None = None,
) -> OptionAction | None:
    """Resolve a transaction fact to its canonical option action.

    ``transaction_type`` may be a string or a transaction mapping/model.  A
    generic ``buy``/``sell`` is considered an option fact only when the local
    contract is explicitly typed ``option``. The two writer transaction
    types are intrinsically option-specific.
    """

    if isinstance(transaction_type, Mapping):
        raw_type = transaction_type.get("transaction_type")
        if derivative_contract is None:
            derivative_contract = transaction_type.get("derivative_contract")
        if contract_type is None:
            contract_type = transaction_type.get("contract_type")
    else:
        # Accept Pydantic/dataclass transaction objects in addition to the
        # persisted mapping shape; callers must not grow a second resolver for
        # API models.
        raw_type = _value(transaction_type, "transaction_type")
        if raw_type is None:
            raw_type = transaction_type
        if derivative_contract is None:
            derivative_contract = _value(transaction_type, "derivative_contract")
        if contract_type is None:
            contract_type = _value(transaction_type, "contract_type")

    normalized_type = str(raw_type or "").strip().lower()
    normalized_contract_type = (
        str(contract_type).strip().lower()
        if contract_type is not None and str(contract_type).strip()
        else _contract_type_from_ref(derivative_contract)
    )

    if normalized_type == "option_write":
        return "sell_to_open"
    if normalized_type == "option_buy_to_close":
        return "buy_to_close"
    if normalized_type == "buy" and normalized_contract_type == "option":
        return "buy_to_open"
    if normalized_type == "sell" and normalized_contract_type == "option":
        return "sell_to_close"
    return None


__all__ = [
    "OptionAction",
    "resolve_option_action",
]
