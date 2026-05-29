"""add portfolio instrument universe records

Revision ID: 20260528_0027
Revises: 20260527_0026
Create Date: 2026-05-28 00:27:00
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260528_0027"
down_revision = "20260527_0026"
branch_labels = None
depends_on = None


def _current_utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_float(value: object) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _quantity_delta(row: dict[str, object]) -> float:
    quantity = _safe_float(row.get("quantity"))
    transaction_type = str(row.get("transaction_type") or "")
    if quantity <= 0:
        return 0.0
    if transaction_type in {"opening_balance", "buy", "dividend_reinvestment"}:
        return quantity
    if transaction_type in {"sell", "maturity_redemption"}:
        return -quantity
    if transaction_type == "transfer_in" and row.get("transfer_object_type") == "position":
        return quantity
    if transaction_type == "transfer_out" and row.get("transfer_object_type") == "position":
        return -quantity
    return 0.0


def _transaction_order_key(row: dict[str, object]) -> tuple[str, str, str, str, str]:
    trade_date = row.get("trade_date")
    settlement_date = row.get("settlement_date")
    return (
        trade_date.isoformat() if hasattr(trade_date, "isoformat") else str(trade_date or ""),
        str(row.get("trade_at") or ""),
        str(row.get("created_at") or ""),
        str(row.get("transaction_id") or ""),
        settlement_date.isoformat() if hasattr(settlement_date, "isoformat") else str(settlement_date or ""),
    )


def upgrade() -> None:
    op.create_table(
        "portfolio_instrument_universe_record",
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("instrument_ref_json", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("holding_state", sa.String(), nullable=False),
        sa.Column("first_transaction_date", sa.Date(), nullable=True),
        sa.Column("last_transaction_date", sa.Date(), nullable=True),
        sa.Column("transaction_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio_record.portfolio_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("portfolio_id", "instrument_id"),
    )
    op.create_index(
        "ix_portfolio_instrument_universe_state",
        "portfolio_instrument_universe_record",
        ["portfolio_id", "holding_state", "status"],
        unique=False,
    )
    op.create_index(
        "ix_portfolio_instrument_universe_source",
        "portfolio_instrument_universe_record",
        ["portfolio_id", "source"],
        unique=False,
    )

    connection = op.get_bind()
    transaction_table = sa.table(
        "transaction_record",
        sa.column("transaction_id", sa.String()),
        sa.column("portfolio_id", sa.String()),
        sa.column("transaction_type", sa.String()),
        sa.column("trade_date", sa.Date()),
        sa.column("trade_at", sa.String()),
        sa.column("settlement_date", sa.Date()),
        sa.column("instrument_id", sa.String()),
        sa.column("instrument_ref_json", sa.JSON()),
        sa.column("quantity", sa.Float()),
        sa.column("transfer_object_type", sa.String()),
        sa.column("created_at", sa.String()),
    )
    taxonomy_table = sa.table(
        "taxonomy_record",
        sa.column("taxonomy_id", sa.String()),
        sa.column("portfolio_id", sa.String()),
    )
    assignment_table = sa.table(
        "taxonomy_assignment_record",
        sa.column("taxonomy_id", sa.String()),
        sa.column("target_scope", sa.String()),
        sa.column("target_entity_id", sa.String()),
        sa.column("status", sa.String()),
    )
    universe_table = sa.table(
        "portfolio_instrument_universe_record",
        sa.column("portfolio_id", sa.String()),
        sa.column("instrument_id", sa.String()),
        sa.column("instrument_ref_json", sa.JSON()),
        sa.column("source", sa.String()),
        sa.column("holding_state", sa.String()),
        sa.column("first_transaction_date", sa.Date()),
        sa.column("last_transaction_date", sa.Date()),
        sa.column("transaction_count", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("created_at", sa.String()),
        sa.column("updated_at", sa.String()),
    )

    transaction_rows = [
        dict(row._mapping)
        for row in connection.execute(
            sa.select(
                transaction_table.c.transaction_id,
                transaction_table.c.portfolio_id,
                transaction_table.c.transaction_type,
                transaction_table.c.trade_date,
                transaction_table.c.trade_at,
                transaction_table.c.settlement_date,
                transaction_table.c.instrument_id,
                transaction_table.c.instrument_ref_json,
                transaction_table.c.quantity,
                transaction_table.c.transfer_object_type,
                transaction_table.c.created_at,
            ).where(transaction_table.c.instrument_id.is_not(None))
        )
    ]
    rows_by_key: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in transaction_rows:
        portfolio_id = str(row.get("portfolio_id") or "").strip()
        instrument_id = str(row.get("instrument_id") or "").strip()
        if portfolio_id and instrument_id:
            rows_by_key[(portfolio_id, instrument_id)].append(row)

    now = _current_utc_timestamp()
    inserted_keys: set[tuple[str, str]] = set()
    for (portfolio_id, instrument_id), rows in sorted(rows_by_key.items()):
        latest = max(rows, key=_transaction_order_key)
        quantity = sum(_quantity_delta(row) for row in rows)
        trade_dates = [row.get("trade_date") for row in rows if row.get("trade_date") is not None]
        connection.execute(
            universe_table.insert().values(
                portfolio_id=portfolio_id,
                instrument_id=instrument_id,
                instrument_ref_json=latest.get("instrument_ref_json"),
                source="transaction",
                holding_state="held" if quantity > 1e-9 else "not_held",
                first_transaction_date=min(trade_dates) if trade_dates else None,
                last_transaction_date=max(trade_dates) if trade_dates else None,
                transaction_count=len(rows),
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        inserted_keys.add((portfolio_id, instrument_id))

    assignment_rows = [
        dict(row._mapping)
        for row in connection.execute(
            sa.select(taxonomy_table.c.portfolio_id, assignment_table.c.target_entity_id)
            .select_from(assignment_table.join(taxonomy_table, taxonomy_table.c.taxonomy_id == assignment_table.c.taxonomy_id))
            .where(
                assignment_table.c.target_scope == "instrument",
                assignment_table.c.status == "active",
            )
        )
    ]
    for row in assignment_rows:
        portfolio_id = str(row.get("portfolio_id") or "").strip()
        instrument_id = str(row.get("target_entity_id") or "").strip()
        key = (portfolio_id, instrument_id)
        if not portfolio_id or not instrument_id or key in inserted_keys:
            continue
        connection.execute(
            universe_table.insert().values(
                portfolio_id=portfolio_id,
                instrument_id=instrument_id,
                instrument_ref_json=None,
                source="taxonomy",
                holding_state="not_held",
                first_transaction_date=None,
                last_transaction_date=None,
                transaction_count=0,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        inserted_keys.add(key)


def downgrade() -> None:
    op.drop_index("ix_portfolio_instrument_universe_source", table_name="portfolio_instrument_universe_record")
    op.drop_index("ix_portfolio_instrument_universe_state", table_name="portfolio_instrument_universe_record")
    op.drop_table("portfolio_instrument_universe_record")
