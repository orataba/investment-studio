"""Converge deployed Portfolio schemas to the current-state model.

Revision ID: 20260713_0033
Revises: 20260711_0032

Some early migration files were edited after a local database had already
applied them.  The version table therefore reached head while the deployed
schema retained superseded effective-window columns and pre-rename object
names.  This migration is deliberately conditional: clean databases are
already structurally correct, while drifted databases are brought to the same
shape without maintaining a second runtime model.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0033"
down_revision = "20260711_0032"
branch_labels = None
depends_on = None


def _column_names(connection: sa.Connection, table_name: str) -> set[str]:
    return {
        str(column["name"])
        for column in sa.inspect(connection).get_columns(table_name)
    }


def _index_records(
    connection: sa.Connection,
    table_name: str,
) -> list[dict[str, object]]:
    if connection.dialect.name != "sqlite":
        return list(sa.inspect(connection).get_indexes(table_name))
    records: list[dict[str, object]] = []
    for row in connection.execute(
        sa.text(f"PRAGMA index_list('{table_name}')")
    ).mappings():
        index_name = str(row["name"])
        columns = [
            detail["name"]
            for detail in connection.execute(
                sa.text(f"PRAGMA index_info('{index_name}')")
            ).mappings()
        ]
        records.append(
            {
                "name": index_name,
                "column_names": columns,
                "unique": bool(row["unique"]),
            }
        )
    return records


def _index_names(connection: sa.Connection, table_name: str) -> set[str]:
    return {
        str(index["name"])
        for index in _index_records(connection, table_name)
        if index.get("name")
    }


def _foreign_key_records(
    connection: sa.Connection,
    table_name: str,
) -> list[dict[str, object]]:
    inspector = sa.inspect(connection)
    if connection.dialect.name == "postgresql":
        return list(
            inspector.get_foreign_keys(
                table_name,
                postgresql_ignore_search_path=True,
            )
        )
    return list(inspector.get_foreign_keys(table_name))


def _preflight_deployed_taxonomy_state(connection: sa.Connection) -> None:
    """Refuse to erase any unresolved effective-window or duplicate semantics."""

    window_violations: dict[str, int] = {}
    for table_name in (
        "taxonomy_record",
        "taxonomy_assignment_record",
        "target_set_record",
    ):
        present = _column_names(connection, table_name)
        window_columns = [
            column_name
            for column_name in ("effective_from", "effective_to")
            if column_name in present
        ]
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
            window_violations[table_name] = violation_count
    if window_violations:
        raise RuntimeError(
            "Portfolio taxonomy effective windows contain real values and cannot "
            "be collapsed automatically. Migrate them explicitly before retrying: "
            f"{window_violations}."
        )

    duplicate_assignment = connection.execute(
        sa.text(
            """
            SELECT taxonomy_id, target_scope, target_entity_id, count(*)
            FROM taxonomy_assignment_record
            GROUP BY taxonomy_id, target_scope, target_entity_id
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate_assignment is not None:
        raise RuntimeError(
            "The current-state taxonomy model permits one assignment row per "
            "taxonomy/scope/entity across all statuses. Resolve duplicate rows "
            f"before retrying: {tuple(duplicate_assignment)!r}."
        )

    duplicate_target_set = connection.execute(
        sa.text(
            """
            SELECT taxonomy_id,
                   coalesce(comparator_taxonomy_node_id, ''),
                   target_set_type,
                   count(*)
            FROM target_set_record
            WHERE status = 'active'
            GROUP BY taxonomy_id,
                     coalesce(comparator_taxonomy_node_id, ''),
                     target_set_type
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate_target_set is not None:
        raise RuntimeError(
            "Only one active TargetSet is allowed per taxonomy/comparator/type. "
            "Resolve duplicate active rows before retrying: "
            f"{tuple(duplicate_target_set)!r}."
        )


def _drop_columns_if_present(
    connection: sa.Connection,
    table_name: str,
    column_names: tuple[str, ...],
) -> None:
    present = _column_names(connection, table_name)
    removable = [name for name in column_names if name in present]
    if not removable:
        return
    with op.batch_alter_table(table_name) as batch_op:
        for column_name in removable:
            batch_op.drop_column(column_name)


def _replace_legacy_index(
    connection: sa.Connection,
    *,
    table_name: str,
    legacy_name: str,
    current_name: str,
    columns: list[str],
) -> None:
    indexes = _index_names(connection, table_name)
    if legacy_name in indexes:
        op.drop_index(legacy_name, table_name=table_name)
        indexes.remove(legacy_name)
    if current_name in indexes:
        op.drop_index(current_name, table_name=table_name)
    op.create_index(current_name, table_name, columns, unique=False)


def _drop_index_if_present(
    connection: sa.Connection,
    *,
    table_name: str,
    index_name: str,
) -> None:
    if index_name in _index_names(connection, table_name):
        op.drop_index(index_name, table_name=table_name)


def _ensure_instrument_foreign_key(connection: sa.Connection) -> None:
    legacy_name = "fk_transaction_record_asset_id_instrument"
    current_name = "fk_transaction_record_instrument_id_instrument"
    foreign_keys = _foreign_key_records(connection, "transaction_record")
    current = next(
        (
            constraint
            for constraint in foreign_keys
            if constraint.get("name") == current_name
        ),
        None,
    )
    expected_referred_schema = (
        "instrument_registry" if connection.dialect.name == "postgresql" else None
    )
    current_is_exact = bool(
        current
        and current.get("constrained_columns") == ["instrument_id"]
        and current.get("referred_schema") == expected_referred_schema
        and current.get("referred_table") == "instrument"
        and current.get("referred_columns") == ["instrument_id"]
        and str((current.get("options") or {}).get("ondelete") or "").upper()
        == "RESTRICT"
    )

    for constraint in foreign_keys:
        name = str(constraint.get("name") or "")
        is_instrument_constraint = constraint.get("constrained_columns") == [
            "instrument_id"
        ]
        if not is_instrument_constraint and name not in {legacy_name, current_name}:
            continue
        if name == current_name and current_is_exact:
            continue
        if name:
            with op.batch_alter_table("transaction_record") as batch_op:
                batch_op.drop_constraint(name, type_="foreignkey")

    if current_is_exact:
        return
    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.create_foreign_key(
            current_name,
            "instrument",
            ["instrument_id"],
            ["instrument_id"],
            ondelete="RESTRICT",
            referent_schema=expected_referred_schema,
        )


def _normalized_sql(value: object) -> str:
    return " ".join(str(value or "").lower().replace('"', "").split())


def _assert_schema_converged(connection: sa.Connection) -> None:
    for table_name in (
        "taxonomy_record",
        "taxonomy_assignment_record",
        "target_set_record",
    ):
        unexpected = _column_names(connection, table_name).intersection(
            {"effective_from", "effective_to"}
        )
        if unexpected:
            raise RuntimeError(
                f"Portfolio schema convergence left obsolete columns on {table_name}: "
                f"{sorted(unexpected)}."
            )

    expected_indexes = {
        "portfolio_daily_holding_snapshot": {
            "ix_portfolio_daily_holding_instrument_date": (
                ["portfolio_id", "instrument_id", "as_of_date"],
                False,
            )
        },
        "transaction_record": {
            "ix_transaction_record_portfolio_instrument_trade": (
                ["portfolio_id", "instrument_id", "trade_date", "trade_at"],
                False,
            )
        },
        "target_set_record": {
            "ix_target_set_record_taxonomy_scope_type": (
                [
                    "taxonomy_id",
                    "comparator_taxonomy_node_id",
                    "target_set_type",
                    "target_set_id",
                ],
                False,
            )
        },
        "taxonomy_assignment_record": {
            "uq_taxonomy_assignment_target": (
                ["taxonomy_id", "target_scope", "target_entity_id"],
                True,
            )
        },
    }
    for table_name, required in expected_indexes.items():
        actual = {
            str(index.get("name")): index
            for index in _index_records(connection, table_name)
            if index.get("name")
        }
        for index_name, (columns, unique) in required.items():
            index = actual.get(index_name)
            if (
                index is None
                or index.get("column_names") != columns
                or bool(index.get("unique")) is not unique
            ):
                raise RuntimeError(
                    f"Portfolio index {index_name} does not match its canonical "
                    f"definition: {index!r}."
                )

    target_index_sql: str | None
    if connection.dialect.name == "postgresql":
        target_index_sql = connection.scalar(
            sa.text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'target_set_record'
                  AND indexname = 'uq_target_set_active_scope'
                """
            )
        )
    else:
        target_index_sql = connection.scalar(
            sa.text(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'index'
                  AND name = 'uq_target_set_active_scope'
                """
            )
        )
    normalized_target_index = _normalized_sql(target_index_sql)
    required_fragments = (
        "create unique index",
        "taxonomy_id",
        "coalesce(comparator_taxonomy_node_id",
        "target_set_type",
        "where",
        "status",
        "active",
    )
    if not all(fragment in normalized_target_index for fragment in required_fragments):
        raise RuntimeError(
            "The active TargetSet scope index is missing or has the wrong "
            f"expression/predicate: {target_index_sql!r}."
        )

    foreign_keys = _foreign_key_records(connection, "transaction_record")
    instrument_foreign_keys = [
        constraint
        for constraint in foreign_keys
        if constraint.get("constrained_columns") == ["instrument_id"]
    ]
    if len(instrument_foreign_keys) != 1:
        raise RuntimeError(
            "transaction_record must have exactly one instrument_id foreign key: "
            f"{instrument_foreign_keys!r}."
        )
    instrument_foreign_key = instrument_foreign_keys[0]
    expected_referred_schema = (
        "instrument_registry" if connection.dialect.name == "postgresql" else None
    )
    if not (
        instrument_foreign_key.get("name")
        == "fk_transaction_record_instrument_id_instrument"
        and instrument_foreign_key.get("referred_schema")
        == expected_referred_schema
        and instrument_foreign_key.get("referred_table") == "instrument"
        and instrument_foreign_key.get("referred_columns") == ["instrument_id"]
        and str(
            (instrument_foreign_key.get("options") or {}).get("ondelete") or ""
        ).upper()
        == "RESTRICT"
    ):
        raise RuntimeError(
            "transaction_record instrument_id foreign key is not canonical: "
            f"{instrument_foreign_key!r}."
        )

    rebalance_column = next(
        (
            column
            for column in sa.inspect(connection).get_columns("research_settings_record")
            if column["name"] == "backtest_rebalance_frequency"
        ),
        None,
    )
    if rebalance_column is None or rebalance_column.get("default") is not None:
        raise RuntimeError(
            "research_settings_record.backtest_rebalance_frequency must exist "
            "without a stale server default."
        )


def upgrade() -> None:
    connection = op.get_bind()
    _preflight_deployed_taxonomy_state(connection)

    if "ix_target_set_record_taxonomy_scope_type_effective" in _index_names(
        connection, "target_set_record"
    ):
        op.drop_index(
            "ix_target_set_record_taxonomy_scope_type_effective",
            table_name="target_set_record",
        )

    _drop_columns_if_present(
        connection,
        "taxonomy_record",
        ("effective_to", "effective_from"),
    )
    _drop_columns_if_present(
        connection,
        "taxonomy_assignment_record",
        ("effective_to", "effective_from"),
    )
    _drop_columns_if_present(
        connection,
        "target_set_record",
        ("effective_to", "effective_from"),
    )

    _replace_legacy_index(
        connection,
        table_name="portfolio_daily_holding_snapshot",
        legacy_name="ix_portfolio_daily_holding_asset_date",
        current_name="ix_portfolio_daily_holding_instrument_date",
        columns=["portfolio_id", "instrument_id", "as_of_date"],
    )
    _replace_legacy_index(
        connection,
        table_name="transaction_record",
        legacy_name="ix_transaction_record_portfolio_asset_trade",
        current_name="ix_transaction_record_portfolio_instrument_trade",
        columns=["portfolio_id", "instrument_id", "trade_date", "trade_at"],
    )
    _replace_legacy_index(
        connection,
        table_name="target_set_record",
        legacy_name="ix_target_set_record_taxonomy_scope_type_effective",
        current_name="ix_target_set_record_taxonomy_scope_type",
        columns=[
            "taxonomy_id",
            "comparator_taxonomy_node_id",
            "target_set_type",
            "target_set_id",
        ],
    )
    _ensure_instrument_foreign_key(connection)

    rebalance_column = next(
        (
            column
            for column in sa.inspect(connection).get_columns(
                "research_settings_record"
            )
            if column["name"] == "backtest_rebalance_frequency"
        ),
        None,
    )
    if rebalance_column is not None and rebalance_column.get("default") is not None:
        op.alter_column(
            "research_settings_record",
            "backtest_rebalance_frequency",
            existing_type=sa.String(),
            existing_nullable=False,
            server_default=None,
        )

    _drop_index_if_present(
        connection,
        table_name="taxonomy_assignment_record",
        index_name="uq_taxonomy_assignment_active_target",
    )
    _drop_index_if_present(
        connection,
        table_name="taxonomy_assignment_record",
        index_name="uq_taxonomy_assignment_target",
    )
    op.create_index(
        "uq_taxonomy_assignment_target",
        "taxonomy_assignment_record",
        ["taxonomy_id", "target_scope", "target_entity_id"],
        unique=True,
    )

    _drop_index_if_present(
        connection,
        table_name="target_set_record",
        index_name="uq_target_set_active_scope",
    )
    op.create_index(
        "uq_target_set_active_scope",
        "target_set_record",
        [
            "taxonomy_id",
            sa.text("coalesce(comparator_taxonomy_node_id, '')"),
            "target_set_type",
        ],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    _assert_schema_converged(connection)


def downgrade() -> None:
    raise RuntimeError(
        "20260713_0033 is intentionally irreversible: the removed effective "
        "windows were already superseded by the current-state taxonomy model."
    )
