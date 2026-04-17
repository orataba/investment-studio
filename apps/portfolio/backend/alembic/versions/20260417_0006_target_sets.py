"""add target set tables

Revision ID: 20260417_0006
Revises: 20260417_0005
Create Date: 2026-04-17 16:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260417_0006"
down_revision = "20260417_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "target_set_record",
        sa.Column("target_set_id", sa.String(), nullable=False),
        sa.Column("taxonomy_id", sa.String(), nullable=False),
        sa.Column("comparator_taxonomy_node_id", sa.String(), nullable=True),
        sa.Column("target_set_type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("weight_enabled", sa.Boolean(), nullable=False),
        sa.Column("risk_budget_enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["taxonomy_id"], ["taxonomy_record.taxonomy_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("target_set_id"),
    )
    op.create_index(
        "ix_target_set_record_taxonomy_scope_type_effective",
        "target_set_record",
        ["taxonomy_id", "comparator_taxonomy_node_id", "target_set_type", "effective_from", "target_set_id"],
        unique=False,
    )

    op.create_table(
        "target_set_line_record",
        sa.Column("target_line_id", sa.String(), nullable=False),
        sa.Column("target_set_id", sa.String(), nullable=False),
        sa.Column("taxonomy_node_id", sa.String(), nullable=False),
        sa.Column("target_weight", sa.Float(), nullable=True),
        sa.Column("target_risk_share", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["target_set_id"], ["target_set_record.target_set_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("target_line_id"),
    )
    op.create_index(
        "ix_target_set_line_record_target_set_node",
        "target_set_line_record",
        ["target_set_id", "taxonomy_node_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_target_set_line_record_target_set_node", table_name="target_set_line_record")
    op.drop_table("target_set_line_record")
    op.drop_index("ix_target_set_record_taxonomy_scope_type_effective", table_name="target_set_record")
    op.drop_table("target_set_record")
