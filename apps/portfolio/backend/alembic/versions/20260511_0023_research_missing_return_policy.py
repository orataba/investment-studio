"""add research missing return policy

Revision ID: 20260511_0023
Revises: 20260511_0022
Create Date: 2026-05-11 12:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260511_0023"
down_revision = "20260511_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_settings_record",
        sa.Column("missing_return_policy", sa.String(), nullable=False, server_default="strict"),
    )


def downgrade() -> None:
    op.drop_column("research_settings_record", "missing_return_policy")
