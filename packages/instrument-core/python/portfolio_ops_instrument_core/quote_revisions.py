from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
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
) -> str:
    normalized_status = normalize_revision_status(status)
    payload = {
        "kind": "withdrawn" if normalized_status == WITHDRAWN_STATUS else "observation",
        "source_published_at": canonical_timestamp(source_published_at),
        "source_ref": normalize_source_ref(source_ref),
        "status": normalized_status,
        "value": None if value is None else canonical_decimal_text(value),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
