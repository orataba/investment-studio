"""Add ETFs to fund-like watchlist scopes.

Revision ID: 20260712_0024
Revises: 20260711_0023
"""

from alembic import op
import sqlalchemy as sa


revision = "20260712_0024"
down_revision = "20260711_0023"
branch_labels = None
depends_on = None


field_registry = sa.table(
    "field_registry",
    sa.column("field_key", sa.String()),
    sa.column("instrument_scope_json", sa.JSON()),
)
attribute_definition = sa.table(
    "instrument_attribute_definition",
    sa.column("attribute_key", sa.String()),
    sa.column("instrument_scope_json", sa.JSON()),
)
watchlist_view = sa.table(
    "watchlist_view",
    sa.column("watchlist_view_id", sa.String()),
    sa.column("default_filters_json", sa.JSON()),
)


def _update_scopes(*, add_etf: bool) -> None:
    connection = op.get_bind()
    for table, key_column in (
        (field_registry, field_registry.c.field_key),
        (attribute_definition, attribute_definition.c.attribute_key),
    ):
        for key, raw_scope in connection.execute(
            sa.select(key_column, table.c.instrument_scope_json)
        ):
            scope = [str(item) for item in (raw_scope or [])]
            if add_etf:
                if "fund" not in scope or "etf" in scope:
                    continue
                scope.insert(scope.index("fund") + 1, "etf")
            else:
                if "etf" not in scope:
                    continue
                scope = [item for item in scope if item != "etf"]
            connection.execute(
                sa.update(table).where(key_column == key).values(instrument_scope_json=scope)
            )

    for view_id, raw_filters in connection.execute(
        sa.select(watchlist_view.c.watchlist_view_id, watchlist_view.c.default_filters_json)
    ):
        filters = dict(raw_filters or {})
        instrument_types = [str(item) for item in filters.get("instrument_type", [])]
        if add_etf:
            if "fund" not in instrument_types or "etf" in instrument_types:
                continue
            instrument_types.insert(instrument_types.index("fund") + 1, "etf")
        else:
            if "etf" not in instrument_types:
                continue
            instrument_types = [item for item in instrument_types if item != "etf"]
        filters["instrument_type"] = instrument_types
        connection.execute(
            sa.update(watchlist_view)
            .where(watchlist_view.c.watchlist_view_id == view_id)
            .values(default_filters_json=filters)
        )


def upgrade() -> None:
    _update_scopes(add_etf=True)
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE instrument_detail AS local
                SET instrument_type = 'etf',
                    detail_view_type = 'fund'
                FROM instrument_registry.instrument AS shared
                WHERE shared.instrument_id = local.instrument_id
                  AND shared.instrument_type = 'etf'
                """
            )
        )


def downgrade() -> None:
    _update_scopes(add_etf=False)
    op.execute(
        sa.text(
            """
            UPDATE instrument_detail
            SET instrument_type = 'fund', detail_view_type = 'fund'
            WHERE instrument_type = 'etf'
            """
        )
    )
