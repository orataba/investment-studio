"""Add append-only taxonomy assignment history.

Revision ID: 20260813_0039
Revises: 20260813_0038
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260813_0039"
down_revision = "20260813_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instrument_taxonomy_assignment_history",
        sa.Column("history_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", sa.String(), nullable=False),
        sa.Column("taxonomy_code", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=True),
        sa.Column("path_labels_json", sa.JSON(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_record_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument_detail.instrument_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("history_id"),
    )
    op.create_index(
        "idx_instrument_taxonomy_assignment_history_instrument_time",
        "instrument_taxonomy_assignment_history",
        ["instrument_id", "taxonomy_code", "assigned_at"],
        unique=False,
    )

    bind = op.get_bind()
    assignment = sa.table(
        "instrument_taxonomy_assignment",
        sa.column("instrument_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("node_id", sa.String()),
        sa.column("assigned_at", sa.DateTime(timezone=True)),
        sa.column("source_record_id", sa.String()),
    )
    node = sa.table(
        "instrument_taxonomy_node",
        sa.column("node_id", sa.String()),
        sa.column("path_labels_json", sa.JSON()),
    )
    history = sa.table(
        "instrument_taxonomy_assignment_history",
        sa.column("instrument_id", sa.String()),
        sa.column("taxonomy_code", sa.String()),
        sa.column("node_id", sa.String()),
        sa.column("path_labels_json", sa.JSON()),
        sa.column("assigned_at", sa.DateTime(timezone=True)),
        sa.column("source_record_id", sa.String()),
    )
    rows = bind.execute(
        sa.select(
            assignment.c.instrument_id,
            assignment.c.taxonomy_code,
            assignment.c.node_id,
            assignment.c.assigned_at,
            assignment.c.source_record_id,
            node.c.path_labels_json,
        ).select_from(
            assignment.outerjoin(node, assignment.c.node_id == node.c.node_id)
        )
    ).mappings()
    for row in rows:
        bind.execute(
            sa.insert(history).values(
                instrument_id=row["instrument_id"],
                taxonomy_code=row["taxonomy_code"],
                node_id=row["node_id"],
                path_labels_json=row["path_labels_json"] or [],
                assigned_at=row["assigned_at"],
                source_record_id=row["source_record_id"],
            )
        )


def downgrade() -> None:
    op.drop_index(
        "idx_instrument_taxonomy_assignment_history_instrument_time",
        table_name="instrument_taxonomy_assignment_history",
    )
    op.drop_table("instrument_taxonomy_assignment_history")
