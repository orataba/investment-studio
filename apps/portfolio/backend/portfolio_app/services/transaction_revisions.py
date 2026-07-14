"""Append-only transaction revision primitives.

This module owns the canonical transaction payload representation and the rules
for extending a transaction revision chain.  Callers own the surrounding unit
of work: they must lock the portfolio/existing identities before mutation and
must commit or roll back the SQLAlchemy session themselves.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Literal, Mapping, Sequence, TypeAlias, cast
from uuid import uuid4

from sqlalchemy import Select, func, null, select
from sqlalchemy.orm import Session

from portfolio_app.db.models import (
    TransactionCurrentModel,
    TransactionIdentityRecordModel,
    TransactionRevisionGroupRecordModel,
    TransactionRevisionRecordModel,
)


TRANSACTION_PAYLOAD_SCHEMA_VERSION = "transaction-revision.v1"

TransactionSourceKind: TypeAlias = Literal[
    "manual", "import", "reconciliation", "migration", "system"
]
TransactionActorType: TypeAlias = Literal["user", "service", "migration"]
TransactionActorSource: TypeAlias = Literal[
    "client_asserted",
    "authenticated_principal",
    "trusted_service",
    "migration",
]
InitialRevisionKind: TypeAlias = Literal["baseline", "create"]
DecimalInput: TypeAlias = Decimal | int | str

_SOURCE_KINDS = frozenset({"manual", "import", "reconciliation", "migration", "system"})
_ACTOR_TYPES = frozenset({"user", "service", "migration"})
_ACTOR_SOURCE_TYPES: dict[TransactionActorSource, TransactionActorType] = {
    "client_asserted": "user",
    "authenticated_principal": "user",
    "trusted_service": "service",
    "migration": "migration",
}
_INITIAL_REVISION_KINDS = frozenset({"baseline", "create"})
_DECIMAL_SCALES = {
    "quantity": 12,
    "price": 12,
    "gross_amount": 8,
    "counter_amount": 8,
    "quoted_fx_rate": 18,
    "fees": 8,
    "taxes": 8,
}
_NUMERIC_SCALE_STATES = frozenset({"declared", "legacy_inferred"})
_CONSIDERATION_BASES = frozenset({"exact_quantity_price", "source_reported"})
_METHODOLOGY_OWNED_PER_UNIT_INSTRUMENT_TYPES = frozenset(
    {"equity", "fund", "etf", "exchange_traded_fund"}
)
_PRICE_AMOUNT_TRANSACTION_TYPES = frozenset(
    {"buy", "sell", "dividend_reinvestment", "opening_balance"}
)


class TransactionRevisionError(RuntimeError):
    """Base class for domain failures while appending a revision."""


class TransactionRevisionPayloadError(TransactionRevisionError, ValueError):
    """The command cannot be represented by the canonical payload schema."""


class TransactionRevisionConflictError(TransactionRevisionError):
    """The command was based on state that is no longer current."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        transaction_id: str | None = None,
        expected_revision_id: str | None = None,
        expected_revision_number: int | None = None,
        actual_revision_id: str | None = None,
        actual_revision_number: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.transaction_id = transaction_id
        self.expected_revision_id = expected_revision_id
        self.expected_revision_number = expected_revision_number
        self.actual_revision_id = actual_revision_id
        self.actual_revision_number = actual_revision_number


