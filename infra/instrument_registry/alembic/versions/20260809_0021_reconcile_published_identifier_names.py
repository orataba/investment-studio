"""Reconcile Registry identifiers after historical revisions were renamed in place.

Revision ID: 20260809_0021
Revises: 20260809_0020
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260809_0021"
down_revision = "20260809_0020"
branch_labels = None
depends_on = None


CONSTRAINT_RENAMES = (
    (
        "instrument_identifier",
        "fk_instrument_identifier_asset_id_instrument",
        "fk_instrument_identifier_instrument_id_instrument",
    ),
    (
        "instrument_market_data",
        "fk_instrument_market_data_asset_id_instrument",
        "fk_instrument_market_data_instrument_id_instrument",
    ),
    (
        "instrument_market_data",
        "uq_instrument_market_data_asset_metric_basis_date_currency",
        "uq_instrument_market_data_instrument_metric_basis_date_currency",
    ),
)


def _constraint_exists(bind, table_name: str, constraint_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
                "WHERE ns.nspname = current_schema() "
                "AND rel.relname = :table_name AND con.conname = :constraint_name"
            ),
            {"table_name": table_name, "constraint_name": constraint_name},
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
    for table_name, legacy_name, canonical_name in CONSTRAINT_RENAMES:
        legacy_exists = _constraint_exists(bind, table_name, legacy_name)
        canonical_exists = _constraint_exists(bind, table_name, canonical_name)
        if legacy_exists and not canonical_exists:
            op.execute(
                f'ALTER TABLE {schema}."{table_name}" '
                f'RENAME CONSTRAINT "{legacy_name}" TO "{canonical_name}"'
            )
        elif legacy_exists == canonical_exists:
            raise RuntimeError(
                f"Expected exactly one of {legacy_name!r} or {canonical_name!r} "
                f"on the current Registry schema table {table_name}."
            )


def downgrade() -> None:
    raise RuntimeError(
        "Identifier reconciliation is the canonical schema state and is not downgradable."
    )
