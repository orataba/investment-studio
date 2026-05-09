"""add research calculation frequency

Revision ID: 20260510_0021
Revises: 20260505_0020
Create Date: 2026-05-10 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260510_0021"
down_revision = "20260505_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_settings_record",
        sa.Column("calculation_frequency", sa.String(), nullable=False, server_default="auto"),
    )


def downgrade() -> None:
    op.drop_column("research_settings_record", "calculation_frequency")
