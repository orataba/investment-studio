"""collapse taxonomy effective windows to current state

Revision ID: 20260527_0026
Revises: 20260526_0025
Create Date: 2026-05-27 00:26:00
"""

from __future__ import annotations

from datetime import date

from alembic import op
import sqlalchemy as sa


revision = "20260527_0026"
down_revision = "20260526_0025"
branch_labels = None
depends_on = None


def _latest_row(rows: list[dict[str, object]], *, id_column: str) -> dict[str, object]:
    return max(rows, key=lambda item: (item.get("effective_from") or date.min, str(item.get(id_column) or "")))


def upgrade() -> None:
    connection = op.get_bind()

    assignment_table = sa.table(
        "taxonomy_assignment_record",
        sa.column("assignment_id", sa.String()),
        sa.column("taxonomy_id", sa.String()),
        sa.column("target_scope", sa.String()),
        sa.column("target_entity_id", sa.String()),
        sa.column("effective_from", sa.Date()),
        sa.column("status", sa.String()),
    )
    target_set_table = sa.table(
        "target_set_record",
        sa.column("target_set_id", sa.String()),
        sa.column("taxonomy_id", sa.String()),
        sa.column("comparator_taxonomy_node_id", sa.String()),
        sa.column("target_set_type", sa.String()),
        sa.column("effective_from", sa.Date()),
        sa.column("status", sa.String()),
    )
    target_set_line_table = sa.table(
        "target_set_line_record",
        sa.column("target_set_id", sa.String()),
    )

    assignment_rows = [
        dict(row._mapping)
        for row in connection.execute(
            sa.select(
                assignment_table.c.assignment_id,
                assignment_table.c.taxonomy_id,
                assignment_table.c.target_scope,
                assignment_table.c.target_entity_id,
                assignment_table.c.effective_from,
                assignment_table.c.status,
            )
            .where(assignment_table.c.status == "active")
        )
    ]
    assignments_by_target: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for row in assignment_rows:
        key = (
            str(row.get("taxonomy_id") or ""),
            str(row.get("target_scope") or ""),
            str(row.get("target_entity_id") or ""),
        )
        assignments_by_target.setdefault(key, []).append(row)
    for rows in assignments_by_target.values():
        keeper = _latest_row(rows, id_column="assignment_id")
        for row in rows:
            if row.get("assignment_id") == keeper.get("assignment_id"):
                continue
            connection.execute(
                assignment_table.delete()
                .where(assignment_table.c.assignment_id == row.get("assignment_id"))
            )

    target_set_rows = [
        dict(row._mapping)
        for row in connection.execute(
            sa.select(
                target_set_table.c.target_set_id,
                target_set_table.c.taxonomy_id,
                target_set_table.c.comparator_taxonomy_node_id,
                target_set_table.c.target_set_type,
                target_set_table.c.effective_from,
                target_set_table.c.status,
            )
            .where(target_set_table.c.status == "active")
        )
    ]
    target_sets_by_scope: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for row in target_set_rows:
        key = (
            str(row.get("taxonomy_id") or ""),
            str(row.get("comparator_taxonomy_node_id") or ""),
            str(row.get("target_set_type") or ""),
        )
        target_sets_by_scope.setdefault(key, []).append(row)
    for rows in target_sets_by_scope.values():
        keeper = _latest_row(rows, id_column="target_set_id")
        for row in rows:
            if row.get("target_set_id") == keeper.get("target_set_id"):
                continue
            connection.execute(
                target_set_line_table.delete()
                .where(target_set_line_table.c.target_set_id == row.get("target_set_id"))
            )
            connection.execute(
                target_set_table.delete()
                .where(target_set_table.c.target_set_id == row.get("target_set_id"))
            )

    op.drop_index("ix_target_set_record_taxonomy_scope_type_effective", table_name="target_set_record")
    with op.batch_alter_table("taxonomy_record") as batch_op:
        batch_op.drop_column("effective_to")
        batch_op.drop_column("effective_from")
    with op.batch_alter_table("taxonomy_assignment_record") as batch_op:
        batch_op.drop_column("effective_to")
        batch_op.drop_column("effective_from")
    with op.batch_alter_table("target_set_record") as batch_op:
        batch_op.drop_column("effective_to")
        batch_op.drop_column("effective_from")
    op.create_index(
        "ix_target_set_record_taxonomy_scope_type",
        "target_set_record",
        ["taxonomy_id", "comparator_taxonomy_node_id", "target_set_type", "target_set_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_target_set_record_taxonomy_scope_type", table_name="target_set_record")
    with op.batch_alter_table("target_set_record") as batch_op:
        batch_op.add_column(sa.Column("effective_from", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("effective_to", sa.Date(), nullable=True))
    with op.batch_alter_table("taxonomy_assignment_record") as batch_op:
        batch_op.add_column(sa.Column("effective_from", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("effective_to", sa.Date(), nullable=True))
    with op.batch_alter_table("taxonomy_record") as batch_op:
        batch_op.add_column(sa.Column("effective_from", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("effective_to", sa.Date(), nullable=True))
    op.create_index(
        "ix_target_set_record_taxonomy_scope_type_effective",
        "target_set_record",
        ["taxonomy_id", "comparator_taxonomy_node_id", "target_set_type", "effective_from", "target_set_id"],
        unique=False,
    )
