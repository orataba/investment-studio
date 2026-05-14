"""persist portfolio table view stores

Revision ID: 20260514_0024
Revises: 20260511_0023
Create Date: 2026-05-14 00:25:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260514_0024"
down_revision = "20260511_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_table_view_store",
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("view_scope", sa.String(), nullable=False),
        sa.Column("store_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio_record.portfolio_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("portfolio_id", "view_scope"),
    )


def downgrade() -> None:
    op.drop_table("portfolio_table_view_store")
