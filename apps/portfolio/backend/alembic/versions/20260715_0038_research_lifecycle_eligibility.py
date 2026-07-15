"""Add persisted PM approval for derived Research eligibility.

Revision ID: 20260715_0038
Revises: 20260715_0037

Lifecycle and eligibility remain centrally derived from holding state,
transaction history, and this explicit approval. Existing universe records are
preserved and default to unapproved, so Former instruments fail closed.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_0038"
down_revision = "20260715_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolio_instrument_universe_record",
        sa.Column(
            "research_pm_approved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "portfolio_instrument_universe_record",
        sa.Column("research_pm_approved_at", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(
        "portfolio_instrument_universe_record",
        "research_pm_approved_at",
    )
    op.drop_column(
        "portfolio_instrument_universe_record",
        "research_pm_approved",
    )
