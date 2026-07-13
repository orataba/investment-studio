"""Replace mutable floating-point transactions with revisioned Decimal facts.

Revision ID: 20260713_0036
Revises: 20260713_0035

This is an intentionally breaking ledger migration.  The old current-state
table is captured once as explicit ``baseline`` revisions, verified, and
dropped.  ``transaction_current`` is a read-only view over the newest
non-tombstone revision; it is not a compatibility alias and has no write path.

Two obsolete API defaults are removed during the baseline capture.  An
inapplicable ``entitlement_date`` or ``acquisition_date`` is converted to NULL
only when it exactly equals ``trade_date`` (the value historically injected by
the API).  Legacy floating-point facts are explicitly rounded to the declared
Decimal scale with round-half-even; every affected field is counted in the
migration evidence.  Any other unsafe normalization fails the preflight instead
of silently changing economic history.  Per-portfolio normalization counts are
recorded on the migration revision group.

On PostgreSQL, the legacy table is locked against DML before it is read.  Lock
acquisition has a bounded timeout and the rename, backfill, verification, drop,
and Alembic version advance remain in one transactional-DDL unit: contention or
any later failure leaves revision 0035 and its legacy ledger intact.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from alembic import op
import sqlalchemy as sa


revision = "20260713_0036"
down_revision = "20260713_0035"
branch_labels = None
depends_on = None


_LEGACY_TABLE = "transaction_record"
_LEGACY_RENAMED_TABLE = "transaction_record_legacy_0036"
_POSTGRES_LEGACY_LOCK_TIMEOUT = "5s"
_PAYLOAD_SCHEMA_VERSION = "transaction-revision.v1"
_ACTOR_ID = "system:migration:20260713_0036"
_TRANSACTION_TYPES = {
    "buy", "sell", "dividend", "dividend_reinvestment", "coupon",
    "interest", "return_of_capital", "maturity_redemption", "fee", "tax",
    "deposit", "withdrawal", "fx_conversion", "transfer_in", "transfer_out",
    "opening_balance",
}
_ENTITLEMENT_TYPES = {"dividend", "coupon", "fee", "tax"}
_TRANSFER_TYPES = {"transfer_in", "transfer_out"}
_DECIMAL_SCALES = {
    "quantity": 12,
    "price": 12,
    "gross_amount": 8,
    "counter_amount": 8,
    "fx_rate": 18,
    "fees": 8,
    "taxes": 8,
}
_OPTIONAL_FACT_FIELDS = (
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
    "fx_rate",
    "fees",
    "taxes",
    "currency",
    "transfer_scope",
    "transfer_object_type",
    "transfer_group_id",
    "counterparty_account_id",
    "note",
)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _lock_legacy_ledger_against_postgresql_dml(connection: sa.Connection) -> None:
    """Hold a fail-closed legacy-ledger write lock for the migration transaction.

    ``SHARE ROW EXCLUSIVE`` conflicts with PostgreSQL's ``ROW EXCLUSIVE`` lock
    used by INSERT, UPDATE, and DELETE while still permitting ordinary reads.
    The later rename/drop operations upgrade this lock inside the same Alembic
    transaction.  SQLite has no equivalent table-lock statement and serializes
    writes at the database level, so this guard is intentionally PostgreSQL-only.
    """

    if connection.dialect.name != "postgresql":
        return

    schema = op.get_context().opts.get("version_table_schema")
    preparer = connection.dialect.identifier_preparer
    qualified_table = preparer.quote(_LEGACY_TABLE)
    if schema:
        qualified_table = (
            f"{preparer.quote_schema(str(schema))}.{qualified_table}"
        )

    connection.exec_driver_sql(
        f"SET LOCAL lock_timeout = '{_POSTGRES_LEGACY_LOCK_TIMEOUT}'"
    )
    try:
        connection.exec_driver_sql(
            f"LOCK TABLE {qualified_table} IN SHARE ROW EXCLUSIVE MODE"
        )
    except sa.exc.DBAPIError as error:
        raise RuntimeError(
            "0036 could not acquire the PostgreSQL legacy-ledger write lock "
            f"within {_POSTGRES_LEGACY_LOCK_TIMEOUT}; concurrent DML may be "
            "active. The migration failed closed before reading transaction_record."
        ) from error


def _date_value(value: object, *, field: str, transaction_id: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as error:
        raise RuntimeError(f"{transaction_id}: invalid {field}.") from error


def _time_value(value: object, *, transaction_id: str) -> time:
    if isinstance(value, time):
        parsed = value
    else:
        try:
            parsed = time.fromisoformat(str(value or "").strip())
        except ValueError as error:
            raise RuntimeError(f"{transaction_id}: invalid trade_time.") from error
    if parsed.tzinfo is not None or parsed.microsecond:
        raise RuntimeError(f"{transaction_id}: trade_time must be local second precision without timezone.")
    return parsed.replace(microsecond=0)


def _timestamp_value(value: object, *, field: str, transaction_id: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = str(value or "").strip()
        if not normalized:
            raise RuntimeError(f"{transaction_id}: missing {field}.")
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError as error:
            raise RuntimeError(f"{transaction_id}: invalid {field}.") from error
    if parsed.tzinfo is None:
        raise RuntimeError(f"{transaction_id}: {field} must retain an explicit timezone.")
    return parsed.astimezone(UTC)


def _decimal_value(
    value: object,
    *,
    field: str,
    transaction_id: str,
    precision: int,
    scale: int,
    normalizations: set[str],
    required: bool = False,
) -> Decimal | None:
    if value is None:
        if required:
            raise RuntimeError(f"{transaction_id}: missing {field}.")
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise RuntimeError(f"{transaction_id}: invalid {field}.") from error
    if not parsed.is_finite():
        raise RuntimeError(f"{transaction_id}: {field} must be finite.")
    quantum = Decimal(1).scaleb(-scale)
    try:
        with localcontext() as context:
            context.prec = 80
            quantized = parsed.quantize(quantum, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as error:
        raise RuntimeError(
            f"{transaction_id}: {field} exceeds NUMERIC({precision},{scale})."
        ) from error
    if parsed != quantized:
        normalizations.add(f"{field}_rounded_to_scale_{scale}")
    integer_digits = 1 if quantized == 0 else max(1, quantized.copy_abs().adjusted() + 1)
    if integer_digits > precision - scale:
        raise RuntimeError(f"{transaction_id}: {field} exceeds NUMERIC({precision},{scale}).")
    return quantized


def _json_object(value: object, *, transaction_id: str) -> dict[str, object] | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{transaction_id}: invalid instrument snapshot JSON.") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{transaction_id}: instrument snapshot must be an object.")
    return value


def _canonical_value(value: object, *, field_name: str) -> object:
    if value is None or isinstance(value, (str, bool, int, float, list, dict)):
        return value
    if isinstance(value, Decimal):
        return format(value, f".{_DECIMAL_SCALES[field_name]}f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="microseconds")
    raise RuntimeError(f"cannot canonicalize transaction field {field_name}.")


def _payload_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        {
            "payload_schema_version": _PAYLOAD_SCHEMA_VERSION,
            "is_tombstone": False,
            "facts": {
                field_name: _canonical_value(payload[field_name], field_name=field_name)
                for field_name in _OPTIONAL_FACT_FIELDS
            },
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _legacy_table() -> sa.Table:
    metadata = sa.MetaData()
    # SQLite test/bootstrap databases can apply the Portfolio migration stack
    # before the independently owned Instrument Registry tables are created.
    # We only need the legacy columns here, never the referenced metadata.
    return sa.Table(
        _LEGACY_TABLE,
        metadata,
        autoload_with=op.get_bind(),
        resolve_fks=False,
    )


def _normalize_legacy_row(
    row: sa.RowMapping,
) -> tuple[dict[str, object], datetime, frozenset[str]]:
    transaction_id = str(row["transaction_id"] or "").strip()
    if not transaction_id:
        raise RuntimeError("transaction_record contains a blank transaction_id.")
    transaction_type = str(row["transaction_type"] or "").strip()
    if transaction_type not in _TRANSACTION_TYPES:
        raise RuntimeError(f"{transaction_id}: unsupported transaction_type '{transaction_type}'.")
    trade_date = _date_value(row["trade_date"], field="trade_date", transaction_id=transaction_id)
    settlement_date = _date_value(row["settlement_date"], field="settlement_date", transaction_id=transaction_id)
    if trade_date is None or settlement_date is None or settlement_date < trade_date:
        raise RuntimeError(f"{transaction_id}: invalid trade/settlement date ordering.")
    normalizations: set[str] = set()
    entitlement_date = _date_value(row["entitlement_date"], field="entitlement_date", transaction_id=transaction_id)
    if entitlement_date is not None and transaction_type not in _ENTITLEMENT_TYPES:
        if entitlement_date != trade_date:
            raise RuntimeError(f"{transaction_id}: inapplicable entitlement_date cannot be discarded safely.")
        entitlement_date = None
        normalizations.add("entitlement_date_default")
    acquisition_date = _date_value(row["acquisition_date"], field="acquisition_date", transaction_id=transaction_id)
    if acquisition_date is not None and transaction_type != "opening_balance":
        if acquisition_date != trade_date:
            raise RuntimeError(f"{transaction_id}: inapplicable acquisition_date cannot be discarded safely.")
        acquisition_date = None
        normalizations.add("acquisition_date_default")
    instrument_id = str(row["instrument_id"] or "").strip() or None
    instrument_snapshot = _json_object(row["instrument_ref_json"], transaction_id=transaction_id)
    if bool(instrument_id) != bool(instrument_snapshot):
        raise RuntimeError(f"{transaction_id}: instrument_id and instrument snapshot must be present together.")
    if instrument_snapshot is not None and str(instrument_snapshot.get("instrument_id") or "") != instrument_id:
        raise RuntimeError(f"{transaction_id}: instrument snapshot identity mismatch.")
    currency = str(row["currency"] or "").strip().upper()
    if currency not in {"USD", "HKD", "CNY"}:
        raise RuntimeError(f"{transaction_id}: unsupported currency '{currency}'.")
    account_id = str(row["account_id"] or "").strip()
    if not account_id:
        raise RuntimeError(f"{transaction_id}: missing account_id.")
    payload: dict[str, object] = {
        "transaction_type": transaction_type,
        "trade_date": trade_date,
        "trade_time": _time_value(row["trade_time"], transaction_id=transaction_id),
        "trade_at": _timestamp_value(row["trade_at"], field="trade_at", transaction_id=transaction_id),
        "trade_timezone": str(row["trade_timezone"] or "").strip(),
        "trade_time_is_estimated": bool(row["trade_time_is_estimated"]),
        "settlement_date": settlement_date,
        "entitlement_date": entitlement_date,
        "acquisition_date": acquisition_date,
        "account_id": account_id,
        "settlement_cash_account_id": str(row["settlement_cash_account_id"] or "").strip() or None,
        "instrument_id": instrument_id,
        "instrument_snapshot_json": instrument_snapshot,
        "quantity": _decimal_value(row["quantity"], field="quantity", transaction_id=transaction_id, precision=38, scale=12, normalizations=normalizations),
        "price": _decimal_value(row["price"], field="price", transaction_id=transaction_id, precision=38, scale=12, normalizations=normalizations),
        "gross_amount": _decimal_value(row["gross_amount"], field="gross_amount", transaction_id=transaction_id, precision=38, scale=8, normalizations=normalizations, required=True),
        "counter_amount": _decimal_value(row["counter_amount"], field="counter_amount", transaction_id=transaction_id, precision=38, scale=8, normalizations=normalizations),
        "fx_rate": _decimal_value(row["fx_rate"], field="fx_rate", transaction_id=transaction_id, precision=38, scale=18, normalizations=normalizations),
        "fees": _decimal_value(row["fees"], field="fees", transaction_id=transaction_id, precision=38, scale=8, normalizations=normalizations, required=True),
        "taxes": _decimal_value(row["taxes"], field="taxes", transaction_id=transaction_id, precision=38, scale=8, normalizations=normalizations, required=True),
        "currency": currency,
        "transfer_scope": str(row["transfer_scope"] or "").strip() or None,
        "transfer_object_type": str(row["transfer_object_type"] or "").strip() or None,
        "transfer_group_id": str(row["transfer_group_id"] or "").strip() or None,
        "counterparty_account_id": str(row["counterparty_account_id"] or "").strip() or None,
        # Note text is audit evidence, not an identifier; preserve it byte-for-
        # byte at the string level instead of trimming or interpreting blanks.
        "note": None if row["note"] is None else str(row["note"]),
    }
    if not payload["trade_timezone"]:
        raise RuntimeError(f"{transaction_id}: missing trade_timezone.")
    return (
        payload,
        _timestamp_value(row["created_at"], field="created_at", transaction_id=transaction_id),
        frozenset(normalizations),
    )


def _require_payload(
    condition: bool,
    *,
    transaction_id: str,
    message: str,
) -> None:
    if not condition:
        raise RuntimeError(f"{transaction_id}: {message}")


def _validate_normalized_payload(
    transaction_id: str,
    payload: dict[str, object],
) -> None:
    """Mirror the database invariants before any destructive DDL is issued."""

    transaction_type = str(payload["transaction_type"])
    trade_date = payload["trade_date"]
    trade_time = payload["trade_time"]
    trade_at = payload["trade_at"]
    timezone_name = str(payload["trade_timezone"])
    settlement_date = payload["settlement_date"]
    entitlement_date = payload["entitlement_date"]
    acquisition_date = payload["acquisition_date"]
    instrument_id = payload["instrument_id"]
    instrument_snapshot = payload["instrument_snapshot_json"]
    quantity = payload["quantity"]
    price = payload["price"]
    gross_amount = payload["gross_amount"]
    counter_amount = payload["counter_amount"]
    fx_rate = payload["fx_rate"]
    fees = payload["fees"]
    taxes = payload["taxes"]
    transfer_scope = payload["transfer_scope"]
    transfer_object_type = payload["transfer_object_type"]
    transfer_group_id = payload["transfer_group_id"]
    counterparty_account_id = payload["counterparty_account_id"]

    _require_payload(
        isinstance(trade_date, date) and not isinstance(trade_date, datetime),
        transaction_id=transaction_id,
        message="trade_date is not a date.",
    )
    _require_payload(
        isinstance(settlement_date, date)
        and not isinstance(settlement_date, datetime)
        and settlement_date >= trade_date,
        transaction_id=transaction_id,
        message="settlement_date precedes trade_date.",
    )
    _require_payload(
        isinstance(trade_time, time) and trade_time.tzinfo is None,
        transaction_id=transaction_id,
        message="trade_time must be a timezone-naive time.",
    )
    _require_payload(
        isinstance(trade_at, datetime) and trade_at.tzinfo is not None,
        transaction_id=transaction_id,
        message="trade_at must be timezone-aware.",
    )
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(
            f"{transaction_id}: trade_timezone '{timezone_name}' is not an IANA timezone."
        ) from error
    localized_trade_at = trade_at.astimezone(timezone)
    _require_payload(
        localized_trade_at.date() == trade_date
        and localized_trade_at.time().replace(tzinfo=None) == trade_time,
        transaction_id=transaction_id,
        message="trade_at does not reproduce trade_date/trade_time in trade_timezone.",
    )
    if entitlement_date is not None:
        _require_payload(
            entitlement_date <= trade_date and transaction_type in _ENTITLEMENT_TYPES,
            transaction_id=transaction_id,
            message="invalid entitlement_date applicability or ordering.",
        )
    if acquisition_date is not None:
        _require_payload(
            acquisition_date <= trade_date
            and transaction_type == "opening_balance"
            and instrument_id is not None,
            transaction_id=transaction_id,
            message="acquisition_date is only valid for a security opening balance.",
        )

    for field_name in ("quantity", "price", "counter_amount", "fx_rate"):
        value = payload[field_name]
        _require_payload(
            value is None or (isinstance(value, Decimal) and value > 0),
            transaction_id=transaction_id,
            message=f"{field_name} must be positive when present.",
        )
    for field_name in ("gross_amount", "fees", "taxes"):
        value = payload[field_name]
        _require_payload(
            isinstance(value, Decimal) and value >= 0,
            transaction_id=transaction_id,
            message=f"{field_name} must be non-negative.",
        )
    _require_payload(
        bool(instrument_id) == bool(instrument_snapshot),
        transaction_id=transaction_id,
        message="instrument_id and instrument_snapshot_json must be present together.",
    )

    is_fx = transaction_type == "fx_conversion"
    is_transfer = transaction_type in _TRANSFER_TYPES
    if is_fx:
        _require_payload(
            instrument_id is None
            and quantity is None
            and price is None
            and payload["settlement_cash_account_id"] is None
            and counter_amount is not None
            and fx_rate is not None
            and counterparty_account_id is not None
            and gross_amount > 0
            and fees == 0
            and taxes == 0,
            transaction_id=transaction_id,
            message="invalid fx_conversion payload.",
        )
    else:
        _require_payload(
            counter_amount is None and fx_rate is None,
            transaction_id=transaction_id,
            message="counter_amount/fx_rate are only valid for fx_conversion.",
        )

    if is_transfer:
        _require_payload(
            transfer_scope == "internal_portfolio"
            and transfer_object_type in {"cash", "position"}
            and payload["settlement_cash_account_id"] is None
            and bool(transfer_group_id)
            and bool(counterparty_account_id)
            and fees == 0
            and taxes == 0,
            transaction_id=transaction_id,
            message="invalid internal transfer envelope.",
        )
        if transfer_object_type == "cash":
            _require_payload(
                instrument_id is None and quantity is None and price is None and gross_amount > 0,
                transaction_id=transaction_id,
                message="invalid cash transfer facts.",
            )
        else:
            _require_payload(
                instrument_id is not None and quantity is not None and price is None and gross_amount >= 0,
                transaction_id=transaction_id,
                message="invalid position transfer facts.",
            )
    else:
        _require_payload(
            transfer_scope is None
            and transfer_object_type is None
            and transfer_group_id is None
            and (is_fx or counterparty_account_id is None),
            transaction_id=transaction_id,
            message="transfer-only fields are present on an ordinary transaction.",
        )

    if transaction_type in {"buy", "sell"}:
        valid_shape = instrument_id is not None and quantity is not None and price is not None and gross_amount > 0
    elif transaction_type in {"dividend", "coupon", "return_of_capital"}:
        valid_shape = instrument_id is not None and quantity is None and price is None
    elif transaction_type == "dividend_reinvestment":
        valid_shape = (
            instrument_id is not None
            and quantity is not None
            and gross_amount > 0
            and payload["settlement_cash_account_id"] is None
            and fees == 0
            and taxes == 0
        )
    elif transaction_type == "maturity_redemption":
        valid_shape = instrument_id is not None and quantity is not None and price is None
    elif transaction_type in {"deposit", "withdrawal", "interest"}:
        valid_shape = (
            instrument_id is None
            and quantity is None
            and price is None
            and payload["settlement_cash_account_id"] is None
            and fees == 0
            and taxes == 0
        )
    elif transaction_type == "fx_conversion":
        valid_shape = True
    elif transaction_type in {"fee", "tax"}:
        valid_shape = quantity is None and price is None and fees == 0 and taxes == 0
        if entitlement_date is not None:
            valid_shape = valid_shape and instrument_id is not None
    elif is_transfer:
        valid_shape = True
    else:  # opening_balance
        valid_shape = (
            payload["settlement_cash_account_id"] is None
            and fees == 0
            and taxes == 0
            and (
                (
                    instrument_id is not None
                    and quantity is not None
                    and acquisition_date is not None
                )
                or (
                    instrument_id is None
                    and quantity is None
                    and price is None
                    and acquisition_date is None
                )
            )
        )
    _require_payload(
        valid_shape,
        transaction_id=transaction_id,
        message=f"invalid {transaction_type} transaction fact shape.",
    )


def _preflight_legacy_rows(
    connection: sa.Connection,
) -> list[dict[str, object]]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_LEGACY_TABLE):
        raise RuntimeError(f"required legacy table {_LEGACY_TABLE} does not exist.")
    collisions = [
        name
        for name in (
            _LEGACY_RENAMED_TABLE,
            "transaction_identity_record",
            "transaction_revision_group_record",
            "transaction_revision_record",
        )
        if inspector.has_table(name)
    ]
    if collisions or "transaction_current" in inspector.get_view_names():
        raise RuntimeError(
            "0036 target ledger objects already exist; refusing a partial migration: "
            f"{collisions}."
        )

    required_columns = {
        "transaction_id",
        "portfolio_id",
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
        "instrument_ref_json",
        "quantity",
        "price",
        "gross_amount",
        "counter_amount",
        "fx_rate",
        "fees",
        "taxes",
        "currency",
        "transfer_scope",
        "transfer_object_type",
        "transfer_group_id",
        "counterparty_account_id",
        "note",
        "created_at",
    }
    actual_columns = {str(column["name"]) for column in inspector.get_columns(_LEGACY_TABLE)}
    missing_columns = sorted(required_columns - actual_columns)
    if missing_columns:
        raise RuntimeError(f"transaction_record is missing required columns: {missing_columns}.")

    legacy = _legacy_table()
    rows = list(
        connection.execute(
            sa.select(legacy).order_by(legacy.c.transaction_id)
        ).mappings()
    )
    portfolio_ids = {
        str(value)
        for value in connection.execute(
            sa.text("SELECT portfolio_id FROM portfolio_record")
        ).scalars()
    }
    account_keys = {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            sa.text("SELECT portfolio_id, account_id FROM account_record")
        )
    }

    prepared: list[dict[str, object]] = []
    transaction_ids: set[str] = set()
    for row in rows:
        transaction_id = str(row["transaction_id"] or "").strip()
        portfolio_id = str(row["portfolio_id"] or "").strip()
        if transaction_id in transaction_ids:
            raise RuntimeError(f"duplicate transaction_id in legacy ledger: {transaction_id}.")
        transaction_ids.add(transaction_id)
        if not portfolio_id or portfolio_id not in portfolio_ids:
            raise RuntimeError(f"{transaction_id}: unknown or blank portfolio_id '{portfolio_id}'.")
        payload, created_at, normalizations = _normalize_legacy_row(row)
        _validate_normalized_payload(transaction_id, payload)
        for field_name in (
            "account_id",
            "settlement_cash_account_id",
            "counterparty_account_id",
        ):
            account_id = payload[field_name]
            if account_id is not None and (portfolio_id, str(account_id)) not in account_keys:
                raise RuntimeError(
                    f"{transaction_id}: {field_name} '{account_id}' does not belong to portfolio '{portfolio_id}'."
                )
        prepared.append(
            {
                "transaction_id": transaction_id,
                "portfolio_id": portfolio_id,
                "created_at": created_at,
                "payload": payload,
                "payload_hash": _payload_hash(payload),
                "normalizations": normalizations,
            }
        )
    transfer_groups: dict[tuple[str, str], list[dict[str, object]]] = {}
    for item in prepared:
        payload = item["payload"]
        if not isinstance(payload, dict) or payload["transaction_type"] not in _TRANSFER_TYPES:
            continue
        transfer_groups.setdefault(
            (str(item["portfolio_id"]), str(payload["transfer_group_id"])),
            [],
        ).append(item)

    mirrored_fields = (
        "trade_date",
        "trade_time",
        "trade_at",
        "trade_timezone",
        "trade_time_is_estimated",
        "settlement_date",
        "transfer_scope",
        "transfer_object_type",
        "instrument_id",
        "instrument_snapshot_json",
        "quantity",
        "price",
        "gross_amount",
        "currency",
    )
    for (portfolio_id, transfer_group_id), items in transfer_groups.items():
        if len(items) != 2:
            raise RuntimeError(
                f"{portfolio_id}/{transfer_group_id}: legacy internal transfer requires exactly two legs."
            )
        payloads = [item["payload"] for item in items]
        if any(not isinstance(payload, dict) for payload in payloads):  # pragma: no cover
            raise RuntimeError(f"{portfolio_id}/{transfer_group_id}: invalid transfer payload.")
        outbound = next(
            (payload for payload in payloads if payload["transaction_type"] == "transfer_out"),
            None,
        )
        inbound = next(
            (payload for payload in payloads if payload["transaction_type"] == "transfer_in"),
            None,
        )
        if outbound is None or inbound is None:
            raise RuntimeError(
                f"{portfolio_id}/{transfer_group_id}: legacy internal transfer requires one in leg and one out leg."
            )
        if (
            outbound["counterparty_account_id"] != inbound["account_id"]
            or inbound["counterparty_account_id"] != outbound["account_id"]
        ):
            raise RuntimeError(
                f"{portfolio_id}/{transfer_group_id}: legacy internal transfer account links are not reciprocal."
            )
        mismatches = [
            field_name
            for field_name in mirrored_fields
            if outbound[field_name] != inbound[field_name]
        ]
        if mismatches:
            raise RuntimeError(
                f"{portfolio_id}/{transfer_group_id}: legacy internal transfer legs differ in "
                f"{', '.join(mismatches)}."
            )
    return prepared


def _ensure_account_composite_key(connection: sa.Connection) -> None:
    expected_name = "uq_account_record_portfolio_account"
    unique_constraints = sa.inspect(connection).get_unique_constraints("account_record")
    if any(
        constraint.get("name") == expected_name
        and list(constraint.get("column_names") or []) == ["portfolio_id", "account_id"]
        for constraint in unique_constraints
    ):
        return
    with op.batch_alter_table("account_record") as batch_op:
        batch_op.create_unique_constraint(
            expected_name,
            ["portfolio_id", "account_id"],
        )


def _create_ledger_tables(connection: sa.Connection) -> None:
    op.create_table(
        "transaction_identity_record",
        sa.Column("transaction_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.CheckConstraint(
            "length(trim(transaction_id)) > 0",
            name="ck_transaction_identity_record_transaction_id_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(created_by)) > 0",
            name="ck_transaction_identity_record_created_by_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            name="fk_transaction_identity_record_portfolio_id_portfolio_record",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "transaction_id",
            name="pk_transaction_identity_record",
        ),
        sa.UniqueConstraint(
            "portfolio_id",
            "transaction_id",
            name="uq_transaction_identity_portfolio_transaction",
        ),
    )
    op.create_index(
        "ix_transaction_identity_portfolio_created",
        "transaction_identity_record",
        ["portfolio_id", "created_at", "transaction_id"],
        unique=False,
    )

    op.create_table(
        "transaction_revision_group_record",
        sa.Column("revision_group_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("change_reason", sa.String(), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("actor_display_name", sa.String(), nullable=False),
        sa.Column("actor_source", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.CheckConstraint(
            "length(trim(revision_group_id)) > 0",
            name="ck_transaction_revision_group_record_group_id_nonblank",
        ),
        sa.CheckConstraint(
            "source_kind IN ('manual', 'import', 'reconciliation', 'migration', 'system')",
            name="ck_transaction_revision_group_record_source_kind",
        ),
        sa.CheckConstraint(
            "length(trim(change_reason)) > 0",
            name="ck_transaction_revision_group_record_change_reason_nonblank",
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'service', 'migration')",
            name="ck_transaction_revision_group_record_actor_type",
        ),
        sa.CheckConstraint(
            "length(trim(actor_id)) > 0",
            name="ck_transaction_revision_group_record_actor_id_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(actor_display_name)) > 0",
            name="ck_transaction_revision_group_record_actor_display_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(actor_source)) > 0",
            name="ck_transaction_revision_group_record_actor_source_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            name="fk_txn_revision_group_portfolio",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "revision_group_id",
            name="pk_transaction_revision_group_record",
        ),
        sa.UniqueConstraint(
            "portfolio_id",
            "revision_group_id",
            name="uq_transaction_revision_group_portfolio_group",
        ),
    )
    op.create_index(
        "ix_transaction_revision_group_portfolio_recorded",
        "transaction_revision_group_record",
        ["portfolio_id", "recorded_at", "revision_group_id"],
        unique=False,
    )
    op.create_index(
        "uq_transaction_revision_group_portfolio_idempotency",
        "transaction_revision_group_record",
        ["portfolio_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "transaction_revision_record",
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("transaction_id", sa.String(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("revision_group_id", sa.String(), nullable=False),
        sa.Column("revision_kind", sa.String(), nullable=False),
        sa.Column("is_tombstone", sa.Boolean(), nullable=False),
        sa.Column("supersedes_revision_id", sa.String(), nullable=True),
        sa.Column("supersedes_revision_number", sa.Integer(), nullable=True),
        sa.Column("payload_schema_version", sa.String(), nullable=False),
        sa.Column("payload_hash", sa.String(length=71), nullable=False),
        sa.Column("transaction_type", sa.String(), nullable=True),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("trade_time", sa.Time(timezone=False), nullable=True),
        sa.Column("trade_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trade_timezone", sa.String(), nullable=True),
        sa.Column("trade_time_is_estimated", sa.Boolean(), nullable=True),
        sa.Column("settlement_date", sa.Date(), nullable=True),
        sa.Column("entitlement_date", sa.Date(), nullable=True),
        sa.Column("acquisition_date", sa.Date(), nullable=True),
        sa.Column("account_id", sa.String(), nullable=True),
        sa.Column("settlement_cash_account_id", sa.String(), nullable=True),
        sa.Column("instrument_id", sa.String(), nullable=True),
        sa.Column("instrument_snapshot_json", sa.JSON(), nullable=True),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=True),
        sa.Column("price", sa.Numeric(38, 12), nullable=True),
        sa.Column("gross_amount", sa.Numeric(38, 8), nullable=True),
        sa.Column("counter_amount", sa.Numeric(38, 8), nullable=True),
        sa.Column("fx_rate", sa.Numeric(38, 18), nullable=True),
        sa.Column("fees", sa.Numeric(38, 8), nullable=True),
        sa.Column("taxes", sa.Numeric(38, 8), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("transfer_scope", sa.String(), nullable=True),
        sa.Column("transfer_object_type", sa.String(), nullable=True),
        sa.Column("transfer_group_id", sa.String(), nullable=True),
        sa.Column("counterparty_account_id", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.CheckConstraint(
            "length(trim(revision_id)) > 0 AND length(trim(transaction_id)) > 0 "
            "AND length(trim(revision_group_id)) > 0",
            name="ck_transaction_revision_record_identifiers_nonblank",
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_transaction_revision_record_positive_revision_number",
        ),
        sa.CheckConstraint(
            "revision_kind IN ('baseline', 'create', 'amend', 'delete')",
            name="ck_transaction_revision_record_revision_kind",
        ),
        sa.CheckConstraint(
            "is_tombstone = (revision_kind = 'delete')",
            name="ck_transaction_revision_record_tombstone_kind",
        ),
        sa.CheckConstraint(
            "((revision_number = 1 AND revision_kind IN ('baseline', 'create') "
            "AND supersedes_revision_id IS NULL AND supersedes_revision_number IS NULL) "
            "OR (revision_number > 1 AND revision_kind IN ('amend', 'delete') "
            "AND supersedes_revision_id IS NOT NULL "
            "AND supersedes_revision_number = revision_number - 1))",
            name="ck_transaction_revision_record_revision_chain_shape",
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 71 AND payload_hash LIKE 'sha256:%'",
            name="ck_transaction_revision_record_payload_hash_shape",
        ),
        sa.CheckConstraint(
            "payload_schema_version = 'transaction-revision.v1'",
            name="ck_transaction_revision_record_payload_schema_version",
        ),
        sa.CheckConstraint(
            "NOT is_tombstone OR payload_hash = "
            f"'{_tombstone_payload_hash()}'",
            name="ck_transaction_revision_record_tombstone_payload_hash",
        ),
        sa.CheckConstraint(
            "((is_tombstone AND transaction_type IS NULL AND trade_date IS NULL "
            "AND trade_time IS NULL AND trade_at IS NULL AND trade_timezone IS NULL "
            "AND trade_time_is_estimated IS NULL AND settlement_date IS NULL "
            "AND entitlement_date IS NULL AND acquisition_date IS NULL "
            "AND account_id IS NULL AND settlement_cash_account_id IS NULL "
            "AND instrument_id IS NULL AND instrument_snapshot_json IS NULL "
            "AND quantity IS NULL AND price IS NULL AND gross_amount IS NULL "
            "AND counter_amount IS NULL AND fx_rate IS NULL AND fees IS NULL "
            "AND taxes IS NULL AND currency IS NULL AND transfer_scope IS NULL "
            "AND transfer_object_type IS NULL AND transfer_group_id IS NULL "
            "AND counterparty_account_id IS NULL AND note IS NULL) OR "
            "(NOT is_tombstone AND transaction_type IS NOT NULL "
            "AND trade_date IS NOT NULL AND trade_time IS NOT NULL "
            "AND trade_at IS NOT NULL AND length(trim(trade_timezone)) > 0 "
            "AND trade_time_is_estimated IS NOT NULL AND settlement_date IS NOT NULL "
            "AND account_id IS NOT NULL AND gross_amount IS NOT NULL "
            "AND fees IS NOT NULL AND taxes IS NOT NULL AND currency IS NOT NULL))",
            name="ck_transaction_revision_record_tombstone_payload",
        ),
        sa.CheckConstraint(
            "transaction_type IS NULL OR transaction_type IN "
            "('buy', 'sell', 'dividend', 'dividend_reinvestment', 'coupon', "
            "'interest', 'return_of_capital', 'maturity_redemption', 'fee', "
            "'tax', 'deposit', 'withdrawal', 'fx_conversion', 'transfer_in', "
            "'transfer_out', 'opening_balance')",
            name="ck_transaction_revision_record_transaction_type",
        ),
        sa.CheckConstraint(
            "is_tombstone OR settlement_date >= trade_date",
            name="ck_transaction_revision_record_settlement_not_before_trade",
        ),
        sa.CheckConstraint(
            "is_tombstone OR entitlement_date IS NULL OR entitlement_date <= trade_date",
            name="ck_transaction_revision_record_entitlement_not_after_trade",
        ),
        sa.CheckConstraint(
            "is_tombstone OR acquisition_date IS NULL OR acquisition_date <= trade_date",
            name="ck_transaction_revision_record_acquisition_not_after_trade",
        ),
        sa.CheckConstraint(
            "is_tombstone OR currency IN ('USD', 'HKD', 'CNY')",
            name="ck_transaction_revision_record_supported_currency",
        ),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity > 0",
            name="ck_transaction_revision_record_positive_quantity",
        ),
        sa.CheckConstraint(
            "price IS NULL OR price > 0",
            name="ck_transaction_revision_record_positive_price",
        ),
        sa.CheckConstraint(
            "gross_amount IS NULL OR gross_amount >= 0",
            name="ck_transaction_revision_record_nonnegative_gross_amount",
        ),
        sa.CheckConstraint(
            "counter_amount IS NULL OR counter_amount > 0",
            name="ck_transaction_revision_record_positive_counter_amount",
        ),
        sa.CheckConstraint(
            "fx_rate IS NULL OR fx_rate > 0",
            name="ck_transaction_revision_record_positive_fx_rate",
        ),
        sa.CheckConstraint(
            "fees IS NULL OR fees >= 0",
            name="ck_transaction_revision_record_nonnegative_fees",
        ),
        sa.CheckConstraint(
            "taxes IS NULL OR taxes >= 0",
            name="ck_transaction_revision_record_nonnegative_taxes",
        ),
        sa.CheckConstraint(
            "is_tombstone OR entitlement_date IS NULL OR "
            "transaction_type IN ('dividend', 'coupon', 'fee', 'tax')",
            name="ck_transaction_revision_record_entitlement_applicability",
        ),
        sa.CheckConstraint(
            "is_tombstone OR entitlement_date IS NULL OR "
            "transaction_type NOT IN ('fee', 'tax') OR instrument_id IS NOT NULL",
            name="ck_transaction_revision_record_expense_entitlement_instrument",
        ),
        sa.CheckConstraint(
            "is_tombstone OR acquisition_date IS NULL OR "
            "(transaction_type = 'opening_balance' AND instrument_id IS NOT NULL)",
            name="ck_transaction_revision_record_acquisition_applicability",
        ),
        sa.CheckConstraint(
            "is_tombstone OR ((transaction_type = 'fx_conversion' "
            "AND instrument_id IS NULL AND instrument_snapshot_json IS NULL "
            "AND quantity IS NULL AND price IS NULL "
            "AND settlement_cash_account_id IS NULL "
            "AND counter_amount IS NOT NULL AND fx_rate IS NOT NULL "
            "AND counterparty_account_id IS NOT NULL AND fees = 0 AND taxes = 0) "
            "OR (transaction_type <> 'fx_conversion' AND counter_amount IS NULL "
            "AND fx_rate IS NULL))",
            name="ck_transaction_revision_record_fx_conversion_fields",
        ),
        sa.CheckConstraint(
            "is_tombstone OR ((transaction_type IN ('transfer_in', 'transfer_out') "
            "AND transfer_scope = 'internal_portfolio' "
            "AND transfer_object_type IN ('cash', 'position') "
            "AND settlement_cash_account_id IS NULL "
            "AND transfer_group_id IS NOT NULL AND counterparty_account_id IS NOT NULL "
            "AND fees = 0 AND taxes = 0) "
            "OR (transaction_type NOT IN ('transfer_in', 'transfer_out') "
            "AND transfer_scope IS NULL AND transfer_object_type IS NULL "
            "AND transfer_group_id IS NULL "
            "AND (transaction_type = 'fx_conversion' OR counterparty_account_id IS NULL)))",
            name="ck_transaction_revision_record_transfer_fields",
        ),
        sa.CheckConstraint(
            "is_tombstone OR ("
            "(transaction_type IN ('buy', 'sell') AND instrument_id IS NOT NULL "
            "AND quantity IS NOT NULL AND price IS NOT NULL AND gross_amount > 0) OR "
            "(transaction_type IN ('dividend', 'coupon', 'return_of_capital') "
            "AND instrument_id IS NOT NULL AND quantity IS NULL AND price IS NULL) OR "
            "(transaction_type = 'dividend_reinvestment' AND instrument_id IS NOT NULL "
            "AND quantity IS NOT NULL AND gross_amount > 0 "
            "AND settlement_cash_account_id IS NULL AND fees = 0 AND taxes = 0) OR "
            "(transaction_type = 'maturity_redemption' AND instrument_id IS NOT NULL "
            "AND quantity IS NOT NULL AND price IS NULL) OR "
            "(transaction_type IN ('deposit', 'withdrawal', 'interest') "
            "AND instrument_id IS NULL AND quantity IS NULL AND price IS NULL "
            "AND settlement_cash_account_id IS NULL AND fees = 0 AND taxes = 0) OR "
            "(transaction_type = 'fx_conversion' AND gross_amount > 0) OR "
            "(transaction_type IN ('fee', 'tax') AND quantity IS NULL AND price IS NULL "
            "AND fees = 0 AND taxes = 0) OR "
            "(transaction_type IN ('transfer_in', 'transfer_out')) OR "
            "(transaction_type = 'opening_balance' AND settlement_cash_account_id IS NULL "
            "AND fees = 0 AND taxes = 0 AND ((instrument_id IS NOT NULL "
            "AND quantity IS NOT NULL AND acquisition_date IS NOT NULL) OR "
            "(instrument_id IS NULL AND quantity IS NULL AND price IS NULL "
            "AND acquisition_date IS NULL))))",
            name="ck_transaction_revision_record_transaction_payload_type_shape",
        ),
        sa.CheckConstraint(
            "is_tombstone OR transaction_type NOT IN ('transfer_in', 'transfer_out') OR "
            "((transfer_object_type = 'cash' AND instrument_id IS NULL "
            "AND quantity IS NULL AND price IS NULL AND gross_amount > 0) OR "
            "(transfer_object_type = 'position' AND instrument_id IS NOT NULL "
            "AND quantity IS NOT NULL AND price IS NULL AND gross_amount >= 0))",
            name="ck_transaction_revision_record_transfer_object_shape",
        ),
        sa.CheckConstraint(
            "is_tombstone OR instrument_id IS NULL OR instrument_snapshot_json IS NOT NULL",
            name="ck_transaction_revision_record_instrument_snapshot_required",
        ),
        sa.CheckConstraint(
            "is_tombstone OR instrument_id IS NOT NULL OR instrument_snapshot_json IS NULL",
            name="ck_transaction_revision_record_snapshot_without_instrument",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id", "transaction_id"],
            [
                "transaction_identity_record.portfolio_id",
                "transaction_identity_record.transaction_id",
            ],
            name="fk_transaction_revision_identity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id", "revision_group_id"],
            [
                "transaction_revision_group_record.portfolio_id",
                "transaction_revision_group_record.revision_group_id",
            ],
            name="fk_transaction_revision_group",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "portfolio_id",
                "transaction_id",
                "supersedes_revision_number",
                "supersedes_revision_id",
            ],
            [
                "transaction_revision_record.portfolio_id",
                "transaction_revision_record.transaction_id",
                "transaction_revision_record.revision_number",
                "transaction_revision_record.revision_id",
            ],
            name="fk_transaction_revision_predecessor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id", "account_id"],
            ["account_record.portfolio_id", "account_record.account_id"],
            name="fk_transaction_revision_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id", "settlement_cash_account_id"],
            ["account_record.portfolio_id", "account_record.account_id"],
            name="fk_transaction_revision_settlement_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id", "counterparty_account_id"],
            ["account_record.portfolio_id", "account_record.account_id"],
            name="fk_transaction_revision_counterparty_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("revision_id", name="pk_transaction_revision_record"),
        sa.UniqueConstraint(
            "portfolio_id",
            "transaction_id",
            "revision_number",
            name="uq_transaction_revision_portfolio_transaction_number",
        ),
        sa.UniqueConstraint(
            "portfolio_id",
            "transaction_id",
            "revision_number",
            "revision_id",
            name="uq_transaction_revision_chain_target",
        ),
        sa.UniqueConstraint(
            "revision_group_id",
            "transaction_id",
            name="uq_transaction_revision_group_transaction",
        ),
    )

    partial_predicates = {
        "postgresql_where": sa.text("is_tombstone IS FALSE"),
        "sqlite_where": sa.text("is_tombstone = 0"),
    }
    for index_name, columns in (
        (
            "ix_transaction_revision_portfolio_trade",
            ["portfolio_id", "trade_date", "trade_at", "transaction_id"],
        ),
        (
            "ix_transaction_revision_portfolio_account_trade",
            ["portfolio_id", "account_id", "trade_date", "trade_at"],
        ),
        (
            "ix_transaction_revision_portfolio_type_trade",
            ["portfolio_id", "transaction_type", "trade_date", "trade_at"],
        ),
        (
            "ix_transaction_revision_portfolio_instrument_trade",
            ["portfolio_id", "instrument_id", "trade_date", "trade_at"],
        ),
        (
            "ix_transaction_revision_portfolio_counterparty_trade",
            ["portfolio_id", "counterparty_account_id", "trade_date", "trade_at"],
        ),
    ):
        op.create_index(
            index_name,
            "transaction_revision_record",
            columns,
            unique=False,
            **partial_predicates,
        )

    if connection.dialect.name == "postgresql":
        with op.batch_alter_table("transaction_revision_record") as batch_op:
            batch_op.create_foreign_key(
                "fk_transaction_revision_record_instrument_id_instrument",
                "instrument",
                ["instrument_id"],
                ["instrument_id"],
                ondelete="RESTRICT",
                referent_schema="instrument_registry",
            )
        op.create_check_constraint(
            "ck_transaction_revision_record_payload_hash_hex",
            "transaction_revision_record",
            "payload_hash ~ '^sha256:[0-9a-f]{64}$'",
        )
        op.create_check_constraint(
            "ck_transaction_revision_record_instrument_snapshot_object",
            "transaction_revision_record",
            "instrument_snapshot_json IS NULL OR "
            "json_typeof(instrument_snapshot_json) = 'object'",
        )
        op.create_check_constraint(
            "ck_transaction_revision_record_trade_moment_consistency",
            "transaction_revision_record",
            "is_tombstone OR ("
            "trade_date = (trade_at AT TIME ZONE trade_timezone)::date AND "
            "trade_time = (trade_at AT TIME ZONE trade_timezone)::time)",
        )


def _deterministic_id(kind: str, value: str) -> str:
    return uuid5(
        NAMESPACE_URL,
        f"portfolio-operations-workbench:{revision}:{kind}:{value}",
    ).hex


def _backfill_baseline(
    connection: sa.Connection,
    prepared: list[dict[str, object]],
    *,
    recorded_at: datetime,
) -> dict[str, str]:
    identity = sa.Table(
        "transaction_identity_record",
        sa.MetaData(),
        autoload_with=connection,
    )
    group = sa.Table(
        "transaction_revision_group_record",
        sa.MetaData(),
        autoload_with=connection,
    )
    revision_table = sa.Table(
        "transaction_revision_record",
        sa.MetaData(),
        autoload_with=connection,
    )

    portfolio_ids = sorted({str(item["portfolio_id"]) for item in prepared})
    group_ids = {
        portfolio_id: _deterministic_id("baseline-group", portfolio_id)
        for portfolio_id in portfolio_ids
    }
    group_rows: list[dict[str, object]] = []
    for portfolio_id in portfolio_ids:
        portfolio_items = [
            item for item in prepared if item["portfolio_id"] == portfolio_id
        ]
        entitlement_count = sum(
            "entitlement_date_default" in item["normalizations"]
            for item in portfolio_items
        )
        acquisition_count = sum(
            "acquisition_date_default" in item["normalizations"]
            for item in portfolio_items
        )
        normalization_summary = {
            "legacy_transaction_count": len(portfolio_items),
            "entitlement_date_default_to_null": entitlement_count,
            "entitlement_date_default_transaction_ids": sorted(
                str(item["transaction_id"])
                for item in portfolio_items
                if "entitlement_date_default" in item["normalizations"]
            ),
            "acquisition_date_default_to_null": acquisition_count,
            "acquisition_date_default_transaction_ids": sorted(
                str(item["transaction_id"])
                for item in portfolio_items
                if "acquisition_date_default" in item["normalizations"]
            ),
            "decimal_rounding": {
                marker: sum(
                    marker in item["normalizations"]
                    for item in portfolio_items
                )
                for marker in sorted(
                    {
                        marker
                        for item in portfolio_items
                        for marker in item["normalizations"]
                        if "_rounded_to_scale_" in marker
                    }
                )
            },
            "decimal_rounding_transaction_ids": {
                marker: sorted(
                    str(item["transaction_id"])
                    for item in portfolio_items
                    if marker in item["normalizations"]
                )
                for marker in sorted(
                    {
                        marker
                        for item in portfolio_items
                        for marker in item["normalizations"]
                        if "_rounded_to_scale_" in marker
                    }
                )
            },
        }
        group_rows.append(
            {
                "revision_group_id": group_ids[portfolio_id],
                "portfolio_id": portfolio_id,
                "source_kind": "migration",
                "change_reason": (
                    "Legacy current transaction state captured as immutable baseline; "
                    f"normalizations={json.dumps(normalization_summary, sort_keys=True, separators=(',', ':'))}."
                ),
                "actor_type": "migration",
                "actor_id": _ACTOR_ID,
                "actor_display_name": "Portfolio ledger migration 0036",
                "actor_source": "alembic",
                "recorded_at": recorded_at,
                "request_id": f"migration:{revision}",
                "idempotency_key": f"migration:{revision}:baseline:{portfolio_id}",
                "source_ref": json.dumps(
                    normalization_summary,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
    if group_rows:
        connection.execute(sa.insert(group), group_rows)

    identity_rows = [
        {
            "transaction_id": item["transaction_id"],
            "portfolio_id": item["portfolio_id"],
            "created_at": item["created_at"],
            "created_by": _ACTOR_ID,
        }
        for item in prepared
    ]
    if identity_rows:
        connection.execute(sa.insert(identity), identity_rows)

    revision_rows: list[dict[str, object]] = []
    for item in prepared:
        transaction_id = str(item["transaction_id"])
        payload = dict(item["payload"])
        revision_rows.append(
            {
                "revision_id": _deterministic_id("baseline-revision", transaction_id),
                "portfolio_id": item["portfolio_id"],
                "transaction_id": transaction_id,
                "revision_number": 1,
                "revision_group_id": group_ids[str(item["portfolio_id"])],
                "revision_kind": "baseline",
                "is_tombstone": False,
                "supersedes_revision_id": None,
                "supersedes_revision_number": None,
                "payload_schema_version": _PAYLOAD_SCHEMA_VERSION,
                "payload_hash": item["payload_hash"],
                **payload,
            }
        )
    # SQLAlchemy's generic JSON type encodes Python None as JSON ``null`` by
    # default.  The ledger invariant requires SQL NULL when no instrument
    # snapshot exists, so insert each baseline with an explicit SQL null.
    for revision_row in revision_rows:
        if revision_row["instrument_snapshot_json"] is None:
            revision_row["instrument_snapshot_json"] = sa.null()
        connection.execute(sa.insert(revision_table).values(**revision_row))
    return group_ids


def _verify_baseline_backfill(
    connection: sa.Connection,
    prepared: list[dict[str, object]],
) -> None:
    expected_count = len(prepared)
    for table_name in (
        "transaction_identity_record",
        "transaction_revision_record",
    ):
        actual_count = int(
            connection.scalar(sa.text(f"SELECT count(*) FROM {table_name}")) or 0
        )
        if actual_count != expected_count:
            raise RuntimeError(
                f"0036 baseline count mismatch for {table_name}: "
                f"expected {expected_count}, found {actual_count}."
            )
    expected_group_count = len({item["portfolio_id"] for item in prepared})
    actual_group_count = int(
        connection.scalar(
            sa.text("SELECT count(*) FROM transaction_revision_group_record")
        )
        or 0
    )
    if actual_group_count != expected_group_count:
        raise RuntimeError(
            "0036 baseline revision-group count mismatch: "
            f"expected {expected_group_count}, found {actual_group_count}."
        )

    revision_table = sa.Table(
        "transaction_revision_record",
        sa.MetaData(),
        autoload_with=connection,
    )
    actual_rows = {
        str(row["transaction_id"]): row
        for row in connection.execute(sa.select(revision_table)).mappings()
    }
    for item in prepared:
        transaction_id = str(item["transaction_id"])
        row = actual_rows.get(transaction_id)
        if row is None:
            raise RuntimeError(f"0036 baseline revision missing for {transaction_id}.")
        if (
            row["revision_number"] != 1
            or row["revision_kind"] != "baseline"
            or bool(row["is_tombstone"])
            or row["supersedes_revision_id"] is not None
            or row["supersedes_revision_number"] is not None
        ):
            raise RuntimeError(f"0036 invalid baseline chain metadata for {transaction_id}.")
        persisted_payload = {
            field_name: row[field_name]
            for field_name in _OPTIONAL_FACT_FIELDS
        }
        persisted_hash = _payload_hash(persisted_payload)
        if persisted_hash != item["payload_hash"] or row["payload_hash"] != persisted_hash:
            raise RuntimeError(
                f"0036 baseline payload hash mismatch for {transaction_id}."
            )

    orphan_count = int(
        connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM transaction_identity_record i
                LEFT JOIN transaction_revision_record r
                  ON r.portfolio_id = i.portfolio_id
                 AND r.transaction_id = i.transaction_id
                WHERE r.revision_id IS NULL
                """
            )
        )
        or 0
    )
    if orphan_count:
        raise RuntimeError(f"0036 created {orphan_count} transaction identities without revisions.")


