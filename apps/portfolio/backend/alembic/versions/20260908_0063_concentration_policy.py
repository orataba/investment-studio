"""Dated portfolio concentration limits and FCN principal allocation settings."""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0063"
down_revision = "20260908_0062"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "concentration_policy_revision",
        sa.Column("portfolio_id", sa.String(), sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("settings_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint("revision > 0", name="positive_revision"),
    )
    op.create_index("ix_concentration_policy_effective", "concentration_policy_revision", ["portfolio_id", "effective_from", "revision"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM concentration_policy_revision LIMIT 1")).first():
        raise RuntimeError("Cannot erase saved concentration policies; restore the pre-migration backup instead.")
    op.drop_table("concentration_policy_revision")
