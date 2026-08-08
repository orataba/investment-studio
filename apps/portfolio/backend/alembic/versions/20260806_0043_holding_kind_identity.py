"""Make holding kind part of materialized holding identity.

The holding table is a rebuildable read model. Existing rows are discarded so
canonical instrument ids can represent both a long position and a written
option obligation without encoding the row kind into the instrument id.

Revision ID: 20260806_0043
Revises: 20260804_0042
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260806_0043"
down_revision = "20260804_0042"
branch_labels = None
depends_on = None


TABLE = "portfolio_daily_holding_snapshot"
PORTFOLIO_DATE_INDEX = "ix_portfolio_daily_holding_portfolio_date"
INSTRUMENT_DATE_INDEX = "ix_portfolio_daily_holding_instrument_date"
ACCOUNT_DATE_INDEX = "ix_portfolio_daily_holding_account_date"
PORTFOLIO_FOREIGN_KEY = "fk_portfolio_holding_snapshot_portfolio"


def _create_holding_table(*, include_holding_kind: bool) -> None:
    identity_columns = [
        "portfolio_id",
        "as_of_date",
        "account_id",
        "instrument_id",
    ]
    columns: list[sa.Column[object] | sa.Constraint] = [
        sa.Column(
            "portfolio_id",
            sa.String(),
            nullable=False,
        ),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("instrument_id", sa.String(), nullable=False),
    ]
    if include_holding_kind:
        columns.append(sa.Column("holding_kind", sa.String(), nullable=False))
        identity_columns.append("holding_kind")
    columns.extend(
        [
            sa.Column("currency", sa.String(), nullable=False),
            sa.Column("quantity", sa.Float(), nullable=False),
            sa.Column("cost_basis", sa.Float(), nullable=True),
            sa.Column("cost_basis_base", sa.Float(), nullable=True),
            sa.Column("last_price", sa.Float(), nullable=True),
            sa.Column("market_value", sa.Float(), nullable=True),
            sa.Column("market_value_base", sa.Float(), nullable=True),
            sa.Column("portfolio_weight", sa.Float(), nullable=True),
            sa.Column("holding_json", sa.JSON(), nullable=False),
            sa.Column("calculated_at", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(
                ["portfolio_id"],
                ["portfolio_record.portfolio_id"],
                name=PORTFOLIO_FOREIGN_KEY,
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint(*identity_columns),
        ]
    )
    op.create_table(TABLE, *columns)
    op.create_index(PORTFOLIO_DATE_INDEX, TABLE, ["portfolio_id", "as_of_date"])
    op.create_index(
        INSTRUMENT_DATE_INDEX,
        TABLE,
        ["portfolio_id", "instrument_id", "as_of_date"],
    )
    op.create_index(
        ACCOUNT_DATE_INDEX,
        TABLE,
        ["portfolio_id", "account_id", "as_of_date"],
    )


def _discard_materialized_holdings(*, include_holding_kind: bool) -> None:
    op.drop_table(TABLE)
    _create_holding_table(include_holding_kind=include_holding_kind)
    op.execute(
        "UPDATE portfolio_calculation_state "
        "SET daily_snapshot_status = 'stale', error_message = NULL"
    )


def upgrade() -> None:
    _discard_materialized_holdings(include_holding_kind=True)


def downgrade() -> None:
    _discard_materialized_holdings(include_holding_kind=False)