class TransactionRevisionNoOpError(TransactionRevisionError):
    """The requested change would not alter the current transaction state."""

    def __init__(
        self,
        message: str,
        *,
        transaction_id: str,
        revision_id: str,
        revision_number: int,
        payload_hash: str,
    ) -> None:
        super().__init__(message)
        self.transaction_id = transaction_id
        self.revision_id = revision_id
        self.revision_number = revision_number
        self.payload_hash = payload_hash


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TransactionRevisionPayloadError(f"{field_name} must be a non-blank string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TransactionRevisionPayloadError(f"{field_name} must be a string or null")
    stripped = value.strip()
    return stripped or None


def _aware_utc(value: object, field_name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise TransactionRevisionPayloadError(f"{field_name} must be an ISO datetime") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TransactionRevisionPayloadError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _date_value(value: object, field_name: str) -> date:
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError as exc:
            raise TransactionRevisionPayloadError(f"{field_name} must be an ISO date") from exc
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TransactionRevisionPayloadError(f"{field_name} must be a date")
    return value


def _optional_date(value: object, field_name: str) -> date | None:
    return None if value is None else _date_value(value, field_name)


def _time_value(value: object, field_name: str) -> time:
    if isinstance(value, str):
        try:
            value = time.fromisoformat(value)
        except ValueError as exc:
            raise TransactionRevisionPayloadError(f"{field_name} must be an ISO time") from exc
    if not isinstance(value, time) or value.tzinfo is not None:
        raise TransactionRevisionPayloadError(f"{field_name} must be a timezone-naive time")
    return value


def _canonical_decimal_string(value: Decimal) -> str:
    """Render a finite Decimal without context rounding or presentation zeroes."""

    if value.is_zero():
        return "0"
    sign, digits, exponent = value.as_tuple()
    normalized_digits = list(digits)
    while normalized_digits and normalized_digits[-1] == 0:
        normalized_digits.pop()
        exponent += 1
    coefficient = "".join(str(digit) for digit in normalized_digits) or "0"
    if exponent >= 0:
        rendered = coefficient + ("0" * exponent)
    else:
        split_at = len(coefficient) + exponent
        if split_at > 0:
            rendered = f"{coefficient[:split_at]}.{coefficient[split_at:]}"
        else:
            rendered = f"0.{('0' * -split_at)}{coefficient}"
    return f"-{rendered}" if sign else rendered


def _decimal_fractional_digits(value: Decimal) -> int:
    if value.is_zero():
        return 0
    _sign, digits, exponent = value.as_tuple()
    normalized_digits = list(digits)
    while normalized_digits and normalized_digits[-1] == 0:
        normalized_digits.pop()
        exponent += 1
    return max(-exponent, 0)


def _decimal_integer_digits(value: Decimal) -> int:
    if value.is_zero():
        return 0
    _sign, digits, exponent = value.as_tuple()
    normalized_digits = list(digits)
    while normalized_digits and normalized_digits[-1] == 0:
        normalized_digits.pop()
        exponent += 1
    return max(len(normalized_digits) + exponent, 0)


def _decimal_value_and_input_scale(
    value: object,
    *,
    field_name: str,
    scale: int,
) -> tuple[Decimal, int]:
    if isinstance(value, float):
        raise TransactionRevisionPayloadError(
            f"{field_name} must not cross the fact boundary as a binary float"
        )
    if isinstance(value, bool) or not isinstance(
        value,
        (Decimal, int, str),
    ):
        raise TransactionRevisionPayloadError(f"{field_name} must be decimal-compatible")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TransactionRevisionPayloadError(f"{field_name} must be a valid decimal") from exc
    if not decimal_value.is_finite():
        raise TransactionRevisionPayloadError(f"{field_name} must be finite")

    input_scale = max(-decimal_value.as_tuple().exponent, 0)
    if input_scale > scale:
        raise TransactionRevisionPayloadError(
            f"{field_name} input declares more than {scale} decimal places"
        )
    if _decimal_fractional_digits(decimal_value) > scale:
        raise TransactionRevisionPayloadError(
            f"{field_name} has more than {scale} decimal places; implicit rounding is forbidden"
        )
    if _decimal_integer_digits(decimal_value) > 38 - scale:
        raise TransactionRevisionPayloadError(f"{field_name} exceeds Numeric(38, {scale})")
    canonical = Decimal(_canonical_decimal_string(decimal_value))
    return canonical, input_scale


def _input_scale(
    value: object,
    *,
    field_name: str,
    max_scale: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TransactionRevisionPayloadError(
            f"{field_name} must be an integer between 0 and {max_scale}"
        )
    if not 0 <= value <= max_scale:
        raise TransactionRevisionPayloadError(
            f"{field_name} must be between 0 and {max_scale}"
        )
    return value


def _exact_decimal_product(left: Decimal, right: Decimal) -> Decimal:
    """Multiply two finite decimals without consulting the ambient context."""

    left_sign, left_digits, left_exponent = left.as_tuple()
    right_sign, right_digits, right_exponent = right.as_tuple()
    left_coefficient = int("".join(str(digit) for digit in left_digits) or "0")
    right_coefficient = int("".join(str(digit) for digit in right_digits) or "0")
    coefficient = left_coefficient * right_coefficient
    if left_sign != right_sign:
        coefficient = -coefficient
    rendered = Decimal((1 if coefficient < 0 else 0, tuple(
        int(character) for character in str(abs(coefficient))
    ), left_exponent + right_exponent))
    return rendered.copy_abs() if rendered.is_zero() else rendered


def _json_value(value: object, path: str = "instrument_snapshot_json") -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        raise TransactionRevisionPayloadError(
            f"{path} contains a binary float; exact decimals must be canonical strings"
        )
    if isinstance(value, list | tuple):
        return [_json_value(item, f"{path}[]") for item in value]
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        if any(not isinstance(key, str) for key in value):
            raise TransactionRevisionPayloadError(f"{path} keys must be strings")
        for key in sorted(cast(Mapping[str, object], value)):
            normalized[key] = _json_value(value[key], f"{path}.{key}")
        return normalized
    raise TransactionRevisionPayloadError(f"{path} contains a non-JSON value")


@dataclass(frozen=True, slots=True)
class TransactionRevisionContext:
    """Audit metadata shared by every revision in one atomic command."""

    source_kind: TransactionSourceKind
    change_reason: str
    actor_type: TransactionActorType
    actor_id: str
    actor_display_name: str
    actor_source: TransactionActorSource
    recorded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: str | None = None
    idempotency_key: str | None = None
    source_ref: str | None = None

    def __post_init__(self) -> None:
        source_kind = _required_text(self.source_kind, "source_kind")
        actor_type = _required_text(self.actor_type, "actor_type")
        if source_kind not in _SOURCE_KINDS:
            raise TransactionRevisionPayloadError(f"unsupported source_kind: {source_kind}")
        if actor_type not in _ACTOR_TYPES:
            raise TransactionRevisionPayloadError(f"unsupported actor_type: {actor_type}")
        actor_source = _required_text(self.actor_source, "actor_source")
        if actor_source not in _ACTOR_SOURCE_TYPES:
            raise TransactionRevisionPayloadError(
                f"unsupported actor_source: {actor_source}"
            )
        expected_actor_type = _ACTOR_SOURCE_TYPES[
            cast(TransactionActorSource, actor_source)
        ]
        if actor_type != expected_actor_type:
            raise TransactionRevisionPayloadError(
                "actor_source "
                f"{actor_source} requires actor_type {expected_actor_type}, "
                f"not {actor_type}"
            )
        object.__setattr__(self, "source_kind", cast(TransactionSourceKind, source_kind))
        object.__setattr__(self, "change_reason", _required_text(self.change_reason, "change_reason"))
        object.__setattr__(self, "actor_type", cast(TransactionActorType, actor_type))
        object.__setattr__(self, "actor_id", _required_text(self.actor_id, "actor_id"))
        object.__setattr__(
            self, "actor_display_name", _required_text(self.actor_display_name, "actor_display_name")
        )
        object.__setattr__(
            self,
            "actor_source",
            cast(TransactionActorSource, actor_source),
        )
        object.__setattr__(self, "recorded_at", _aware_utc(self.recorded_at, "recorded_at"))
        for name in ("request_id", "idempotency_key", "source_ref"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class TransactionFactPayload:
    """Canonical live transaction facts stored on each non-tombstone revision."""

    transaction_type: str
    trade_date: date
    trade_time: time
    trade_at: datetime
    trade_timezone: str
    trade_time_is_estimated: bool
    settlement_date: date
    account_id: str
    gross_amount: DecimalInput
    fees: DecimalInput
    taxes: DecimalInput
    currency: str
    entitlement_date: date | None = None
    acquisition_date: date | None = None
    settlement_cash_account_id: str | None = None
    instrument_id: str | None = None
    instrument_snapshot_json: dict[str, object] | None = None
    quantity: DecimalInput | None = None
    price: DecimalInput | None = None
    counter_amount: DecimalInput | None = None
    quoted_fx_rate: DecimalInput | None = None
    consideration_basis: str | None = None
    numeric_scale_state: str = "declared"
    quantity_input_scale: int | None = None
    price_input_scale: int | None = None
    gross_amount_input_scale: int | None = None
    counter_amount_input_scale: int | None = None
    quoted_fx_rate_input_scale: int | None = None
    fees_input_scale: int | None = None
    taxes_input_scale: int | None = None
    transfer_scope: str | None = None
    transfer_object_type: str | None = None
    transfer_group_id: str | None = None
    counterparty_account_id: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "transaction_type", _required_text(self.transaction_type, "transaction_type"))
        object.__setattr__(self, "trade_date", _date_value(self.trade_date, "trade_date"))
        object.__setattr__(self, "trade_time", _time_value(self.trade_time, "trade_time"))
        object.__setattr__(self, "trade_at", _aware_utc(self.trade_at, "trade_at"))
        object.__setattr__(self, "trade_timezone", _required_text(self.trade_timezone, "trade_timezone"))
        if not isinstance(self.trade_time_is_estimated, bool):
            raise TransactionRevisionPayloadError("trade_time_is_estimated must be boolean")
        object.__setattr__(self, "settlement_date", _date_value(self.settlement_date, "settlement_date"))
        object.__setattr__(self, "entitlement_date", _optional_date(self.entitlement_date, "entitlement_date"))
        object.__setattr__(self, "acquisition_date", _optional_date(self.acquisition_date, "acquisition_date"))
        object.__setattr__(self, "account_id", _required_text(self.account_id, "account_id"))
        for name in (
            "settlement_cash_account_id",
            "instrument_id",
            "transfer_scope",
            "transfer_object_type",
            "transfer_group_id",
            "counterparty_account_id",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        consideration_basis = _optional_text(
            self.consideration_basis,
            "consideration_basis",
        )
        if (
            consideration_basis is not None
            and consideration_basis not in _CONSIDERATION_BASES
        ):
            raise TransactionRevisionPayloadError(
                f"unsupported consideration_basis: {consideration_basis}"
            )
        object.__setattr__(self, "consideration_basis", consideration_basis)
        numeric_scale_state = _required_text(
            self.numeric_scale_state,
            "numeric_scale_state",
        )
        if numeric_scale_state not in _NUMERIC_SCALE_STATES:
            raise TransactionRevisionPayloadError(
                f"unsupported numeric_scale_state: {numeric_scale_state}"
            )
        object.__setattr__(self, "numeric_scale_state", numeric_scale_state)
        if self.instrument_snapshot_json is not None:
            snapshot = _json_value(self.instrument_snapshot_json)
            if not isinstance(snapshot, dict):
                raise TransactionRevisionPayloadError("instrument_snapshot_json must be an object")
            object.__setattr__(self, "instrument_snapshot_json", snapshot)
        for name, scale in _DECIMAL_SCALES.items():
            value = getattr(self, name)
            scale_field = f"{name}_input_scale"
            declared_scale = getattr(self, scale_field)
            if value is None:
                if declared_scale is not None:
                    raise TransactionRevisionPayloadError(
                        f"{scale_field} must be null when {name} is null"
                    )
                continue
            decimal_value, inferred_scale = _decimal_value_and_input_scale(
                value,
                field_name=name,
                scale=scale,
            )
            resolved_scale = (
                inferred_scale
                if declared_scale is None
                else _input_scale(
                    declared_scale,
                    field_name=scale_field,
                    max_scale=scale,
                )
            )
            if _decimal_fractional_digits(decimal_value) > resolved_scale:
                raise TransactionRevisionPayloadError(
                    f"{scale_field} cannot represent {name} exactly"
                )
            object.__setattr__(self, name, decimal_value)
            object.__setattr__(self, scale_field, resolved_scale)
        for required_decimal in ("gross_amount", "fees", "taxes"):
            if getattr(self, required_decimal) is None:
                raise TransactionRevisionPayloadError(f"{required_decimal} is required")
        instrument_type = (
            str(self.instrument_snapshot_json.get("instrument_type") or "")
            .strip()
            .lower()
            if self.instrument_snapshot_json is not None
            else ""
        )
        uses_security_consideration = (
            self.transaction_type in _PRICE_AMOUNT_TRANSACTION_TYPES
            and self.instrument_id is not None
        )
        if uses_security_consideration:
            if self.consideration_basis is None:
                raise TransactionRevisionPayloadError(
                    f"{self.transaction_type} requires consideration_basis"
                )
            if self.quantity is None:
                raise TransactionRevisionPayloadError(
                    f"{self.transaction_type} requires quantity"
                )
        elif self.consideration_basis is not None:
            raise TransactionRevisionPayloadError(
                "consideration_basis is only valid for security price/amount transactions"
            )
        if self.consideration_basis == "exact_quantity_price":
            if instrument_type not in _METHODOLOGY_OWNED_PER_UNIT_INSTRUMENT_TYPES:
                raise TransactionRevisionPayloadError(
                    "exact_quantity_price requires a methodology-owned per-unit "
                    "fund, ETF, or equity contract"
                )
            if self.quantity is None or self.price is None:
                raise TransactionRevisionPayloadError(
                    "exact_quantity_price requires quantity and price"
                )
            expected_gross_amount = _exact_decimal_product(
                self.quantity,
                self.price,
            )
            if self.gross_amount != expected_gross_amount:
                raise TransactionRevisionPayloadError(
                    "gross_amount must exactly equal the amount implied by "
                    "quantity, price, contract_multiplier=1, and price_factor=1 "
                    f"for {instrument_type}"
                )
        if self.transaction_type == "fx_conversion":
            if self.counter_amount is None:
                raise TransactionRevisionPayloadError(
                    "fx_conversion requires authoritative counter_amount"
                )
        elif self.counter_amount is not None or self.quoted_fx_rate is not None:
            raise TransactionRevisionPayloadError(
                "counter_amount and quoted_fx_rate are only valid for fx_conversion"
            )
        object.__setattr__(self, "currency", _required_text(self.currency, "currency").upper())
        if self.note is not None and not isinstance(self.note, str):
            raise TransactionRevisionPayloadError("note must be a string or null")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> TransactionFactPayload:
        allowed = {item.name for item in fields(cls)}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise TransactionRevisionPayloadError(f"unknown transaction fact fields: {', '.join(unknown)}")
        try:
            return cls(**dict(value))  # type: ignore[arg-type]
        except TypeError as exc:
            raise TransactionRevisionPayloadError(str(exc)) from exc

    def as_record_values(self) -> dict[str, object]:
        return {item.name: deepcopy(getattr(self, item.name)) for item in fields(self)}


_TRANSACTION_FACT_FIELDS = tuple(item.name for item in fields(TransactionFactPayload))


def _canonical_scalar(value: object, *, field_name: str) -> object:
    if value is None or isinstance(value, (str, bool, int, float, list, dict)):
        return value
    if isinstance(value, Decimal):
        return _canonical_decimal_string(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="microseconds")
    raise TransactionRevisionPayloadError(f"cannot canonicalize {field_name}")


def canonical_transaction_payload(facts: TransactionFactPayload) -> bytes:
    """Return the exact UTF-8 bytes whose digest is persisted with a live revision."""

    body = {
        "payload_schema_version": TRANSACTION_PAYLOAD_SCHEMA_VERSION,
        "is_tombstone": False,
        "facts": {
            name: _canonical_scalar(getattr(facts, name), field_name=name)
            for name in _TRANSACTION_FACT_FIELDS
        },
    }
    return json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def transaction_payload_hash(facts: TransactionFactPayload) -> str:
    return f"sha256:{sha256(canonical_transaction_payload(facts)).hexdigest()}"


def tombstone_payload_hash() -> str:
    payload = json.dumps(
        {
            "payload_schema_version": TRANSACTION_PAYLOAD_SCHEMA_VERSION,
            "is_tombstone": True,
            "facts": None,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(payload).hexdigest()}"


@dataclass(frozen=True, slots=True)
class CreateTransactionRevision:
    transaction_id: str
    facts: TransactionFactPayload
    created_at: datetime
    revision_kind: InitialRevisionKind = "create"

    def __post_init__(self) -> None:
        object.__setattr__(self, "transaction_id", _required_text(self.transaction_id, "transaction_id"))
        if not isinstance(self.facts, TransactionFactPayload):
            raise TransactionRevisionPayloadError("facts must be TransactionFactPayload")
        object.__setattr__(self, "created_at", _aware_utc(self.created_at, "created_at"))
        kind = _required_text(self.revision_kind, "revision_kind")
        if kind not in _INITIAL_REVISION_KINDS:
            raise TransactionRevisionPayloadError("initial revision_kind must be baseline or create")
        object.__setattr__(self, "revision_kind", cast(InitialRevisionKind, kind))


@dataclass(frozen=True, slots=True)
class AmendTransactionRevision:
    transaction_id: str
    expected_revision_id: str
    facts: TransactionFactPayload
    expected_revision_number: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "transaction_id", _required_text(self.transaction_id, "transaction_id"))
        object.__setattr__(
            self,
            "expected_revision_id",
            _required_text(self.expected_revision_id, "expected_revision_id"),
        )
        if not isinstance(self.facts, TransactionFactPayload):
            raise TransactionRevisionPayloadError("facts must be TransactionFactPayload")
        if self.expected_revision_number is not None and self.expected_revision_number < 1:
            raise TransactionRevisionPayloadError("expected_revision_number must be positive")


@dataclass(frozen=True, slots=True)
class DeleteTransactionRevision:
    transaction_id: str
    expected_revision_id: str
    expected_revision_number: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "transaction_id", _required_text(self.transaction_id, "transaction_id"))
        object.__setattr__(
            self,
            "expected_revision_id",
            _required_text(self.expected_revision_id, "expected_revision_id"),
        )
        if self.expected_revision_number is not None and self.expected_revision_number < 1:
            raise TransactionRevisionPayloadError("expected_revision_number must be positive")


TransactionRevisionMutation: TypeAlias = (
    CreateTransactionRevision | AmendTransactionRevision | DeleteTransactionRevision
)


@dataclass(frozen=True, slots=True)
class AppendedTransactionRevisionBatch:
    group: TransactionRevisionGroupRecordModel
    revisions: tuple[TransactionRevisionRecordModel, ...]
    created_identities: tuple[TransactionIdentityRecordModel, ...]


@dataclass(frozen=True, slots=True)
class TransactionRevisionHistoryEntry:
    revision: TransactionRevisionRecordModel
    group: TransactionRevisionGroupRecordModel


def _latest_revisions(
    session: Session,
    *,
    portfolio_id: str,
    transaction_ids: Sequence[str],
) -> dict[str, TransactionRevisionRecordModel]:
    if not transaction_ids:
        return {}
    ranked = (
        select(
            TransactionRevisionRecordModel.revision_id.label("revision_id"),
            func.row_number()
            .over(
                partition_by=TransactionRevisionRecordModel.transaction_id,
                order_by=TransactionRevisionRecordModel.revision_number.desc(),
            )
            .label("rank"),
        )
        .where(
            TransactionRevisionRecordModel.portfolio_id == portfolio_id,
            TransactionRevisionRecordModel.transaction_id.in_(transaction_ids),
        )
        .subquery()
    )
    revisions = session.scalars(
        select(TransactionRevisionRecordModel)
        .join(ranked, ranked.c.revision_id == TransactionRevisionRecordModel.revision_id)
        .where(ranked.c.rank == 1)
        .execution_options(autoflush=False)
    ).all()
    return {revision.transaction_id: revision for revision in revisions}


def _assert_expected_revision(
    mutation: AmendTransactionRevision | DeleteTransactionRevision,
    current: TransactionRevisionRecordModel,
) -> None:
    number_mismatch = (
        mutation.expected_revision_number is not None
        and mutation.expected_revision_number != current.revision_number
    )
    if mutation.expected_revision_id != current.revision_id or number_mismatch:
        raise TransactionRevisionConflictError(
            f"transaction {mutation.transaction_id} has changed since it was read",
            code="stale_revision",
            transaction_id=mutation.transaction_id,
            expected_revision_id=mutation.expected_revision_id,
            expected_revision_number=mutation.expected_revision_number,
            actual_revision_id=current.revision_id,
            actual_revision_number=current.revision_number,
        )


_TRANSFER_TYPES = frozenset({"transfer_in", "transfer_out"})
_TRANSFER_MIRRORED_FIELDS = (
    "trade_date",
    "trade_time",
    "trade_at",
    "trade_timezone",
    "trade_time_is_estimated",
    "settlement_date",
    "entitlement_date",
    "acquisition_date",
    "settlement_cash_account_id",
    "instrument_id",
    "instrument_snapshot_json",
    "quantity",
    "price",
    "gross_amount",
    "counter_amount",
    "quoted_fx_rate",
    "fees",
    "taxes",
    "consideration_basis",
    "numeric_scale_state",
    "quantity_input_scale",
    "price_input_scale",
    "gross_amount_input_scale",
    "counter_amount_input_scale",
    "quoted_fx_rate_input_scale",
    "fees_input_scale",
    "taxes_input_scale",
    "currency",
    "transfer_scope",
    "transfer_object_type",
    "note",
)


def _validate_transfer_create_commands(
    session: Session,
    *,
    portfolio_id: str,
    commands: Sequence[TransactionRevisionMutation],
) -> None:
    groups: dict[str, list[CreateTransactionRevision]] = {}
    for command in commands:
        if not isinstance(command, CreateTransactionRevision):
            continue
        if command.facts.transaction_type not in _TRANSFER_TYPES:
            continue
        transfer_group_id = command.facts.transfer_group_id
        if transfer_group_id is None:
            raise TransactionRevisionPayloadError(
                f"internal transfer {command.transaction_id} requires transfer_group_id"
            )
        groups.setdefault(transfer_group_id, []).append(command)

    if not groups:
        return
    historical_group_ids = set(
        session.scalars(
            select(TransactionRevisionRecordModel.transfer_group_id)
            .where(
                TransactionRevisionRecordModel.portfolio_id == portfolio_id,
                TransactionRevisionRecordModel.transfer_group_id.in_(groups),
            )
            .distinct()
            .execution_options(autoflush=False)
        ).all()
    )
    for transfer_group_id, group_commands in groups.items():
        if transfer_group_id in historical_group_ids:
            raise TransactionRevisionConflictError(
                f"internal transfer group '{transfer_group_id}' already exists in ledger history",
                code="transfer_group_already_exists",
            )
        if len(group_commands) != 2 or {
            command.facts.transaction_type for command in group_commands
        } != _TRANSFER_TYPES:
            raise TransactionRevisionPayloadError(
                f"internal transfer group '{transfer_group_id}' requires exactly one in leg and one out leg"
            )
        outbound = next(
            command.facts
            for command in group_commands
            if command.facts.transaction_type == "transfer_out"
        )
        inbound = next(
            command.facts
            for command in group_commands
            if command.facts.transaction_type == "transfer_in"
        )
        if (
            outbound.counterparty_account_id != inbound.account_id
            or inbound.counterparty_account_id != outbound.account_id
        ):
            raise TransactionRevisionPayloadError(
                f"internal transfer group '{transfer_group_id}' account links are not reciprocal"
            )
        mismatches = [
            field_name
            for field_name in _TRANSFER_MIRRORED_FIELDS
            if getattr(outbound, field_name) != getattr(inbound, field_name)
        ]
        if mismatches:
            raise TransactionRevisionPayloadError(
                f"internal transfer group '{transfer_group_id}' has inconsistent legs: "
                f"{', '.join(mismatches)}"
            )


def _validate_transfer_delete_commands(
    session: Session,
    *,
    portfolio_id: str,
    plans: Sequence[
        tuple[
            TransactionRevisionMutation,
            TransactionRevisionRecordModel | None,
            str,
        ]
    ],
) -> None:
    delete_ids_by_group: dict[str, set[str]] = {}
    for command, current, _payload_hash in plans:
        if not isinstance(command, DeleteTransactionRevision) or current is None:
            continue
        is_transfer = current.transaction_type in _TRANSFER_TYPES
        transfer_group_id = current.transfer_group_id
        if is_transfer != bool(transfer_group_id):
            raise TransactionRevisionPayloadError(
                f"transaction {command.transaction_id} has an invalid internal transfer envelope"
            )
        if is_transfer:
            assert transfer_group_id is not None
            delete_ids_by_group.setdefault(transfer_group_id, set()).add(
                command.transaction_id
            )

    for transfer_group_id, requested_ids in delete_ids_by_group.items():
        current_legs = session.scalars(
            select(TransactionCurrentModel)
            .where(
                TransactionCurrentModel.portfolio_id == portfolio_id,
                TransactionCurrentModel.transfer_group_id == transfer_group_id,
            )
            .execution_options(autoflush=False)
        ).all()
        current_ids = {leg.transaction_id for leg in current_legs}
        if (
            len(current_legs) != 2
            or {leg.transaction_type for leg in current_legs} != _TRANSFER_TYPES
            or requested_ids != current_ids
        ):
            raise TransactionRevisionPayloadError(
                f"internal transfer group '{transfer_group_id}' must be deleted atomically"
            )


def append_transaction_revision_batch_unchecked(
    session: Session,
    *,
    portfolio_id: str,
    context: TransactionRevisionContext,
    mutations: Sequence[TransactionRevisionMutation],
) -> AppendedTransactionRevisionBatch:
    """Low-level append only; it does not prove the resulting book replayable.

    Production callers must hold the portfolio mutation lock and run
    ``validate_prospective_transaction_history`` in the same database
    transaction before committing.  Direct use is otherwise reserved for
    fixture, migration, and database-constraint tests.  The complete batch is
    structurally validated before any ORM object is added to the session.
    """

    portfolio_id = _required_text(portfolio_id, "portfolio_id")
    if not isinstance(context, TransactionRevisionContext):
        raise TransactionRevisionPayloadError("context must be TransactionRevisionContext")
    commands = tuple(mutations)
    if not commands:
        raise TransactionRevisionPayloadError("a revision batch must contain at least one mutation")
    mutation_types = (
        CreateTransactionRevision,
        AmendTransactionRevision,
        DeleteTransactionRevision,
    )
    if any(not isinstance(command, mutation_types) for command in commands):
        raise TransactionRevisionPayloadError("unsupported transaction revision mutation")

    _validate_transfer_create_commands(
        session,
        portfolio_id=portfolio_id,
        commands=commands,
    )

    transaction_ids = [command.transaction_id for command in commands]
    if len(transaction_ids) != len(set(transaction_ids)):
        raise TransactionRevisionConflictError(
            "a revision group may mutate each transaction at most once",
            code="duplicate_transaction_in_batch",
        )

    identities = session.scalars(
        select(TransactionIdentityRecordModel).where(
            TransactionIdentityRecordModel.transaction_id.in_(transaction_ids),
        ).execution_options(autoflush=False)
    ).all()
    identities_by_id = {identity.transaction_id: identity for identity in identities}
    latest_by_id = _latest_revisions(
        session,
        portfolio_id=portfolio_id,
        transaction_ids=transaction_ids,
    )

    if context.idempotency_key is not None:
        existing_group = session.scalar(
            select(TransactionRevisionGroupRecordModel).where(
                TransactionRevisionGroupRecordModel.portfolio_id == portfolio_id,
                TransactionRevisionGroupRecordModel.idempotency_key == context.idempotency_key,
            ).execution_options(autoflush=False)
        )
        if existing_group is not None:
            raise TransactionRevisionConflictError(
                f"idempotency key already belongs to revision group {existing_group.revision_group_id}",
                code="idempotency_key_conflict",
            )

    plans: list[tuple[TransactionRevisionMutation, TransactionRevisionRecordModel | None, str]] = []
    for command in commands:
        identity = identities_by_id.get(command.transaction_id)
        current = latest_by_id.get(command.transaction_id)
        if isinstance(command, CreateTransactionRevision):
            if identity is not None or current is not None:
                raise TransactionRevisionConflictError(
                    f"transaction {command.transaction_id} already exists",
                    code="transaction_already_exists",
                    transaction_id=command.transaction_id,
                    actual_revision_id=current.revision_id if current else None,
                    actual_revision_number=current.revision_number if current else None,
                )
            plans.append((command, None, transaction_payload_hash(command.facts)))
            continue

        if identity is not None and identity.portfolio_id != portfolio_id:
            raise TransactionRevisionConflictError(
                f"transaction {command.transaction_id} belongs to another portfolio",
                code="transaction_portfolio_mismatch",
                transaction_id=command.transaction_id,
                expected_revision_id=command.expected_revision_id,
                expected_revision_number=command.expected_revision_number,
            )
        if identity is None or current is None:
            raise TransactionRevisionConflictError(
                f"transaction {command.transaction_id} does not exist",
                code="transaction_not_found",
                transaction_id=command.transaction_id,
                expected_revision_id=command.expected_revision_id,
                expected_revision_number=command.expected_revision_number,
            )
        _assert_expected_revision(command, current)
        if isinstance(command, AmendTransactionRevision):
            if (
                current.transaction_type in _TRANSFER_TYPES
                or command.facts.transaction_type in _TRANSFER_TYPES
            ):
                raise TransactionRevisionPayloadError(
                    "internal transfer legs cannot be amended; delete and recreate the atomic pair"
                )
            if current.is_tombstone:
                raise TransactionRevisionConflictError(
                    f"deleted transaction {command.transaction_id} cannot be amended",
                    code="transaction_deleted",
                    transaction_id=command.transaction_id,
                    actual_revision_id=current.revision_id,
                    actual_revision_number=current.revision_number,
                )
            payload_hash = transaction_payload_hash(command.facts)
            if payload_hash == current.payload_hash:
                raise TransactionRevisionNoOpError(
                    f"amendment does not change transaction {command.transaction_id}",
                    transaction_id=command.transaction_id,
                    revision_id=current.revision_id,
                    revision_number=current.revision_number,
                    payload_hash=current.payload_hash,
                )
        else:
            payload_hash = tombstone_payload_hash()
            if current.is_tombstone:
                raise TransactionRevisionNoOpError(
                    f"transaction {command.transaction_id} is already deleted",
                    transaction_id=command.transaction_id,
                    revision_id=current.revision_id,
                    revision_number=current.revision_number,
                    payload_hash=current.payload_hash,
                )
        plans.append((command, current, payload_hash))

    _validate_transfer_delete_commands(
        session,
        portfolio_id=portfolio_id,
        plans=plans,
    )

    group = TransactionRevisionGroupRecordModel(
        revision_group_id=uuid4().hex,
        portfolio_id=portfolio_id,
        source_kind=context.source_kind,
        change_reason=context.change_reason,
        actor_type=context.actor_type,
        actor_id=context.actor_id,
        actor_display_name=context.actor_display_name,
        actor_source=context.actor_source,
        recorded_at=context.recorded_at,
        request_id=context.request_id,
        idempotency_key=context.idempotency_key,
        source_ref=context.source_ref,
    )
    created_identities: list[TransactionIdentityRecordModel] = []
    revisions: list[TransactionRevisionRecordModel] = []
    null_facts = {name: None for name in _TRANSACTION_FACT_FIELDS}
    for command, current, payload_hash in plans:
        if isinstance(command, CreateTransactionRevision):
            identity = TransactionIdentityRecordModel(
                transaction_id=command.transaction_id,
                portfolio_id=portfolio_id,
                created_at=command.created_at,
                created_by=context.actor_id,
            )
            created_identities.append(identity)
            revision_number = 1
            revision_kind = command.revision_kind
            is_tombstone = False
            fact_values = command.facts.as_record_values()
        else:
            assert current is not None
            revision_number = current.revision_number + 1
            revision_kind = "amend" if isinstance(command, AmendTransactionRevision) else "delete"
            is_tombstone = isinstance(command, DeleteTransactionRevision)
            fact_values = dict(null_facts) if is_tombstone else command.facts.as_record_values()

        # SQLAlchemy JSON otherwise persists Python None as JSON ``null``.  The
        # ledger constraints intentionally distinguish that value from SQL NULL.
        if fact_values["instrument_snapshot_json"] is None:
            fact_values["instrument_snapshot_json"] = null()

        revisions.append(
            TransactionRevisionRecordModel(
                revision_id=uuid4().hex,
                portfolio_id=portfolio_id,
                transaction_id=command.transaction_id,
                revision_number=revision_number,
                revision_group_id=group.revision_group_id,
                revision_kind=revision_kind,
                is_tombstone=is_tombstone,
                supersedes_revision_id=current.revision_id if current else None,
                supersedes_revision_number=current.revision_number if current else None,
                payload_schema_version=TRANSACTION_PAYLOAD_SCHEMA_VERSION,
                payload_hash=payload_hash,
                **fact_values,
            )
        )

    session.add(group)
    session.add_all(created_identities)
    session.add_all(revisions)
    session.flush()
    return AppendedTransactionRevisionBatch(
        group=group,
        revisions=tuple(revisions),
        created_identities=tuple(created_identities),
    )
def get_latest_transaction_revision(
    session: Session, *, portfolio_id: str, transaction_id: str
) -> TransactionRevisionRecordModel | None:
    return session.scalar(
        select(TransactionRevisionRecordModel)
        .where(
            TransactionRevisionRecordModel.portfolio_id == portfolio_id,
            TransactionRevisionRecordModel.transaction_id == transaction_id,
        )
        .order_by(TransactionRevisionRecordModel.revision_number.desc())
        .limit(1)
    )


def get_current_transaction(
    session: Session, *, portfolio_id: str, transaction_id: str
) -> TransactionCurrentModel | None:
    return session.scalar(
        select(TransactionCurrentModel).where(
            TransactionCurrentModel.portfolio_id == portfolio_id,
            TransactionCurrentModel.transaction_id == transaction_id,
        )
    )


def list_transaction_revision_history(
    session: Session, *, portfolio_id: str, transaction_id: str
) -> tuple[TransactionRevisionHistoryEntry, ...]:
    rows = session.execute(
        select(TransactionRevisionRecordModel, TransactionRevisionGroupRecordModel)
        .join(
            TransactionRevisionGroupRecordModel,
            TransactionRevisionGroupRecordModel.revision_group_id
            == TransactionRevisionRecordModel.revision_group_id,
        )
        .where(
            TransactionRevisionRecordModel.portfolio_id == portfolio_id,
            TransactionRevisionRecordModel.transaction_id == transaction_id,
        )
        .order_by(TransactionRevisionRecordModel.revision_number.asc())
    ).all()
    return tuple(TransactionRevisionHistoryEntry(revision=row[0], group=row[1]) for row in rows)


def _current_query(
    *,
    portfolio_id: str,
    transaction_ids: Sequence[str] | None = None,
    account_id: str | None = None,
    instrument_id: str | None = None,
    transaction_type: str | None = None,
    trade_date_from: date | None = None,
    trade_date_to: date | None = None,
) -> Select[tuple[TransactionCurrentModel]]:
    statement = select(TransactionCurrentModel).where(
        TransactionCurrentModel.portfolio_id == portfolio_id
    )
    if transaction_ids is not None:
        if not transaction_ids:
            return statement.where(False)
        statement = statement.where(TransactionCurrentModel.transaction_id.in_(transaction_ids))
    if account_id is not None:
        statement = statement.where(TransactionCurrentModel.account_id == account_id)
    if instrument_id is not None:
        statement = statement.where(TransactionCurrentModel.instrument_id == instrument_id)
    if transaction_type is not None:
        statement = statement.where(TransactionCurrentModel.transaction_type == transaction_type)
    if trade_date_from is not None:
        statement = statement.where(TransactionCurrentModel.trade_date >= trade_date_from)
    if trade_date_to is not None:
        statement = statement.where(TransactionCurrentModel.trade_date <= trade_date_to)
    return statement.order_by(
        TransactionCurrentModel.trade_at.asc(), TransactionCurrentModel.transaction_id.asc()
    )


def list_current_transactions(
    session: Session,
    *,
    portfolio_id: str,
    transaction_ids: Sequence[str] | None = None,
    account_id: str | None = None,
    instrument_id: str | None = None,
    transaction_type: str | None = None,
    trade_date_from: date | None = None,
    trade_date_to: date | None = None,
) -> tuple[TransactionCurrentModel, ...]:
    return tuple(
        session.scalars(
            _current_query(
                portfolio_id=portfolio_id,
                transaction_ids=transaction_ids,
                account_id=account_id,
                instrument_id=instrument_id,
                transaction_type=transaction_type,
                trade_date_from=trade_date_from,
                trade_date_to=trade_date_to,
            )
        ).all()
    )


def refresh_transaction_current_projection(
    session: Session,
    *,
    portfolio_id: str,
    transaction_ids: Sequence[str],
) -> dict[str, TransactionCurrentModel]:
    """Flush pending revisions and reread the ordinary current-state SQL view."""

    session.flush()
    current = session.scalars(
        _current_query(portfolio_id=portfolio_id, transaction_ids=transaction_ids).execution_options(
            populate_existing=True
        )
    ).all()
    return {transaction.transaction_id: transaction for transaction in current}


__all__ = [
    "TRANSACTION_PAYLOAD_SCHEMA_VERSION",
    "AmendTransactionRevision",
    "AppendedTransactionRevisionBatch",
    "CreateTransactionRevision",
    "DeleteTransactionRevision",
    "TransactionFactPayload",
    "TransactionRevisionConflictError",
    "TransactionRevisionContext",
    "TransactionRevisionError",
    "TransactionRevisionHistoryEntry",
    "TransactionRevisionNoOpError",
    "TransactionRevisionPayloadError",
    "append_transaction_revision_batch_unchecked",
    "canonical_transaction_payload",
    "get_current_transaction",
    "get_latest_transaction_revision",
    "list_current_transactions",
    "list_transaction_revision_history",
    "refresh_transaction_current_projection",
    "tombstone_payload_hash",
    "transaction_payload_hash",
]
