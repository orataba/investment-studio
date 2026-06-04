"""add research top sleeve weight bounds

Revision ID: 20260603_0029
Revises: 20260602_0028
Create Date: 2026-06-03 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260603_0029"
down_revision = "20260602_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.add_column(sa.Column("top_sleeve_weight_bounds_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.drop_column("top_sleeve_weight_bounds_json")
