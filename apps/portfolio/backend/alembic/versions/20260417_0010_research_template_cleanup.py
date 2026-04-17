"""normalize research run template names

Revision ID: 20260417_0010
Revises: 20260417_0009
Create Date: 2026-04-17 23:45:00
"""

from __future__ import annotations

from alembic import op


revision = "20260417_0010"
down_revision = "20260417_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE research_settings_record "
        "SET run_template = 'taxonomy_backtest' "
        "WHERE run_template IS NULL OR run_template = '' OR run_template = 'portfolio_snapshot'"
    )
    op.execute(
        "UPDATE research_run_record "
        "SET job_type = 'taxonomy_backtest' "
        "WHERE job_type IS NULL OR job_type = '' OR job_type = 'portfolio_snapshot'"
    )
    op.execute(
        "UPDATE research_run_record "
        "SET run_template = 'taxonomy_backtest' "
        "WHERE run_template IS NULL OR run_template = '' OR run_template = 'portfolio_snapshot'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE research_run_record "
        "SET job_type = 'portfolio_snapshot' "
        "WHERE job_type = 'taxonomy_backtest'"
    )
    op.execute(
        "UPDATE research_run_record "
        "SET run_template = 'portfolio_snapshot' "
        "WHERE run_template = 'taxonomy_backtest'"
    )
    op.execute(
        "UPDATE research_settings_record "
        "SET run_template = 'portfolio_snapshot' "
        "WHERE run_template = 'taxonomy_backtest'"
    )
