"""Classify listed ETFs separately from ordinary funds.

Revision ID: 20260712_0004
Revises: 20260711_0003
"""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260712_0004"
down_revision = "20260711_0003"
branch_labels = None
depends_on = None


ETF_QUOTE_POLICY = {
    "trading": ["last", "close"],
    "valuation": ["close", "last"],
    "total_return": ["adjusted_close", "close", "last"],
    "chart": ["adjusted_close", "close", "last"],
    "reference": ["close", "last"],
}

FUND_QUOTE_POLICY = {
    "trading": ["last", "close", "official_nav"],
    "valuation": ["official_nav", "close", "last"],
    "total_return": [
        "total_return_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
        "adjusted_close",
        "official_nav",
        "close",
    ],
    "chart": [
        "total_return_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
        "adjusted_close",
        "official_nav",
        "close",
    ],
    "reference": ["official_nav", "close", "last"],
}


instrument = sa.table(
    "instrument",
    sa.column("instrument_id", sa.String()),
    sa.column("instrument_name", sa.String()),
    sa.column("instrument_type", sa.String()),
    sa.column("quote_selection_policy_json", sa.JSON()),
    sa.column("market_data_updated_at", sa.String()),
)
market_data = sa.table(
    "instrument_market_data",
    sa.column("instrument_id", sa.String()),
    sa.column("metric_family", sa.String()),
    sa.column("quote_basis", sa.String()),
)
registry_metadata = sa.table(
    "registry_metadata",
    sa.column("registry_key", sa.String()),
    sa.column("market_data_updated_at", sa.String()),
)


def _watermark() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def upgrade() -> None:
    connection = op.get_bind()
    listed_etf_ids = list(
        connection.scalars(
            sa.select(instrument.c.instrument_id).where(
                instrument.c.instrument_type == "fund",
                sa.func.upper(instrument.c.instrument_name).like("%ETF%"),
                ~instrument.c.instrument_name.like("%ETF联接%"),
                sa.exists(
                    sa.select(1).where(
                        market_data.c.instrument_id == instrument.c.instrument_id,
                        market_data.c.metric_family == "price",
                        market_data.c.quote_basis == "close",
                    )
                ),
            )
        )
    )
    if not listed_etf_ids:
        return
    changed_at = _watermark()
    connection.execute(
        sa.update(instrument)
        .where(instrument.c.instrument_id.in_(listed_etf_ids))
        .values(
            instrument_type="etf",
            quote_selection_policy_json=ETF_QUOTE_POLICY,
            market_data_updated_at=changed_at,
        )
    )
    connection.execute(
        sa.update(registry_metadata)
        .where(registry_metadata.c.registry_key == "shared")
        .values(market_data_updated_at=changed_at)
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.update(instrument)
        .where(
            instrument.c.instrument_type == "etf",
            sa.func.upper(instrument.c.instrument_name).like("%ETF%"),
            ~instrument.c.instrument_name.like("%ETF联接%"),
        )
        .values(
            instrument_type="fund",
            quote_selection_policy_json=FUND_QUOTE_POLICY,
            market_data_updated_at=_watermark(),
        )
    )
