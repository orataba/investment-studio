"""Rename source cutoff to the canonical market-data input watermark.

Revision ID: 20260713_0028
Revises: 20260713_0027
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0028"
down_revision = "20260713_0027"
branch_labels = None
depends_on = None


_TABLES = (
    "instrument_summary_read_model",
    "instrument_chart_read_model",
    "instrument_performance_read_model",
    "instrument_risk_read_model",
    "performance_snapshot",
    "risk_snapshot",
)
_OLD_COLUMN = "source_cutoff_at"
_NEW_COLUMN = "market_data_input_watermark_at"


def _column_names(connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        raise RuntimeError(
            f"Required table {table_name!r} is missing; refusing a partial watermark rename."
        )
    return {str(column["name"]) for column in inspector.get_columns(table_name)}


def _rename(*, old_name: str, new_name: str) -> None:
    connection = op.get_bind()
    for table_name in _TABLES:
        columns = _column_names(connection, table_name)
        if old_name not in columns or new_name in columns:
            raise RuntimeError(
                f"{table_name} must contain {old_name!r} and not {new_name!r}; "
                "refusing a partial watermark rename."
            )
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                old_name,
                new_column_name=new_name,
                existing_type=sa.DateTime(timezone=True),
                nullable=True,
            )


def upgrade() -> None:
    _rename(old_name=_OLD_COLUMN, new_name=_NEW_COLUMN)


def downgrade() -> None:
    _rename(old_name=_NEW_COLUMN, new_name=_OLD_COLUMN)
