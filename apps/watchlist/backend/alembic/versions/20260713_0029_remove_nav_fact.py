"""Remove the empty duplicate NAV fact store and rename its row watermark.

Revision ID: 20260713_0029
Revises: 20260713_0028
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260713_0029"
down_revision = "20260713_0028"
branch_labels = None
depends_on = None


_OLD_COLUMN = "last_fact_update_at"
_NEW_COLUMN = "market_data_input_watermark_at"
_RETURN_YTD_DESCRIPTION = "Derived from canonical total-return quote resolution."


def _column_names(connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        raise RuntimeError(
            f"Required table {table_name!r} is missing; refusing partial NAV fact cleanup."
        )
    return {str(column["name"]) for column in inspector.get_columns(table_name)}


def _rename_row_watermark(*, old_name: str, new_name: str) -> None:
    connection = op.get_bind()
    columns = _column_names(connection, "watchlist_row_read_model")
    if old_name not in columns or new_name in columns:
        raise RuntimeError(
            "watchlist_row_read_model must contain "
            f"{old_name!r} and not {new_name!r}; refusing a partial watermark rename."
        )
    with op.batch_alter_table("watchlist_row_read_model") as batch_op:
        batch_op.alter_column(
            old_name,
            new_column_name=new_name,
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if not inspector.has_table("nav_fact"):
        raise RuntimeError("nav_fact is missing; refusing a partial migration.")
    nav_fact_count = connection.scalar(sa.text("SELECT COUNT(*) FROM nav_fact"))
    if int(nav_fact_count or 0) != 0:
        raise RuntimeError(
            "nav_fact still contains rows. Import required observations into the "
            "canonical quote store and empty nav_fact before migrating."
        )
    if not inspector.has_table("field_registry"):
        raise RuntimeError(
            "field_registry is missing; refusing a partial NAV fact cleanup."
        )
    return_ytd_count = connection.scalar(
        sa.text(
            "SELECT COUNT(*) FROM field_registry WHERE field_key = 'return_ytd'"
        )
    )
    if int(return_ytd_count or 0) != 1:
        raise RuntimeError(
            "field_registry must contain exactly one return_ytd row; refusing a "
            "partial NAV fact cleanup."
        )

    _rename_row_watermark(old_name=_OLD_COLUMN, new_name=_NEW_COLUMN)
    op.drop_table("nav_fact")
    update_result = connection.execute(
        sa.text(
            "UPDATE field_registry "
            "SET description = :description "
            "WHERE field_key = 'return_ytd'"
        ),
        {"description": _RETURN_YTD_DESCRIPTION},
    )
    if update_result.rowcount != 1:
        raise RuntimeError(
            "return_ytd field metadata update did not affect exactly one row."
        )


def downgrade() -> None:
    raise RuntimeError(
        "20260713_0029 is intentionally irreversible: the duplicate NavFact "
        "boundary and its legacy field are not recreated. Restore a "
        "pre-migration backup if rollback is required."
    )
