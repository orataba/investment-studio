"""Personal version-bound in-app risk notice receipts and silence preferences."""
from alembic import op
import sqlalchemy as sa

revision = "20261014_0074"
down_revision = "20261014_0073"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("watchlist_user_settings", sa.Column("risk_notice_preferences_json", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("watchlist_user_settings", "risk_notice_preferences_json")
