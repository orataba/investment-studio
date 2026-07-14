from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from numbers import Integral, Real
from typing import Literal, cast
from uuid import UUID, uuid5


# These namespaces are part of the persisted identity contract. Never rotate them.
QUOTE_SERIES_NAMESPACE = UUID("0c8c59ee-8dd1-5cc7-b079-b8f0888b0a06")
QUOTE_OBSERVATION_NAMESPACE = UUID("eda93e14-59b7-5073-be9a-151fac3992c5")
QUOTE_REVISION_NAMESPACE = UUID("ca0c3ef2-3a5e-5cd8-be26-5ce75056af7f")

WITHDRAWN_STATUS = "withdrawn"
USABLE_CURRENT_STATUS = "complete"
SOURCE_OBSERVATION_STATUSES = frozenset({"complete", "partial", "rejected"})
REVISION_STATUSES = frozenset({*SOURCE_OBSERVATION_STATUSES, WITHDRAWN_STATUS})
VALID_METRIC_FAMILIES = frozenset({"price", "nav", "fx"})
VALID_QUOTE_BASES: dict[str, str] = {
    "last": "price",
    "close": "price",
    "adjusted_close": "price",
    "official_nav": "nav",
    "total_return_nav": "nav",
    "cumulative_nav": "nav",
    "accumulated_nav": "nav",
    "cum_nav": "nav",
    "dividend_adjusted_nav": "nav",
    "reinvested_nav": "nav",
    "spot": "fx",
    "clean_price": "price",
    "dirty_price": "price",
    "par": "price",
}

QUOTE_REVISION_PAYLOAD_SCHEMA_V1 = 1
QUOTE_REVISION_PAYLOAD_SCHEMA_V2 = 2
NumericScaleState = Literal["declared", "binary_inferred", "legacy_inferred"]
CURRENT_NUMERIC_SCALE_STATES = frozenset({"declared", "binary_inferred"})


@dataclass(frozen=True)
class QuoteNumericEvidence:
    """Canonical quote value plus the scale of the representation we received.

    ``value`` is deliberately canonicalized for arithmetic and storage, while
    ``value_input_scale`` remains separate so ``1.2300`` is not silently made
    indistinguishable from ``1.23``.  A binary floating-point input can only be
    labelled ``binary_inferred``; it is converted through Python's stable
    shortest round-trip representation and never masquerades as provider-
    declared decimal precision.
    """

    value: str
    value_input_scale: int
    numeric_scale_state: NumericScaleState


def _input_decimal_and_state(value: object) -> tuple[Decimal, NumericScaleState]:
    if isinstance(value, bool):
        raise ValueError("quote value must be a decimal, not a boolean")
    if isinstance(value, Decimal):
        return value, "declared"
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError("quote value must be a valid decimal")
        try:
            return Decimal(normalized), "declared"
        except InvalidOperation as error:
            raise ValueError("quote value must be a valid decimal") from error
    if isinstance(value, Integral):
        return Decimal(int(value)), "declared"
    if isinstance(value, Real):
        # ``repr(float(...))`` is Python's stable shortest decimal that round-
        # trips to the same binary value.  It is reproducible, but explicitly
        # not evidence of the source system's reported decimal scale.
        return Decimal(repr(float(value))), "binary_inferred"
    raise ValueError(
        "quote value must be supplied as decimal text, Decimal, integer, or float"
    )


def quote_numeric_evidence(
    value: object,
    *,
    value_input_scale: object = None,
    numeric_scale_state: object = None,
) -> QuoteNumericEvidence:
    resolved, scale_state = _input_decimal_and_state(value)
    if not resolved.is_finite():
        raise ValueError("quote value must be finite")
    inferred_scale = max(-resolved.as_tuple().exponent, 0)
    if value_input_scale is None and numeric_scale_state is None:
        input_scale = inferred_scale
    elif value_input_scale is None or numeric_scale_state is None:
        raise ValueError("quote input scale and state must be supplied together")
    else:
        if isinstance(value_input_scale, bool):
            raise ValueError("quote input scale must be a non-negative integer")
        if isinstance(value_input_scale, Integral):
            input_scale = int(value_input_scale)
        elif isinstance(value_input_scale, str):
            normalized_scale = value_input_scale.strip()
            if not (
                normalized_scale == "0"
                or (
                    normalized_scale[:1] in "123456789"
                    and (not normalized_scale[1:] or normalized_scale[1:].isdigit())
                )
            ):
                raise ValueError("quote input scale must be a non-negative integer")
            input_scale = int(normalized_scale)
        else:
            raise ValueError("quote input scale must be a non-negative integer")
        if input_scale < 0 or input_scale < max(
            -Decimal(canonical_decimal_text(resolved)).as_tuple().exponent,
            0,
        ):
            raise ValueError("quote input scale is inconsistent with its decimal value")
        normalized_state = str(numeric_scale_state).strip()
        if normalized_state not in CURRENT_NUMERIC_SCALE_STATES:
            raise ValueError(
                "new quote numeric evidence must be declared or binary_inferred"
            )
        if scale_state == "binary_inferred" and normalized_state != "binary_inferred":
            raise ValueError("binary floating-point input cannot be marked declared")
        scale_state = cast(NumericScaleState, normalized_state)
    return QuoteNumericEvidence(
        value=canonical_decimal_text(resolved),
        value_input_scale=input_scale,
        numeric_scale_state=scale_state,
    )


