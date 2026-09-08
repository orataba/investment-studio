"""PostgreSQL owns publication and current projections; Parquet owns history."""
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, JSON, MetaData, String, Table, Text

metadata = MetaData(schema="market_data")
datasets = Table("datasets", metadata,
    Column("name", String(100), primary_key=True),
    Column("description", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True)),
)
batches = Table("batches", metadata,
    Column("id", String(36), primary_key=True),
    Column("dataset", String(100), ForeignKey("market_data.datasets.name"), nullable=False),
    Column("source", String(100), nullable=False),
    Column("status", String(20), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True)),
    Column("row_count", BigInteger, nullable=False, default=0),
    Column("details", JSON, nullable=False, default=dict),
    Column("error", Text),
)
Index("ix_market_batches_dataset_status", batches.c.dataset, batches.c.status)
files = Table("files", metadata,
    Column("batch_id", String(36), ForeignKey("market_data.batches.id"), primary_key=True),
    Column("part", Integer, primary_key=True),
    Column("path", Text, nullable=False, unique=True),
    Column("row_count", BigInteger, nullable=False),
    Column("first_row", BigInteger, nullable=False),
    Column("last_row", BigInteger, nullable=False),
    Column("min_date", String(10)), Column("max_date", String(10)),
    Column("bytes", BigInteger, nullable=False),
)
current = Table("current", metadata,
    Column("dataset", String(100), primary_key=True),
    Column("key", Text, primary_key=True),
    Column("symbol", String(100)),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("batch_id", String(36), ForeignKey("market_data.batches.id"), nullable=False),
    Column("row_index", BigInteger, nullable=False),
    Column("payload", JSON, nullable=False),
)
Index("ix_market_current_symbol", current.c.dataset, current.c.symbol)

snapshots = Table("snapshots", metadata,
    Column("dataset", String(100), nullable=False),
    Column("scope_key", Text, primary_key=True),
    Column("snapshot_at", DateTime(timezone=True), primary_key=True),
    Column("batch_id", String(36), ForeignKey("market_data.batches.id"), primary_key=True),
    Column("symbol", String(100), nullable=False),
    Column("frequency", String(30), nullable=False, default=""),
    Column("row_count", BigInteger, nullable=False),
)
Index("ix_market_snapshots_scope", snapshots.c.dataset, snapshots.c.symbol, snapshots.c.frequency, snapshots.c.snapshot_at)