def _create_current_view(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            CREATE VIEW transaction_current AS
            SELECT
                i.transaction_id,
                i.portfolio_id,
                r.revision_id AS current_revision_id,
                r.revision_number AS current_revision_number,
                r.revision_group_id,
                r.revision_kind,
                r.payload_schema_version,
                r.payload_hash,
                i.created_at,
                i.created_by,
                g.source_kind,
                g.change_reason,
                g.actor_type,
                g.actor_id,
                g.actor_display_name,
                g.actor_source,
                g.recorded_at,
                r.transaction_type,
                r.trade_date,
                r.trade_time,
                r.trade_at,
                r.trade_timezone,
                r.trade_time_is_estimated,
                r.settlement_date,
                r.entitlement_date,
                r.acquisition_date,
                r.account_id,
                r.settlement_cash_account_id,
                r.instrument_id,
                r.instrument_snapshot_json,
                r.quantity,
                r.price,
                r.gross_amount,
                r.counter_amount,
                r.fx_rate,
                r.fees,
                r.taxes,
                r.currency,
                r.transfer_scope,
                r.transfer_object_type,
                r.transfer_group_id,
                r.counterparty_account_id,
                r.note
            FROM transaction_identity_record i
            JOIN transaction_revision_record r
              ON r.portfolio_id = i.portfolio_id
             AND r.transaction_id = i.transaction_id
            JOIN transaction_revision_group_record g
              ON g.portfolio_id = r.portfolio_id
             AND g.revision_group_id = r.revision_group_id
            WHERE NOT r.is_tombstone
              AND NOT EXISTS (
                    SELECT 1
                    FROM transaction_revision_record newer
                    WHERE newer.portfolio_id = r.portfolio_id
                      AND newer.transaction_id = r.transaction_id
                      AND newer.revision_number > r.revision_number
              )
            """
        )
    )


def _tombstone_payload_hash() -> str:
    encoded = json.dumps(
        {
            "payload_schema_version": _PAYLOAD_SCHEMA_VERSION,
            "is_tombstone": True,
            "facts": None,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _install_postgresql_triggers(connection: sa.Connection) -> None:
    schema = op.get_context().opts.get("version_table_schema")
    schema_prefix = f'"{schema}".' if schema else ""
    immutable_function = f"{schema_prefix}reject_transaction_ledger_mutation"
    transition_function = f"{schema_prefix}validate_transaction_revision_insert"
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {immutable_function}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            BEGIN
                RAISE EXCEPTION '% is append-only; % is forbidden',
                    TG_TABLE_NAME, TG_OP
                    USING ERRCODE = '55000';
            END;
            $function$
            """
        )
    )
    connection.execute(
        sa.text(
            f"""
            CREATE FUNCTION {transition_function}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $function$
            DECLARE
                predecessor_is_tombstone boolean;
                predecessor_payload_hash text;
                latest_revision_number integer;
            BEGIN
                PERFORM 1
                FROM {schema_prefix}transaction_identity_record
                WHERE portfolio_id = NEW.portfolio_id
                  AND transaction_id = NEW.transaction_id
                FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'transaction identity does not exist';
                END IF;

                SELECT max(revision_number)
                  INTO latest_revision_number
                FROM {schema_prefix}transaction_revision_record
                WHERE portfolio_id = NEW.portfolio_id
                  AND transaction_id = NEW.transaction_id;
                IF NEW.revision_number <> coalesce(latest_revision_number, 0) + 1 THEN
                    RAISE EXCEPTION 'revision_number must append exactly after the current revision';
                END IF;

                IF NEW.revision_number > 1 THEN
                    SELECT is_tombstone, payload_hash
                      INTO predecessor_is_tombstone, predecessor_payload_hash
                    FROM {schema_prefix}transaction_revision_record
                    WHERE portfolio_id = NEW.portfolio_id
                      AND transaction_id = NEW.transaction_id
                      AND revision_number = NEW.supersedes_revision_number
                      AND revision_id = NEW.supersedes_revision_id;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'declared predecessor revision does not exist';
                    END IF;
                    IF predecessor_is_tombstone THEN
                        RAISE EXCEPTION 'a deleted transaction cannot be restored or revised';
                    END IF;
                    IF NEW.revision_kind = 'amend'
                       AND NEW.payload_hash = predecessor_payload_hash THEN
                        RAISE EXCEPTION 'an amendment must change the canonical payload';
                    END IF;
                END IF;
                IF NEW.revision_kind = 'delete'
                   AND NEW.payload_hash <> '{_tombstone_payload_hash()}' THEN
                    RAISE EXCEPTION 'delete revision uses a non-canonical tombstone hash';
                END IF;
                RETURN NEW;
            END;
            $function$
            """
        )
    )
    for table_name in (
        "transaction_identity_record",
        "transaction_revision_group_record",
        "transaction_revision_record",
    ):
        connection.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table_name}_append_only
                BEFORE UPDATE OR DELETE ON {schema_prefix}{table_name}
                FOR EACH ROW EXECUTE FUNCTION {immutable_function}()
                """
            )
        )
        connection.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_{table_name}_truncate_forbidden
                BEFORE TRUNCATE ON {schema_prefix}{table_name}
                FOR EACH STATEMENT EXECUTE FUNCTION {immutable_function}()
                """
            )
        )
    connection.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_transaction_revision_record_transition
            BEFORE INSERT ON {schema_prefix}transaction_revision_record
            FOR EACH ROW EXECUTE FUNCTION {transition_function}()
            """
        )
    )


def _install_sqlite_triggers(connection: sa.Connection) -> None:
    for table_name in (
        "transaction_identity_record",
        "transaction_revision_group_record",
        "transaction_revision_record",
    ):
        for operation in ("UPDATE", "DELETE"):
            connection.execute(
                sa.text(
                    f"""
                    CREATE TRIGGER trg_{table_name}_{operation.lower()}_forbidden
                    BEFORE {operation} ON {table_name}
                    BEGIN
                        SELECT RAISE(ABORT, '{table_name} is append-only');
                    END
                    """
                )
            )
    connection.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_transaction_revision_record_transition
            BEFORE INSERT ON transaction_revision_record
            BEGIN
                SELECT CASE
                    WHEN NEW.revision_number <> coalesce((
                        SELECT max(revision_number)
                        FROM transaction_revision_record
                        WHERE portfolio_id = NEW.portfolio_id
                          AND transaction_id = NEW.transaction_id
                    ), 0) + 1
                    THEN RAISE(ABORT, 'revision_number must append exactly after the current revision')
                END;
                SELECT CASE
                    WHEN NEW.revision_number > 1 AND NOT EXISTS (
                        SELECT 1
                        FROM transaction_revision_record predecessor
                        WHERE predecessor.portfolio_id = NEW.portfolio_id
                          AND predecessor.transaction_id = NEW.transaction_id
                          AND predecessor.revision_number = NEW.supersedes_revision_number
                          AND predecessor.revision_id = NEW.supersedes_revision_id
                    )
                    THEN RAISE(ABORT, 'declared predecessor revision does not exist')
                END;
                SELECT CASE
                    WHEN NEW.revision_number > 1 AND EXISTS (
                        SELECT 1
                        FROM transaction_revision_record predecessor
                        WHERE predecessor.portfolio_id = NEW.portfolio_id
                          AND predecessor.transaction_id = NEW.transaction_id
                          AND predecessor.revision_number = NEW.supersedes_revision_number
                          AND predecessor.revision_id = NEW.supersedes_revision_id
                          AND predecessor.is_tombstone = 1
                    )
                    THEN RAISE(ABORT, 'a deleted transaction cannot be restored or revised')
                END;
                SELECT CASE
                    WHEN NEW.revision_kind = 'amend' AND EXISTS (
                        SELECT 1
                        FROM transaction_revision_record predecessor
                        WHERE predecessor.portfolio_id = NEW.portfolio_id
                          AND predecessor.transaction_id = NEW.transaction_id
                          AND predecessor.revision_number = NEW.supersedes_revision_number
                          AND predecessor.revision_id = NEW.supersedes_revision_id
                          AND predecessor.payload_hash = NEW.payload_hash
                    )
                    THEN RAISE(ABORT, 'an amendment must change the canonical payload')
                END;
                SELECT CASE
                    WHEN NEW.revision_kind = 'delete'
                     AND NEW.payload_hash <> '{_tombstone_payload_hash()}'
                    THEN RAISE(ABORT, 'delete revision uses a non-canonical tombstone hash')
                END;
            END
            """
        )
    )


