"""Retain independent FMP text acquisition coverage at its actual check time."""
from alembic import op
import sqlalchemy as sa

revision = "studio_market_0003"
down_revision = "studio_market_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collection_receipt",
        sa.Column("receipt_id", sa.String(), primary_key=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("channel_id", sa.String(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("content_scope", sa.String(), nullable=False),
        sa.Column("error_type", sa.String()),
        sa.Column("details", sa.JSON(), nullable=False),
        schema="market_text",
    )
    op.create_index("ix_text_collection_scope_clock", "collection_receipt",
                    ["symbol", "channel_id", "checked_at"], schema="market_text")


def downgrade() -> None:
    op.drop_table("collection_receipt", schema="market_text")
