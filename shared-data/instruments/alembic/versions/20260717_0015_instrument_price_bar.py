"""Store canonical raw OHLCV bars for exchange-traded instruments.

Revision ID: 20260717_0015
Revises: 20260717_0014
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260717_0015"
down_revision = "20260717_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instrument_price_bar",
        sa.Column(
            "instrument_price_bar_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("open_price", sa.Text(), nullable=False),
        sa.Column("high_price", sa.Text(), nullable=False),
        sa.Column("low_price", sa.Text(), nullable=False),
        sa.Column("close_price", sa.Text(), nullable=False),
        sa.Column("previous_close", sa.Text(), nullable=True),
        sa.Column("volume", sa.Text(), nullable=True),
        sa.Column("turnover", sa.Text(), nullable=True),
        sa.Column("adjustment_factor", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("volume_unit", sa.String(), nullable=True),
        sa.Column("turnover_unit", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.CheckConstraint(
            "CAST(open_price AS NUMERIC) > 0 "
            "AND CAST(high_price AS NUMERIC) > 0 "
            "AND CAST(low_price AS NUMERIC) > 0 "
            "AND CAST(close_price AS NUMERIC) > 0 "
            "AND CAST(high_price AS NUMERIC) >= CAST(open_price AS NUMERIC) "
            "AND CAST(high_price AS NUMERIC) >= CAST(close_price AS NUMERIC) "
            "AND CAST(low_price AS NUMERIC) <= CAST(open_price AS NUMERIC) "
            "AND CAST(low_price AS NUMERIC) <= CAST(close_price AS NUMERIC)",
            name=op.f("ck_instrument_price_bar_instrument_price_bar_ohlc_contract"),
        ),
        sa.CheckConstraint(
            "previous_close IS NULL OR CAST(previous_close AS NUMERIC) > 0",
            name=op.f(
                "ck_instrument_price_bar_instrument_price_bar_previous_close_contract"
            ),
        ),
        sa.CheckConstraint(
            "volume IS NULL OR CAST(volume AS NUMERIC) >= 0",
            name=op.f("ck_instrument_price_bar_instrument_price_bar_volume_contract"),
        ),
        sa.CheckConstraint(
            "turnover IS NULL OR CAST(turnover AS NUMERIC) >= 0",
            name=op.f("ck_instrument_price_bar_instrument_price_bar_turnover_contract"),
        ),
        sa.CheckConstraint(
            "adjustment_factor IS NULL OR CAST(adjustment_factor AS NUMERIC) > 0",
            name=op.f(
                "ck_instrument_price_bar_instrument_price_bar_adjustment_factor_contract"
            ),
        ),
        sa.CheckConstraint(
            "currency = upper(trim(currency)) AND length(currency) BETWEEN 1 AND 8",
            name=op.f("ck_instrument_price_bar_instrument_price_bar_currency_contract"),
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'partial')",
            name=op.f("ck_instrument_price_bar_instrument_price_bar_status_contract"),
        ),
        sa.CheckConstraint(
            "length(trim(provider)) > 0 "
            "AND ((volume IS NULL AND volume_unit IS NULL) "
            "OR (volume IS NOT NULL AND length(trim(volume_unit)) > 0)) "
            "AND ((turnover IS NULL AND turnover_unit IS NULL) "
            "OR (turnover IS NOT NULL AND length(trim(turnover_unit)) > 0))",
            name=op.f("ck_instrument_price_bar_instrument_price_bar_source_contract"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.instrument_id"],
            name=op.f("fk_instrument_price_bar_instrument_id_instrument"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instrument_price_bar_id",
            name=op.f("pk_instrument_price_bar"),
        ),
        sa.UniqueConstraint(
            "instrument_id",
            "as_of_date",
            name="uq_instrument_price_bar_instrument_date",
        ),
    )
    op.create_index(
        "ix_instrument_price_bar_instrument_date",
        "instrument_price_bar",
        ["instrument_id", "as_of_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_instrument_price_bar_instrument_date",
        table_name="instrument_price_bar",
    )
    op.drop_table("instrument_price_bar")
