"""Reconcile deployed 0032 schemas with the retained canonical lineage.

Revision ID: 20260715_0032r
Revises: 20260711_0032

Some local databases applied early revisions before those revision files were
corrected.  Their version table is therefore at 20260711_0032 while a small
set of physical names, nullable effective-window columns, and one server
default still reflect the superseded definitions.

This migration repairs only drift already covered by retained revisions.  It
does not restore any table, constraint, or uniqueness policy from the reverted
Portfolio overhaul.  Effective-window columns are removed only after proving
that every value is NULL, and the legacy instrument foreign key is renamed
only when its definition is exactly equivalent to the canonical constraint.

Downgrade is intentionally a no-op.  The canonical physical schema is the
correct shape for revision 20260711_0032; recreating historical drift would
misrepresent the retained lineage and could reintroduce ignored semantics.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0032r"
down_revision: str | None = "20260711_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_WINDOW_TABLES = (
    "taxonomy_record",
    "taxonomy_assignment_record",
    "target_set_record",
)
_WINDOW_COLUMNS = ("effective_from", "effective_to")

_TARGET_SET_TABLE = "target_set_record"
_TARGET_SET_LEGACY_INDEX = "ix_target_set_record_taxonomy_scope_type_effective"
_TARGET_SET_LEGACY_COLUMNS = (
    "taxonomy_id",
    "comparator_taxonomy_node_id",
    "target_set_type",
    "effective_from",
    "target_set_id",
)
_TARGET_SET_CURRENT_INDEX = "ix_target_set_record_taxonomy_scope_type"
_TARGET_SET_CURRENT_COLUMNS = (
    "taxonomy_id",
    "comparator_taxonomy_node_id",
    "target_set_type",
    "target_set_id",
)

_EQUIVALENT_INDEX_RENAMES = (
    (
        "portfolio_daily_holding_snapshot",
        "ix_portfolio_daily_holding_asset_date",
        "ix_portfolio_daily_holding_instrument_date",
        ("portfolio_id", "instrument_id", "as_of_date"),
    ),
    (
        "transaction_record",
        "ix_transaction_record_portfolio_asset_trade",
        "ix_transaction_record_portfolio_instrument_trade",
        ("portfolio_id", "instrument_id", "trade_date", "trade_at"),
    ),
)

_TRANSACTION_TABLE = "transaction_record"
_LEGACY_INSTRUMENT_FOREIGN_KEY = "fk_transaction_record_asset_id_instrument"
_CURRENT_INSTRUMENT_FOREIGN_KEY = "fk_transaction_record_instrument_id_instrument"

_RESEARCH_SETTINGS_TABLE = "research_settings_record"
_REBALANCE_COLUMN = "backtest_rebalance_frequency"


def _column_records(
    connection: sa.Connection,
    table_name: str,
) -> dict[str, dict[str, object]]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        raise RuntimeError(
            f"Portfolio schema reconciliation requires table {table_name}."
        )
    return {
        str(column["name"]): dict(column)
        for column in inspector.get_columns(table_name)
    }


def _index_records(
    connection: sa.Connection,
    table_name: str,
) -> dict[str, dict[str, object]]:
    return {
        str(index["name"]): dict(index)
        for index in sa.inspect(connection).get_indexes(table_name)
        if index.get("name")
    }


def _assert_index_definition(
    index: dict[str, object],
    *,
    index_name: str,
    columns: tuple[str, ...],
) -> None:
    actual_columns = tuple(str(column) for column in index.get("column_names") or ())
    if actual_columns != columns or bool(index.get("unique")):
        raise RuntimeError(
            "Portfolio schema reconciliation found an unexpected definition for "
            f"index {index_name}: columns={actual_columns!r}, "
            f"unique={bool(index.get('unique'))!r}."
        )


def _preflight_effective_windows(connection: sa.Connection) -> None:
    violations: dict[str, int] = {}
    for table_name in _WINDOW_TABLES:
        present = _column_records(connection, table_name)
        window_columns = [name for name in _WINDOW_COLUMNS if name in present]
        if not window_columns:
            continue
        table = sa.table(
            table_name,
            *(sa.column(column_name) for column_name in window_columns),
        )
        violation_count = int(
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(table)
                .where(
                    sa.or_(
                        *(table.c[column_name].is_not(None) for column_name in window_columns)
                    )
                )
            )
            or 0
        )
        if violation_count:
            violations[table_name] = violation_count
    if violations:
        raise RuntimeError(
            "Portfolio schema reconciliation found non-NULL taxonomy effective "
            "windows and will not discard them automatically; migrate those "
            f"semantics explicitly before retrying: {violations}."
        )


def _converge_effective_windows(connection: sa.Connection) -> None:
    indexes = _index_records(connection, _TARGET_SET_TABLE)
    legacy_index = indexes.get(_TARGET_SET_LEGACY_INDEX)
    if legacy_index is not None:
        _assert_index_definition(
            legacy_index,
            index_name=_TARGET_SET_LEGACY_INDEX,
            columns=_TARGET_SET_LEGACY_COLUMNS,
        )
        op.drop_index(
            _TARGET_SET_LEGACY_INDEX,
            table_name=_TARGET_SET_TABLE,
        )

    current_index = indexes.get(_TARGET_SET_CURRENT_INDEX)
    if current_index is not None:
        _assert_index_definition(
            current_index,
            index_name=_TARGET_SET_CURRENT_INDEX,
            columns=_TARGET_SET_CURRENT_COLUMNS,
        )

    for table_name in _WINDOW_TABLES:
        present = _column_records(connection, table_name)
        removable = [name for name in reversed(_WINDOW_COLUMNS) if name in present]
        if not removable:
            continue
        with op.batch_alter_table(table_name) as batch_op:
            for column_name in removable:
                batch_op.drop_column(column_name)

    if current_index is None:
        op.create_index(
            _TARGET_SET_CURRENT_INDEX,
            _TARGET_SET_TABLE,
            list(_TARGET_SET_CURRENT_COLUMNS),
            unique=False,
        )


def _rename_index(connection: sa.Connection, legacy_name: str, current_name: str) -> None:
    quote = connection.dialect.identifier_preparer.quote
    op.execute(
        sa.text(
            f"ALTER INDEX {quote(legacy_name)} RENAME TO {quote(current_name)}"
        )
    )


def _converge_equivalent_index(
    connection: sa.Connection,
    *,
    table_name: str,
    legacy_name: str,
    current_name: str,
    columns: tuple[str, ...],
) -> None:
    indexes = _index_records(connection, table_name)
    legacy_index = indexes.get(legacy_name)
    current_index = indexes.get(current_name)
    if legacy_index is not None:
        _assert_index_definition(
            legacy_index,
            index_name=legacy_name,
            columns=columns,
        )
    if current_index is not None:
        _assert_index_definition(
            current_index,
            index_name=current_name,
            columns=columns,
        )

    if current_index is not None:
        if legacy_index is not None:
            op.drop_index(legacy_name, table_name=table_name)
        return
    if legacy_index is None:
        op.create_index(current_name, table_name, list(columns), unique=False)
        return

    if connection.dialect.name == "postgresql":
        _rename_index(connection, legacy_name, current_name)
        return
    op.drop_index(legacy_name, table_name=table_name)
    op.create_index(current_name, table_name, list(columns), unique=False)


def _foreign_key_records(connection: sa.Connection) -> dict[str, dict[str, object]]:
    inspector = sa.inspect(connection)
    if connection.dialect.name == "postgresql":
        records = inspector.get_foreign_keys(
            _TRANSACTION_TABLE,
            postgresql_ignore_search_path=True,
        )
    else:
        records = inspector.get_foreign_keys(_TRANSACTION_TABLE)
    return {
        str(record["name"]): dict(record)
        for record in records
        if record.get("name")
    }


def _instrument_foreign_key_is_canonical(
    connection: sa.Connection,
    record: dict[str, object],
) -> bool:
    expected_schema = (
        "instrument_registry" if connection.dialect.name == "postgresql" else None
    )
    options = record.get("options") or {}
    return bool(
        record.get("constrained_columns") == ["instrument_id"]
        and record.get("referred_schema") == expected_schema
        and record.get("referred_table") == "instrument"
        and record.get("referred_columns") == ["instrument_id"]
        and str(options.get("ondelete") or "").upper() == "RESTRICT"
    )


def _converge_instrument_foreign_key(connection: sa.Connection) -> None:
    # SQLite does not have the cross-schema Registry foreign key from retained
    # revision 20260421_0012, so there is no corresponding drift to repair.
    if connection.dialect.name != "postgresql":
        return

    foreign_keys = _foreign_key_records(connection)
    legacy = foreign_keys.get(_LEGACY_INSTRUMENT_FOREIGN_KEY)
    current = foreign_keys.get(_CURRENT_INSTRUMENT_FOREIGN_KEY)
    if legacy is not None and not _instrument_foreign_key_is_canonical(connection, legacy):
        raise RuntimeError(
            "Portfolio schema reconciliation will not rename legacy foreign key "
            f"{_LEGACY_INSTRUMENT_FOREIGN_KEY} because its definition is not "
            f"equivalent to the canonical instrument constraint: {legacy!r}."
        )
    if current is not None and not _instrument_foreign_key_is_canonical(connection, current):
        raise RuntimeError(
            "Portfolio schema reconciliation found a non-canonical definition "
            f"for foreign key {_CURRENT_INSTRUMENT_FOREIGN_KEY}: {current!r}."
        )
    if current is not None:
        if legacy is not None:
            op.drop_constraint(
                _LEGACY_INSTRUMENT_FOREIGN_KEY,
                _TRANSACTION_TABLE,
                type_="foreignkey",
            )
        return
    if legacy is None:
        raise RuntimeError(
            "Portfolio schema reconciliation requires either canonical foreign "
            f"key {_CURRENT_INSTRUMENT_FOREIGN_KEY} or its equivalent legacy name "
            f"{_LEGACY_INSTRUMENT_FOREIGN_KEY}."
        )

    quote = connection.dialect.identifier_preparer.quote
    op.execute(
        sa.text(
            f"ALTER TABLE {quote(_TRANSACTION_TABLE)} "
            f"RENAME CONSTRAINT {quote(_LEGACY_INSTRUMENT_FOREIGN_KEY)} "
            f"TO {quote(_CURRENT_INSTRUMENT_FOREIGN_KEY)}"
        )
    )


def _normalized_default(value: object) -> str:
    normalized = " ".join(str(value or "").strip().lower().split())
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    for suffix in ("::character varying", "::varchar", "::text"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    return normalized


def _converge_rebalance_default(connection: sa.Connection) -> None:
    column = _column_records(connection, _RESEARCH_SETTINGS_TABLE).get(_REBALANCE_COLUMN)
    if column is None:
        raise RuntimeError(
            "Portfolio schema reconciliation requires "
            f"{_RESEARCH_SETTINGS_TABLE}.{_REBALANCE_COLUMN}."
        )
    default = column.get("default")
    if default is None:
        return
    if _normalized_default(default) not in {"'1m'", '"1m"', "1m"}:
        raise RuntimeError(
            "Portfolio schema reconciliation will not remove an unexpected "
            f"server default from {_RESEARCH_SETTINGS_TABLE}.{_REBALANCE_COLUMN}: "
            f"{default!r}."
        )
    with op.batch_alter_table(_RESEARCH_SETTINGS_TABLE) as batch_op:
        batch_op.alter_column(
            _REBALANCE_COLUMN,
            existing_type=sa.String(),
            existing_nullable=False,
            server_default=None,
        )


def _assert_converged(connection: sa.Connection) -> None:
    for table_name in _WINDOW_TABLES:
        unexpected = set(_column_records(connection, table_name)).intersection(
            _WINDOW_COLUMNS
        )
        if unexpected:
            raise RuntimeError(
                f"Portfolio schema reconciliation left obsolete columns on "
                f"{table_name}: {sorted(unexpected)}."
            )

    expected_indexes = (
        (
            _TARGET_SET_TABLE,
            _TARGET_SET_CURRENT_INDEX,
            _TARGET_SET_CURRENT_COLUMNS,
            _TARGET_SET_LEGACY_INDEX,
        ),
        *(
            (table_name, current_name, columns, legacy_name)
            for table_name, legacy_name, current_name, columns in _EQUIVALENT_INDEX_RENAMES
        ),
    )
    for table_name, current_name, columns, legacy_name in expected_indexes:
        indexes = _index_records(connection, table_name)
        if legacy_name in indexes:
            raise RuntimeError(
                f"Portfolio schema reconciliation left legacy index {legacy_name}."
            )
        current = indexes.get(current_name)
        if current is None:
            raise RuntimeError(
                f"Portfolio schema reconciliation did not create index {current_name}."
            )
        _assert_index_definition(
            current,
            index_name=current_name,
            columns=columns,
        )

    column = _column_records(connection, _RESEARCH_SETTINGS_TABLE)[_REBALANCE_COLUMN]
    if column.get("default") is not None:
        raise RuntimeError(
            "Portfolio schema reconciliation left a server default on "
            f"{_RESEARCH_SETTINGS_TABLE}.{_REBALANCE_COLUMN}."
        )

    if connection.dialect.name == "postgresql":
        foreign_keys = _foreign_key_records(connection)
        if _LEGACY_INSTRUMENT_FOREIGN_KEY in foreign_keys:
            raise RuntimeError(
                "Portfolio schema reconciliation left legacy foreign key "
                f"{_LEGACY_INSTRUMENT_FOREIGN_KEY}."
            )
        current = foreign_keys.get(_CURRENT_INSTRUMENT_FOREIGN_KEY)
        if current is None or not _instrument_foreign_key_is_canonical(connection, current):
            raise RuntimeError(
                "Portfolio schema reconciliation did not produce the canonical "
                f"instrument foreign key: {current!r}."
            )


def upgrade() -> None:
    connection = op.get_bind()
    _preflight_effective_windows(connection)
    _converge_effective_windows(connection)
    for table_name, legacy_name, current_name, columns in _EQUIVALENT_INDEX_RENAMES:
        _converge_equivalent_index(
            connection,
            table_name=table_name,
            legacy_name=legacy_name,
            current_name=current_name,
            columns=columns,
        )
    _converge_instrument_foreign_key(connection)
    _converge_rebalance_default(connection)
    _assert_converged(connection)


def downgrade() -> None:
    # Revision 20260711_0032 already declares the canonical physical shape.
    # Recreating superseded names, ignored columns, or a stale server default
    # would manufacture drift instead of reversing a retained business change.
    pass
