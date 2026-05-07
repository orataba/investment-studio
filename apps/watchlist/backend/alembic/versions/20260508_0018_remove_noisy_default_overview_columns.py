"""remove noisy columns from default overview

Revision ID: 20260508_0018
Revises: 20260506_0017
Create Date: 2026-05-08 10:45:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260508_0018"
down_revision = "20260506_0017"
branch_labels = None
depends_on = None


REMOVED_OVERVIEW_FIELDS = (
    "attr.peer_overall_percentile",
    "data_freshness_status",
)


def _tables() -> tuple[sa.TableClause, sa.TableClause]:
    view_table = sa.table(
        "watchlist_view",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("kind", sa.String()),
    )
    column_table = sa.table(
        "watchlist_view_column",
        sa.column("watchlist_view_id", sa.String()),
        sa.column("field_key", sa.String()),
        sa.column("display_order", sa.Integer()),
        sa.column("width", sa.Integer()),
        sa.column("is_visible", sa.Boolean()),
        sa.column("pin_side", sa.String()),
    )
    return view_table, column_table


def _system_overview_view_ids(bind, view_table: sa.TableClause) -> list[str]:
    return [
        str(row["watchlist_view_id"])
        for row in bind.execute(
            sa.select(view_table.c.watchlist_view_id).where(
                view_table.c.kind == "system",
                sa.or_(
                    view_table.c.watchlist_view_id == "overview",
                    view_table.c.watchlist_view_id.like("%::overview"),
                ),
            )
        ).mappings()
    ]


def _resequence_view_columns(bind, column_table: sa.TableClause, view_id: str) -> None:
    rows = list(
        bind.execute(
            sa.select(column_table.c.field_key)
            .where(column_table.c.watchlist_view_id == view_id)
            .order_by(column_table.c.display_order, column_table.c.field_key)
        ).mappings()
    )
    for display_order, row in enumerate(rows, start=1):
        bind.execute(
            sa.update(column_table)
            .where(
                column_table.c.watchlist_view_id == view_id,
                column_table.c.field_key == row["field_key"],
            )
            .values(display_order=display_order)
        )


def upgrade() -> None:
    bind = op.get_bind()
    view_table, column_table = _tables()
    view_ids = _system_overview_view_ids(bind, view_table)
    if not view_ids:
        return

    bind.execute(
        sa.delete(column_table).where(
            column_table.c.watchlist_view_id.in_(view_ids),
            column_table.c.field_key.in_(REMOVED_OVERVIEW_FIELDS),
        )
    )
    for view_id in view_ids:
        _resequence_view_columns(bind, column_table, view_id)


def downgrade() -> None:
    bind = op.get_bind()
    view_table, column_table = _tables()
    view_ids = _system_overview_view_ids(bind, view_table)
    if not view_ids:
        return

    existing_columns = {
        (str(row["watchlist_view_id"]), str(row["field_key"]))
        for row in bind.execute(
            sa.select(column_table.c.watchlist_view_id, column_table.c.field_key).where(
                column_table.c.watchlist_view_id.in_(view_ids),
                column_table.c.field_key.in_(REMOVED_OVERVIEW_FIELDS),
            )
        ).mappings()
    }
    restore_rows = []
    for view_id in view_ids:
        if (view_id, "attr.peer_overall_percentile") not in existing_columns:
            restore_rows.append(
                {
                    "watchlist_view_id": view_id,
                    "field_key": "attr.peer_overall_percentile",
                    "display_order": 10,
                    "width": 120,
                    "is_visible": True,
                    "pin_side": None,
                }
            )
        if (view_id, "data_freshness_status") not in existing_columns:
            restore_rows.append(
                {
                    "watchlist_view_id": view_id,
                    "field_key": "data_freshness_status",
                    "display_order": 11,
                    "width": 140,
                    "is_visible": True,
                    "pin_side": None,
                }
            )
    if restore_rows:
        op.bulk_insert(column_table, restore_rows)
    for view_id in view_ids:
        _resequence_view_columns(bind, column_table, view_id)
