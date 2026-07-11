"""Add transaction id allocation and source freshness state.

Revision ID: 20260711_0030
Revises: 20260603_0029
Create Date: 2026-07-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260711_0030"
down_revision: str | None = "20260603_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _next_transaction_number() -> int:
    connection = op.get_bind()
    transaction_record = sa.table(
        "transaction_record",
        sa.column("transaction_id", sa.String()),
    )
    next_number = 1
    for transaction_id in connection.execute(sa.select(transaction_record.c.transaction_id)).scalars():
        normalized = str(transaction_id or "")
        if not normalized.startswith("txn-"):
            continue
        try:
            next_number = max(next_number, int(normalized.split("-", 1)[1]) + 1)
        except ValueError:
            continue
    return next_number


def upgrade() -> None:
    with op.batch_alter_table("portfolio_record") as batch_op:
        batch_op.alter_column("nav", existing_type=sa.Float(), nullable=True)
        batch_op.alter_column("day_change_value", existing_type=sa.Float(), nullable=True)
        batch_op.alter_column("day_change_pct", existing_type=sa.Float(), nullable=True)
    op.add_column(
        "portfolio_calculation_state",
        sa.Column("source_market_data_updated_at", sa.String(), nullable=True),
    )
    op.create_table(
        "transaction_id_allocator",
        sa.Column("allocator_key", sa.String(), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("allocator_key", name=op.f("pk_transaction_id_allocator")),
    )
    allocator = sa.table(
        "transaction_id_allocator",
        sa.column("allocator_key", sa.String()),
        sa.column("next_value", sa.Integer()),
    )
    op.get_bind().execute(
        sa.insert(allocator).values(
            allocator_key="transaction",
            next_value=_next_transaction_number(),
        )
    )


def downgrade() -> None:
    op.execute("UPDATE portfolio_record SET nav = 0 WHERE nav IS NULL")
    op.execute("UPDATE portfolio_record SET day_change_value = 0 WHERE day_change_value IS NULL")
    op.execute("UPDATE portfolio_record SET day_change_pct = 0 WHERE day_change_pct IS NULL")
    op.drop_table("transaction_id_allocator")
    op.drop_column("portfolio_calculation_state", "source_market_data_updated_at")
    with op.batch_alter_table("portfolio_record") as batch_op:
        batch_op.alter_column("nav", existing_type=sa.Float(), nullable=False)
        batch_op.alter_column("day_change_value", existing_type=sa.Float(), nullable=False)
        batch_op.alter_column("day_change_pct", existing_type=sa.Float(), nullable=False)
