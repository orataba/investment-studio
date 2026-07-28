"""Add an explicit position-recognition date to transaction facts.

Revision ID: 20260728_0041
Revises: 20260716_0040

Existing rows remain null and therefore retain their historical same-day
``trade_date`` behavior. A later date can be recorded for fund subscriptions,
redemptions, or other confirmed-later position facts without changing the
execution or pricing date.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_0041"
down_revision = "20260716_0040"
branch_labels = None
depends_on = None

CHECK_CONSTRAINT_NAME = "position_effective_date"


def upgrade() -> None:
    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.add_column(
            sa.Column("position_effective_date", sa.Date(), nullable=True),
        )
        batch_op.create_check_constraint(
            CHECK_CONSTRAINT_NAME,
            "position_effective_date IS NULL OR ("
            "transaction_type IN ('buy', 'sell', 'dividend_reinvestment', "
            "'maturity_redemption') "
            "AND position_effective_date >= trade_date"
            ")",
        )
        batch_op.create_index(
            "ix_transaction_record_portfolio_position_effective",
            ["portfolio_id", "position_effective_date"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("transaction_record") as batch_op:
        batch_op.drop_index(
            "ix_transaction_record_portfolio_position_effective",
        )
        batch_op.drop_constraint(
            CHECK_CONSTRAINT_NAME,
            type_="check",
        )
        batch_op.drop_column("position_effective_date")
