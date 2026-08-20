"""Add the portfolio opening-balance boundary.

Revision ID: 20260820_0054
Revises: 20260818_0053
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_0054"
down_revision: str | None = "20260818_0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    invalid_openings = connection.execute(
        sa.text(
            "SELECT transaction_record.transaction_id "
            "FROM transaction_record "
            "JOIN portfolio_record ON portfolio_record.portfolio_id = "
            "transaction_record.portfolio_id "
            "WHERE transaction_record.transaction_type = 'opening_balance' "
            "AND (transaction_record.trade_date <> COALESCE("
            "(SELECT MIN(candidate.trade_date) FROM transaction_record AS candidate "
            "WHERE candidate.portfolio_id = portfolio_record.portfolio_id), "
            "portfolio_record.as_of_date, CURRENT_DATE) "
            "OR transaction_record.settlement_date <> COALESCE("
            "(SELECT MIN(candidate.trade_date) FROM transaction_record AS candidate "
            "WHERE candidate.portfolio_id = portfolio_record.portfolio_id), "
            "portfolio_record.as_of_date, CURRENT_DATE)) "
            "ORDER BY transaction_record.transaction_id"
        )
    ).scalars().all()
    if invalid_openings:
        transaction_ids = ", ".join(str(item) for item in invalid_openings)
        raise RuntimeError(
            "Opening balances outside the inferred portfolio inception date must "
            f"be corrected before migration: {transaction_ids}"
        )

    op.add_column(
        "portfolio_record",
        sa.Column("inception_date", sa.Date(), nullable=True),
    )
    connection.execute(
        sa.text(
            "UPDATE portfolio_record SET inception_date = COALESCE("
            "(SELECT MIN(transaction_record.trade_date) FROM transaction_record "
            "WHERE transaction_record.portfolio_id = portfolio_record.portfolio_id), "
            "as_of_date, CURRENT_DATE)"
        )
    )

    with op.batch_alter_table("portfolio_record") as batch_op:
        batch_op.alter_column(
            "inception_date",
            existing_type=sa.Date(),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("portfolio_record") as batch_op:
        batch_op.drop_column("inception_date")
