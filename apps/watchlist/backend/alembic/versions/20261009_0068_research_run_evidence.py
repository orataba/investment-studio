"""Store new computation originals without rewriting retained run snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "20261009_0068"
down_revision = "20261009_0067"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("research_run_evidence",
        sa.Column("run_id", sa.String(), sa.ForeignKey("research_entry.entry_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_id", sa.Text(), primary_key=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("index_json", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False))
    # Historical originals stay exactly where they were. The read boundary
    # resolves their original run/source identity; no bulk copy or rewrite.


def downgrade():
    # Dropping the only copy of an accepted original is not a reversible downgrade.
    connection = op.get_bind()
    rows = sa.table("research_run_evidence", sa.column("source_id", sa.Text()))
    if connection.scalar(sa.select(sa.func.count()).select_from(rows)):
        raise RuntimeError("research_run_evidence contains retained originals; export or migrate them before downgrade")
    op.drop_table("research_run_evidence")
