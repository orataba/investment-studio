"""expand target set lines to direct scope members

Revision ID: 20260417_0008
Revises: 20260417_0007
Create Date: 2026-04-17 23:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260417_0008"
down_revision = "20260417_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("target_set_line_record") as batch_op:
        batch_op.add_column(
            sa.Column("target_member_type", sa.String(), nullable=False, server_default="taxonomy_node")
        )
        batch_op.add_column(sa.Column("target_member_id", sa.String(), nullable=True))
        batch_op.alter_column("taxonomy_node_id", existing_type=sa.String(), nullable=True)

    op.execute(
        sa.text(
            """
            UPDATE target_set_line_record
            SET target_member_type = 'taxonomy_node',
                target_member_id = taxonomy_node_id
            WHERE target_member_id IS NULL
            """
        )
    )

    with op.batch_alter_table("target_set_line_record") as batch_op:
        batch_op.alter_column("target_member_id", existing_type=sa.String(), nullable=False)
        batch_op.create_index(
            "ix_target_set_line_record_target_set_member",
            ["target_set_id", "target_member_type", "target_member_id"],
            unique=True,
        )

    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        with op.batch_alter_table("target_set_line_record") as batch_op:
            batch_op.alter_column("target_member_type", server_default=None)


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE target_set_line_record
            SET taxonomy_node_id = target_member_id
            WHERE target_member_type = 'taxonomy_node'
            """
        )
    )

    with op.batch_alter_table("target_set_line_record") as batch_op:
        batch_op.drop_index("ix_target_set_line_record_target_set_member")
        batch_op.drop_column("target_member_id")
        batch_op.drop_column("target_member_type")
        batch_op.alter_column("taxonomy_node_id", existing_type=sa.String(), nullable=False)
