"""Separate cheap research scans, pending changes, attempts and successful application."""
from alembic import op
import sqlalchemy as sa

revision = "20261012_0070"
down_revision = "20261012_0069"
branch_labels = None
depends_on = None


def upgrade():
    # No historical model run is relabelled as a successful scan. Each instrument
    # initializes from its last actual research coverage on its next allowed scan.
    op.create_table("research_monitor_state",
        sa.Column("team_id", sa.Text(), primary_key=True),
        sa.Column("instrument_id", sa.Text(), sa.ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("scan_cursor_json", sa.JSON(), nullable=False),
        sa.Column("pending_changes_json", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("attempted_revision", sa.Integer(), nullable=False),
        sa.Column("attempt_run_id", sa.Text(), sa.ForeignKey("research_entry.entry_id", ondelete="SET NULL")))


def downgrade():
    op.drop_table("research_monitor_state")