def _mark_calculation_states_stale(
    connection: sa.Connection,
    prepared: list[dict[str, object]],
) -> None:
    earliest_by_portfolio: dict[str, date] = {}
    for item in prepared:
        portfolio_id = str(item["portfolio_id"])
        trade_date = item["payload"]["trade_date"]
        current = earliest_by_portfolio.get(portfolio_id)
        if current is None or trade_date < current:
            earliest_by_portfolio[portfolio_id] = trade_date

    state = sa.table(
        "portfolio_calculation_state",
        sa.column("portfolio_id", sa.String()),
        sa.column("daily_snapshot_status", sa.String()),
        sa.column("dirty_from", sa.Date()),
        sa.column("refresh_request_id", sa.String()),
        sa.column("refresh_started_at", sa.String()),
        sa.column("refresh_completed_at", sa.String()),
        sa.column("error_message", sa.String()),
    )
    rows = connection.execute(
        sa.select(state.c.portfolio_id, state.c.dirty_from)
    ).mappings()
    for row in rows:
        portfolio_id = str(row["portfolio_id"])
        existing_dirty_from = _date_value(
            row["dirty_from"],
            field="dirty_from",
            transaction_id=f"calculation-state:{portfolio_id}",
        )
        earliest_trade_date = earliest_by_portfolio.get(portfolio_id)
        candidates = [
            candidate
            for candidate in (existing_dirty_from, earliest_trade_date)
            if candidate is not None
        ]
        dirty_from = min(candidates) if candidates else None
        connection.execute(
            state.update()
            .where(state.c.portfolio_id == portfolio_id)
            .values(
                daily_snapshot_status="stale",
                dirty_from=dirty_from,
                refresh_request_id=f"ledger-contract:{revision}",
                refresh_started_at=None,
                refresh_completed_at=None,
                error_message=None,
            )
        )


