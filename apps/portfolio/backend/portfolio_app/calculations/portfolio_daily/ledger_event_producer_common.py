"""Shared strict primitives and immutable records for ledger-event production."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
import math
import re
from typing import TypeAlias, cast
from uuid import UUID

from portfolio_app.calculations.portfolio_daily.ledger_contracts import (
    CostBasisMethod,
    FactLineage,
    LedgerContractError,
    LedgerEvent,
    LedgerFactKind,
    require_currency,
    require_fact,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_contracts import (
    LedgerEventBuildError,
    LedgerEventBuildErrorCode,
)


CONFIG_TABLE = "portfolio_daily_config_input"
ACCOUNT_TABLE = "portfolio_daily_account_input"
TRANSACTION_TABLE = "portfolio_daily_transaction_input"
INSTRUMENT_TABLE = "portfolio_daily_instrument_input"
CORPORATE_ACTION_TABLE = "portfolio_daily_corp_action_input"
CORPORATE_ACTION_WINDOW_TABLE = "portfolio_daily_corp_action_window"
FX_PATH_TABLE = "portfolio_daily_fx_path"
FX_LEG_TABLE = "portfolio_daily_fx_leg"

SUPPORTED_TRANSACTION_PAYLOAD_SCHEMA_VERSION = "transaction-revision.v1"
TOMBSTONE_PAYLOAD_HASH = (
    "sha256:bc5787018813b8e0562f74da4098be5dc135994b08a078955af74b9d11b7ef91"
)

_SHA256_PREFIXED = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_CASH_ACCOUNT_TYPES = frozenset({"deposit_account"})
_POSITION_ACCOUNT_TYPES = frozenset({"securities_account"})
_SUPPORTED_TRANSACTION_TYPES = frozenset(
    {
        "buy",
        "sell",
        "dividend",
        "dividend_reinvestment",
        "coupon",
        "interest",
        "return_of_capital",
        "maturity_redemption",
        "fee",
        "tax",
        "deposit",
        "withdrawal",
        "fx_conversion",
        "transfer_in",
        "transfer_out",
        "opening_balance",
    }
)
_TRANSACTION_FACT_FIELDS = (
    "transaction_type",
    "trade_date",
    "trade_time",
    "trade_at",
    "trade_timezone",
    "trade_time_is_estimated",
    "settlement_date",
    "entitlement_date",
    "acquisition_date",
    "account_id",
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
    "transfer_group_id",
    "counterparty_account_id",
    "note",
)
RowsByTable: TypeAlias = Mapping[str, Sequence[Mapping[str, object]]]
EventBuilder: TypeAlias = Callable[[int], LedgerEvent]


def _fail(
    message: str,
    *,
    code: LedgerEventBuildErrorCode,
    table: str,
    record_id: str,
    field: str | None = None,
) -> None:
    raise LedgerEventBuildError(
        message,
        code=code,
        source_table=table,
        source_record_id=record_id,
        field_name=field,
    )


def _required(row: Mapping[str, object], field: str, *, table: str, record_id: str) -> object:
    if field not in row:
        _fail(
            f"{table}.{field} is absent from the sealed input row",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    return row[field]


def _text(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str,
) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(
            f"{table}.{field} must be a non-empty canonical string",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    return cast(str, value)


def _optional_text(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str,
) -> str | None:
    if value is None:
        return None
    return _text(value, table=table, record_id=record_id, field=field)


def _date(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str,
) -> date:
    if type(value) is not date:
        _fail(
            f"{table}.{field} must be a date",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    return cast(date, value)


def _optional_date(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str,
) -> date | None:
    if value is None:
        return None
    return _date(value, table=table, record_id=record_id, field=field)


def _aware_datetime(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str,
) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        _fail(
            f"{table}.{field} must be a timezone-aware datetime",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    return cast(datetime, value).astimezone(UTC)


def _time(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str,
) -> time:
    if type(value) is not time or value.tzinfo is not None:
        _fail(
            f"{table}.{field} must be a timezone-free time",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    return cast(time, value)


def _integer(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(
            f"{table}.{field} must be an integer",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    resolved = cast(int, value)
    if minimum is not None and resolved < minimum:
        _fail(
            f"{table}.{field} must be at least {minimum}",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    return resolved


def _boolean(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str,
) -> bool:
    if type(value) is not bool:
        _fail(
            f"{table}.{field} must be boolean",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    return cast(bool, value)


def _uuid(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str,
) -> UUID:
    if not isinstance(value, UUID):
        _fail(
            f"{table}.{field} must be a UUID",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    return cast(UUID, value)


def _currency(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str = "currency",
) -> str:
    try:
        return require_currency(value, field_name=field)
    except LedgerContractError as exc:
        _fail(
            str(exc),
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )


def _fact(
    value: object,
    *,
    kind: LedgerFactKind,
    table: str,
    record_id: str,
    field: str,
    minimum: Decimal | None = None,
    strictly_positive: bool = False,
) -> Decimal:
    try:
        return require_fact(
            value,
            kind=kind,
            field_name=field,
            minimum=minimum,
            strictly_positive=strictly_positive,
        )
    except LedgerContractError as exc:
        _fail(
            str(exc),
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )


def _optional_fact(
    value: object,
    *,
    kind: LedgerFactKind,
    table: str,
    record_id: str,
    field: str,
    minimum: Decimal | None = None,
    strictly_positive: bool = False,
) -> Decimal | None:
    if value is None:
        return None
    return _fact(
        value,
        kind=kind,
        table=table,
        record_id=record_id,
        field=field,
        minimum=minimum,
        strictly_positive=strictly_positive,
    )


def _exact_decimal(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str,
    strictly_positive: bool = False,
) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        _fail(
            f"{table}.{field} must be a finite Decimal",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    resolved = cast(Decimal, value)
    if strictly_positive and resolved <= 0:
        _fail(
            f"{table}.{field} must be positive",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    return resolved


def _reason_codes(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        _fail(
            f"{table}.{field} must be an array of canonical strings",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    resolved = tuple(
        _text(item, table=table, record_id=record_id, field=field)
        for item in cast(Sequence[object], value)
    )
    if tuple(sorted(set(resolved))) != resolved:
        _fail(
            f"{table}.{field} must be sorted and unique",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    return resolved


def _json_value(value: object, *, table: str, record_id: str, field: str) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        _fail(
            f"{table}.{field} contains a non-finite float",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    if isinstance(value, list):
        return [
            _json_value(item, table=table, record_id=record_id, field=field)
            for item in value
        ]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            _fail(
                f"{table}.{field} contains a non-string object key",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=table,
                record_id=record_id,
                field=field,
            )
        return {
            cast(str, key): _json_value(
                item,
                table=table,
                record_id=record_id,
                field=field,
            )
            for key, item in value.items()
        }
    _fail(
        f"{table}.{field} contains unsupported {type(value).__name__}",
        code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
        table=table,
        record_id=record_id,
        field=field,
    )


def _json_object(
    value: object,
    *,
    table: str,
    record_id: str,
    field: str,
) -> Mapping[str, object]:
    resolved = _json_value(value, table=table, record_id=record_id, field=field)
    if not isinstance(resolved, Mapping):
        _fail(
            f"{table}.{field} must be an object",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=table,
            record_id=record_id,
            field=field,
        )
    return cast(Mapping[str, object], resolved)


@dataclass(frozen=True, slots=True)
class _Account:
    account_id: str
    account_type: str
    currency: str
    default_settlement_cash_account_id: str | None
    cost_basis_method: CostBasisMethod | None
    opened_at: date | None
    closed_at: date | None


@dataclass(frozen=True, slots=True)
class _Instrument:
    instrument_id: str
    instrument_type: str
    currency: str
    price_unit: str | None
    contract_multiplier: Decimal | None
    price_factor: Decimal | None
    valuation_contract_state: str


@dataclass(frozen=True, slots=True)
class _Transaction:
    row: Mapping[str, object]
    transaction_id: str
    revision_id: str
    revision_group_id: str
    group_recorded_at: datetime
    transaction_type: str
    trade_date: date
    trade_at: datetime
    settlement_date: date
    entitlement_date: date | None
    acquisition_date: date | None
    account_id: str
    settlement_cash_account_id: str | None
    instrument_id: str | None
    quantity: Decimal | None
    price: Decimal | None
    gross_amount: Decimal
    counter_amount: Decimal | None
    quoted_fx_rate: Decimal | None
    effective_fx_rate: Decimal | None
    consideration_basis: str | None
    fees: Decimal
    taxes: Decimal
    currency: str
    transfer_scope: str | None
    transfer_object_type: str | None
    transfer_group_id: str | None
    counterparty_account_id: str | None

    @property
    def order_key(self) -> tuple[datetime, datetime, str, str, str]:
        return (
            self.trade_at,
            self.group_recorded_at,
            "transaction",
            self.transaction_id,
            self.revision_id,
        )

    @property
    def lineage(self) -> FactLineage:
        return FactLineage(
            source_record_id=self.transaction_id,
            source_revision_id=self.revision_id,
            manifest_fact_key=(
                f"{TRANSACTION_TABLE}/{self.transaction_id}/{self.revision_id}"
            ),
        )


@dataclass(frozen=True, slots=True)
class _Draft:
    order_key: tuple[datetime, datetime, str, str, str]
    event_id: str
    build: EventBuilder


@dataclass(frozen=True, slots=True)
class _BuildContext:
    rows: RowsByTable
    manifest_id: UUID
    run_id: UUID
    portfolio_id: str
    base_currency: str
    effective_as_of: date
    range_start: date
    knowledge_cutoff_at: datetime
    accounts: Mapping[str, _Account]
    instruments: Mapping[str, _Instrument]
