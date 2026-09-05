"""Add per-period price-risk review lines and their initial calibration."""
from alembic import op
import sqlalchemy as sa

revision = "20260905_0054"
down_revision = "20260905_0053"
branch_labels = depends_on = None


def upgrade():
    op.add_column("risk_review_rule", sa.Column("period_limits_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("risk_review_rule", sa.Column("calibration_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade():
    op.drop_column("risk_review_rule", "calibration_json")
    op.drop_column("risk_review_rule", "period_limits_json")