def canonical_decimal_text(value: object) -> str:
    try:
        resolved = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError("quote value must be a valid decimal") from error
    if not resolved.is_finite():
        raise ValueError("quote value must be finite")
    if resolved == 0:
        return "0"
    fixed = format(resolved, "f")
    return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed


def normalize_source_ref(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def normalize_revision_status(value: object) -> str:
    normalized = str(value or USABLE_CURRENT_STATUS).strip().lower()
    normalized = normalized or USABLE_CURRENT_STATUS
    if normalized not in REVISION_STATUSES:
        raise ValueError(f'unsupported quote revision status "{normalized}"')
    return normalized


def normalize_optional_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = str(value).strip()
        if not normalized:
            return None
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def canonical_timestamp(value: object) -> str | None:
    parsed = normalize_optional_timestamp(value)
    if parsed is None:
        return None
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def make_quote_series_id(
    *,
    instrument_id: str,
    metric_family: str,
    quote_basis: str,
    currency: str,
) -> str:
    identity = "\x1f".join(
        (
            instrument_id.strip(),
            metric_family.strip().lower(),
            quote_basis.strip().lower(),
            currency.strip().upper(),
        )
    )
    return str(uuid5(QUOTE_SERIES_NAMESPACE, identity))


def make_quote_observation_id(*, quote_series_id: str, as_of_date: date) -> str:
    return str(
        uuid5(
            QUOTE_OBSERVATION_NAMESPACE,
            f"{quote_series_id.strip()}\x1f{as_of_date.isoformat()}",
        )
    )


def make_quote_revision_id(*, observation_id: str, revision_number: int) -> str:
    if revision_number < 1:
        raise ValueError("revision_number must be positive")
    return str(
        uuid5(
            QUOTE_REVISION_NAMESPACE,
            f"{observation_id.strip()}\x1f{revision_number}",
        )
    )


def quote_revision_payload_hash(
    *,
    value: str | None,
    source_ref: object,
    status: object,
    source_published_at: object = None,
    payload_schema_version: int = QUOTE_REVISION_PAYLOAD_SCHEMA_V1,
    value_input_scale: int | None = None,
    numeric_scale_state: NumericScaleState | None = None,
) -> str:
    normalized_status = normalize_revision_status(status)
    if payload_schema_version not in {
        QUOTE_REVISION_PAYLOAD_SCHEMA_V1,
        QUOTE_REVISION_PAYLOAD_SCHEMA_V2,
    }:
        raise ValueError("unsupported quote revision payload schema version")
    payload = {
        "kind": "withdrawn" if normalized_status == WITHDRAWN_STATUS else "observation",
        "source_published_at": canonical_timestamp(source_published_at),
        "source_ref": normalize_source_ref(source_ref),
        "status": normalized_status,
        "value": None if value is None else canonical_decimal_text(value),
    }
    if payload_schema_version == QUOTE_REVISION_PAYLOAD_SCHEMA_V2:
        if normalized_status == WITHDRAWN_STATUS:
            if (
                value is not None
                or value_input_scale is not None
                or numeric_scale_state is not None
            ):
                raise ValueError(
                    "withdrawn quote payload must not carry numeric evidence"
                )
        else:
            if value is None:
                raise ValueError("non-withdrawn quote payload requires a value")
            if value_input_scale is None or value_input_scale < 0:
                raise ValueError("quote payload requires a non-negative input scale")
            if numeric_scale_state not in CURRENT_NUMERIC_SCALE_STATES:
                raise ValueError(
                    "quote payload scale state must be declared or binary_inferred"
                )
        payload.update(
            {
                "numeric_scale_state": numeric_scale_state,
                "payload_schema_version": payload_schema_version,
                "value_input_scale": value_input_scale,
            }
        )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
