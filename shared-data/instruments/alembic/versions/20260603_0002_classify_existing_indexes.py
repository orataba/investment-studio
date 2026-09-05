"""classify existing index instruments

Revision ID: 20260603_0002
Revises: 20260415_0001
Create Date: 2026-06-03 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260603_0002"
down_revision = "20260415_0001"
branch_labels = None
depends_on = None


INDEX_INSTRUMENT_IDS = (
    "881001-wi",
    "000300-sh",
    "000905-sh",
    "000852-sh",
    "932000-csi",
    "h11001-csi",
)

INDEX_QUOTE_SELECTION_POLICY = {
    "trading": ["close", "last"],
    "valuation": ["close", "adjusted_close", "last"],
    "total_return": ["adjusted_close", "close", "last"],
    "chart": ["adjusted_close", "close", "last"],
    "reference": ["close", "last"],
}

OTHER_QUOTE_SELECTION_POLICY = {
    "trading": ["last", "close"],
    "valuation": ["close", "last"],
    "total_return": ["adjusted_close", "close", "last"],
    "chart": ["adjusted_close", "close", "last"],
    "reference": ["close", "last"],
}


def _instrument_table() -> sa.TableClause:
    return sa.table(
        "instrument",
        sa.column("instrument_id", sa.String()),
        sa.column("instrument_type", sa.String()),
        sa.column("quote_selection_policy_json", sa.JSON()),
    )


def upgrade() -> None:
    instrument_table = _instrument_table()
    bind = op.get_bind()
    bind.execute(
        sa.update(instrument_table)
        .where(instrument_table.c.instrument_id.in_(INDEX_INSTRUMENT_IDS))
        .where(instrument_table.c.instrument_type == "other")
        .values(
            instrument_type="index",
            quote_selection_policy_json=INDEX_QUOTE_SELECTION_POLICY,
        )
    )


def downgrade() -> None:
    instrument_table = _instrument_table()
    bind = op.get_bind()
    bind.execute(
        sa.update(instrument_table)
        .where(instrument_table.c.instrument_id.in_(INDEX_INSTRUMENT_IDS))
        .where(instrument_table.c.instrument_type == "index")
        .values(
            instrument_type="other",
            quote_selection_policy_json=OTHER_QUOTE_SELECTION_POLICY,
        )
    )
