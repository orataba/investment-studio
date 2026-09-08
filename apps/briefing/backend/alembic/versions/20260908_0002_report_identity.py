"""Report ownership and the identity that requested an edition."""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0002"
down_revision = "20260907_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("report", sa.Column("team_id", sa.String(64), nullable=False, server_default="default"))
    op.add_column("report", sa.Column("created_by_user_id", sa.String(64)))
    op.add_column("report", sa.Column("created_by_service_id", sa.String(64)))
    op.create_index("ix_report_team_id", "report", ["team_id"])


def downgrade():
    op.drop_index("ix_report_team_id", "report")
    for name in ("created_by_service_id", "created_by_user_id", "team_id"):
        op.drop_column("report", name)
