"""Add explicit option physical-delivery facts.

Revision ID: 20260902_0058
Revises: 20260902_0057
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260902_0058"
down_revision: str | None = "20260902_0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LIFECYCLE_CHECK_NAME = "lifecycle_event_type"
LIFECYCLE_CHECK = (
    "lifecycle_event_type IS NULL OR lifecycle_event_type IN ("
    "'fcn_knock_in', 'fcn_knock_out', 'fcn_maturity', "
    "'option_long_expiry', 'option_long_cash_settlement', "
    "'option_long_exercise', 'option_writer_expiry', "
    "'option_writer_cash_settlement', 'option_writer_assignment')"
)


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SET LOCAL lock_timeout = '30s'"))

    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.drop_constraint(LIFECYCLE_CHECK_NAME, type_="check")
        batch_op.create_check_constraint(
            LIFECYCLE_CHECK_NAME,
            LIFECYCLE_CHECK,
        )

    op.create_table(
        "option_delivery_link",
        sa.Column("option_transaction_id", sa.String(), nullable=False),
        sa.Column("stock_transaction_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("underlying_instrument_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint(
            "option_transaction_id <> stock_transaction_id",
            name="ck_option_delivery_link_distinct_transactions",
        ),
        sa.ForeignKeyConstraint(
            ["option_transaction_id"],
            ["transaction_record.transaction_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stock_transaction_id"],
            ["transaction_record.transaction_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"],
            ["portfolio_record.portfolio_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("option_transaction_id"),
        sa.UniqueConstraint(
            "stock_transaction_id",
            name="uq_option_delivery_link_stock_transaction",
        ),
    )
    op.create_index(
        "ix_option_delivery_link_portfolio_underlying",
        "option_delivery_link",
        ["portfolio_id", "underlying_instrument_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    link_count = connection.scalar(
        sa.text("SELECT count(*) FROM option_delivery_link")
    )
    physical_event_count = connection.scalar(
        sa.text(
            "SELECT count(*) FROM transaction_record "
            "WHERE lifecycle_event_type IN "
            "('option_long_exercise', 'option_writer_assignment')"
        )
    )
    if link_count or physical_event_count:
        raise RuntimeError(
            "Option physical-delivery facts exist. Restore a pre-migration backup "
            "instead of dropping their relationship semantics."
        )

    op.drop_index(
        "ix_option_delivery_link_portfolio_underlying",
        table_name="option_delivery_link",
    )
    op.drop_table("option_delivery_link")
    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.drop_constraint(LIFECYCLE_CHECK_NAME, type_="check")
        batch_op.create_check_constraint(
            LIFECYCLE_CHECK_NAME,
            "lifecycle_event_type IS NULL OR lifecycle_event_type IN ("
            "'fcn_knock_in', 'fcn_knock_out', 'fcn_maturity', "
            "'option_long_expiry', 'option_long_cash_settlement', "
            "'option_writer_expiry', 'option_writer_cash_settlement')",
        )
