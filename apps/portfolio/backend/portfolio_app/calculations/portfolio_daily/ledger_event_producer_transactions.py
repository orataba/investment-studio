"""Strict sealed transaction revision validation for ledger-event production."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from hashlib import sha256
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from portfolio_app.calculations.numeric import canonical_decimal, exact_decimal_product
from portfolio_app.calculations.portfolio_daily.ledger_contracts import (
    LedgerFactKind,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_common import (
    INSTRUMENT_TABLE,
    SUPPORTED_TRANSACTION_PAYLOAD_SCHEMA_VERSION,
    TOMBSTONE_PAYLOAD_HASH,
    TRANSACTION_TABLE,
    _Account,
    _BuildContext,
    _CASH_ACCOUNT_TYPES,
    _Instrument,
    _POSITION_ACCOUNT_TYPES,
    _SHA256_PREFIXED,
    _SUPPORTED_TRANSACTION_TYPES,
    _TRANSACTION_FACT_FIELDS,
    _Transaction,
    _aware_datetime,
    _boolean,
    _currency,
    _date,
    _exact_decimal,
    _fact,
    _fail,
    _integer,
    _json_object,
    _optional_date,
    _optional_fact,
    _optional_text,
    _required,
    _text,
    _time,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_contracts import (
    LedgerEventBuildDiagnostic,
    LedgerEventBuildErrorCode,
    LedgerEventDiagnosticCode,
)


def _canonical_transaction_scalar(value: object, *, field_name: str) -> object:
    if value is None or isinstance(value, (str, bool, int, float, list, dict)):
        return value
    if isinstance(value, Decimal):
        return canonical_decimal(value, field_name=field_name)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="microseconds")
    raise TypeError(f"cannot canonicalize transaction field {field_name}")


def _transaction_payload_hash(row: Mapping[str, object]) -> str:
    body = {
        "payload_schema_version": SUPPORTED_TRANSACTION_PAYLOAD_SCHEMA_VERSION,
        "is_tombstone": False,
        "facts": {
            field: _canonical_transaction_scalar(row[field], field_name=field)
            for field in _TRANSACTION_FACT_FIELDS
        },
    }
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _validate_revision_header(
    row: Mapping[str, object],
    *,
    cutoff: datetime,
) -> tuple[str, str, str, datetime, bool]:
    transaction_id = _text(
        _required(
            row,
            "transaction_id",
            table=TRANSACTION_TABLE,
            record_id="__row__",
        ),
        table=TRANSACTION_TABLE,
        record_id="__row__",
        field="transaction_id",
    )
    revision_id = _text(
        _required(
            row,
            "revision_id",
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
        ),
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="revision_id",
    )
    revision_group_id = _text(
        _required(
            row,
            "revision_group_id",
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
        ),
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="revision_group_id",
    )
    group_recorded_at = _aware_datetime(
        _required(
            row,
            "group_recorded_at",
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
        ),
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="group_recorded_at",
    )
    if group_recorded_at > cutoff:
        _fail(
            "transaction revision group is after the manifest knowledge cutoff",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="group_recorded_at",
        )
    revision_number = _integer(
        _required(
            row,
            "revision_number",
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
        ),
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="revision_number",
        minimum=1,
    )
    revision_kind = _text(
        _required(
            row,
            "revision_kind",
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
        ),
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="revision_kind",
    )
    tombstone = _boolean(
        _required(
            row,
            "is_tombstone",
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
        ),
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="is_tombstone",
    )
    supersedes_id = _optional_text(
        _required(
            row,
            "supersedes_revision_id",
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
        ),
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="supersedes_revision_id",
    )
    raw_supersedes_number = _required(
        row,
        "supersedes_revision_number",
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
    )
    supersedes_number = (
        None
        if raw_supersedes_number is None
        else _integer(
            raw_supersedes_number,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="supersedes_revision_number",
            minimum=1,
        )
    )
    if revision_number == 1:
        valid_chain = (
            revision_kind in {"baseline", "create"}
            and not tombstone
            and supersedes_id is None
            and supersedes_number is None
        )
    else:
        valid_chain = (
            revision_kind in {"amend", "delete"}
            and tombstone == (revision_kind == "delete")
            and supersedes_id is not None
            and supersedes_number == revision_number - 1
        )
    if not valid_chain:
        _fail(
            "transaction revision chain header is inconsistent",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="revision_kind",
        )
    schema = _text(
        _required(
            row,
            "payload_schema_version",
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
        ),
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="payload_schema_version",
    )
    if schema != SUPPORTED_TRANSACTION_PAYLOAD_SCHEMA_VERSION:
        _fail(
            f"unsupported transaction payload schema {schema}",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="payload_schema_version",
        )
    payload_hash = _text(
        _required(
            row,
            "payload_hash",
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
        ),
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="payload_hash",
    )
    if _SHA256_PREFIXED.fullmatch(payload_hash) is None:
        _fail(
            "transaction payload_hash is not a canonical SHA-256 digest",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="payload_hash",
        )
    selected_reason = _text(
        _required(
            row,
            "selected_reason_code",
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
        ),
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="selected_reason_code",
    )
    if selected_reason != "latest_at_knowledge_cutoff":
        _fail(
            f"unsupported transaction selection reason {selected_reason}",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="selected_reason_code",
        )
    for field in _TRANSACTION_FACT_FIELDS:
        _required(row, field, table=TRANSACTION_TABLE, record_id=transaction_id)
    if tombstone:
        if any(row[field] is not None for field in _TRANSACTION_FACT_FIELDS):
            _fail(
                "tombstone transaction revision carries live fact fields",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
            )
        if payload_hash != TOMBSTONE_PAYLOAD_HASH:
            _fail(
                "tombstone transaction payload hash is invalid",
                code=LedgerEventBuildErrorCode.HASH_MISMATCH,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="payload_hash",
            )
    return (
        transaction_id,
        revision_id,
        revision_group_id,
        group_recorded_at,
        tombstone,
    )


def _require_absent(
    row: Mapping[str, object],
    fields: Sequence[str],
    *,
    transaction_id: str,
) -> None:
    present = tuple(field for field in fields if row[field] is not None)
    if present:
        _fail(
            "transaction carries inapplicable fields: " + ", ".join(present),
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field=present[0],
        )


def _account(
    context: _BuildContext,
    account_id: str,
    *,
    transaction_id: str,
    role: str,
    expected_types: frozenset[str],
    event_dates: Sequence[date],
) -> _Account:
    account = context.accounts.get(account_id)
    if account is None:
        _fail(
            f"{role} account {account_id} is absent from the sealed manifest",
            code=LedgerEventBuildErrorCode.MISSING_REFERENCE,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field=role,
        )
    if account.account_type not in expected_types:
        _fail(
            f"{role} account has incompatible type {account.account_type}",
            code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field=role,
        )
    for event_date in event_dates:
        if account.opened_at is not None and event_date < account.opened_at:
            _fail(
                f"{role} account was not open on {event_date.isoformat()}",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field=role,
            )
        if account.closed_at is not None and event_date > account.closed_at:
            _fail(
                f"{role} account was closed before {event_date.isoformat()}",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field=role,
            )
    return account


def _instrument(
    context: _BuildContext,
    instrument_id: str,
    *,
    transaction_id: str,
) -> _Instrument:
    instrument = context.instruments.get(instrument_id)
    if instrument is None:
        _fail(
            f"instrument {instrument_id} is absent from the sealed manifest",
            code=LedgerEventBuildErrorCode.MISSING_REFERENCE,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="instrument_id",
        )
    return instrument


def _priced_instrument(
    context: _BuildContext,
    instrument_id: str,
    *,
    transaction_id: str,
) -> _Instrument:
    instrument = _instrument(
        context,
        instrument_id,
        transaction_id=transaction_id,
    )
    if (
        instrument.valuation_contract_state != "available"
        or instrument.price_unit is None
        or instrument.contract_multiplier is None
        or instrument.price_factor is None
    ):
        _fail(
            "priced ledger event requires an available instrument valuation contract",
            code=LedgerEventBuildErrorCode.INSTRUMENT_CONTRACT_UNAVAILABLE,
            table=INSTRUMENT_TABLE,
            record_id=instrument_id,
            field="valuation_contract_state",
        )
    return instrument


def _validate_live_transaction(
    context: _BuildContext,
    row: Mapping[str, object],
    *,
    transaction_id: str,
    revision_id: str,
    revision_group_id: str,
    group_recorded_at: datetime,
) -> _Transaction:
    transaction_type = _text(
        row["transaction_type"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="transaction_type",
    )
    if transaction_type not in _SUPPORTED_TRANSACTION_TYPES:
        _fail(
            f"unsupported transaction_type {transaction_type}",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="transaction_type",
        )
    trade_date = _date(
        row["trade_date"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="trade_date",
    )
    trade_time = _time(
        row["trade_time"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="trade_time",
    )
    trade_at = _aware_datetime(
        row["trade_at"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="trade_at",
    )
    timezone_name = _text(
        row["trade_timezone"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="trade_timezone",
    )
    try:
        local_trade_at = trade_at.astimezone(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError:
        _fail(
            f"unknown trade timezone {timezone_name}",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="trade_timezone",
        )
    if (
        local_trade_at.date() != trade_date
        or local_trade_at.timetz().replace(tzinfo=None) != trade_time
    ):
        _fail(
            "trade_at does not agree with trade_date/time/timezone",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="trade_at",
        )
    _boolean(
        row["trade_time_is_estimated"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="trade_time_is_estimated",
    )
    settlement_date = _date(
        row["settlement_date"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="settlement_date",
    )
    entitlement_date = _optional_date(
        row["entitlement_date"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="entitlement_date",
    )
    acquisition_date = _optional_date(
        row["acquisition_date"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="acquisition_date",
    )
    if settlement_date < trade_date:
        _fail(
            "settlement_date precedes trade_date",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="settlement_date",
        )
    if entitlement_date is not None and entitlement_date > trade_date:
        _fail(
            "entitlement_date follows trade_date",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="entitlement_date",
        )
    if acquisition_date is not None and acquisition_date > trade_date:
        _fail(
            "acquisition_date follows trade_date",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="acquisition_date",
        )
    account_id = _text(
        row["account_id"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="account_id",
    )
    settlement_cash_account_id = _optional_text(
        row["settlement_cash_account_id"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="settlement_cash_account_id",
    )
    instrument_id = _optional_text(
        row["instrument_id"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="instrument_id",
    )
    quantity = _optional_fact(
        row["quantity"],
        kind=LedgerFactKind.QUANTITY,
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="quantity",
        strictly_positive=True,
    )
    price = _optional_fact(
        row["price"],
        kind=LedgerFactKind.PRICE,
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="price",
        strictly_positive=True,
    )
    gross_amount = _fact(
        row["gross_amount"],
        kind=LedgerFactKind.AMOUNT,
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="gross_amount",
        minimum=Decimal("0"),
    )
    counter_amount = _optional_fact(
        row["counter_amount"],
        kind=LedgerFactKind.AMOUNT,
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="counter_amount",
        strictly_positive=True,
    )
    quoted_fx_rate = _optional_fact(
        row["quoted_fx_rate"],
        kind=LedgerFactKind.RATIO,
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="quoted_fx_rate",
        strictly_positive=True,
    )
    effective_fx_rate = (
        None
        if row["effective_fx_rate_method50"] is None
        else _exact_decimal(
            row["effective_fx_rate_method50"],
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="effective_fx_rate_method50",
            strictly_positive=True,
        )
    )
    consideration_basis = _optional_text(
        row["consideration_basis"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="consideration_basis",
    )
    fees = _fact(
        row["fees"],
        kind=LedgerFactKind.AMOUNT,
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="fees",
        minimum=Decimal("0"),
    )
    taxes = _fact(
        row["taxes"],
        kind=LedgerFactKind.AMOUNT,
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="taxes",
        minimum=Decimal("0"),
    )
    currency = _currency(
        row["currency"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
    )
    transfer_scope = _optional_text(
        row["transfer_scope"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="transfer_scope",
    )
    transfer_object_type = _optional_text(
        row["transfer_object_type"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="transfer_object_type",
    )
    transfer_group_id = _optional_text(
        row["transfer_group_id"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="transfer_group_id",
    )
    counterparty_account_id = _optional_text(
        row["counterparty_account_id"],
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="counterparty_account_id",
    )
    if row["note"] is not None and not isinstance(row["note"], str):
        _fail(
            "transaction note must be a string or null",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="note",
        )
    if instrument_id is None:
        if row["instrument_snapshot_json"] is not None:
            _fail(
                "instrument snapshot exists without instrument_id",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="instrument_snapshot_json",
            )
        instrument = None
    else:
        instrument = _instrument(context, instrument_id, transaction_id=transaction_id)
        snapshot = _json_object(
            row["instrument_snapshot_json"],
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="instrument_snapshot_json",
        )
        if (
            snapshot.get("instrument_id") != instrument_id
            or snapshot.get("instrument_type") != instrument.instrument_type
            or snapshot.get("currency") != instrument.currency
        ):
            _fail(
                "transaction instrument snapshot conflicts with sealed instrument",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="instrument_snapshot_json",
            )
        if currency != instrument.currency:
            _fail(
                "transaction and instrument currencies differ",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="currency",
            )

    transaction = _Transaction(
        row=row,
        transaction_id=transaction_id,
        revision_id=revision_id,
        revision_group_id=revision_group_id,
        group_recorded_at=group_recorded_at,
        transaction_type=transaction_type,
        trade_date=trade_date,
        trade_at=trade_at,
        settlement_date=settlement_date,
        entitlement_date=entitlement_date,
        acquisition_date=acquisition_date,
        account_id=account_id,
        settlement_cash_account_id=settlement_cash_account_id,
        instrument_id=instrument_id,
        quantity=quantity,
        price=price,
        gross_amount=gross_amount,
        counter_amount=counter_amount,
        quoted_fx_rate=quoted_fx_rate,
        effective_fx_rate=effective_fx_rate,
        consideration_basis=consideration_basis,
        fees=fees,
        taxes=taxes,
        currency=currency,
        transfer_scope=transfer_scope,
        transfer_object_type=transfer_object_type,
        transfer_group_id=transfer_group_id,
        counterparty_account_id=counterparty_account_id,
    )
    _validate_transaction_shape(context, transaction)
    expected_hash = _transaction_payload_hash(row)
    if row["payload_hash"] != expected_hash:
        _fail(
            "transaction payload_hash does not match typed facts",
            code=LedgerEventBuildErrorCode.HASH_MISMATCH,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="payload_hash",
        )
    return transaction


def _require_positive_gross(transaction: _Transaction) -> None:
    if transaction.gross_amount <= 0:
        _fail(
            f"{transaction.transaction_type} requires positive gross_amount",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction.transaction_id,
            field="gross_amount",
        )


def _require_zero_costs(transaction: _Transaction) -> None:
    if transaction.fees != 0 or transaction.taxes != 0:
        _fail(
            f"{transaction.transaction_type} must not carry nested fees or taxes",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction.transaction_id,
            field="fees" if transaction.fees != 0 else "taxes",
        )


def _settlement_cash_account(
    context: _BuildContext,
    transaction: _Transaction,
) -> _Account:
    position_account = context.accounts.get(transaction.account_id)
    settlement_id = transaction.settlement_cash_account_id or (
        None
        if position_account is None
        else position_account.default_settlement_cash_account_id
    )
    if settlement_id is None:
        _fail(
            f"{transaction.transaction_type} requires settlement cash account",
            code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction.transaction_id,
            field="settlement_cash_account_id",
        )
    cash = _account(
        context,
        settlement_id,
        transaction_id=transaction.transaction_id,
        role="settlement_cash_account_id",
        expected_types=_CASH_ACCOUNT_TYPES,
        event_dates=(transaction.settlement_date,),
    )
    if cash.currency != transaction.currency:
        _fail(
            "settlement cash account currency differs from transaction currency",
            code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction.transaction_id,
            field="settlement_cash_account_id",
        )
    return cash


def _validate_amount_bridge(
    transaction: _Transaction,
    instrument: _Instrument,
) -> None:
    if transaction.consideration_basis == "source_reported":
        return
    if transaction.consideration_basis != "exact_quantity_price":
        _fail(
            "security price/amount transaction requires consideration_basis",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction.transaction_id,
            field="consideration_basis",
        )
    assert transaction.quantity is not None
    if transaction.price is None:
        _fail(
            "exact_quantity_price requires price",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction.transaction_id,
            field="price",
        )
    assert instrument.contract_multiplier is not None
    assert instrument.price_factor is not None
    exact = exact_decimal_product(
        transaction.quantity,
        transaction.price,
        instrument.contract_multiplier,
        instrument.price_factor,
    )
    if exact != transaction.gross_amount:
        _fail(
            "gross_amount does not exactly equal quantity * price * "
            f"contract_multiplier * price_factor; expected {exact}",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction.transaction_id,
            field="gross_amount",
        )


def _validate_transaction_shape(
    context: _BuildContext,
    transaction: _Transaction,
) -> None:
    row = transaction.row
    transaction_type = transaction.transaction_type
    transaction_id = transaction.transaction_id
    if transaction_type != "fx_conversion":
        _require_absent(
            row,
            ("counter_amount", "quoted_fx_rate"),
            transaction_id=transaction_id,
        )
    if transaction_type not in {"fx_conversion", "transfer_in", "transfer_out"}:
        _require_absent(
            row,
            ("counterparty_account_id",),
            transaction_id=transaction_id,
        )
    if transaction_type not in {"transfer_in", "transfer_out"}:
        _require_absent(
            row,
            ("transfer_scope", "transfer_object_type", "transfer_group_id"),
            transaction_id=transaction_id,
        )
    if transaction.entitlement_date is not None and transaction_type not in {
        "dividend",
        "coupon",
        "fee",
        "tax",
    }:
        _fail(
            "entitlement_date is inapplicable to this transaction type",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="entitlement_date",
        )
    if transaction.acquisition_date is not None and not (
        transaction_type == "opening_balance" and transaction.instrument_id is not None
    ):
        _fail(
            "acquisition_date is only supported for opening positions",
            code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
            table=TRANSACTION_TABLE,
            record_id=transaction_id,
            field="acquisition_date",
        )

    if transaction_type in {"buy", "sell"}:
        if (
            transaction.instrument_id is None
            or transaction.quantity is None
        ):
            _fail(
                f"{transaction_type} requires instrument and quantity",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
            )
        _require_positive_gross(transaction)
        position = _account(
            context,
            transaction.account_id,
            transaction_id=transaction_id,
            role="account_id",
            expected_types=_POSITION_ACCOUNT_TYPES,
            event_dates=(transaction.trade_date,),
        )
        if position.currency != transaction.currency:
            _fail(
                "position account currency differs from transaction currency",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="account_id",
            )
        _settlement_cash_account(context, transaction)
        instrument = _priced_instrument(
            context,
            transaction.instrument_id,
            transaction_id=transaction_id,
        )
        _validate_amount_bridge(transaction, instrument)
        return

    if transaction_type in {"dividend", "coupon"}:
        if transaction.instrument_id is None:
            _fail(
                f"{transaction_type} requires instrument_id",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="instrument_id",
            )
        _require_absent(row, ("quantity", "price"), transaction_id=transaction_id)
        _require_positive_gross(transaction)
        recognition = transaction.entitlement_date or transaction.trade_date
        position = _account(
            context,
            transaction.account_id,
            transaction_id=transaction_id,
            role="account_id",
            expected_types=_POSITION_ACCOUNT_TYPES,
            event_dates=(recognition,),
        )
        if position.currency != transaction.currency:
            _fail(
                "position account currency differs from income currency",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="account_id",
            )
        _settlement_cash_account(context, transaction)
        return

    if transaction_type == "interest":
        _require_absent(
            row,
            ("instrument_id", "instrument_snapshot_json", "quantity", "price", "settlement_cash_account_id"),
            transaction_id=transaction_id,
        )
        _require_positive_gross(transaction)
        _require_zero_costs(transaction)
        account = _account(
            context,
            transaction.account_id,
            transaction_id=transaction_id,
            role="account_id",
            expected_types=_CASH_ACCOUNT_TYPES,
            event_dates=(transaction.trade_date, transaction.settlement_date),
        )
        if account.currency != transaction.currency:
            _fail(
                "interest currency differs from cash account currency",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="currency",
            )
        return

    if transaction_type == "return_of_capital":
        if transaction.instrument_id is None:
            _fail(
                "return_of_capital requires instrument_id",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="instrument_id",
            )
        _require_absent(row, ("quantity", "price"), transaction_id=transaction_id)
        _require_positive_gross(transaction)
        position = _account(
            context,
            transaction.account_id,
            transaction_id=transaction_id,
            role="account_id",
            expected_types=_POSITION_ACCOUNT_TYPES,
            event_dates=(transaction.trade_date,),
        )
        if position.currency != transaction.currency:
            _fail(
                "position account currency differs from return-of-capital currency",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="account_id",
            )
        _settlement_cash_account(context, transaction)
        return

    if transaction_type == "dividend_reinvestment":
        if transaction.instrument_id is None or transaction.quantity is None:
            _fail(
                "dividend_reinvestment requires instrument and quantity",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
            )
        _require_absent(
            row,
            ("settlement_cash_account_id",),
            transaction_id=transaction_id,
        )
        _require_positive_gross(transaction)
        _require_zero_costs(transaction)
        position = _account(
            context,
            transaction.account_id,
            transaction_id=transaction_id,
            role="account_id",
            expected_types=_POSITION_ACCOUNT_TYPES,
            event_dates=(transaction.trade_date,),
        )
        if position.currency != transaction.currency:
            _fail(
                "position account currency differs from reinvestment currency",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="account_id",
            )
        instrument = _priced_instrument(
            context,
            transaction.instrument_id,
            transaction_id=transaction_id,
        )
        _validate_amount_bridge(transaction, instrument)
        return

    if transaction_type == "maturity_redemption":
        if transaction.instrument_id is None or transaction.quantity is None:
            _fail(
                "maturity_redemption requires instrument and quantity",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
            )
        _require_absent(row, ("price",), transaction_id=transaction_id)
        position = _account(
            context,
            transaction.account_id,
            transaction_id=transaction_id,
            role="account_id",
            expected_types=_POSITION_ACCOUNT_TYPES,
            event_dates=(transaction.trade_date,),
        )
        if position.currency != transaction.currency:
            _fail(
                "position account currency differs from redemption currency",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="account_id",
            )
        _settlement_cash_account(context, transaction)
        return

    if transaction_type in {"deposit", "withdrawal"}:
        _require_absent(
            row,
            ("instrument_id", "instrument_snapshot_json", "quantity", "price", "settlement_cash_account_id"),
            transaction_id=transaction_id,
        )
        _require_positive_gross(transaction)
        _require_zero_costs(transaction)
        account = _account(
            context,
            transaction.account_id,
            transaction_id=transaction_id,
            role="account_id",
            expected_types=_CASH_ACCOUNT_TYPES,
            event_dates=(transaction.settlement_date,),
        )
        if account.currency != transaction.currency:
            _fail(
                "external cash flow currency differs from account currency",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="currency",
            )
        return

    if transaction_type == "fx_conversion":
        _require_absent(
            row,
            ("instrument_id", "instrument_snapshot_json", "quantity", "price", "settlement_cash_account_id"),
            transaction_id=transaction_id,
        )
        _require_positive_gross(transaction)
        _require_zero_costs(transaction)
        if (
            transaction.counter_amount is None
            or transaction.effective_fx_rate is None
            or transaction.counterparty_account_id is None
        ):
            _fail(
                "fx_conversion requires actual target amount, effective rate, and account",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
            )
        source = _account(
            context,
            transaction.account_id,
            transaction_id=transaction_id,
            role="account_id",
            expected_types=_CASH_ACCOUNT_TYPES,
            event_dates=(transaction.trade_date, transaction.settlement_date),
        )
        target = _account(
            context,
            transaction.counterparty_account_id,
            transaction_id=transaction_id,
            role="counterparty_account_id",
            expected_types=_CASH_ACCOUNT_TYPES,
            event_dates=(transaction.trade_date, transaction.settlement_date),
        )
        if source.account_id == target.account_id or source.currency == target.currency:
            _fail(
                "FX conversion requires distinct accounts and currencies",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="counterparty_account_id",
            )
        if source.currency != transaction.currency:
            _fail(
                "FX conversion source currency differs from source account",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="currency",
            )
        return

    if transaction_type in {"fee", "tax"}:
        _require_absent(row, ("quantity", "price"), transaction_id=transaction_id)
        _require_positive_gross(transaction)
        _require_zero_costs(transaction)
        account = context.accounts.get(transaction.account_id)
        if account is None:
            _fail(
                "expense account is absent from sealed manifest",
                code=LedgerEventBuildErrorCode.MISSING_REFERENCE,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="account_id",
            )
        recognition = transaction.entitlement_date or transaction.trade_date
        if account.account_type in _CASH_ACCOUNT_TYPES:
            if transaction.instrument_id is not None or transaction.settlement_cash_account_id is not None:
                _fail(
                    "cash-account expense cannot carry instrument/settlement account",
                    code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                    table=TRANSACTION_TABLE,
                    record_id=transaction_id,
                )
            _account(
                context,
                account.account_id,
                transaction_id=transaction_id,
                role="account_id",
                expected_types=_CASH_ACCOUNT_TYPES,
                event_dates=(recognition, transaction.settlement_date),
            )
        else:
            _account(
                context,
                account.account_id,
                transaction_id=transaction_id,
                role="account_id",
                expected_types=_POSITION_ACCOUNT_TYPES,
                event_dates=(recognition,),
            )
            _settlement_cash_account(context, transaction)
        if transaction.entitlement_date is not None and transaction.instrument_id is None:
            _fail(
                "entitlement-date expense requires instrument_id",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="entitlement_date",
            )
        if transaction.currency != account.currency:
            _fail(
                "expense currency differs from account currency",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="currency",
            )
        return

    if transaction_type in {"transfer_in", "transfer_out"}:
        _require_absent(row, ("price", "settlement_cash_account_id"), transaction_id=transaction_id)
        _require_zero_costs(transaction)
        if (
            transaction.transfer_scope != "internal_portfolio"
            or transaction.transfer_object_type not in {"cash", "position"}
            or transaction.transfer_group_id is None
            or transaction.counterparty_account_id is None
        ):
            _fail(
                "internal transfer fields are incomplete",
                code=LedgerEventBuildErrorCode.TRANSFER_PAIR_INVALID,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
            )
        expected_types = (
            _CASH_ACCOUNT_TYPES
            if transaction.transfer_object_type == "cash"
            else _POSITION_ACCOUNT_TYPES
        )
        source_or_destination = _account(
            context,
            transaction.account_id,
            transaction_id=transaction_id,
            role="account_id",
            expected_types=expected_types,
            event_dates=(transaction.trade_date, transaction.settlement_date),
        )
        counterparty = _account(
            context,
            transaction.counterparty_account_id,
            transaction_id=transaction_id,
            role="counterparty_account_id",
            expected_types=expected_types,
            event_dates=(transaction.trade_date, transaction.settlement_date),
        )
        if source_or_destination.account_id == counterparty.account_id:
            _fail(
                "internal transfer accounts must be distinct",
                code=LedgerEventBuildErrorCode.TRANSFER_PAIR_INVALID,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="counterparty_account_id",
            )
        if (
            source_or_destination.currency != transaction.currency
            or counterparty.currency != transaction.currency
        ):
            _fail(
                "internal transfer accounts/transaction have different currencies",
                code=LedgerEventBuildErrorCode.TRANSFER_PAIR_INVALID,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="currency",
            )
        if transaction.transfer_object_type == "cash":
            _require_absent(
                row,
                ("instrument_id", "instrument_snapshot_json", "quantity"),
                transaction_id=transaction_id,
            )
            _require_positive_gross(transaction)
        else:
            if transaction.instrument_id is None or transaction.quantity is None:
                _fail(
                    "position transfer requires instrument and quantity",
                    code=LedgerEventBuildErrorCode.TRANSFER_PAIR_INVALID,
                    table=TRANSACTION_TABLE,
                    record_id=transaction_id,
                )
            if source_or_destination.cost_basis_method is not counterparty.cost_basis_method:
                _fail(
                    "position transfer between different cost-basis methods is unsupported",
                    code=LedgerEventBuildErrorCode.TRANSFER_PAIR_INVALID,
                    table=TRANSACTION_TABLE,
                    record_id=transaction_id,
                    field="counterparty_account_id",
                )
        return

    if transaction_type == "opening_balance":
        _require_absent(
            row,
            ("settlement_cash_account_id",),
            transaction_id=transaction_id,
        )
        _require_zero_costs(transaction)
        if transaction.settlement_date != transaction.trade_date:
            _fail(
                "opening balance settlement_date must equal its effective trade_date",
                code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="settlement_date",
            )
        if transaction.instrument_id is None:
            _require_absent(
                row,
                ("quantity", "price", "acquisition_date"),
                transaction_id=transaction_id,
            )
            account = _account(
                context,
                transaction.account_id,
                transaction_id=transaction_id,
                role="account_id",
                expected_types=_CASH_ACCOUNT_TYPES,
                event_dates=(transaction.trade_date,),
            )
        else:
            if transaction.quantity is None or transaction.acquisition_date is None:
                _fail(
                    "opening position requires quantity and acquisition_date",
                    code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
                    table=TRANSACTION_TABLE,
                    record_id=transaction_id,
                )
            account = _account(
                context,
                transaction.account_id,
                transaction_id=transaction_id,
                role="account_id",
                expected_types=_POSITION_ACCOUNT_TYPES,
                event_dates=(transaction.trade_date,),
            )
            instrument = _priced_instrument(
                context,
                transaction.instrument_id,
                transaction_id=transaction_id,
            )
            _validate_amount_bridge(transaction, instrument)
        if account.currency != transaction.currency:
            _fail(
                "opening balance currency differs from account currency",
                code=LedgerEventBuildErrorCode.ACCOUNT_CONTRACT_VIOLATION,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="currency",
            )
        return

    _fail(
        f"transaction type {transaction_type} has no exact producer mapping",
        code=LedgerEventBuildErrorCode.INPUT_CONTRACT_VIOLATION,
        table=TRANSACTION_TABLE,
        record_id=transaction_id,
        field="transaction_type",
    )


def _validate_transactions(
    context: _BuildContext,
) -> tuple[tuple[_Transaction, ...], list[LedgerEventBuildDiagnostic]]:
    transactions: list[_Transaction] = []
    diagnostics: list[LedgerEventBuildDiagnostic] = []
    seen: set[str] = set()
    for row in context.rows[TRANSACTION_TABLE]:
        (
            transaction_id,
            revision_id,
            revision_group_id,
            group_recorded_at,
            tombstone,
        ) = _validate_revision_header(row, cutoff=context.knowledge_cutoff_at)
        if transaction_id in seen:
            _fail(
                f"duplicate transaction_id {transaction_id}",
                code=LedgerEventBuildErrorCode.DUPLICATE_NATURAL_KEY,
                table=TRANSACTION_TABLE,
                record_id=transaction_id,
                field="transaction_id",
            )
        seen.add(transaction_id)
        if tombstone:
            diagnostics.append(
                LedgerEventBuildDiagnostic(
                    code=LedgerEventDiagnosticCode.TOMBSTONE_IGNORED,
                    source_table=TRANSACTION_TABLE,
                    source_record_id=transaction_id,
                    field_name="is_tombstone",
                    message="latest sealed transaction revision is a tombstone",
                    context=(("revision_id", revision_id),),
                )
            )
            continue
        transaction = _validate_live_transaction(
            context,
            row,
            transaction_id=transaction_id,
            revision_id=revision_id,
            revision_group_id=revision_group_id,
            group_recorded_at=group_recorded_at,
        )
        if transaction.trade_date > context.effective_as_of:
            diagnostics.append(
                LedgerEventBuildDiagnostic(
                    code=LedgerEventDiagnosticCode.FUTURE_TRANSACTION_IGNORED,
                    source_table=TRANSACTION_TABLE,
                    source_record_id=transaction_id,
                    field_name="trade_date",
                    message="transaction is after the manifest effective_as_of",
                    context=(
                        ("effective_as_of", context.effective_as_of.isoformat()),
                        ("trade_date", transaction.trade_date.isoformat()),
                    ),
                )
            )
            continue
        transactions.append(transaction)
    return tuple(sorted(transactions, key=lambda item: item.order_key)), diagnostics
