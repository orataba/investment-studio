"""shared asset foreign keys

Revision ID: 20260421_0001
Revises: 1b7d2e8c4f90
Create Date: 2026-04-21 00:01:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260421_0001"
down_revision = "1b7d2e8c4f90"
branch_labels = None
depends_on = None


def _has_foreign_key(bind: sa.engine.Connection, schema: str, table: str, constraint_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(
        foreign_key.get("name") == constraint_name
        for foreign_key in inspector.get_foreign_keys(table, schema=schema)
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    if not _has_foreign_key(bind, "watchlist", "asset_detail", "fk_asset_detail_asset_id_instrument"):
        op.create_foreign_key(
            "fk_asset_detail_asset_id_instrument",
            "asset_detail",
            "instrument",
            ["asset_id"],
            ["asset_id"],
            source_schema="watchlist",
            referent_schema="shared_asset",
            ondelete="RESTRICT",
        )
    if not _has_foreign_key(bind, "watchlist", "watchlist_item", "fk_watchlist_item_asset_id_instrument"):
        op.create_foreign_key(
            "fk_watchlist_item_asset_id_instrument",
            "watchlist_item",
            "instrument",
            ["asset_id"],
            ["asset_id"],
            source_schema="watchlist",
            referent_schema="shared_asset",
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    if _has_foreign_key(bind, "watchlist", "watchlist_item", "fk_watchlist_item_asset_id_instrument"):
        op.drop_constraint(
            "fk_watchlist_item_asset_id_instrument",
            "watchlist_item",
            schema="watchlist",
            type_="foreignkey",
        )
    if _has_foreign_key(bind, "watchlist", "asset_detail", "fk_asset_detail_asset_id_instrument"):
        op.drop_constraint(
            "fk_asset_detail_asset_id_instrument",
            "asset_detail",
            schema="watchlist",
            type_="foreignkey",
        )
