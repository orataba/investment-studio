"""add frozen taxonomy node ids to research settings

Revision ID: 20260423_0015
Revises: 20260423_0014
Create Date: 2026-04-23 23:25:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0015"
down_revision = "20260423_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.add_column(sa.Column("frozen_taxonomy_node_ids_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("research_settings_record") as batch_op:
        batch_op.drop_column("frozen_taxonomy_node_ids_json")
