"""Finish the Watchlist asset-to-instrument identifier cleanup.

Revision ID: 20260809_0035
Revises: 20260809_0034
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260809_0035"
down_revision = "20260809_0034"
branch_labels = None
depends_on = None


TABLE_NAME = "watchlist_item"
LEGACY_NAME = "uq_watchlist_item_watchlist_asset"
CANONICAL_NAME = "uq_watchlist_item_watchlist_instrument"


def _constraint_exists(bind, constraint_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
                "WHERE ns.nspname = current_schema() AND rel.relname = :table_name "
                "AND con.conname = :constraint_name"
            ),
            {"table_name": TABLE_NAME, "constraint_name": constraint_name},
        ).first()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
    schema = bind.dialect.identifier_preparer.quote_schema(
        str(bind.scalar(sa.text("SELECT current_schema()")))
    )
    legacy_exists = _constraint_exists(bind, LEGACY_NAME)
    canonical_exists = _constraint_exists(bind, CANONICAL_NAME)
    if legacy_exists and not canonical_exists:
        op.execute(
            f'ALTER TABLE {schema}."{TABLE_NAME}" '
            f'RENAME CONSTRAINT "{LEGACY_NAME}" TO "{CANONICAL_NAME}"'
        )
        return
    if legacy_exists == canonical_exists:
        raise RuntimeError(
            f"Expected exactly one of {LEGACY_NAME!r} or {CANONICAL_NAME!r} "
            f"on watchlist.{TABLE_NAME}."
        )


def downgrade() -> None:
    raise RuntimeError(
        "The canonical instrument identifier is not downgradable to the retired asset name."
    )