def _postflight(
    connection: sa.Connection,
    prepared: list[dict[str, object]],
) -> None:
    inspector = sa.inspect(connection)
    expected_tables = {
        "transaction_identity_record",
        "transaction_revision_group_record",
        "transaction_revision_record",
    }
    missing_tables = sorted(expected_tables - set(inspector.get_table_names()))
    if missing_tables or "transaction_current" not in inspector.get_view_names():
        raise RuntimeError(
            f"0036 postflight is missing ledger objects: tables={missing_tables}."
        )
    if inspector.has_table(_LEGACY_TABLE) or inspector.has_table(_LEGACY_RENAMED_TABLE):
        raise RuntimeError("0036 postflight found a legacy transaction table.")

    expected_count = len(prepared)
    current_count = int(
        connection.scalar(sa.text("SELECT count(*) FROM transaction_current")) or 0
    )
    if current_count != expected_count:
        raise RuntimeError(
            f"0036 current-view count mismatch: expected {expected_count}, found {current_count}."
        )
    invalid_chain_count = int(
        connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM transaction_revision_record
                WHERE revision_number <> 1
                   OR revision_kind <> 'baseline'
                   OR is_tombstone
                   OR supersedes_revision_id IS NOT NULL
                   OR supersedes_revision_number IS NOT NULL
                """
            )
        )
        or 0
    )
    if invalid_chain_count:
        raise RuntimeError(f"0036 postflight found {invalid_chain_count} invalid baseline revisions.")
    non_stale_count = int(
        connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM portfolio_calculation_state
                WHERE daily_snapshot_status <> 'stale'
                """
            )
        )
        or 0
    )
    if non_stale_count:
        raise RuntimeError(f"0036 failed to stale {non_stale_count} calculation states.")


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            f"0036 append-only enforcement is not implemented for {connection.dialect.name}."
        )
    _lock_legacy_ledger_against_postgresql_dml(connection)
    prepared = _preflight_legacy_rows(connection)
    recorded_at = _utc_now()

    _ensure_account_composite_key(connection)
    op.rename_table(_LEGACY_TABLE, _LEGACY_RENAMED_TABLE)
    _create_ledger_tables(connection)
    _backfill_baseline(connection, prepared, recorded_at=recorded_at)
    _verify_baseline_backfill(connection, prepared)

    op.drop_table(_LEGACY_RENAMED_TABLE)
    _create_current_view(connection)
    if connection.dialect.name == "postgresql":
        _install_postgresql_triggers(connection)
    else:
        _install_sqlite_triggers(connection)
    _mark_calculation_states_stale(connection, prepared)
    _postflight(connection, prepared)


def downgrade() -> None:
    raise RuntimeError(
        "20260713_0036 is intentionally irreversible: append-only transaction "
        "revisions and tombstones cannot be collapsed back into transaction_record. "
        "Restore the verified pre-migration database backup instead."
    )
