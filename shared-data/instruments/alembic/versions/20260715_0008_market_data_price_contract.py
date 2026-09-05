"""Add an explicit unit and scale contract to canonical market data.

Revision ID: 20260715_0008
Revises: 20260712_0007
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_0008"
down_revision = "20260712_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    legacy_bond_price_rows = op.get_bind().scalar(
        sa.text(
            """
            SELECT count(*)
            FROM instrument_market_data AS market_data
            JOIN instrument
              ON instrument.instrument_id = market_data.instrument_id
            WHERE lower(instrument.instrument_type) = 'bond'
              AND market_data.metric_family = 'price'
            """
        )
    )
    if int(legacy_bond_price_rows or 0) > 0:
        raise RuntimeError(
            "Cannot apply 20260715_0008: found "
            f"{legacy_bond_price_rows} legacy bond price row(s) without an "
            "explicit price contract; audit and normalize them before retrying."
        )

    op.add_column(
        "instrument_market_data",
        sa.Column("price_unit", sa.String(), nullable=True),
    )
    op.add_column(
        "instrument_market_data",
        sa.Column("price_scale", sa.Numeric(28, 12), nullable=True),
    )

    # Keep the columns nullable for additive rollout compatibility. Legacy bond
    # price rows are intentionally not guessed or converted: the preflight above
    # blocks them, and the WHERE clause leaves any concurrent arrival untouched.
    # FX instruments/series are rates; all remaining rows are per-unit.
    op.execute(
        sa.text(
            """
            UPDATE instrument_market_data
            SET
                price_unit = CASE
                    WHEN metric_family = 'fx'
                         OR quote_basis = 'spot'
                         OR EXISTS (
                             SELECT 1
                             FROM instrument
                             WHERE instrument.instrument_id = instrument_market_data.instrument_id
                               AND lower(instrument.instrument_type) = 'fx'
                         )
                        THEN 'rate'
                    ELSE 'per_unit'
                END,
                price_scale = 1
            WHERE NOT (
                metric_family = 'price'
                AND EXISTS (
                    SELECT 1
                    FROM instrument
                    WHERE instrument.instrument_id = instrument_market_data.instrument_id
                      AND lower(instrument.instrument_type) = 'bond'
                )
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("instrument_market_data", "price_scale")
    op.drop_column("instrument_market_data", "price_unit")
