"""Track heartbeats for actively running recalc jobs.

Revision ID: 20260711_0022
Revises: 20260605_0021
"""

from alembic import op
import sqlalchemy as sa


revision = "20260711_0022"
down_revision = "20260605_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recalc_job",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recalc_job", "heartbeat_at")
