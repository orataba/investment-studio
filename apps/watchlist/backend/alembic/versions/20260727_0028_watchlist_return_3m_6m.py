"""Project 3M and 6M calculation-series returns into Watchlist rows.

Revision ID: 20260727_0028
Revises: 20260716_0027
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260727_0028"
down_revision = "20260716_0027"
branch_labels = None
depends_on = None


RETURN_FIELDS = (
    {
        "field_key": "return_3m",
        "label": "3M",
        "description": "3-month return from the selected calculation series.",
        "category_code": "performance_risk",
        "data_type": "number",
        "formatter_code": "percent",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "none",
        "instrument_scope_json": ["fund", "etf", "index"],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["performance_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "performance_snapshot.return_3m",
        "default_width": 150,
        "default_visible": True,
    },
    {
        "field_key": "return_6m",
        "label": "6M",
        "description": "6-month return from the selected calculation series.",
        "category_code": "performance_risk",
        "data_type": "number",
        "formatter_code": "percent",
        "sort_mode": "numeric",
        "filter_mode": "range",
        "group_mode": "none",
        "instrument_scope_json": ["fund", "etf", "index"],
        "product_scope_json": [],
        "availability_rule_json": {"requires": ["performance_snapshot"]},
        "source_domain": "snapshot",
        "source_metric_code": "performance_snapshot.return_6m",
        "default_width": 150,
        "default_visible": True,
    },
)


def upgrade() -> None:
    op.add_column(
        "watchlist_row_read_model",
        sa.Column("return_3m", sa.Numeric(precision=12, scale=6), nullable=True),
    )
    op.add_column(
        "watchlist_row_read_model",
        sa.Column("return_6m", sa.Numeric(precision=12, scale=6), nullable=True),
    )

    connection = op.get_bind()
    metadata = sa.MetaData()
    field_table = sa.Table("field_registry", metadata, autoload_with=connection)
    performance_table = sa.Table(
        "performance_snapshot", metadata, autoload_with=connection
    )
    row_table = sa.Table(
        "watchlist_row_read_model", metadata, autoload_with=connection
    )

    for payload in RETURN_FIELDS:
        connection.execute(
            sa.delete(field_table).where(
                field_table.c.field_key == payload["field_key"]
            )
        )
        connection.execute(sa.insert(field_table).values(**payload))

    current_3m = (
        sa.select(performance_table.c.return_3m)
        .where(
            performance_table.c.instrument_id == row_table.c.instrument_id,
            performance_table.c.is_current.is_(True),
        )
        .order_by(performance_table.c.as_of_date.desc())
        .limit(1)
        .scalar_subquery()
    )
    current_6m = (
        sa.select(performance_table.c.return_6m)
        .where(
            performance_table.c.instrument_id == row_table.c.instrument_id,
            performance_table.c.is_current.is_(True),
        )
        .order_by(performance_table.c.as_of_date.desc())
        .limit(1)
        .scalar_subquery()
    )
    connection.execute(
        sa.update(row_table).values(return_3m=current_3m, return_6m=current_6m)
    )


def downgrade() -> None:
    connection = op.get_bind()
    metadata = sa.MetaData()
    field_table = sa.Table("field_registry", metadata, autoload_with=connection)
    connection.execute(
        sa.delete(field_table).where(
            field_table.c.field_key.in_(["return_3m", "return_6m"])
        )
    )
    op.drop_column("watchlist_row_read_model", "return_6m")
    op.drop_column("watchlist_row_read_model", "return_3m")
