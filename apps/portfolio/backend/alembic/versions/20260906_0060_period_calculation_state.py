"""Persist private period-calculation boundary inputs with daily snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "20260906_0060"
down_revision = "20260905_0059"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("portfolio_daily_snapshot", sa.Column("calculation_state_json", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("portfolio_daily_snapshot", "calculation_state_json")
