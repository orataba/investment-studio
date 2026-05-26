"""add portfolio risk policy

Revision ID: 20260526_0025
Revises: 20260514_0024
Create Date: 2026-05-26 00:25:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260526_0025"
down_revision = "20260514_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portfolio_record", sa.Column("risk_policy_json", sa.JSON(), nullable=True))
    portfolio_table = sa.table(
        "portfolio_record",
        sa.column("portfolio_id", sa.String()),
        sa.column("risk_policy_json", sa.JSON()),
    )
    research_settings_table = sa.table(
        "research_settings_record",
        sa.column("portfolio_id", sa.String()),
        sa.column("lookback_days", sa.Integer()),
        sa.column("calculation_frequency", sa.String()),
        sa.column("missing_return_policy", sa.String()),
    )
    connection = op.get_bind()
    settings_rows = connection.execute(
        sa.select(
            research_settings_table.c.portfolio_id,
            research_settings_table.c.lookback_days,
            research_settings_table.c.calculation_frequency,
            research_settings_table.c.missing_return_policy,
        )
    )
    for row in settings_rows:
        policy = {
            "model_name": "Production Risk Model",
            "covariance_model_id": "ewma_vol_shrinkage_corr_covariance",
            "lookback_days": max(7, min(int(row.lookback_days or 90), 730)),
            "calculation_frequency": row.calculation_frequency or "auto",
            "missing_return_policy": row.missing_return_policy or "strict",
            "contribution_mode": "signed",
        }
        connection.execute(
            portfolio_table.update()
            .where(portfolio_table.c.portfolio_id == row.portfolio_id)
            .where(portfolio_table.c.risk_policy_json.is_(None))
            .values(risk_policy_json=policy)
        )


def downgrade() -> None:
    op.drop_column("portfolio_record", "risk_policy_json")
