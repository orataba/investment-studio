"""rename daily snapshot TWR columns

Revision ID: 20260505_0020
Revises: 20260504_0019
Create Date: 2026-05-05 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260505_0020"
down_revision = "20260504_0019"
branch_labels = None
depends_on = None


def _table_columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _rename_or_remove_legacy_column(
    table_name: str,
    *,
    legacy_name: str,
    canonical_name: str,
) -> None:
    columns = _table_columns(table_name)
    if legacy_name not in columns:
        return
    if canonical_name not in columns:
        op.alter_column(table_name, legacy_name, new_column_name=canonical_name)
        return
    op.execute(
        sa.text(
            f"UPDATE {table_name} "
            f"SET {canonical_name} = COALESCE({canonical_name}, {legacy_name})"
        )
    )
    op.drop_column(table_name, legacy_name)


def upgrade() -> None:
    _rename_or_remove_legacy_column(
        "portfolio_daily_snapshot",
        legacy_name="daily_ttwror",
        canonical_name="daily_twr",
    )
    _rename_or_remove_legacy_column(
        "portfolio_daily_snapshot",
        legacy_name="cumulative_ttwror",
        canonical_name="cumulative_twr",
    )
    op.execute("UPDATE portfolio_calculation_state SET daily_snapshot_status = 'stale', error_message = NULL")


def downgrade() -> None:
    columns = _table_columns("portfolio_daily_snapshot")
    if "daily_twr" in columns and "daily_ttwror" not in columns:
        op.alter_column("portfolio_daily_snapshot", "daily_twr", new_column_name="daily_ttwror")
    if "cumulative_twr" in columns and "cumulative_ttwror" not in columns:
        op.alter_column("portfolio_daily_snapshot", "cumulative_twr", new_column_name="cumulative_ttwror")
    op.execute("UPDATE portfolio_calculation_state SET daily_snapshot_status = 'stale', error_message = NULL")
