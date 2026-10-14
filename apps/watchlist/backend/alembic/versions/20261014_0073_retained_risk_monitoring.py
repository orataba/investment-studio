"""Retain fixed monitoring definitions, samples and outputs with risk snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "20261014_0073"
down_revision = "20261014_0072"
branch_labels = None
depends_on = None


def upgrade():
    # Existing snapshots predate this contract. Empty means unavailable, never
    # recompute historical outputs using subsequently corrected source data.
    op.add_column("risk_snapshot", sa.Column("monitoring_json", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("risk_snapshot", "monitoring_json")
