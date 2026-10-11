"""Move research execution ownership out of the HTTP process and evidence JSON."""
from alembic import op
import sqlalchemy as sa

revision = "20261009_0066"
down_revision = "20261006_0065"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("research_execution",
        sa.Column("run_id", sa.String(), sa.ForeignKey("research_entry.entry_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("delegation_token", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt > 0", name="attempt_positive"),
        sa.CheckConstraint("status IN ('queued', 'running', 'finished')", name="status_allowed"))
    op.create_index("ix_research_execution_queue", "research_execution", ["status", "queued_at"])
    # Old HTTP-owned work has no transferable original-subject grant. Preserve
    # its inputs/draft, but do not invent authority or adopt it as the service.
    op.execute(sa.text("UPDATE research_entry SET status='failed', "
        "body='研究执行已切换为独立队列，旧任务未继续；输入与草稿已保留，请重新发起。', "
        "completed_at=CURRENT_TIMESTAMP WHERE kind='analysis' AND status IN ('queued','running')"))


def downgrade():
    op.drop_index("ix_research_execution_queue", table_name="research_execution")
    op.drop_table("research_execution")
