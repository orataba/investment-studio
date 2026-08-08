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


def _instrument_type_from_ref(instrument_ref: object | None) -> str | None:
    """Return a normalised instrument type from dict or Pydantic objects."""

    raw = _value(instrument_ref, "instrument_type")
    if raw is None:
        return None
    normalized = str(raw).strip().lower()
    return normalized or None


def resolve_option_action(
    transaction_type: object | Mapping[str, object],
    instrument_ref: object | None = None,
    *,
    instrument_type: object | None = None,
) -> OptionAction | None:
    """Resolve a transaction fact to its canonical option action.

    ``transaction_type`` may be a string or a transaction mapping/model.  A
    generic ``buy``/``sell`` is considered an option fact only when the
    instrument is explicitly typed ``option``. The two writer transaction
    types are intrinsically option-specific.
    """

    if isinstance(transaction_type, Mapping):
        raw_type = transaction_type.get("transaction_type")
        if instrument_ref is None:
            instrument_ref = transaction_type.get("instrument_ref")
        if instrument_type is None:
            instrument_type = transaction_type.get("instrument_type")
    else:
        # Accept Pydantic/dataclass transaction objects in addition to the
        # persisted mapping shape; callers must not grow a second resolver for
        # API models.
        raw_type = _value(transaction_type, "transaction_type")
        if raw_type is None:
            raw_type = transaction_type
        if instrument_ref is None:
            instrument_ref = _value(transaction_type, "instrument_ref")
        if instrument_type is None:
            instrument_type = _value(transaction_type, "instrument_type")

    normalized_type = str(raw_type or "").strip().lower()
    normalized_instrument_type = (
        str(instrument_type).strip().lower()
        if instrument_type is not None and str(instrument_type).strip()
        else _instrument_type_from_ref(instrument_ref)
    )

    if normalized_type == "option_write":
        return "sell_to_open"
    if normalized_type == "option_buy_to_close":
        return "buy_to_close"
    if normalized_type == "buy" and normalized_instrument_type == "option":
        return "buy_to_open"
    if normalized_type == "sell" and normalized_instrument_type == "option":
        return "sell_to_close"
    return None


__all__ = [
    "OptionAction",
    "resolve_option_action",
]
