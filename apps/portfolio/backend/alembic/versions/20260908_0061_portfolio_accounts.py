"""Add explicit portfolio access, personal preferences and stable transaction actors.

Existing portfolios intentionally receive no grants. An owner must explicitly assign
initial managers through the recovery operation or the migration assignment command.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0061"
down_revision = "20260906_0060"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("portfolio_access_state",
        sa.Column("portfolio_id", sa.String(), sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("team_id", sa.String(), nullable=False))
    op.create_index("ix_portfolio_access_state_team_id", "portfolio_access_state", ["team_id"])
    op.create_table("portfolio_membership",
        sa.Column("portfolio_id", sa.String(), sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("granted_by", sa.String(), nullable=False),
        sa.Column("granted_at", sa.String(), nullable=False),
        sa.CheckConstraint("role IN ('manager', 'editor', 'viewer')", name="ck_portfolio_membership_role"))
    op.create_table("portfolio_access_audit",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("portfolio_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String()),
        sa.Column("actor_name", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.String(), nullable=False))
    op.create_index("ix_portfolio_access_audit_portfolio_id", "portfolio_access_audit", ["portfolio_id"])
    op.create_table("portfolio_user_preference",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("preference_key", sa.String(), primary_key=True),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False))
    op.add_column("transaction_change_log", sa.Column("actor_user_id", sa.String()))
    op.add_column("transaction_change_log", sa.Column("actor_name", sa.String()))


def downgrade():
    op.drop_column("transaction_change_log", "actor_name")
    op.drop_column("transaction_change_log", "actor_user_id")
    for table in ("portfolio_user_preference", "portfolio_access_audit", "portfolio_membership", "portfolio_access_state"):
        op.drop_table(table)
