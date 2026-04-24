"""add capital overlay settings to research workbench

Revision ID: 20260423_0014
Revises: 20260421_0013
Create Date: 2026-04-23 18:55:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0014"
down_revision = "20260421_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_settings_record",
        sa.Column("capital_mode", sa.String(), nullable=False, server_default="unit_notional"),
    )
    op.add_column("research_settings_record", sa.Column("gross_exposure", sa.Float(), nullable=True))
    op.add_column("research_settings_record", sa.Column("target_volatility", sa.Float(), nullable=True))
    op.add_column("research_settings_record", sa.Column("max_gross_exposure", sa.Float(), nullable=True))
    op.execute(
        "UPDATE research_settings_record "
        "SET capital_mode = 'unit_notional' "
        "WHERE capital_mode IS NULL OR capital_mode = ''"
    )


def downgrade() -> None:
    op.drop_column("research_settings_record", "max_gross_exposure")
    op.drop_column("research_settings_record", "target_volatility")
    op.drop_column("research_settings_record", "gross_exposure")
    op.drop_column("research_settings_record", "capital_mode")
