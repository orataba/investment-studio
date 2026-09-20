"""Cover latest-quote series selection without reading historical payloads.

Revision ID: 20260920_0036
Revises: 20260908_0035
"""
from alembic import op
import sqlalchemy as sa


revision = "20260920_0036"
down_revision = "20260908_0035"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_instrument_market_data_series_latest"


def upgrade():
    # The existing unique constraint remains the authority for point identity.
    # Only series keys/date belong in this index, not values or NAV evidence.
    op.create_index(
        INDEX_NAME,
        "instrument_market_data",
        ["instrument_id", "metric_family", "quote_basis", "currency",
         "price_unit", "price_scale", sa.text("as_of_date DESC")],
    )


def downgrade():
    op.drop_index(INDEX_NAME, table_name="instrument_market_data")
