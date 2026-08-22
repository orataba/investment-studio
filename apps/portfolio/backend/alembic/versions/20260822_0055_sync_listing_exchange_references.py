"""Sync canonical listing exchanges into persisted Portfolio references.

Revision ID: 20260822_0055
Revises: 20260820_0054
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260822_0055"
down_revision: str | None = "20260820_0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _registry_listing_map(connection: sa.Connection) -> dict[str, tuple[str, str]]:
    schema = "instrument_registry" if connection.dialect.name == "postgresql" else None
    if not sa.inspect(connection).has_table("instrument", schema=schema):
        if connection.dialect.name == "postgresql":
            raise RuntimeError("Shared Instrument Registry must be migrated before Portfolio.")
        return {}
    qualified_name = "instrument_registry.instrument" if schema else "instrument"
    rows = list(
        connection.execute(
            sa.text(
                "SELECT instrument_id, instrument_type, exchange_code "
                f"FROM {qualified_name} "
                "WHERE instrument_type IN ('equity', 'etf') "
                "ORDER BY instrument_id"
            )
        )
    )
    missing = [str(row.instrument_id) for row in rows if not row.exchange_code]
    if missing:
        raise RuntimeError(
            "Shared Instrument Registry listing migration must run first; missing "
            "exchange identity for: " + ", ".join(missing)
        )
    return {
        str(row.instrument_id): (str(row.instrument_type), str(row.exchange_code))
        for row in rows
    }


def _listing_reference(
    raw_reference: object,
    *,
    instrument_id: object,
    listing_map: dict[str, tuple[str, str]],
    context: str,
) -> dict[str, object] | None:
    normalized_instrument_id = str(instrument_id or "")
    identity = listing_map.get(normalized_instrument_id)
    if identity is None:
        return None
    if not isinstance(raw_reference, dict):
        raise RuntimeError(
            f"{context} for listed instrument {normalized_instrument_id} has no reference."
        )
    instrument_type, exchange_code = identity
    updated = dict(raw_reference)
    updated["instrument_type"] = instrument_type
    updated["exchange_code"] = exchange_code
    return updated if updated != raw_reference else None


def _sync_reference_table(
    connection: sa.Connection,
    *,
    table_name: str,
    key_columns: tuple[str, ...],
    listing_map: dict[str, tuple[str, str]],
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
        updated = _listing_reference(
            row["instrument_ref_json"],
            instrument_id=row["instrument_id"],
            listing_map=listing_map,
            context=table_name,
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
    listing_map: dict[str, tuple[str, str]],
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
        instrument_id = row["instrument_id"]
        if str(instrument_id or "") not in listing_map:
            continue
        if not isinstance(holding, dict):
            raise RuntimeError(
                "portfolio_daily_holding_snapshot for listed instrument "
                f"{instrument_id} has no holding payload."
            )
        updated_reference = _listing_reference(
            holding.get("instrument_ref"),
            instrument_id=instrument_id,
            listing_map=listing_map,
            context="portfolio_daily_holding_snapshot",
        )
        if updated_reference is None:
            continue
        updated_holding = dict(holding)
        updated_holding["instrument_ref"] = updated_reference
        connection.execute(
            sa.update(table)
            .where(*(table.c[column_name] == row[column_name] for column_name in key_columns))
            .values(holding_json=updated_holding)
        )


def upgrade() -> None:
    connection = op.get_bind()
    listing_map = _registry_listing_map(connection)
    if not listing_map:
        return
    _sync_reference_table(
        connection,
        table_name="transaction_record",
        key_columns=("transaction_id",),
        listing_map=listing_map,
    )
    _sync_reference_table(
        connection,
        table_name="portfolio_instrument_universe_record",
        key_columns=("portfolio_id", "instrument_id"),
        listing_map=listing_map,
    )
    _sync_holding_snapshots(connection, listing_map)


def downgrade() -> None:
    raise RuntimeError(
        "Restore the pre-migration database backup instead of removing canonical "
        "listing exchanges from Portfolio audit references."
    )
