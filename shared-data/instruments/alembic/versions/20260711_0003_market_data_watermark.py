"""Add a per-instrument market-data change watermark.

Revision ID: 20260711_0003
Revises: 20260603_0002
"""

from alembic import op
import sqlalchemy as sa


revision = "20260711_0003"
down_revision = "20260603_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "registry_metadata",
        sa.Column("market_data_updated_at", sa.String(), nullable=True),
    )
    op.add_column(
        "instrument",
        sa.Column("market_data_updated_at", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("instrument", "market_data_updated_at")
    op.drop_column("registry_metadata", "market_data_updated_at")
