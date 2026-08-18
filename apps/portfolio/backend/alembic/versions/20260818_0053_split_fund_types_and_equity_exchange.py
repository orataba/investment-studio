"""Align current Portfolio references with canonical instrument types.

Revision ID: 20260818_0053
Revises: 20260817_0052
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260818_0053"
down_revision: str | None = "20260817_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _registry_identity_map(
    connection: sa.Connection,
) -> dict[str, tuple[str, str | None]]:
    inspector = sa.inspect(connection)
    schema = "instrument_registry" if connection.dialect.name == "postgresql" else None
    if not inspector.has_table("instrument", schema=schema):
        return {}
    qualified_name = "instrument_registry.instrument" if schema else "instrument"
    return {
        str(row.instrument_id): (
            str(row.instrument_type),
            str(row.exchange_code) if row.exchange_code else None,
        )
        for row in connection.execute(
            sa.text(
                "SELECT instrument_id, instrument_type, exchange_code "
                f"FROM {qualified_name}"
            )
        )
    }


def _updated_ref(
    raw_ref: object,
    *,
    instrument_id: object,
    identity_map: dict[str, tuple[str, str | None]],
) -> dict[str, object] | None:
    if not isinstance(raw_ref, dict):
        return None
    identity = identity_map.get(str(instrument_id or ""))
    if identity is None:
        return None
    instrument_type, exchange_code = identity
    updated = dict(raw_ref)
    updated["instrument_type"] = instrument_type
    if instrument_type == "equity":
        updated["exchange_code"] = exchange_code
    else:
        updated.pop("exchange_code", None)
    return updated if updated != raw_ref else None


def _sync_reference_table(
    connection: sa.Connection,
    *,
    table_name: str,
    key_columns: tuple[str, ...],
    identity_map: dict[str, tuple[str, str | None]],
) -> None:
    column_names = (*key_columns, "instrument_id", "instrument_ref_json")
    table = sa.table(
        table_name,
        *(
            sa.column(
                column_name,
                sa.JSON() if column_name == "instrument_ref_json" else sa.String(),
            )
            for column_name in dict.fromkeys(column_names)
        ),
    )
    for row in connection.execute(sa.select(table)).mappings():
        updated = _updated_ref(
            row["instrument_ref_json"],
            instrument_id=row["instrument_id"],
            identity_map=identity_map,
        )
        if updated is None:
            continue
        connection.execute(
            sa.update(table)
            .where(*(table.c[column_name] == row[column_name] for column_name in key_columns))
            .values(instrument_ref_json=updated)
        )


def _sync_holding_snapshots(
    connection: sa.Connection,
    identity_map: dict[str, tuple[str, str | None]],
) -> None:
    table = sa.table(
        "portfolio_daily_holding_snapshot",
        sa.column("portfolio_id", sa.String()),
        sa.column("as_of_date", sa.Date()),
        sa.column("account_id", sa.String()),
        sa.column("position_reference_id", sa.String()),
        sa.column("holding_kind", sa.String()),
        sa.column("instrument_id", sa.String()),
        sa.column("holding_json", sa.JSON()),
    )
    key_columns = (
        "portfolio_id",
        "as_of_date",
        "account_id",
        "position_reference_id",
        "holding_kind",
    )
    for row in connection.execute(sa.select(table)).mappings():
        holding = row["holding_json"]
        if not isinstance(holding, dict):
            continue
        updated_ref = _updated_ref(
            holding.get("instrument_ref"),
            instrument_id=row["instrument_id"],
            identity_map=identity_map,
        )
        if updated_ref is None:
            continue
        updated_holding = dict(holding)
        updated_holding["instrument_ref"] = updated_ref
        connection.execute(
            sa.update(table)
            .where(*(table.c[column_name] == row[column_name] for column_name in key_columns))
            .values(holding_json=updated_holding)
        )


def upgrade() -> None:
    connection = op.get_bind()
    identity_map = _registry_identity_map(connection)
    if not identity_map:
        return
    _sync_reference_table(
        connection,
        table_name="transaction_record",
        key_columns=("transaction_id",),
        identity_map=identity_map,
    )
    _sync_reference_table(
        connection,
        table_name="portfolio_instrument_universe_record",
        key_columns=("portfolio_id", "instrument_id"),
        identity_map=identity_map,
    )
    _sync_holding_snapshots(connection, identity_map)


def downgrade() -> None:
    raise RuntimeError(
        "Revision 20260818_0053 removes the ambiguous fund type from current "
        "Portfolio references. Restore the pre-migration database backup instead."
    )
