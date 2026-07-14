"""Converge deployed transaction ledgers to the exact revision contract.

Revision ID: 20260714_0043
Revises: 20260714_0042

The source for 0036/0037 was corrected after an early local deployment had
already stamped those revisions.  That database therefore had the old
37-column ledger even though Alembic reported 0038.  This forward-only repair
recognizes only that complete legacy shape or the complete current shape.  A
mixed shape fails closed instead of guessing which facts are authoritative.

The PostgreSQL repair holds ACCESS EXCLUSIVE for its whole transaction.  It
also refuses to rewrite ledger hashes once any Portfolio Daily transaction
input has been captured: those rows are immutable evidence derived from the
old hashes and must never be silently detached from their source.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

from alembic import op
import sqlalchemy as sa


revision = "20260714_0043"
down_revision = "20260714_0042"
branch_labels = None
depends_on = None


_TABLE = "transaction_revision_record"
_VIEW = "transaction_current"
_LOCK_TIMEOUT = "5s"
_HARDENING_SOURCE_SHA256 = (
    "f5b40da0a6ae696d258a7b4230172fec581cd35bbf74a4c75e4f3066a1b54770"
)
_CHECK_CONTRACT_SHA256 = (
    "66bb27d4c502cb6150f7a71c2a3780f9c427ecd2f338ad8aaa5393626deaa3b1"
)
_FUNCTION_CONTRACT_SHA256 = (
    "e828f0f602f9c799ce41a8332d240d0137b859a9c8803b59761194b7bece67d1"
)
_TRIGGER_CONTRACT_SHA256 = (
    "483feafbbda03d09f2556cea8107dadc475489c5e47e433638e362dafc401349"
)
_NUMERIC_SCALES = {
    "quantity": 12,
    "price": 12,
    "gross_amount": 8,
    "counter_amount": 8,
    "quoted_fx_rate": 18,
    "fees": 8,
    "taxes": 8,
}

_LEGACY_COLUMNS = (
    "revision_id", "portfolio_id", "transaction_id", "revision_number",
    "revision_group_id", "revision_kind", "is_tombstone",
    "supersedes_revision_id", "supersedes_revision_number",
    "payload_schema_version", "payload_hash", "transaction_type",
    "trade_date", "trade_time", "trade_at", "trade_timezone",
    "trade_time_is_estimated", "settlement_date", "entitlement_date",
    "acquisition_date", "account_id", "settlement_cash_account_id",
    "instrument_id", "instrument_snapshot_json", "quantity", "price",
    "gross_amount", "counter_amount", "fx_rate", "fees", "taxes",
    "currency", "transfer_scope", "transfer_object_type",
    "transfer_group_id", "counterparty_account_id", "note",
)

_CURRENT_COLUMNS = (
    "revision_id", "portfolio_id", "transaction_id", "revision_number",
    "revision_group_id", "revision_kind", "is_tombstone",
    "supersedes_revision_id", "supersedes_revision_number",
    "payload_schema_version", "payload_hash", "transaction_type",
    "trade_date", "trade_time", "trade_at", "trade_timezone",
    "trade_time_is_estimated", "settlement_date", "entitlement_date",
    "acquisition_date", "account_id", "settlement_cash_account_id",
    "instrument_id", "instrument_snapshot_json", "quantity", "price",
    "gross_amount", "counter_amount", "quoted_fx_rate", "fees", "taxes",
    "consideration_basis", "numeric_scale_state", "quantity_input_scale",
    "price_input_scale", "gross_amount_input_scale",
    "counter_amount_input_scale", "quoted_fx_rate_input_scale",
    "fees_input_scale", "taxes_input_scale", "currency", "transfer_scope",
    "transfer_object_type", "transfer_group_id", "counterparty_account_id",
    "note",
)

_CURRENT_VIEW_COLUMNS = (
    "transaction_id", "portfolio_id", "current_revision_id",
    "current_revision_number", "revision_group_id", "revision_kind",
    "payload_schema_version", "payload_hash", "created_at", "created_by",
    "source_kind", "change_reason", "actor_type", "actor_id",
    "actor_display_name", "actor_source", "recorded_at", "transaction_type",
    "trade_date", "trade_time", "trade_at", "trade_timezone",
    "trade_time_is_estimated", "settlement_date", "entitlement_date",
    "acquisition_date", "account_id", "settlement_cash_account_id",
    "instrument_id", "instrument_snapshot_json", "quantity", "price",
    "gross_amount", "counter_amount", "quoted_fx_rate", "fees", "taxes",
    "consideration_basis", "numeric_scale_state", "quantity_input_scale",
    "price_input_scale", "gross_amount_input_scale",
    "counter_amount_input_scale", "quoted_fx_rate_input_scale",
    "fees_input_scale", "taxes_input_scale", "currency", "transfer_scope",
    "transfer_object_type", "transfer_group_id", "counterparty_account_id",
    "note",
)

_EXPECTED_REVISION_TRIGGERS = {
    "trg_40_pd_trg_transaction_revision_insert",
    "trg_transaction_revision_record_append_only",
    "trg_transaction_revision_record_payload_v1",
    "trg_transaction_revision_record_transfer_v1",
    "trg_transaction_revision_record_transition",
    "trg_transaction_revision_record_truncate_forbidden",
}

# Kept byte-for-byte equivalent to the constraints created by the corrected
# 0036.  During the legacy repair every old check is dropped by catalog
# identity before these canonical names are installed, so hashed names from
# the deployed schema cannot survive unnoticed.
_CHECK_CONTRACTS: tuple[tuple[str, str], ...] = (
    (
        "ck_transaction_revision_record_identifiers_nonblank",
        "length(trim(revision_id)) > 0 AND length(trim(transaction_id)) > 0 "
        "AND length(trim(revision_group_id)) > 0",
    ),
    ("ck_transaction_revision_record_positive_revision_number", "revision_number > 0"),
    (
        "ck_transaction_revision_record_revision_kind",
        "revision_kind IN ('baseline', 'create', 'amend', 'delete')",
    ),
    (
        "ck_transaction_revision_record_tombstone_kind",
        "is_tombstone = (revision_kind = 'delete')",
    ),
    (
        "ck_transaction_revision_record_revision_chain_shape",
        "((revision_number = 1 AND revision_kind IN ('baseline', 'create') "
        "AND supersedes_revision_id IS NULL AND supersedes_revision_number IS NULL) "
        "OR (revision_number > 1 AND revision_kind IN ('amend', 'delete') "
        "AND supersedes_revision_id IS NOT NULL "
        "AND supersedes_revision_number = revision_number - 1))",
    ),
    (
        "ck_transaction_revision_record_payload_hash_shape",
        "length(payload_hash) = 71 AND payload_hash LIKE 'sha256:%'",
    ),
    (
        "ck_transaction_revision_record_payload_schema_version",
        "payload_schema_version = 'transaction-revision.v1'",
    ),
    (
        "ck_transaction_revision_record_tombstone_payload_hash",
        "NOT is_tombstone OR payload_hash = "
        "'sha256:bc5787018813b8e0562f74da4098be5dc135994b08a078955af74b9d11b7ef91'",
    ),
    (
        "ck_transaction_revision_record_tombstone_payload",
        "((is_tombstone AND transaction_type IS NULL AND trade_date IS NULL "
        "AND trade_time IS NULL AND trade_at IS NULL AND trade_timezone IS NULL "
        "AND trade_time_is_estimated IS NULL AND settlement_date IS NULL "
        "AND entitlement_date IS NULL AND acquisition_date IS NULL "
        "AND account_id IS NULL AND settlement_cash_account_id IS NULL "
        "AND instrument_id IS NULL AND instrument_snapshot_json IS NULL "
        "AND quantity IS NULL AND price IS NULL AND gross_amount IS NULL "
        "AND counter_amount IS NULL AND quoted_fx_rate IS NULL AND fees IS NULL "
        "AND taxes IS NULL AND consideration_basis IS NULL "
        "AND numeric_scale_state IS NULL AND quantity_input_scale IS NULL "
        "AND price_input_scale IS NULL AND gross_amount_input_scale IS NULL "
        "AND counter_amount_input_scale IS NULL "
        "AND quoted_fx_rate_input_scale IS NULL "
        "AND fees_input_scale IS NULL AND taxes_input_scale IS NULL "
        "AND currency IS NULL AND transfer_scope IS NULL "
        "AND transfer_object_type IS NULL AND transfer_group_id IS NULL "
        "AND counterparty_account_id IS NULL AND note IS NULL) OR "
        "(NOT is_tombstone AND transaction_type IS NOT NULL "
        "AND trade_date IS NOT NULL AND trade_time IS NOT NULL "
        "AND trade_at IS NOT NULL AND length(trim(trade_timezone)) > 0 "
        "AND trade_time_is_estimated IS NOT NULL AND settlement_date IS NOT NULL "
        "AND account_id IS NOT NULL AND gross_amount IS NOT NULL "
        "AND fees IS NOT NULL AND taxes IS NOT NULL "
        "AND numeric_scale_state IS NOT NULL AND currency IS NOT NULL))",
    ),
    (
        "ck_transaction_revision_record_transaction_type",
        "transaction_type IS NULL OR transaction_type IN "
        "('buy', 'sell', 'dividend', 'dividend_reinvestment', 'coupon', "
        "'interest', 'return_of_capital', 'maturity_redemption', 'fee', "
        "'tax', 'deposit', 'withdrawal', 'fx_conversion', 'transfer_in', "
        "'transfer_out', 'opening_balance')",
    ),
    (
        "ck_transaction_revision_record_settlement_not_before_trade",
        "is_tombstone OR settlement_date >= trade_date",
    ),
    (
        "ck_transaction_revision_record_entitlement_not_after_trade",
        "is_tombstone OR entitlement_date IS NULL OR entitlement_date <= trade_date",
    ),
    (
        "ck_transaction_revision_record_acquisition_not_after_trade",
        "is_tombstone OR acquisition_date IS NULL OR acquisition_date <= trade_date",
    ),
    (
        "ck_transaction_revision_record_supported_currency",
        "is_tombstone OR currency IN ('USD', 'HKD', 'CNY')",
    ),
    ("ck_transaction_revision_record_positive_quantity", "quantity IS NULL OR quantity > 0"),
    ("ck_transaction_revision_record_positive_price", "price IS NULL OR price > 0"),
    (
        "ck_transaction_revision_record_nonnegative_gross_amount",
        "gross_amount IS NULL OR gross_amount >= 0",
    ),
    (
        "ck_transaction_revision_record_positive_counter_amount",
        "counter_amount IS NULL OR counter_amount > 0",
    ),
    (
        "ck_transaction_revision_record_positive_quoted_fx_rate",
        "quoted_fx_rate IS NULL OR quoted_fx_rate > 0",
    ),
    ("ck_transaction_revision_record_nonnegative_fees", "fees IS NULL OR fees >= 0"),
    ("ck_transaction_revision_record_nonnegative_taxes", "taxes IS NULL OR taxes >= 0"),
    (
        "ck_transaction_revision_record_numeric_scale_state",
        "numeric_scale_state IS NULL OR numeric_scale_state IN "
        "('declared', 'legacy_inferred')",
    ),
    (
        "ck_transaction_revision_record_consideration_basis",
        "consideration_basis IS NULL OR consideration_basis IN "
        "('exact_quantity_price', 'source_reported')",
    ),
    (
        "ck_transaction_revision_record_numeric_input_scale_pairing",
        "((quantity IS NULL AND quantity_input_scale IS NULL) OR "
        "(quantity IS NOT NULL AND quantity_input_scale IS NOT NULL "
        "AND quantity_input_scale BETWEEN 0 AND 12)) AND "
        "((price IS NULL AND price_input_scale IS NULL) OR "
        "(price IS NOT NULL AND price_input_scale IS NOT NULL "
        "AND price_input_scale BETWEEN 0 AND 12)) AND "
        "((gross_amount IS NULL AND gross_amount_input_scale IS NULL) OR "
        "(gross_amount IS NOT NULL AND gross_amount_input_scale IS NOT NULL "
        "AND gross_amount_input_scale BETWEEN 0 AND 8)) AND "
        "((counter_amount IS NULL AND counter_amount_input_scale IS NULL) OR "
        "(counter_amount IS NOT NULL AND counter_amount_input_scale IS NOT NULL "
        "AND counter_amount_input_scale BETWEEN 0 AND 8)) AND "
        "((quoted_fx_rate IS NULL AND quoted_fx_rate_input_scale IS NULL) OR "
        "(quoted_fx_rate IS NOT NULL AND quoted_fx_rate_input_scale IS NOT NULL "
        "AND quoted_fx_rate_input_scale BETWEEN 0 AND 18)) AND "
        "((fees IS NULL AND fees_input_scale IS NULL) OR "
        "(fees IS NOT NULL AND fees_input_scale IS NOT NULL "
        "AND fees_input_scale BETWEEN 0 AND 8)) AND "
        "((taxes IS NULL AND taxes_input_scale IS NULL) OR "
        "(taxes IS NOT NULL AND taxes_input_scale IS NOT NULL "
        "AND taxes_input_scale BETWEEN 0 AND 8))",
    ),
    (
        "ck_transaction_revision_record_consideration_basis_scope",
        "is_tombstone OR ((transaction_type IN "
        "('buy', 'sell', 'dividend_reinvestment') OR "
        "(transaction_type = 'opening_balance' AND instrument_id IS NOT NULL)) "
        "AND consideration_basis IS NOT NULL AND quantity IS NOT NULL) OR "
        "(transaction_type NOT IN ('buy', 'sell', 'dividend_reinvestment', "
        "'opening_balance') AND consideration_basis IS NULL) OR "
        "(transaction_type = 'opening_balance' AND instrument_id IS NULL "
        "AND consideration_basis IS NULL)",
    ),
    (
        "ck_transaction_revision_record_entitlement_applicability",
        "is_tombstone OR entitlement_date IS NULL OR "
        "transaction_type IN ('dividend', 'coupon', 'fee', 'tax')",
    ),
    (
        "ck_transaction_revision_record_expense_entitlement_instrument",
        "is_tombstone OR entitlement_date IS NULL OR "
        "transaction_type NOT IN ('fee', 'tax') OR instrument_id IS NOT NULL",
    ),
    (
        "ck_transaction_revision_record_acquisition_applicability",
        "is_tombstone OR acquisition_date IS NULL OR "
        "(transaction_type = 'opening_balance' AND instrument_id IS NOT NULL)",
    ),
    (
        "ck_transaction_revision_record_fx_conversion_fields",
        "is_tombstone OR ((transaction_type = 'fx_conversion' "
        "AND instrument_id IS NULL AND instrument_snapshot_json IS NULL "
        "AND quantity IS NULL AND price IS NULL "
        "AND settlement_cash_account_id IS NULL "
        "AND counter_amount IS NOT NULL "
        "AND counterparty_account_id IS NOT NULL AND fees = 0 AND taxes = 0) "
        "OR (transaction_type <> 'fx_conversion' AND counter_amount IS NULL "
        "AND quoted_fx_rate IS NULL))",
    ),
    (
        "ck_transaction_revision_record_transfer_fields",
        "is_tombstone OR ((transaction_type IN ('transfer_in', 'transfer_out') "
        "AND transfer_scope = 'internal_portfolio' "
        "AND transfer_object_type IN ('cash', 'position') "
        "AND settlement_cash_account_id IS NULL "
        "AND transfer_group_id IS NOT NULL AND counterparty_account_id IS NOT NULL "
        "AND fees = 0 AND taxes = 0) OR "
        "(transaction_type NOT IN ('transfer_in', 'transfer_out') "
        "AND transfer_scope IS NULL AND transfer_object_type IS NULL "
        "AND transfer_group_id IS NULL "
        "AND (transaction_type = 'fx_conversion' OR counterparty_account_id IS NULL)))",
    ),
    (
        "ck_transaction_revision_record_transaction_payload_type_shape",
        "is_tombstone OR ("
        "(transaction_type IN ('buy', 'sell') AND instrument_id IS NOT NULL "
        "AND quantity IS NOT NULL AND gross_amount > 0) OR "
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
    ),
    (
        "ck_transaction_revision_record_transfer_object_shape",
        "is_tombstone OR transaction_type NOT IN ('transfer_in', 'transfer_out') OR "
        "((transfer_object_type = 'cash' AND instrument_id IS NULL "
        "AND quantity IS NULL AND price IS NULL AND gross_amount > 0) OR "
        "(transfer_object_type = 'position' AND instrument_id IS NOT NULL "
        "AND quantity IS NOT NULL AND price IS NULL AND gross_amount >= 0))",
    ),
    (
        "ck_transaction_revision_record_instrument_snapshot_required",
        "is_tombstone OR instrument_id IS NULL OR instrument_snapshot_json IS NOT NULL",
    ),
    (
        "ck_transaction_revision_record_snapshot_without_instrument",
        "is_tombstone OR instrument_id IS NOT NULL OR instrument_snapshot_json IS NULL",
    ),
    (
        "ck_transaction_revision_record_payload_hash_hex",
        "payload_hash ~ '^sha256:[0-9a-f]{64}$'",
    ),
    (
        "ck_transaction_revision_record_instrument_snapshot_object",
        "instrument_snapshot_json IS NULL OR "
        "json_typeof(instrument_snapshot_json) = 'object'",
    ),
    (
        "ck_transaction_revision_record_trade_moment_consistency",
        "is_tombstone OR (trade_date = "
        "(trade_at AT TIME ZONE trade_timezone)::date AND "
        "trade_time = (trade_at AT TIME ZONE trade_timezone)::time)",
    ),
    (
        "ck_transaction_revision_record_quantity_price_exact_domain",
        "(quantity IS NULL OR (quantity::text NOT IN "
        "('NaN', 'Infinity', '-Infinity') AND abs(quantity) < 1e26 "
        "AND quantity = trunc(quantity, 12))) AND "
        "(price IS NULL OR (price::text NOT IN "
        "('NaN', 'Infinity', '-Infinity') AND abs(price) < 1e26 "
        "AND price = trunc(price, 12)))",
    ),
    (
        "ck_transaction_revision_record_amount_exact_domain",
        "(gross_amount IS NULL OR (gross_amount::text NOT IN "
        "('NaN', 'Infinity', '-Infinity') AND abs(gross_amount) < 1e30 "
        "AND gross_amount = trunc(gross_amount, 8))) AND "
        "(counter_amount IS NULL OR (counter_amount::text NOT IN "
        "('NaN', 'Infinity', '-Infinity') AND abs(counter_amount) < 1e30 "
        "AND counter_amount = trunc(counter_amount, 8))) AND "
        "(fees IS NULL OR (fees::text NOT IN "
        "('NaN', 'Infinity', '-Infinity') AND abs(fees) < 1e30 "
        "AND fees = trunc(fees, 8))) AND "
        "(taxes IS NULL OR (taxes::text NOT IN "
        "('NaN', 'Infinity', '-Infinity') AND abs(taxes) < 1e30 "
        "AND taxes = trunc(taxes, 8)))",
    ),
    (
        "ck_transaction_revision_record_quoted_fx_rate_exact_domain",
        "(quoted_fx_rate IS NULL OR (quoted_fx_rate::text NOT IN "
        "('NaN', 'Infinity', '-Infinity') AND abs(quoted_fx_rate) < 1e20 "
        "AND quoted_fx_rate = trunc(quoted_fx_rate, 18)))",
    ),
    (
        "ck_transaction_revision_record_numeric_value_fits_input_scale",
        "(quantity IS NULL OR quantity = trunc(quantity, quantity_input_scale)) AND "
        "(price IS NULL OR price = trunc(price, price_input_scale)) AND "
        "(gross_amount IS NULL OR gross_amount = "
        "trunc(gross_amount, gross_amount_input_scale)) AND "
        "(counter_amount IS NULL OR counter_amount = "
        "trunc(counter_amount, counter_amount_input_scale)) AND "
        "(quoted_fx_rate IS NULL OR quoted_fx_rate = "
        "trunc(quoted_fx_rate, quoted_fx_rate_input_scale)) AND "
        "(fees IS NULL OR fees = trunc(fees, fees_input_scale)) AND "
        "(taxes IS NULL OR taxes = trunc(taxes, taxes_input_scale))",
    ),
    (
        "ck_transaction_revision_record_exact_consideration_contract",
        "is_tombstone OR consideration_basis <> 'exact_quantity_price' OR "
        "(price IS NOT NULL AND gross_amount = quantity * price AND "
        "lower(instrument_snapshot_json ->> 'instrument_type') IN "
        "('equity', 'fund', 'etf', 'exchange_traded_fund'))",
    ),
)


def _schema_name() -> str | None:
    value = op.get_context().opts.get("version_table_schema")
    return str(value) if value else None


def _qualified(connection: sa.Connection, object_name: str) -> str:
    preparer = connection.dialect.identifier_preparer
    rendered = preparer.quote(object_name)
    schema = _schema_name()
    return f"{preparer.quote_schema(schema)}.{rendered}" if schema else rendered


def _effective_schema(connection: sa.Connection) -> str:
    return _schema_name() or str(connection.scalar(sa.text("SELECT current_schema()")))


@contextmanager
def _deterministic_deparser_search_path(
    connection: sa.Connection,
) -> Iterator[None]:
    previous = str(
        connection.scalar(sa.text("SELECT current_setting('search_path')"))
    )
    connection.execute(
        sa.text("SELECT set_config('search_path', 'pg_catalog, public', true)")
    )
    try:
        yield
    finally:
        connection.execute(
            sa.text("SELECT set_config('search_path', :search_path, true)"),
            {"search_path": previous},
        )


def _columns(connection: sa.Connection) -> list[dict[str, object]]:
    return sa.inspect(connection).get_columns(_TABLE, schema=_schema_name())


def _shape(columns: list[dict[str, object]]) -> str:
    names = {str(column["name"]) for column in columns}
    if len(columns) == len(_CURRENT_COLUMNS) and names == set(_CURRENT_COLUMNS):
        return "current"
    if len(columns) == len(_LEGACY_COLUMNS) and names == set(_LEGACY_COLUMNS):
        return "legacy"
    legacy_only = sorted(names - set(_CURRENT_COLUMNS))
    missing_current = sorted(set(_CURRENT_COLUMNS) - names)
    raise RuntimeError(
        "0043 found a partial or unknown transaction revision schema; refusing "
        f"to infer a repair (count={len(columns)}, legacy_only={legacy_only}, "
        f"missing_current={missing_current})."
    )


def _assert_numeric_shape(
    columns: list[dict[str, object]],
    *,
    legacy: bool,
) -> None:
    by_name = {str(column["name"]): column for column in columns}
    expected = dict(_NUMERIC_SCALES)
    if legacy:
        expected["fx_rate"] = expected.pop("quoted_fx_rate")
    for name, scale in expected.items():
        column_type = by_name[name]["type"]
        if not isinstance(column_type, sa.Numeric):
            raise RuntimeError(f"0043 expected {name} to be NUMERIC, found {column_type!s}.")
        if legacy:
            if column_type.precision != 38 or column_type.scale != scale:
                raise RuntimeError(
                    f"0043 legacy {name} is not NUMERIC(38,{scale}); refusing repair."
                )
        elif column_type.precision is not None or column_type.scale is not None:
            raise RuntimeError(
                f"0043 current {name} still has a NUMERIC typmod; exact storage "
                "requires unbounded NUMERIC plus CHECK constraints."
            )


def _load_frozen_hardening_helpers() -> ModuleType:
    """Reuse the immutable 0037 SQL contract without importing application code."""

    path = Path(__file__).with_name(
        "20260713_0037_harden_transaction_ledger_database_invariants.py"
    )
    actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_digest != _HARDENING_SOURCE_SHA256:
        raise RuntimeError(
            "0043 frozen 0037 ledger helper digest changed; refusing to run "
            f"unreviewed migration SQL (found {actual_digest})."
        )
    spec = importlib.util.spec_from_file_location(
        "portfolio_migration_20260713_0037_contract", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("0043 could not load the frozen 0037 ledger contract.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lock_ledger(connection: sa.Connection) -> None:
    connection.exec_driver_sql(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    try:
        connection.exec_driver_sql(
            f"LOCK TABLE {_qualified(connection, _TABLE)} IN ACCESS EXCLUSIVE MODE"
        )
    except sa.exc.DBAPIError as error:
        raise RuntimeError(
            "0043 could not acquire its PostgreSQL transaction-ledger cutover "
            f"lock within {_LOCK_TIMEOUT}; migration failed closed."
        ) from error


def _require_no_captured_daily_transactions(connection: sa.Connection) -> None:
    inspector = sa.inspect(connection)
    if not inspector.has_table(
        "portfolio_daily_transaction_input", schema=_schema_name()
    ):
        return
    count = int(
        connection.scalar(
            sa.text(
                f"SELECT count(*) FROM "
                f"{_qualified(connection, 'portfolio_daily_transaction_input')}"
            )
        )
        or 0
    )
    if count:
        raise RuntimeError(
            "0043 cannot rewrite transaction payload hashes after exact Portfolio "
            f"Daily inputs have been captured ({count} rows found). Delete no "
            "evidence; restore/rebuild through an explicit invalidation workflow."
        )


def _drop_legacy_dependents(connection: sa.Connection) -> None:
    table = _qualified(connection, _TABLE)
    schema_prefix = (
        f"{connection.dialect.identifier_preparer.quote_schema(_schema_name())}."
        if _schema_name()
        else ""
    )
    connection.exec_driver_sql(f"DROP VIEW {_qualified(connection, _VIEW)}")
    for trigger_name in (
        "trg_transaction_revision_record_transfer_v1",
        "trg_transaction_revision_record_payload_v1",
        "trg_transaction_revision_record_append_only",
    ):
        connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{trigger_name}" ON {table}')

    for signature in (
        "validate_internal_transfer_revision_deferred_v1()",
        "assert_internal_transfer_group_v1(text, text)",
        "validate_transaction_revision_payload_insert_v1()",
        f"transaction_revision_payload_hash_v1({table})",
        f"canonical_transaction_revision_payload_v1({table})",
        "canonical_transaction_json_v1(json)",
        "transaction_json_number_v1(text)",
    ):
        connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {schema_prefix}{signature}")


def _drop_all_check_constraints(connection: sa.Connection) -> None:
    schema = _effective_schema(connection)
    names = connection.scalars(
        sa.text(
            """
            SELECT constraint_row.conname
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS relation ON relation.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = :schema_name
              AND relation.relname = :table_name
              AND constraint_row.contype = 'c'
            ORDER BY constraint_row.conname
            """
        ),
        {"schema_name": schema, "table_name": _TABLE},
    ).all()
    table = _qualified(connection, _TABLE)
    preparer = connection.dialect.identifier_preparer
    for name in names:
        connection.exec_driver_sql(
            f"ALTER TABLE {table} DROP CONSTRAINT {preparer.quote(str(name))}"
        )


def _alter_legacy_shape(connection: sa.Connection) -> None:
    table = _qualified(connection, _TABLE)
    connection.exec_driver_sql(
        f"ALTER TABLE {table} RENAME COLUMN fx_rate TO quoted_fx_rate"
    )
    for column_name in _NUMERIC_SCALES:
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ALTER COLUMN {column_name} "
            f"TYPE NUMERIC USING {column_name}::numeric"
        )
    connection.exec_driver_sql(
        f"""
        ALTER TABLE {table}
            ADD COLUMN consideration_basis VARCHAR,
            ADD COLUMN numeric_scale_state VARCHAR,
            ADD COLUMN quantity_input_scale INTEGER,
            ADD COLUMN price_input_scale INTEGER,
            ADD COLUMN gross_amount_input_scale INTEGER,
            ADD COLUMN counter_amount_input_scale INTEGER,
            ADD COLUMN quoted_fx_rate_input_scale INTEGER,
            ADD COLUMN fees_input_scale INTEGER,
            ADD COLUMN taxes_input_scale INTEGER
        """
    )
    connection.exec_driver_sql(
        f"""
        UPDATE {table}
        SET consideration_basis = CASE
                WHEN NOT is_tombstone
                 AND transaction_type IN (
                     'buy', 'sell', 'dividend_reinvestment', 'opening_balance'
                 )
                 AND instrument_id IS NOT NULL
                THEN 'source_reported'
                ELSE NULL
            END,
            numeric_scale_state = CASE
                WHEN is_tombstone THEN NULL ELSE 'legacy_inferred'
            END,
            quantity_input_scale = CASE
                WHEN quantity IS NULL THEN NULL ELSE min_scale(quantity)
            END,
            price_input_scale = CASE
                WHEN price IS NULL THEN NULL ELSE min_scale(price)
            END,
            gross_amount_input_scale = CASE
                WHEN gross_amount IS NULL THEN NULL ELSE min_scale(gross_amount)
            END,
            counter_amount_input_scale = CASE
                WHEN counter_amount IS NULL THEN NULL ELSE min_scale(counter_amount)
            END,
            quoted_fx_rate_input_scale = CASE
                WHEN quoted_fx_rate IS NULL THEN NULL ELSE min_scale(quoted_fx_rate)
            END,
            fees_input_scale = CASE
                WHEN fees IS NULL THEN NULL ELSE min_scale(fees)
            END,
            taxes_input_scale = CASE
                WHEN taxes IS NULL THEN NULL ELSE min_scale(taxes)
            END
        """
    )


def _install_check_contract(connection: sa.Connection) -> None:
    if len(_CHECK_CONTRACTS) != 42 or len({name for name, _ in _CHECK_CONTRACTS}) != 42:
        raise RuntimeError("0043 source does not contain the exact 42-check ledger contract.")
    table = _qualified(connection, _TABLE)
    preparer = connection.dialect.identifier_preparer
    for name, expression in _CHECK_CONTRACTS:
        connection.execute(
            sa.text(
                f"ALTER TABLE {table} ADD CONSTRAINT {preparer.quote(name)} "
                f"CHECK ({expression})"
            )
        )


def _create_current_view(connection: sa.Connection) -> None:
    identity = _qualified(connection, "transaction_identity_record")
    revision_table = _qualified(connection, _TABLE)
    revision_group = _qualified(connection, "transaction_revision_group_record")
    view = _qualified(connection, _VIEW)
    connection.exec_driver_sql(
        f"""
        CREATE VIEW {view} AS
        SELECT
            i.transaction_id, i.portfolio_id,
            r.revision_id AS current_revision_id,
            r.revision_number AS current_revision_number,
            r.revision_group_id, r.revision_kind, r.payload_schema_version,
            r.payload_hash, i.created_at, i.created_by, g.source_kind,
            g.change_reason, g.actor_type, g.actor_id, g.actor_display_name,
            g.actor_source, g.recorded_at, r.transaction_type, r.trade_date,
            r.trade_time, r.trade_at, r.trade_timezone,
            r.trade_time_is_estimated, r.settlement_date, r.entitlement_date,
            r.acquisition_date, r.account_id, r.settlement_cash_account_id,
            r.instrument_id, r.instrument_snapshot_json, r.quantity, r.price,
            r.gross_amount, r.counter_amount, r.quoted_fx_rate, r.fees, r.taxes,
            r.consideration_basis, r.numeric_scale_state,
            r.quantity_input_scale, r.price_input_scale,
            r.gross_amount_input_scale, r.counter_amount_input_scale,
            r.quoted_fx_rate_input_scale, r.fees_input_scale,
            r.taxes_input_scale, r.currency, r.transfer_scope,
            r.transfer_object_type, r.transfer_group_id,
            r.counterparty_account_id, r.note
        FROM {identity} AS i
        JOIN {revision_table} AS r
          ON r.portfolio_id = i.portfolio_id
         AND r.transaction_id = i.transaction_id
        JOIN {revision_group} AS g
          ON g.portfolio_id = r.portfolio_id
         AND g.revision_group_id = r.revision_group_id
        WHERE NOT r.is_tombstone
          AND NOT EXISTS (
              SELECT 1 FROM {revision_table} AS newer
              WHERE newer.portfolio_id = r.portfolio_id
                AND newer.transaction_id = r.transaction_id
                AND newer.revision_number > r.revision_number
          )
        """
    )


def _recreate_revision_append_only_guard(connection: sa.Connection) -> None:
    table = _qualified(connection, _TABLE)
    function = _qualified(connection, "reject_transaction_ledger_mutation")
    connection.exec_driver_sql(
        f"""
        CREATE TRIGGER trg_transaction_revision_record_append_only
        BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION {function}()
        """
    )


def _catalog_contract(
    connection: sa.Connection,
) -> tuple[set[str], set[str], str, str]:
    schema = _effective_schema(connection)
    check_rows = connection.execute(
        sa.text(
            """
            SELECT constraint_row.conname,
                   pg_get_constraintdef(constraint_row.oid, false) AS definition
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS relation ON relation.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = :schema_name
              AND relation.relname = :table_name
              AND constraint_row.contype = 'c'
            ORDER BY constraint_row.conname
            """
        ),
        {"schema_name": schema, "table_name": _TABLE},
    ).mappings().all()
    checks = {str(row["conname"]) for row in check_rows}
    check_contract_digest = hashlib.sha256(
        json.dumps(
            [
                [str(row["conname"]), str(row["definition"])]
                for row in check_rows
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    trigger_rows = connection.execute(
        sa.text(
            """
            SELECT trigger_row.tgname,
                   trigger_row.tgenabled,
                   trigger_row.tgtype,
                   trigger_row.tgdeferrable,
                   trigger_row.tginitdeferred,
                   pg_get_triggerdef(trigger_row.oid, false) AS definition,
                   coalesce(trigger_constraint.condeferrable, false)
                       AS constraint_deferrable,
                   coalesce(trigger_constraint.condeferred, false)
                       AS constraint_initially_deferred
            FROM pg_trigger AS trigger_row
            JOIN pg_class AS relation ON relation.oid = trigger_row.tgrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            LEFT JOIN pg_constraint AS trigger_constraint
              ON trigger_constraint.oid = trigger_row.tgconstraint
            WHERE namespace.nspname = :schema_name
              AND relation.relname = :table_name
              AND NOT trigger_row.tgisinternal
            ORDER BY trigger_row.tgname
            """
        ),
        {"schema_name": schema, "table_name": _TABLE},
    ).mappings().all()
    triggers = {str(row["tgname"]) for row in trigger_rows}
    trigger_contract_digest = hashlib.sha256(
        json.dumps(
            [
                [
                    str(row["tgname"]),
                    str(row["tgenabled"]),
                    int(row["tgtype"]),
                    bool(row["tgdeferrable"]),
                    bool(row["tginitdeferred"]),
                    str(row["definition"]),
                    bool(row["constraint_deferrable"]),
                    bool(row["constraint_initially_deferred"]),
                ]
                for row in trigger_rows
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return checks, triggers, check_contract_digest, trigger_contract_digest


def _verify_postgresql_contract(
    connection: sa.Connection,
    hardening: ModuleType,
) -> None:
    columns = _columns(connection)
    if _shape(columns) != "current":
        raise RuntimeError("0043 postflight did not produce the current ledger shape.")
    _assert_numeric_shape(columns, legacy=False)

    # PostgreSQL deparsers omit schema qualification for objects visible in
    # search_path.  Pin a catalog/public-only path only while reading the
    # definitions, then restore Alembic's path before any later revision runs.
    with _deterministic_deparser_search_path(connection):
        (
            checks,
            triggers,
            check_contract_digest,
            trigger_contract_digest,
        ) = _catalog_contract(connection)
    expected_checks = {name for name, _ in _CHECK_CONTRACTS}
    if checks != expected_checks:
        raise RuntimeError(
            "0043 transaction CHECK contract mismatch: "
            f"missing={sorted(expected_checks - checks)}, "
            f"unexpected={sorted(checks - expected_checks)}."
        )
    if check_contract_digest != _CHECK_CONTRACT_SHA256:
        raise RuntimeError(
            "0043 transaction CHECK definitions do not match the exact "
            f"42-contract (found digest {check_contract_digest})."
        )
    if triggers != _EXPECTED_REVISION_TRIGGERS:
        raise RuntimeError(
            "0043 transaction trigger contract mismatch: "
            f"found={sorted(triggers)}."
        )
    if trigger_contract_digest != _TRIGGER_CONTRACT_SHA256:
        raise RuntimeError(
            "0043 transaction trigger definitions, enabled events, or deferred "
            f"semantics do not match the exact contract (found digest "
            f"{trigger_contract_digest})."
        )

    inspector = sa.inspect(connection)
    if _VIEW not in inspector.get_view_names(schema=_schema_name()):
        raise RuntimeError("0043 transaction_current view is missing.")
    view_columns = tuple(
        str(column["name"])
        for column in inspector.get_columns(_VIEW, schema=_schema_name())
    )
    if view_columns != _CURRENT_VIEW_COLUMNS:
        raise RuntimeError(
            "0043 transaction_current has a stale projection: "
            f"found={view_columns}."
        )

    schema = _effective_schema(connection)
    with _deterministic_deparser_search_path(connection):
        function_rows = connection.execute(
            sa.text(
                """
                SELECT procedure.proname,
                       pg_get_function_identity_arguments(procedure.oid)
                           AS identity_arguments,
                       pg_get_functiondef(procedure.oid) AS definition
                FROM pg_proc AS procedure
                JOIN pg_namespace AS namespace
                  ON namespace.oid = procedure.pronamespace
                WHERE namespace.nspname = :schema_name
                  AND procedure.proname IN (
                      'canonical_transaction_revision_payload_v1',
                      'transaction_revision_payload_hash_v1',
                      'validate_transaction_revision_payload_insert_v1',
                      'assert_internal_transfer_group_v1',
                      'validate_internal_transfer_revision_deferred_v1'
                  )
                ORDER BY procedure.proname,
                         pg_get_function_identity_arguments(procedure.oid)
                """
            ),
            {"schema_name": schema},
        ).mappings().all()
    functions = {
        str(row["proname"]): str(row["definition"])
        for row in function_rows
    }
    expected_functions = {
        "canonical_transaction_revision_payload_v1",
        "transaction_revision_payload_hash_v1",
        "validate_transaction_revision_payload_insert_v1",
        "assert_internal_transfer_group_v1",
        "validate_internal_transfer_revision_deferred_v1",
    }
    if set(functions) != expected_functions:
        raise RuntimeError(
            "0043 ledger function contract is incomplete: "
            f"found={sorted(functions)}."
        )
    function_contract_digest = hashlib.sha256(
        json.dumps(
            [
                [
                    str(row["proname"]),
                    str(row["identity_arguments"]),
                    str(row["definition"]),
                ]
                for row in function_rows
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if function_contract_digest != _FUNCTION_CONTRACT_SHA256:
        raise RuntimeError(
            "0043 ledger function definitions do not match the exact contract "
            f"(found digest {function_contract_digest})."
        )
    for name in (
        "canonical_transaction_revision_payload_v1",
        "assert_internal_transfer_group_v1",
    ):
        if "quoted_fx_rate_input_scale" not in functions[name]:
            raise RuntimeError(f"0043 found a stale {name} definition.")

    hardening._verify_existing_postgresql_ledger(connection)


def _upgrade_legacy_postgresql(connection: sa.Connection) -> None:
    _require_no_captured_daily_transactions(connection)
    inspector = sa.inspect(connection)
    if _VIEW not in inspector.get_view_names(schema=_schema_name()):
        raise RuntimeError("0043 legacy ledger is missing transaction_current; refusing repair.")

    hardening = _load_frozen_hardening_helpers()
    _drop_legacy_dependents(connection)
    _drop_all_check_constraints(connection)
    _alter_legacy_shape(connection)

    # Install the exact canonical hash function first, then rewrite every hash
    # from the now-complete facts while the append-only UPDATE guard is absent.
    hardening._install_postgresql_hash_contract(connection)
    table = _qualified(connection, _TABLE)
    hash_function = _qualified(connection, "transaction_revision_payload_hash_v1")
    connection.exec_driver_sql(
        f"UPDATE {table} AS revision_row "
        f"SET payload_hash = {hash_function}(revision_row)"
    )

    _install_check_contract(connection)
    hardening._install_postgresql_transfer_contract(connection)
    _create_current_view(connection)
    _recreate_revision_append_only_guard(connection)
    _verify_postgresql_contract(connection, hardening)


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            f"0043 ledger convergence is not implemented for {connection.dialect.name}."
        )
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE, schema=_schema_name()):
        raise RuntimeError("0043 transaction_revision_record is missing.")

    if connection.dialect.name == "sqlite":
        if _shape(_columns(connection)) != "current":
            raise RuntimeError("0043 cannot repair a legacy transaction ledger on SQLite.")
        return

    _lock_ledger(connection)
    columns = _columns(connection)
    shape = _shape(columns)
    _assert_numeric_shape(columns, legacy=shape == "legacy")
    if shape == "current":
        # A correct fresh installation is intentionally read-only in 0043.
        _verify_postgresql_contract(connection, _load_frozen_hardening_helpers())
        return
    _upgrade_legacy_postgresql(connection)


def downgrade() -> None:
    # This convergence repairs what the already-applied 0036/0037 contract was
    # supposed to create.  It cannot safely reintroduce the stale shape or old
    # payload hashes, so the structural contract intentionally remains exact.
    raise RuntimeError(
        "20260714_0043 is intentionally irreversible: the stale 37-column "
        "ledger and its obsolete payload hashes cannot be restored safely."
    )
