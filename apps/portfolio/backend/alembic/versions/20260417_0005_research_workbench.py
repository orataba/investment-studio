"""add research settings and run tables

Revision ID: 20260417_0005
Revises: 20260416_0004
Create Date: 2026-04-17 09:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260417_0005"
down_revision = "20260416_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_settings_record",
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("planning_taxonomy_id", sa.String(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("lookback_days", sa.Integer(), nullable=False),
        sa.Column("benchmark_mode", sa.String(), nullable=False),
        sa.Column("run_template", sa.String(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio_record.portfolio_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("portfolio_id"),
    )
    op.create_table(
        "research_run_record",
        sa.Column("research_run_id", sa.String(), nullable=False),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("requested_at", sa.String(), nullable=True),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("finished_at", sa.String(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("planning_taxonomy_id", sa.String(), nullable=True),
        sa.Column("lookback_days", sa.Integer(), nullable=False),
        sa.Column("benchmark_mode", sa.String(), nullable=False),
        sa.Column("run_template", sa.String(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=True),
        sa.Column("headline", sa.String(), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column("artifacts_json", sa.JSON(), nullable=True),
        sa.Column("request_payload_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio_record.portfolio_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("research_run_id"),
    )
    op.create_index(
        "ix_research_run_record_portfolio_requested",
        "research_run_record",
        ["portfolio_id", "requested_at", "research_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_research_run_record_portfolio_requested", table_name="research_run_record")
    op.drop_table("research_run_record")
    op.drop_table("research_settings_record")
