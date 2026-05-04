"""coordinate portfolio daily snapshot refreshes

Revision ID: 20260504_0019
Revises: 20260503_0018
Create Date: 2026-05-04 19:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260504_0019"
down_revision = "20260503_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portfolio_calculation_state", sa.Column("refresh_request_id", sa.String(), nullable=True))
    op.add_column("portfolio_calculation_state", sa.Column("refresh_started_at", sa.String(), nullable=True))
    op.add_column("portfolio_calculation_state", sa.Column("refresh_completed_at", sa.String(), nullable=True))
    op.execute("UPDATE portfolio_calculation_state SET daily_snapshot_status = 'stale', error_message = NULL")


def downgrade() -> None:
    op.drop_column("portfolio_calculation_state", "refresh_completed_at")
    op.drop_column("portfolio_calculation_state", "refresh_started_at")
    op.drop_column("portfolio_calculation_state", "refresh_request_id")
