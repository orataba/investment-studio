from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
CURRENCY_CODE_CHECK = (
    "length(currency) = 3 "
    "AND substr(currency, 1, 1) BETWEEN 'A' AND 'Z' "
    "AND substr(currency, 2, 1) BETWEEN 'A' AND 'Z' "
    "AND substr(currency, 3, 1) BETWEEN 'A' AND 'Z'"
)


class InstrumentRegistryBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CanonicalNumeric(TypeDecorator[Decimal]):
    """Exact arbitrary-precision numeric with a text-backed SQLite variant."""

    impl = Numeric
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "sqlite":
            return dialect.type_descriptor(Text())
        return dialect.type_descriptor(Numeric(asdecimal=True))

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        try:
            resolved = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("canonical numeric value must be a decimal") from error
        if not resolved.is_finite():
            raise ValueError("canonical numeric value must be finite")
        if dialect.name == "sqlite":
            return format(resolved, "f")
        return resolved

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        del dialect
        return None if value is None else Decimal(str(value))


class RegistryMetadata(InstrumentRegistryBase):
    __tablename__ = "registry_metadata"

    registry_key: Mapped[str] = mapped_column(
        String, primary_key=True, default="shared"
    )
    registry_name: Mapped[str] = mapped_column(String, nullable=False)
    market_data_updated_at: Mapped[str | None] = mapped_column(String)


class Instrument(InstrumentRegistryBase):
    __tablename__ = "instrument"
    __table_args__ = (CheckConstraint(CURRENCY_CODE_CHECK, name="currency_iso_code"),)

    instrument_id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_name: Mapped[str] = mapped_column(String, nullable=False)
    instrument_type: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    quote_selection_policy_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    source_settings_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    refresh_status_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    lifecycle_state_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    market_data_updated_at: Mapped[str | None] = mapped_column(String)

    identifiers: Mapped[list["InstrumentIdentifier"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
    quote_series: Mapped[list["QuoteSeries"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
    corporate_action_events: Mapped[list["CorporateActionEvent"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )


class InstrumentIdentifier(InstrumentRegistryBase):
    __tablename__ = "instrument_identifier"
    __table_args__ = (
        UniqueConstraint(
            "identifier_type",
            "identifier_value",
            name="uq_instrument_identifier_type_value",
        ),
    )

    instrument_identifier_id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="CASCADE"),
        nullable=False,
    )
    identifier_type: Mapped[str] = mapped_column(String, nullable=False)
    identifier_value: Mapped[str] = mapped_column(String, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    instrument: Mapped[Instrument] = relationship(back_populates="identifiers")


class QuoteSeries(InstrumentRegistryBase):
    __tablename__ = "quote_series"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "metric_family",
            "quote_basis",
            "currency",
            name="uq_quote_series_instrument_metric_basis_currency",
        ),
        CheckConstraint(
            "((metric_family = 'price' AND quote_basis IN "
            "('last', 'close', 'adjusted_close', 'clean_price', 'dirty_price', 'par')) "
            "OR (metric_family = 'nav' AND quote_basis IN "
            "('official_nav', 'total_return_nav', 'cumulative_nav', "
            "'accumulated_nav', 'cum_nav', 'dividend_adjusted_nav', 'reinvested_nav')) "
            "OR (metric_family = 'fx' AND quote_basis = 'spot'))",
            name="metric_basis",
        ),
        CheckConstraint(
            CURRENCY_CODE_CHECK,
            name="currency_iso_code",
        ),
    )

    quote_series_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        primary_key=True,
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_family: Mapped[str] = mapped_column(String, nullable=False)
    quote_basis: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    data_updated_at: Mapped[str | None] = mapped_column(String)

    instrument: Mapped[Instrument] = relationship(back_populates="quote_series")
    observations: Mapped[list["QuoteObservation"]] = relationship(
        back_populates="quote_series",
        cascade="all, delete-orphan",
        order_by="QuoteObservation.as_of_date",
    )


class QuoteObservation(InstrumentRegistryBase):
    __tablename__ = "quote_observation"
    __table_args__ = (
        UniqueConstraint(
            "quote_series_id",
            "as_of_date",
            name="uq_quote_observation_series_date",
        ),
    )

    observation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        primary_key=True,
    )
    quote_series_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("quote_series.quote_series_id", ondelete="CASCADE"),
        nullable=False,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)

    quote_series: Mapped[QuoteSeries] = relationship(back_populates="observations")
    revisions: Mapped[list["QuoteObservationRevision"]] = relationship(
        back_populates="observation",
        cascade="all, delete-orphan",
        order_by="QuoteObservationRevision.revision_number",
    )


