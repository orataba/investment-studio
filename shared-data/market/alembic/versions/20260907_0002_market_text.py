"""Own versioned public text and authenticated import receipts."""

from alembic import op
import sqlalchemy as sa

revision = "studio_market_0002"
down_revision = "studio_market_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS market_text")
    op.create_table(
        "import_receipt",
        sa.Column("bundle_id", sa.String(), primary_key=True),
        sa.Column("sha256", sa.String(), nullable=False, unique=True),
        sa.Column("producer", sa.String(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True)),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=False),
        schema="market_text",
    )
    op.create_table(
        "document_version",
        sa.Column("version_id", sa.String(), primary_key=True),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False, unique=True),
        sa.Column("channel_id", sa.String(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("information_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("published_date", sa.Date()),
        sa.Column("published_at_precision", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("occurred_date", sa.Date()),
        sa.Column("occurred_at_precision", sa.String(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.String(), nullable=False),
        sa.Column("raw_sha256", sa.String(), nullable=False),
        sa.Column("raw_format", sa.String(), nullable=False),
        sa.Column("content_completeness", sa.String(), nullable=False),
        sa.Column("content_warnings", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("record_sha256", sa.String(), nullable=False),
        schema="market_text",
    )
    op.create_index("ix_text_document_observed", "document_version", ["document_id", "observed_at", "version_id"], schema="market_text")
    op.create_index("ix_text_publication", "document_version", ["published_at"], schema="market_text")
    op.create_index("ix_text_channel", "document_version", ["channel_id"], schema="market_text")
    op.create_table(
        "entity",
        sa.Column("entity_id", sa.String(), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        schema="market_text",
    )
    op.create_table(
        "document_entity",
        sa.Column("version_id", sa.String(), sa.ForeignKey("market_text.document_version.version_id"), primary_key=True),
        sa.Column("entity_id", sa.String(), sa.ForeignKey("market_text.entity.entity_id"), primary_key=True),
        schema="market_text",
    )
    op.create_table(
        "document_event",
        sa.Column("version_id", sa.String(), sa.ForeignKey("market_text.document_version.version_id"), primary_key=True),
        sa.Column("event_id", sa.String(), primary_key=True),
        schema="market_text",
    )


def downgrade() -> None:
    for table in ("document_event", "document_entity", "entity", "document_version", "import_receipt"):
        op.drop_table(table, schema="market_text")
    op.execute("DROP SCHEMA market_text")
