from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from platform_app.db.base import Base


class ExactFundNavDecimal(TypeDecorator[Decimal]):
    """Use exact NUMERIC on PostgreSQL and lossless text on SQLite.

    SQLite's NUMERIC affinity round-trips decimal bindings through binary
    floating point.  The string variant keeps local/test projections exact
    while PostgreSQL retains its native sortable NUMERIC(38, 18) column.
    """

    impl = Numeric
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(precision=38, scale=18)

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(38, 18))

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        normalized = Decimal(str(value))
        if not normalized.is_finite():
            raise ValueError("Fund NAV candidate decimals must be finite.")
        if dialect.name == "sqlite":
            if normalized == 0:
                return "0"
            return format(normalized.normalize(), "f")
        return normalized

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        del dialect
        if value is None:
            return None
        return Decimal(str(value))


class EmailMailboxIngestionLease(Base):
    """Crash-recoverable single-writer lease for one physical mailbox."""

    __tablename__ = "email_mailbox_ingestion_lease"
    __table_args__ = (
        CheckConstraint(
            "(lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="lease_state_contract",
        ),
    )

    mailbox_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class EmailFolderCursor(Base):
    """Durable acquisition checkpoint for one mailbox folder generation."""

    __tablename__ = "email_folder_cursor"
    __table_args__ = (
        CheckConstraint(
            "uid_validity IS NULL OR uid_validity > 0",
            name="uid_validity_positive",
        ),
        CheckConstraint(
            "last_committed_uid >= 0",
            name="last_uid_nonnegative",
        ),
        CheckConstraint(
            "last_observed_uid_next IS NULL OR last_observed_uid_next > 0",
            name="uid_next_positive",
        ),
        UniqueConstraint(
            "mailbox_key",
            "folder_name",
            name="uq_email_folder_cursor_mailbox_folder",
        ),
    )

    email_folder_cursor_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    mailbox_key: Mapped[str] = mapped_column(String(64), nullable=False)
    folder_name: Mapped[str] = mapped_column(String(512), nullable=False)
    uid_validity: Mapped[int | None] = mapped_column(BigInteger)
    last_committed_uid: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_observed_uid_next: Mapped[int | None] = mapped_column(BigInteger)
    last_scan_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    message_occurrences: Mapped[list["EmailMessageOccurrence"]] = relationship(
        back_populates="folder_cursor",
        cascade="all, delete-orphan",
    )


