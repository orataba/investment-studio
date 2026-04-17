"""add default planning taxonomy to portfolio

Revision ID: 20260416_0004
Revises: 20260416_0003
Create Date: 2026-04-16 23:35:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260416_0004"
down_revision = "20260416_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portfolio_record", sa.Column("default_planning_taxonomy_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("portfolio_record", "default_planning_taxonomy_id")
