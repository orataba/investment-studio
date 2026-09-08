"""Shared numeric publication catalog and current projections."""
from alembic import op
import sqlalchemy as sa

revision = "studio_market_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS market_data")
    op.create_table(
        "datasets",
        sa.Column("name", sa.String(100), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        schema="market_data",
    )
    op.create_table(
        "batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset", sa.String(100), sa.ForeignKey("market_data.datasets.name"), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text()),
        schema="market_data",
    )
    op.create_index("ix_market_batches_dataset_status", "batches", ["dataset", "status"], schema="market_data")
    op.create_table(
        "files",
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("market_data.batches.id"), primary_key=True),
        sa.Column("part", sa.Integer(), primary_key=True),
        sa.Column("path", sa.Text(), nullable=False, unique=True),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("first_row", sa.BigInteger(), nullable=False),
        sa.Column("last_row", sa.BigInteger(), nullable=False),
        sa.Column("min_date", sa.String(10)),
        sa.Column("max_date", sa.String(10)),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        schema="market_data",
    )
    op.create_table(
        "current",
        sa.Column("dataset", sa.String(100), primary_key=True),
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("symbol", sa.String(100)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("market_data.batches.id"), nullable=False),
        sa.Column("row_index", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        schema="market_data",
    )
    op.create_index("ix_market_current_symbol", "current", ["dataset", "symbol"], schema="market_data")
    op.create_table(
        "snapshots",
        sa.Column("dataset", sa.String(100), nullable=False),
        sa.Column("scope_key", sa.Text(), primary_key=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("market_data.batches.id"), primary_key=True),
        sa.Column("symbol", sa.String(100), nullable=False),
        sa.Column("frequency", sa.String(30), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        schema="market_data",
    )
    op.create_index("ix_market_snapshots_scope", "snapshots", ["dataset", "symbol", "frequency", "snapshot_at"], schema="market_data")


def downgrade():
    for table in ("snapshots", "current", "files", "batches", "datasets"):
        op.drop_table(table, schema="market_data")
    # Alembic retains its empty revision table in this schema after downgrade.