class QuoteObservationRevision(InstrumentRegistryBase):
    __tablename__ = "quote_observation_revision"
    __table_args__ = (
        UniqueConstraint(
            "observation_id",
            "revision_number",
            name="uq_quote_observation_revision_number",
        ),
        CheckConstraint(
            "revision_number > 0",
            name="revision_number_positive",
        ),
        CheckConstraint(
            "(is_current AND superseded_at IS NULL) OR "
            "((NOT is_current) AND superseded_at IS NOT NULL)",
            name="current_state",
        ),
        CheckConstraint(
            "status IN ('complete', 'partial', 'rejected', 'withdrawn')",
            name="status",
        ),
        CheckConstraint(
            "(status = 'withdrawn' AND value IS NULL) OR "
            "(status <> 'withdrawn' AND value IS NOT NULL "
            "AND CAST(value AS NUMERIC) > 0)",
            name="value_status",
        ),
        CheckConstraint(
            "payload_schema_version IN (1, 2)",
            name="payload_schema_version",
        ),
        CheckConstraint(
            "(status = 'withdrawn' AND value_input_scale IS NULL "
            "AND numeric_scale_state IS NULL) OR "
            "(status <> 'withdrawn' AND value_input_scale IS NOT NULL "
            "AND value_input_scale >= 0 AND numeric_scale_state IS NOT NULL)",
            name="numeric_evidence_presence",
        ),
        CheckConstraint(
            "numeric_scale_state IS NULL OR numeric_scale_state IN "
            "('declared', 'binary_inferred', 'legacy_inferred')",
            name="numeric_scale_state",
        ),
        CheckConstraint(
            "status = 'withdrawn' OR "
            "(payload_schema_version = 1 "
            "AND numeric_scale_state = 'legacy_inferred') OR "
            "(payload_schema_version = 2 "
            "AND numeric_scale_state IN ('declared', 'binary_inferred'))",
            name="numeric_evidence_schema",
        ),
        Index(
            "uq_quote_observation_revision_current",
            "observation_id",
            unique=True,
            sqlite_where=text("is_current"),
            postgresql_where=text("is_current IS TRUE"),
        ),
    )

    revision_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        primary_key=True,
    )
    observation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("quote_observation.observation_id", ondelete="CASCADE"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[Decimal | None] = mapped_column(CanonicalNumeric())
    value_input_scale: Mapped[int | None] = mapped_column(Integer)
    numeric_scale_state: Mapped[str | None] = mapped_column(String)
    payload_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False)
    source_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_hash: Mapped[str] = mapped_column(String, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    observation: Mapped[QuoteObservation] = relationship(back_populates="revisions")


class MarketDataOutboxEvent(InstrumentRegistryBase):
    __tablename__ = "market_data_outbox_event"
    __table_args__ = (
        UniqueConstraint(
            "quote_revision_id",
            name="uq_market_data_outbox_event_quote_revision_id",
        ),
        CheckConstraint(
            "event_type = 'watchlist_market_data_refresh_requested'",
            name="event_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'delivered', 'dead')",
            name="status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="attempts",
        ),
        CheckConstraint(
            "((source_kind = 'quote_revision' AND quote_revision_id IS NOT NULL) OR "
            "(source_kind = 'quote_selection_policy' AND quote_revision_id IS NULL)) "
            "AND TRIM(source_version) <> ''",
            name="source_lineage",
        ),
        CheckConstraint(
            "(status = 'pending' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND delivered_at IS NULL "
            "AND dead_at IS NULL) OR "
            "(status = 'processing' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND delivered_at IS NULL "
            "AND dead_at IS NULL) OR "
            "(status = 'delivered' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND delivered_at IS NOT NULL "
            "AND dead_at IS NULL) OR "
            "(status = 'dead' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND delivered_at IS NULL "
            "AND dead_at IS NOT NULL)",
            name="lifecycle",
        ),
        UniqueConstraint(
            "instrument_id",
            "source_kind",
            "source_version",
            name="uq_market_data_outbox_event_source_lineage",
        ),
        Index(
            "ix_market_data_outbox_event_claim",
            "status",
            "available_at",
            "created_at",
            "event_id",
        ),
        Index(
            "ix_market_data_outbox_event_expired_lease",
            "lease_expires_at",
            "event_id",
            postgresql_where=text("status = 'processing'"),
            sqlite_where=text("status = 'processing'"),
        ),
    )

    event_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    quote_revision_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey(
            "quote_observation_revision.revision_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    source_kind: Mapped[str] = mapped_column(String, nullable=False)
    source_version: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=8,
        server_default=text("8"),
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    lease_owner: Mapped[str | None] = mapped_column(String)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketDataOutboxWorkerHeartbeat(InstrumentRegistryBase):
    __tablename__ = "market_data_outbox_worker_heartbeat"
    __table_args__ = (
        CheckConstraint("process_id > 0", name="process_id"),
        CheckConstraint(
            "worker_state IN ('running', 'stopped')",
            name="state",
        ),
        CheckConstraint(
            "(worker_state = 'running' AND stopped_at IS NULL) OR "
            "(worker_state = 'stopped' AND stopped_at IS NOT NULL)",
            name="lifecycle",
        ),
        Index(
            "ix_market_data_outbox_worker_heartbeat_state",
            "worker_state",
            "heartbeat_at",
            "last_successful_poll_at",
        ),
    )

    worker_id: Mapped[str] = mapped_column(String, primary_key=True)
    hostname: Mapped[str] = mapped_column(String, nullable=False)
    process_id: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_state: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_poll_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_poll_error: Mapped[str | None] = mapped_column(String(4000))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CorporateActionEvent(InstrumentRegistryBase):
    """Canonical security-master event that changes the units in circulation.

    Ratios are stored as decimal text so the ledger can apply the exact
    provider/issuer ratio without a binary floating-point round trip.  A
    ``share_split`` uses ``new_units / old_units`` (for example 2 / 1 for a
    two-for-one split and 1 / 2 for a reverse split).
    """

    __tablename__ = "corporate_action_event"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "action_type",
            "effective_date",
            name="uq_corporate_action_instrument_type_effective_date",
        ),
        Index(
            "ix_corporate_action_instrument_effective_date",
            "instrument_id",
            "effective_date",
        ),
    )

    corporate_action_event_id: Mapped[str] = mapped_column(String, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument.instrument_id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    announcement_date: Mapped[date | None] = mapped_column(Date)
    record_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    payable_date: Mapped[date | None] = mapped_column(Date)
    new_units: Mapped[str] = mapped_column(Text, nullable=False)
    old_units: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_rounding: Mapped[str] = mapped_column(
        String, nullable=False, default="exact"
    )
    quantity_precision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_basis_treatment: Mapped[str] = mapped_column(
        String, nullable=False, default="carry"
    )
    source: Mapped[str] = mapped_column(String, nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="confirmed")
    provenance_json: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    instrument: Mapped[Instrument] = relationship(
        back_populates="corporate_action_events"
    )
