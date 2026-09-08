"""Versioned public text and import receipts owned by Investment Studio."""

from sqlalchemy import (
    JSON, Column, Date, DateTime, ForeignKey, Index, Integer, MetaData, String, Table, Text,
)

metadata = MetaData(schema="market_text")

bundles = Table(
    "import_receipt", metadata,
    Column("bundle_id", String, primary_key=True),
    Column("sha256", String, nullable=False, unique=True),
    Column("producer", String, nullable=False),
    Column("window_start", DateTime(timezone=True)),
    Column("window_end", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("document_count", Integer, nullable=False),
    Column("coverage", JSON, nullable=False),
)

versions = Table(
    "document_version", metadata,
    Column("version_id", String, primary_key=True),
    Column("document_id", String, nullable=False),
    Column("source_id", String, nullable=False, unique=True),
    Column("channel_id", String, nullable=False),
    Column("source_name", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("url", Text, nullable=False),
    Column("information_type", String, nullable=False),
    Column("status", String, nullable=False),
    Column("published_at", DateTime(timezone=True)),
    Column("published_date", Date),
    Column("published_at_precision", String, nullable=False),
    Column("occurred_at", DateTime(timezone=True)),
    Column("occurred_date", Date),
    Column("occurred_at_precision", String, nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("content_text", Text, nullable=False),
    Column("body_sha256", String, nullable=False),
    Column("raw_sha256", String, nullable=False),
    Column("raw_format", String, nullable=False),
    Column("content_completeness", String, nullable=False),
    Column("content_warnings", JSON, nullable=False),
    Column("provenance", JSON, nullable=False),
    Column("record_sha256", String, nullable=False),
)
Index("ix_text_document_observed", versions.c.document_id, versions.c.observed_at, versions.c.version_id)
Index("ix_text_publication", versions.c.published_at)
Index("ix_text_channel", versions.c.channel_id)

entities = Table(
    "entity", metadata,
    Column("entity_id", String, primary_key=True),
    Column("label", Text, nullable=False),
    Column("kind", String, nullable=False),
)
document_entities = Table(
    "document_entity", metadata,
    Column("version_id", String, ForeignKey(versions.c.version_id), primary_key=True),
    Column("entity_id", String, ForeignKey(entities.c.entity_id), primary_key=True),
)
document_events = Table(
    "document_event", metadata,
    Column("version_id", String, ForeignKey(versions.c.version_id), primary_key=True),
    Column("event_id", String, primary_key=True),
)
