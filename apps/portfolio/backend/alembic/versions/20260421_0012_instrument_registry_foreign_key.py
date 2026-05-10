"""instrument registry foreign key

Revision ID: 20260421_0012
Revises: 20260419_0011
Create Date: 2026-04-21 00:02:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260421_0012"
down_revision = "20260419_0011"
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

    if _has_foreign_key(bind, "portfolio", "transaction_record", "fk_transaction_record_instrument_id_instrument"):
        return

    op.create_foreign_key(
        "fk_transaction_record_instrument_id_instrument",
        "transaction_record",
        "instrument",
        ["instrument_id"],
        ["instrument_id"],
        source_schema="portfolio",
        referent_schema="instrument_registry",
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    if not _has_foreign_key(bind, "portfolio", "transaction_record", "fk_transaction_record_instrument_id_instrument"):
        return

    op.drop_constraint(
        "fk_transaction_record_instrument_id_instrument",
        "transaction_record",
        schema="portfolio",
        type_="foreignkey",
    )
