"""Portfolio-owned PM judgments, immutable revisions and explicit current stance."""
from alembic import op
import sqlalchemy as sa

revision = "20261008_0071"
down_revision = "20260929_0070"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("portfolio_investment_view",
        sa.Column("portfolio_id", sa.String(), sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("note_id", sa.String(), primary_key=True),
        sa.Column("latest_revision", sa.Integer(), nullable=False),
        sa.Column("author_user_id", sa.String(), nullable=False),
        sa.Column("author_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.CheckConstraint("latest_revision > 0", name="investment_view_revision_positive"))
    op.create_table("portfolio_investment_view_revision",
        sa.Column("portfolio_id", sa.String(), primary_key=True),
        sa.Column("note_id", sa.String(), primary_key=True),
        sa.Column("revision_number", sa.Integer(), primary_key=True),
        sa.Column("view_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("instrument_refs_json", sa.JSON(), nullable=False),
        sa.Column("revised_by_user_id", sa.String(), nullable=False),
        sa.Column("revised_by_name", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id", "note_id"],
            ["portfolio_investment_view.portfolio_id", "portfolio_investment_view.note_id"], ondelete="CASCADE"),
        sa.CheckConstraint("revision_number > 0", name="investment_view_revision_number_positive"))
    op.create_table("portfolio_investment_stance",
        sa.Column("portfolio_id", sa.String(), sa.ForeignKey("portfolio_record.portfolio_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("note_id", sa.String(), nullable=True),
        sa.Column("revision_number", sa.Integer(), nullable=True),
        sa.Column("selection_version", sa.Integer(), nullable=False),
        sa.Column("selected_at", sa.String(), nullable=False),
        sa.Column("selected_by_user_id", sa.String(), nullable=False),
        sa.Column("selected_by_name", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id", "note_id", "revision_number"],
            ["portfolio_investment_view_revision.portfolio_id", "portfolio_investment_view_revision.note_id", "portfolio_investment_view_revision.revision_number"]),
        sa.CheckConstraint("(note_id IS NULL AND revision_number IS NULL) OR (note_id IS NOT NULL AND revision_number IS NOT NULL)", name="investment_stance_reference_pair"),
        sa.CheckConstraint("selection_version > 0", name="investment_stance_version_positive"))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM portfolio_investment_view LIMIT 1")).first():
        raise RuntimeError("Restore the pre-migration backup to remove investment views without losing PM judgments.")
    op.drop_table("portfolio_investment_stance")
    op.drop_table("portfolio_investment_view_revision")
    op.drop_table("portfolio_investment_view")
