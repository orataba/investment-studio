"""add root default target dimension to taxonomies

Revision ID: 20260419_0011
Revises: 20260417_0010
Create Date: 2026-04-19 10:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260419_0011"
down_revision = "20260417_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "taxonomy_record",
        sa.Column("root_default_target_dimension", sa.String(), nullable=False, server_default="weight"),
    )
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.alter_column("taxonomy_record", "root_default_target_dimension", server_default=None)


def downgrade() -> None:
    op.drop_column("taxonomy_record", "root_default_target_dimension")
