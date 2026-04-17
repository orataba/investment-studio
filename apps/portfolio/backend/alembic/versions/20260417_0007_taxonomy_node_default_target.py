"""add default target dimension to taxonomy nodes

Revision ID: 20260417_0007
Revises: 20260417_0006
Create Date: 2026-04-17 20:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260417_0007"
down_revision = "20260417_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "taxonomy_node_record",
        sa.Column("default_target_dimension", sa.String(), nullable=False, server_default="weight"),
    )
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.alter_column("taxonomy_node_record", "default_target_dimension", server_default=None)


def downgrade() -> None:
    op.drop_column("taxonomy_node_record", "default_target_dimension")