class EmailMessageOccurrence(Base):
    """One IMAP UID occurrence, scoped by folder and UIDVALIDITY."""

    __tablename__ = "email_message_occurrence"
    __table_args__ = (
        CheckConstraint(
            "uid_validity > 0 AND message_uid > 0",
            name="uid_positive",
        ),
        CheckConstraint(
            "rfc822_size IS NULL OR rfc822_size >= 0",
            name="size_nonnegative",
        ),
        CheckConstraint(
            "acquisition_attempt_count >= 0",
            name="attempt_nonnegative",
        ),
        CheckConstraint(
            "acquisition_status IN "
            "('discovered', 'materialized', 'ignored', 'retryable', 'dead_letter')",
            name="status_contract",
        ),
        UniqueConstraint(
            "email_folder_cursor_id",
            "uid_validity",
            "message_uid",
            name="uq_email_message_occurrence_folder_generation_uid",
        ),
        Index(
            "ix_email_message_occurrence_rfc_message_id",
            "rfc_message_id",
        ),
        Index(
            "ix_email_message_occurrence_message_sha256",
            "message_sha256",
        ),
        Index(
            "ix_email_message_occurrence_retry",
            "acquisition_status",
            "next_retry_at",
        ),
    )

    email_message_occurrence_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    email_folder_cursor_id: Mapped[int] = mapped_column(
        ForeignKey(
            "email_folder_cursor.email_folder_cursor_id",
            name="fk_email_message_occurrence_folder",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    uid_validity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_uid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rfc_message_id: Mapped[str | None] = mapped_column(String(998))
    message_sha256: Mapped[str | None] = mapped_column(String(64))
    sender_address: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    internal_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rfc822_size: Mapped[int | None] = mapped_column(BigInteger)
    acquisition_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="discovered",
        server_default=text("'discovered'"),
    )
    acquisition_attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    materialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    folder_cursor: Mapped[EmailFolderCursor] = relationship(
        back_populates="message_occurrences"
    )
    attachments: Mapped[list["EmailMessageAttachment"]] = relationship(
        back_populates="message_occurrence",
        cascade="all, delete-orphan",
    )


class EmailAttachmentArtifact(Base):
    """Content-addressed attachment payload shared by all message occurrences."""

    __tablename__ = "email_attachment_artifact"
    __table_args__ = (
        CheckConstraint(
            "length(content_sha256) = 64",
            name="sha256_length",
        ),
        CheckConstraint(
            "byte_size > 0",
            name="size_positive",
        ),
        UniqueConstraint(
            "content_sha256",
            name="uq_email_attachment_artifact_sha256",
        ),
    )

    email_attachment_artifact_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    message_links: Mapped[list["EmailMessageAttachment"]] = relationship(
        back_populates="attachment_artifact"
    )
    parse_attempts: Mapped[list["EmailAttachmentParse"]] = relationship(
        back_populates="attachment_artifact",
        cascade="all, delete-orphan",
    )


class EmailMessageAttachment(Base):
    """MIME-part provenance linking an occurrence to a deduplicated artifact."""

    __tablename__ = "email_message_attachment"
    __table_args__ = (
        UniqueConstraint(
            "email_message_occurrence_id",
            "part_index",
            name="uq_email_message_attachment_occurrence_part",
        ),
        Index(
            "ix_email_message_attachment_artifact",
            "email_attachment_artifact_id",
        ),
    )

    email_message_attachment_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    email_message_occurrence_id: Mapped[int] = mapped_column(
        ForeignKey(
            "email_message_occurrence.email_message_occurrence_id",
            name="fk_email_message_attachment_occurrence",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    email_attachment_artifact_id: Mapped[int] = mapped_column(
        ForeignKey(
            "email_attachment_artifact.email_attachment_artifact_id",
            name="fk_email_message_attachment_artifact",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    part_index: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str | None] = mapped_column(Text)
    content_id: Mapped[str | None] = mapped_column(String(998))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    message_occurrence: Mapped[EmailMessageOccurrence] = relationship(
        back_populates="attachments"
    )
    attachment_artifact: Mapped[EmailAttachmentArtifact] = relationship(
        back_populates="message_links"
    )
    route_contexts: Mapped[list["EmailAttachmentRouteContext"]] = relationship(
        back_populates="message_attachment",
        cascade="all, delete-orphan",
    )


class EmailAttachmentParse(Base):
    """Versioned, leaseable parse job for one attachment artifact."""

    __tablename__ = "email_attachment_parse"
    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name="attempt_nonnegative",
        ),
        CheckConstraint(
            "status IN "
            "('pending', 'processing', 'succeeded', 'retryable', "
            "'dead_letter', 'unsupported')",
            name="status_contract",
        ),
        UniqueConstraint(
            "email_attachment_artifact_id",
            "parser_profile",
            "parser_version",
            "source_format",
            name="uq_email_attachment_parse_artifact_profile_version_format",
        ),
        Index(
            "ix_email_attachment_parse_retry",
            "status",
            "next_retry_at",
        ),
        Index(
            "ix_email_attachment_parse_lease",
            "lease_expires_at",
        ),
    )

    email_attachment_parse_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    email_attachment_artifact_id: Mapped[int] = mapped_column(
        ForeignKey(
            "email_attachment_artifact.email_attachment_artifact_id",
            name="fk_email_attachment_parse_artifact",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    parser_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_format: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
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
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    parser_metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    attachment_artifact: Mapped[EmailAttachmentArtifact] = relationship(
        back_populates="parse_attempts"
    )
    nav_candidates: Mapped[list["EmailNavCandidate"]] = relationship(
        back_populates="attachment_parse",
        cascade="all, delete-orphan",
    )
    route_contexts: Mapped[list["EmailAttachmentRouteContext"]] = relationship(
        back_populates="attachment_parse",
        cascade="all, delete-orphan",
    )


class EmailAttachmentRouteContext(Base):
    """Occurrence-specific routing context for one artifact parser profile."""

    __tablename__ = "email_attachment_route_context"
    __table_args__ = (
        UniqueConstraint(
            "email_message_attachment_id",
            "email_attachment_parse_id",
            name="uq_email_attachment_route_context_attachment_parse",
        ),
        Index(
            "ix_email_attachment_route_context_parse",
            "email_attachment_parse_id",
        ),
    )

    email_attachment_route_context_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    email_message_attachment_id: Mapped[int] = mapped_column(
        ForeignKey(
            "email_message_attachment.email_message_attachment_id",
            name="fk_email_attachment_route_context_message_attachment",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    email_attachment_parse_id: Mapped[int] = mapped_column(
        ForeignKey(
            "email_attachment_parse.email_attachment_parse_id",
            name="fk_email_attachment_route_context_parse",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    routing_context_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    message_attachment: Mapped[EmailMessageAttachment] = relationship(
        back_populates="route_contexts"
    )
    attachment_parse: Mapped[EmailAttachmentParse] = relationship(
        back_populates="route_contexts"
    )
    candidate_routes: Mapped[list["EmailNavCandidateRoute"]] = relationship(
        back_populates="route_context",
        cascade="all, delete-orphan",
    )


class EmailNavCandidate(Base):
    """Occurrence-neutral parsed NAV evidence for one content-addressed artifact."""

    __tablename__ = "email_nav_candidate"
    __table_args__ = (
        CheckConstraint(
            "row_ordinal >= 0",
            name="row_ordinal_nonnegative",
        ),
        UniqueConstraint(
            "email_attachment_parse_id",
            "row_ordinal",
            name="uq_email_nav_candidate_parse_row",
        ),
        Index(
            "ix_email_nav_candidate_normalized_code",
            "normalized_instrument_code",
        ),
    )

    email_nav_candidate_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    email_attachment_parse_id: Mapped[int] = mapped_column(
        ForeignKey(
            "email_attachment_parse.email_attachment_parse_id",
            name="fk_email_nav_candidate_parse",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    row_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    instrument_code: Mapped[str | None] = mapped_column(String(256))
    instrument_name: Mapped[str | None] = mapped_column(Text)
    normalized_instrument_code: Mapped[str | None] = mapped_column(String(256))
    normalized_instrument_name: Mapped[str | None] = mapped_column(Text)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    unit_nav_value: Mapped[str | None] = mapped_column(Text)
    reported_cash_cumulative_nav_value: Mapped[str | None] = mapped_column(Text)
    reported_reinvested_nav_value: Mapped[str | None] = mapped_column(Text)
    reported_frequency: Mapped[str | None] = mapped_column(String(32))
    raw_row_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    attachment_parse: Mapped[EmailAttachmentParse] = relationship(
        back_populates="nav_candidates"
    )
    routes: Mapped[list["EmailNavCandidateRoute"]] = relationship(
        back_populates="nav_candidate",
        cascade="all, delete-orphan",
    )


class EmailNavCandidateRoute(Base):
    """Routing and provenance for one parsed row in one message occurrence."""

    __tablename__ = "email_nav_candidate_route"
    __table_args__ = (
        CheckConstraint(
            "routing_status IN ('pending', 'matched', 'unmatched', 'ambiguous')",
            name="routing_status_contract",
        ),
        CheckConstraint(
            "validation_status IN ('pending', 'valid', 'rejected', 'conflict')",
            name="validation_status_contract",
        ),
        UniqueConstraint(
            "email_nav_candidate_id",
            "email_attachment_route_context_id",
            name="uq_email_nav_candidate_route_candidate_context",
        ),
        Index(
            "ix_email_nav_candidate_route_discovery",
            "routing_status",
            "validation_status",
        ),
        Index(
            "ix_email_nav_candidate_route_instrument",
            "instrument_id",
        ),
    )

    email_nav_candidate_route_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    email_nav_candidate_id: Mapped[int] = mapped_column(
        ForeignKey(
            "email_nav_candidate.email_nav_candidate_id",
            name="fk_email_nav_candidate_route_candidate",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    email_attachment_route_context_id: Mapped[int] = mapped_column(
        ForeignKey(
            "email_attachment_route_context.email_attachment_route_context_id",
            name="fk_email_nav_candidate_route_context",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    routing_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    validation_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    instrument_id: Mapped[str | None] = mapped_column(String)
    routing_rule_fingerprint: Mapped[str | None] = mapped_column(String(64))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    nav_candidate: Mapped[EmailNavCandidate] = relationship(back_populates="routes")
    route_context: Mapped[EmailAttachmentRouteContext] = relationship(
        back_populates="candidate_routes"
    )


class PlatformMetadata(Base):
    """Versioned manifests for Platform-private operational migrations."""

    __tablename__ = "platform_metadata"

    metadata_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class FundNavRawObservation(Base):
    """Non-canonical NAV evidence retained before semantic publication.

    Ordinary cash-cumulative NAV belongs here, never in the shared quote table.
    Legacy total-return values are retained as unverified evidence so a strict
    migration can delete unsafe canonical values without destroying history.
    """

    __tablename__ = "fund_nav_raw_observation"
    __table_args__ = (
        CheckConstraint(
            "total_return_semantics IN "
            "('absent', 'provider_explicit', 'legacy_unverified')",
            name="total_return_semantics",
        ),
        CheckConstraint(
            "unit_nav_value IS NOT NULL OR cash_cumulative_nav_value IS NOT NULL "
            "OR observed_total_return_nav_value IS NOT NULL",
            name="has_value",
        ),
        CheckConstraint(
            "(unit_nav_value IS NULL AND unit_nav_status IS NULL) OR "
            "(unit_nav_value IS NOT NULL AND unit_nav_status IN "
            "('complete', 'partial', 'unavailable'))",
            name="unit_nav_status",
        ),
        CheckConstraint(
            "(cash_cumulative_nav_value IS NULL AND cash_cumulative_nav_status IS NULL) OR "
            "(cash_cumulative_nav_value IS NOT NULL AND cash_cumulative_nav_status IN "
            "('complete', 'partial', 'unavailable'))",
            name="cash_cumulative_nav_status",
        ),
        CheckConstraint(
            "(observed_total_return_nav_value IS NULL "
            "AND observed_total_return_nav_status IS NULL) OR "
            "(observed_total_return_nav_value IS NOT NULL "
            "AND observed_total_return_nav_status IN "
            "('complete', 'partial', 'unavailable'))",
            name="total_return_nav_status",
        ),
        UniqueConstraint(
            "instrument_id",
            "as_of_date",
            "currency",
            "source_kind",
            "source_ref",
            name="uq_fund_nav_raw_observation_identity",
        ),
        Index(
            "ix_fund_nav_raw_observation_instrument_date",
            "instrument_id",
            "as_of_date",
        ),
    )

    fund_nav_raw_observation_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    instrument_id: Mapped[str] = mapped_column(String, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    unit_nav_value: Mapped[str | None] = mapped_column(Text)
    cash_cumulative_nav_value: Mapped[str | None] = mapped_column(Text)
    observed_total_return_nav_value: Mapped[str | None] = mapped_column(Text)
    unit_nav_status: Mapped[str | None] = mapped_column(String(32))
    cash_cumulative_nav_status: Mapped[str | None] = mapped_column(String(32))
    observed_total_return_nav_status: Mapped[str | None] = mapped_column(String(32))
    total_return_semantics: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="absent",
        server_default=text("'absent'"),
    )
    unit_nav_source_provider: Mapped[str | None] = mapped_column(String)
    cash_cumulative_source_provider: Mapped[str | None] = mapped_column(String)
    total_return_source_provider: Mapped[str | None] = mapped_column(String)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    evidence_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class FundNavActionCandidate(Base):
    """Platform-private interval signal awaiting an evidence-backed fund action.

    A candidate records only the cash-balance discontinuity that was observable
    between two NAV disclosures.  It deliberately has no effective-date or
    reinvestment-NAV fields; those facts belong to a confirmed Registry event.
    """

    __tablename__ = "fund_nav_action_candidate"
    __table_args__ = (
        CheckConstraint(
            "candidate_type IN "
            "('cash_distribution_signal', 'cash_balance_discontinuity')",
            name="candidate_type_contract",
        ),
        CheckConstraint(
            "interval_start_date < interval_end_date",
            name="ordered_interval",
        ),
        CheckConstraint(
            "(candidate_type = 'cash_distribution_signal' "
            "AND CAST(observed_cash_delta AS NUMERIC) > 0) OR "
            "(candidate_type = 'cash_balance_discontinuity' "
            "AND CAST(observed_cash_delta AS NUMERIC) <= 0)",
            name="candidate_delta_contract",
        ),
        CheckConstraint(
            "CAST(measurement_uncertainty AS NUMERIC) > 0",
            name="positive_measurement_uncertainty",
        ),
        CheckConstraint(
            "status IN "
            "('open', 'confirming', 'superseded', 'resolved', 'rejected')",
            name="status_contract",
        ),
        CheckConstraint(
            "(status IN ('confirming', 'resolved') "
            "AND resolved_fund_nav_event_id IS NOT NULL "
            "AND length(trim(resolved_fund_nav_event_id)) > 0) OR "
            "(status NOT IN ('confirming', 'resolved') "
            "AND resolved_fund_nav_event_id IS NULL)",
            name="resolution_contract",
        ),
        CheckConstraint(
            "(status = 'rejected' AND rejection_reason IS NOT NULL "
            "AND length(trim(rejection_reason)) > 0) OR "
            "(status <> 'rejected' AND rejection_reason IS NULL)",
            name="rejection_contract",
        ),
        CheckConstraint(
            "(status IN ('confirming', 'resolved', 'rejected') "
            "AND decision_by IS NOT NULL "
            "AND length(trim(decision_by)) > 0) OR "
            "(status NOT IN ('confirming', 'resolved', 'rejected') "
            "AND decision_by IS NULL)",
            name="decision_audit_contract",
        ),
        CheckConstraint(
            "(status IN ('confirming', 'resolved') "
            "AND confirmation_client_mutation_id IS NOT NULL "
            "AND length(trim(confirmation_client_mutation_id)) > 0 "
            "AND confirmation_request_fingerprint IS NOT NULL "
            "AND length(confirmation_request_fingerprint) = 64 "
            "AND confirmation_request_json IS NOT NULL "
            "AND length(trim(CAST(confirmation_request_json AS TEXT))) > 2 "
            "AND lower(trim(CAST(confirmation_request_json AS TEXT))) "
            "NOT IN ('{}', 'null')) OR "
            "(status NOT IN ('confirming', 'resolved') "
            "AND confirmation_client_mutation_id IS NULL "
            "AND confirmation_request_fingerprint IS NULL "
            "AND confirmation_request_json IS NULL)",
            name="confirmation_intent_contract",
        ),
        CheckConstraint(
            "length(trim(fund_nav_action_candidate_id)) > 0 "
            "AND length(trim(instrument_id)) > 0 "
            "AND length(trim(source_provider)) > 0 "
            "AND length(source_revision) = 64",
            name="identity_contract",
        ),
        CheckConstraint(
            "length(trim(CAST(source_evidence_json AS TEXT))) > 2 "
            "AND lower(trim(CAST(source_evidence_json AS TEXT))) "
            "NOT IN ('{}', 'null')",
            name="evidence_contract",
        ),
        UniqueConstraint(
            "instrument_id",
            "candidate_type",
            "interval_start_date",
            "interval_end_date",
            "source_revision",
            name="uq_fund_nav_action_candidate_source_revision",
        ),
        Index(
            "ix_fund_nav_action_candidate_instrument_status_end",
            "instrument_id",
            "status",
            "interval_end_date",
        ),
        Index(
            "uq_fund_nav_action_candidate_open_projection",
            "instrument_id",
            "candidate_type",
            "interval_start_date",
            "interval_end_date",
            unique=True,
            sqlite_where=text("status = 'open'"),
            postgresql_where=text("status = 'open'"),
        ),
    )

    fund_nav_action_candidate_id: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )
    instrument_id: Mapped[str] = mapped_column(String, nullable=False)
    candidate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    interval_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    interval_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    observed_cash_balance_before: Mapped[Decimal] = mapped_column(
        ExactFundNavDecimal(),
        nullable=False,
    )
    observed_cash_balance_after: Mapped[Decimal] = mapped_column(
        ExactFundNavDecimal(),
        nullable=False,
    )
    observed_cash_delta: Mapped[Decimal] = mapped_column(
        ExactFundNavDecimal(),
        nullable=False,
    )
    expected_cash_balance: Mapped[Decimal | None] = mapped_column(
        ExactFundNavDecimal()
    )
    measurement_uncertainty: Mapped[Decimal] = mapped_column(
        ExactFundNavDecimal(),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="open",
        server_default=text("'open'"),
    )
    source_provider: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    source_evidence_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
    )
    resolved_fund_nav_event_id: Mapped[str | None] = mapped_column(String)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    decision_by: Mapped[str | None] = mapped_column(String(512))
    confirmation_client_mutation_id: Mapped[str | None] = mapped_column(
        String(200)
    )
    confirmation_request_fingerprint: Mapped[str | None] = mapped_column(
        String(64)
    )
    confirmation_request_json: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
