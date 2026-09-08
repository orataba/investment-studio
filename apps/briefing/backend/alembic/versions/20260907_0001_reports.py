"""Versioned briefings retain their exact input and accepted report."""
from alembic import op
import sqlalchemy as sa

revision = "20260907_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("report",
        sa.Column("report_id", sa.String(32), primary_key=True),
        sa.Column("report_type", sa.String(10), nullable=False),
        sa.Column("report_date", sa.String(10), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("input_json", sa.JSON, nullable=False),
        sa.Column("draft_json", sa.JSON),
        sa.Column("result_json", sa.JSON),
        sa.Column("error", sa.Text),
        sa.UniqueConstraint("report_type", "report_date", "version", name="uq_briefing_report_version"))
    for field in ("report_type", "report_date", "status"):
        op.create_index(f"ix_report_{field}", "report", [field])


def downgrade():
    op.drop_table("report")
