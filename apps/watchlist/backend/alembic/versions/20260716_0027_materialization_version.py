"""Version Watchlist chart and screener materializations.

Revision ID: 20260716_0027
Revises: 20260716_0026
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260716_0027"
down_revision = "20260716_0026"
branch_labels = None
depends_on = None


UNVERSIONED = "unversioned"


def upgrade() -> None:
    op.add_column(
        "instrument_chart_read_model",
        sa.Column(
            "materialization_version",
            sa.String(),
            nullable=False,
            server_default=UNVERSIONED,
        ),
    )
    op.add_column(
        "watchlist_row_read_model",
        sa.Column(
            "materialization_version",
            sa.String(),
            nullable=False,
            server_default=UNVERSIONED,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE instrument_chart_read_model "
            "SET data_freshness_status = 'stale'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE watchlist_row_read_model "
            "SET data_freshness_status = 'stale', "
            "staleness_reason = 'Calculation policy changed; recalculation required.'"
        )
    )


def downgrade() -> None:
    op.drop_column("watchlist_row_read_model", "materialization_version")
    op.drop_column("instrument_chart_read_model", "materialization_version")
