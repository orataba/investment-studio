"""Retain explicit current investment view selections; never infer them from recency."""
from alembic import op
import sqlalchemy as sa

revision = "20260923_0062"
down_revision = "20260923_0061"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("instrument_investment_stance",
        sa.Column("selection_id", sa.String(), primary_key=True),
        sa.Column("instrument_id", sa.String(), sa.ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("note_id", sa.Text()), sa.Column("note_revision", sa.Integer()),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selected_by", sa.Text(), nullable=False),
        sa.Column("selected_by_name", sa.Text(), nullable=False),
        sa.CheckConstraint("(note_id IS NULL) = (note_revision IS NULL)", name="stance_reference_pair"),
        sa.ForeignKeyConstraint(["instrument_id", "note_id", "note_revision"],
            ["instrument_research_note_revision.instrument_id", "instrument_research_note_revision.note_id", "instrument_research_note_revision.revision_number"]))
    op.create_index("idx_investment_stance_scope_time", "instrument_investment_stance", ["instrument_id", "team_id", "selected_at"])


def downgrade():
    op.drop_table("instrument_investment_stance")
