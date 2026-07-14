from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from portfolio_ops_calculation_core.contracts import (
    CALCULATION_REGISTRY_SCHEMA,
    JsonValue,
)
from portfolio_ops_calculation_core.state import (
    CalculationJobStatus,
    CalculationManifestStatus,
    CalculationRunStatus,
    RecomputeIntentStatus,
)


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
EMPTY_JSON_OBJECT_DEFAULT = text("'{}'::jsonb")
GENERATED_UUID_DEFAULT = text("gen_random_uuid()")
NOW_DEFAULT = text("clock_timestamp()")


def _enum_type(enum_type: type[StrEnum], *, name: str, length: int) -> SqlEnum:
    return SqlEnum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
        length=length,
    )


def _values_sql(enum_type: type[StrEnum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_type)


class CalculationRegistryBase(DeclarativeBase):
    metadata = MetaData(
        schema=CALCULATION_REGISTRY_SCHEMA,
        naming_convention=NAMING_CONVENTION,
    )


class CalculationScopeGeneration(CalculationRegistryBase):
    __tablename__ = "calculation_scope_generation"
    __table_args__ = (
        CheckConstraint(
            "btrim(calculation_kind) <> '' AND calculation_kind = btrim(calculation_kind)",
            name="ck_calc_scope_generation_kind",
        ),
        CheckConstraint(
            "btrim(scope_kind) <> '' AND scope_kind = btrim(scope_kind)",
            name="ck_calc_scope_generation_scope_kind",
        ),
        CheckConstraint(
            "btrim(scope_id) <> '' AND scope_id = btrim(scope_id)",
            name="ck_calc_scope_generation_scope_id",
        ),
        CheckConstraint(
            "generation >= 0",
            name="ck_calc_scope_generation_nonnegative",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_calc_scope_generation_timestamps",
        ),
        Index(
            "ix_calc_scope_generation_kind_generation",
            "calculation_kind",
            "generation",
        ),
    )

    calculation_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )


class CalculationRun(CalculationRegistryBase):
    __tablename__ = "calculation_run"
    __table_args__ = (
        ForeignKeyConstraint(
            ("calculation_kind", "scope_kind", "scope_id"),
            (
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_scope_generation.calculation_kind",
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_scope_generation.scope_kind",
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_scope_generation.scope_id",
            ),
            name="fk_calculation_run_scope_generation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("manifest_id", "run_id"),
            (
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_input_manifest.manifest_id",
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_input_manifest.run_id",
            ),
            name="fk_calculation_run_manifest_pair",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint("manifest_id", name="uq_calculation_run_manifest"),
        CheckConstraint(
            f"status IN ({_values_sql(CalculationRunStatus)})",
            name="ck_calculation_run_status",
        ),
        CheckConstraint(
            "captured_generation >= 0",
            name="ck_calculation_run_generation",
        ),
        CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$'",
            name="ck_calculation_run_dedupe_hash",
        ),
        CheckConstraint(
            "published_output_hash IS NULL OR "
            "published_output_hash ~ '^[0-9a-f]{64}$'",
            name="ck_calculation_run_output_hash",
        ),
        CheckConstraint(
            "jsonb_typeof(status_reason_context) = 'object'",
            name="ck_calculation_run_reason_context",
        ),
        CheckConstraint(
            "status_reason_code IS NULL OR "
            "(btrim(status_reason_code) <> '' "
            "AND status_reason_code = btrim(status_reason_code))",
            name="ck_calculation_run_reason_code",
        ),
        CheckConstraint(
            "effective_as_of <= requested_as_of",
            name="ck_calculation_run_as_of_order",
        ),
        CheckConstraint(
            "btrim(calculation_kind) <> '' AND calculation_kind = btrim(calculation_kind) "
            "AND btrim(scope_kind) <> '' AND scope_kind = btrim(scope_kind) "
            "AND btrim(scope_id) <> '' AND scope_id = btrim(scope_id) "
            "AND btrim(timezone) <> '' AND timezone = btrim(timezone) "
            "AND btrim(methodology_version) <> '' "
            "AND methodology_version = btrim(methodology_version) "
            "AND btrim(input_schema_version) <> '' "
            "AND input_schema_version = btrim(input_schema_version) "
            "AND btrim(output_schema_version) <> '' "
            "AND output_schema_version = btrim(output_schema_version) "
            "AND btrim(requested_by) <> '' AND requested_by = btrim(requested_by)",
            name="ck_calculation_run_nonblank_fields",
        ),
        CheckConstraint(
            "(started_at IS NULL OR started_at >= created_at) "
            "AND (completed_at IS NULL OR completed_at >= created_at) "
            "AND (started_at IS NULL OR completed_at IS NULL "
            "OR completed_at >= started_at)",
            name="ck_calculation_run_timestamp_order",
        ),
        CheckConstraint(
            "(status = 'capturing' AND manifest_id IS NULL AND started_at IS NULL "
            "AND completed_at IS NULL AND published_output_hash IS NULL "
            "AND superseded_by_run_id IS NULL) "
            "OR (status = 'queued' AND manifest_id IS NOT NULL AND started_at IS NULL "
            "AND completed_at IS NULL AND published_output_hash IS NULL "
            "AND superseded_by_run_id IS NULL) "
            "OR (status = 'running' AND manifest_id IS NOT NULL AND started_at IS NOT NULL "
            "AND completed_at IS NULL AND published_output_hash IS NULL "
            "AND superseded_by_run_id IS NULL) "
            "OR (status = 'succeeded' AND manifest_id IS NOT NULL AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND published_output_hash IS NULL "
            "AND superseded_by_run_id IS NULL) "
            "OR (status = 'published' AND manifest_id IS NOT NULL AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND published_output_hash IS NOT NULL "
            "AND superseded_by_run_id IS NULL) "
            "OR (status = 'failed' AND completed_at IS NOT NULL "
            "AND status_reason_code IS NOT NULL AND published_output_hash IS NULL "
            "AND superseded_by_run_id IS NULL) "
            "OR (status = 'superseded' AND completed_at IS NOT NULL "
            "AND status_reason_code IS NOT NULL AND published_output_hash IS NULL "
            "AND superseded_by_run_id IS NOT NULL)",
            name="ck_calculation_run_state_fields",
        ),
        CheckConstraint(
            "superseded_by_run_id IS NULL OR superseded_by_run_id <> run_id",
            name="ck_calculation_run_not_self_superseded",
        ),
        Index(
            "uq_calculation_run_active_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text(
                "status IN ('capturing', 'queued', 'running', 'succeeded')"
            ),
        ),
        Index(
            "ix_calculation_run_status_created",
            "status",
            "created_at",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=GENERATED_UUID_DEFAULT,
    )
    calculation_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_as_of: Mapped[date] = mapped_column(Date, nullable=False)
    effective_as_of: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("transaction_timestamp()"),
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    methodology_version: Mapped[str] = mapped_column(String(128), nullable=False)
    input_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    status: Mapped[CalculationRunStatus] = mapped_column(
        _enum_type(CalculationRunStatus, name="calculation_run_status", length=24),
        nullable=False,
        server_default=text("'capturing'"),
    )
    status_reason_code: Mapped[str | None] = mapped_column(String(64))
    status_reason_context: Mapped[dict[str, JsonValue]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=EMPTY_JSON_OBJECT_DEFAULT,
    )
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manifest_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    published_output_hash: Mapped[str | None] = mapped_column(CHAR(64))
    superseded_by_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            f"{CALCULATION_REGISTRY_SCHEMA}.calculation_run.run_id",
            name="fk_calculation_run_superseded_by",
            ondelete="RESTRICT",
        ),
    )


class CalculationInputManifest(CalculationRegistryBase):
    __tablename__ = "calculation_input_manifest"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_calculation_manifest_run"),
        UniqueConstraint(
            "manifest_id",
            "run_id",
            name="uq_calculation_manifest_id_run",
        ),
        CheckConstraint(
            "captured_generation >= 0",
            name="ck_calculation_manifest_generation",
        ),
        CheckConstraint(
            "btrim(schema_version) <> '' AND schema_version = btrim(schema_version)",
            name="ck_calculation_manifest_schema_version",
        ),
        CheckConstraint(
            f"status IN ({_values_sql(CalculationManifestStatus)})",
            name="ck_calculation_manifest_status",
        ),
        CheckConstraint(
            "canonical_manifest_hash IS NULL OR "
            "canonical_manifest_hash ~ '^[0-9a-f]{64}$'",
            name="ck_calculation_manifest_hash",
        ),
        CheckConstraint(
            "jsonb_typeof(dependency_counts) = 'object'",
            name="ck_calculation_manifest_dependency_counts",
        ),
        CheckConstraint(
            "(status = 'building' AND canonical_manifest_hash IS NULL AND sealed_at IS NULL) "
            "OR (status = 'sealed' AND canonical_manifest_hash IS NOT NULL "
            "AND sealed_at IS NOT NULL AND sealed_at >= created_at)",
            name="ck_calculation_manifest_state_fields",
        ),
    )

    manifest_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=GENERATED_UUID_DEFAULT,
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            f"{CALCULATION_REGISTRY_SCHEMA}.calculation_run.run_id",
            name="fk_calculation_manifest_run",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    captured_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[CalculationManifestStatus] = mapped_column(
        _enum_type(
            CalculationManifestStatus,
            name="calculation_manifest_status",
            length=16,
        ),
        nullable=False,
        server_default=text("'building'"),
    )
    canonical_manifest_hash: Mapped[str | None] = mapped_column(CHAR(64))
    dependency_counts: Mapped[dict[str, int]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=EMPTY_JSON_OBJECT_DEFAULT,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CalculationJob(CalculationRegistryBase):
    __tablename__ = "calculation_job"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_calculation_job_run"),
        CheckConstraint(
            f"status IN ({_values_sql(CalculationJobStatus)})",
            name="ck_calculation_job_status",
        ),
        CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$'",
            name="ck_calculation_job_dedupe_hash",
        ),
        CheckConstraint(
            "attempt >= 0 AND max_attempts > 0 AND attempt <= max_attempts "
            "AND fencing_token >= 0",
            name="ck_calculation_job_attempts",
        ),
        CheckConstraint(
            "lease_owner IS NULL OR (btrim(lease_owner) <> '' "
            "AND lease_owner = btrim(lease_owner))",
            name="ck_calculation_job_lease_owner",
        ),
        CheckConstraint(
            "failure_code IS NULL OR (btrim(failure_code) <> '' "
            "AND failure_code = btrim(failure_code))",
            name="ck_calculation_job_failure_code",
        ),
        CheckConstraint(
            "failure_diagnostic IS NULL OR (btrim(failure_diagnostic) <> '' "
            "AND failure_diagnostic = btrim(failure_diagnostic))",
            name="ck_calculation_job_failure_diagnostic",
        ),
        CheckConstraint(
            "(heartbeat_at IS NULL OR heartbeat_at >= created_at) "
            "AND (lease_expires_at IS NULL OR lease_expires_at >= created_at) "
            "AND (heartbeat_at IS NULL OR lease_expires_at IS NULL "
            "OR lease_expires_at > heartbeat_at) "
            "AND updated_at >= created_at "
            "AND (completed_at IS NULL OR completed_at >= created_at)",
            name="ck_calculation_job_timestamps",
        ),
        CheckConstraint(
            "(status = 'queued' AND attempt = 0 AND fencing_token = 0 "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "AND heartbeat_at IS NULL AND failure_code IS NULL "
            "AND failure_diagnostic IS NULL AND completed_at IS NULL) "
            "OR (status = 'leased' AND attempt >= 1 AND fencing_token = attempt "
            "AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND failure_code IS NULL "
            "AND failure_diagnostic IS NULL AND completed_at IS NULL) "
            "OR (status = 'retry_wait' AND attempt >= 1 AND fencing_token = attempt "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "AND heartbeat_at IS NULL AND failure_code IS NOT NULL "
            "AND completed_at IS NULL) "
            "OR (status = 'succeeded' AND attempt >= 1 AND fencing_token = attempt "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "AND heartbeat_at IS NULL AND failure_code IS NULL "
            "AND failure_diagnostic IS NULL AND completed_at IS NOT NULL) "
            "OR (status = 'failed' AND attempt >= 1 AND fencing_token = attempt "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "AND heartbeat_at IS NULL AND failure_code IS NOT NULL "
            "AND completed_at IS NOT NULL) "
            "OR (status = 'superseded' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND heartbeat_at IS NULL "
            "AND failure_code IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_calculation_job_state_fields",
        ),
        Index(
            "uq_calculation_job_active_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'leased', 'retry_wait')"),
        ),
        Index(
            "ix_calculation_job_queue_claim",
            "status",
            "available_at",
            "created_at",
            postgresql_where=text("status IN ('queued', 'retry_wait')"),
        ),
        Index(
            "ix_calculation_job_expired_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'leased'"),
        ),
    )

    job_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=GENERATED_UUID_DEFAULT,
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            f"{CALCULATION_REGISTRY_SCHEMA}.calculation_run.run_id",
            name="fk_calculation_job_run",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    dedupe_key: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    status: Mapped[CalculationJobStatus] = mapped_column(
        _enum_type(CalculationJobStatus, name="calculation_job_status", length=24),
        nullable=False,
        server_default=text("'queued'"),
    )
    attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("3"),
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_diagnostic: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CalculationPublication(CalculationRegistryBase):
    __tablename__ = "calculation_publication"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_calculation_publication_run"),
        UniqueConstraint("manifest_id", name="uq_calculation_publication_manifest"),
        UniqueConstraint(
            "publication_id",
            "calculation_kind",
            "scope_kind",
            "scope_id",
            name="uq_calculation_publication_scope",
        ),
        ForeignKeyConstraint(
            ("manifest_id", "run_id"),
            (
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_input_manifest.manifest_id",
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_input_manifest.run_id",
            ),
            name="fk_calculation_publication_manifest_run",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "published_fencing_token > 0",
            name="ck_calculation_publication_fencing_token",
        ),
        CheckConstraint(
            "canonical_output_hash ~ '^[0-9a-f]{64}$'",
            name="ck_calculation_publication_output_hash",
        ),
        CheckConstraint(
            "btrim(calculation_kind) <> '' AND calculation_kind = btrim(calculation_kind) "
            "AND btrim(scope_kind) <> '' AND scope_kind = btrim(scope_kind) "
            "AND btrim(scope_id) <> '' AND scope_id = btrim(scope_id) "
            "AND btrim(output_schema_version) <> '' "
            "AND output_schema_version = btrim(output_schema_version)",
            name="ck_calculation_publication_nonblank_fields",
        ),
    )

    publication_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=GENERATED_UUID_DEFAULT,
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            f"{CALCULATION_REGISTRY_SCHEMA}.calculation_run.run_id",
            name="fk_calculation_publication_run",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    manifest_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    calculation_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(255), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    published_fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    canonical_output_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )


class CalculationCurrentPublication(CalculationRegistryBase):
    __tablename__ = "calculation_current_publication"
    __table_args__ = (
        ForeignKeyConstraint(
            ("calculation_kind", "scope_kind", "scope_id"),
            (
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_scope_generation.calculation_kind",
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_scope_generation.scope_kind",
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_scope_generation.scope_id",
            ),
            name="fk_calculation_current_scope_generation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("publication_id", "calculation_kind", "scope_kind", "scope_id"),
            (
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_publication.publication_id",
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_publication.calculation_kind",
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_publication.scope_kind",
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_publication.scope_id",
            ),
            name="fk_calculation_current_publication_scope",
            ondelete="RESTRICT",
        ),
    )

    calculation_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    publication_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )


class CalculationRecomputeIntent(CalculationRegistryBase):
    __tablename__ = "calculation_recompute_intent"
    __table_args__ = (
        ForeignKeyConstraint(
            ("calculation_kind", "scope_kind", "scope_id"),
            (
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_scope_generation.calculation_kind",
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_scope_generation.scope_kind",
                f"{CALCULATION_REGISTRY_SCHEMA}.calculation_scope_generation.scope_id",
            ),
            name="fk_calculation_intent_scope_generation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("dedupe_key", name="uq_calculation_intent_dedupe"),
        UniqueConstraint(
            "calculation_kind",
            "scope_kind",
            "scope_id",
            "requested_generation",
            name="uq_calculation_intent_scope_generation",
        ),
        CheckConstraint(
            "requested_generation >= 0",
            name="ck_calculation_intent_generation",
        ),
        CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$'",
            name="ck_calculation_intent_dedupe_hash",
        ),
        CheckConstraint(
            "btrim(reason_code) <> '' AND reason_code = btrim(reason_code)",
            name="ck_calculation_intent_reason_code",
        ),
        CheckConstraint(
            "jsonb_typeof(reason_context) = 'object'",
            name="ck_calculation_intent_reason_context",
        ),
        CheckConstraint(
            f"status IN ({_values_sql(RecomputeIntentStatus)})",
            name="ck_calculation_intent_status",
        ),
        CheckConstraint(
            "status_reason_code IS NULL OR (btrim(status_reason_code) <> '' "
            "AND status_reason_code = btrim(status_reason_code))",
            name="ck_calculation_intent_status_reason_code",
        ),
        CheckConstraint(
            "(status = 'pending' AND run_id IS NULL AND status_reason_code IS NULL "
            "AND completed_at IS NULL) OR "
            "(status = 'materialized' AND run_id IS NOT NULL "
            "AND status_reason_code IS NULL AND completed_at IS NOT NULL) OR "
            "(status IN ('superseded', 'failed') AND status_reason_code IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name="ck_calculation_intent_state_fields",
        ),
        CheckConstraint(
            "updated_at >= created_at "
            "AND (completed_at IS NULL OR completed_at >= created_at)",
            name="ck_calculation_intent_timestamps",
        ),
        Index(
            "ix_calculation_intent_pending",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    intent_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=GENERATED_UUID_DEFAULT,
    )
    calculation_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_context: Mapped[dict[str, JsonValue]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=EMPTY_JSON_OBJECT_DEFAULT,
    )
    status: Mapped[RecomputeIntentStatus] = mapped_column(
        _enum_type(
            RecomputeIntentStatus,
            name="calculation_recompute_intent_status",
            length=24,
        ),
        nullable=False,
        server_default=text("'pending'"),
    )
    run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            f"{CALCULATION_REGISTRY_SCHEMA}.calculation_run.run_id",
            name="fk_calculation_intent_run",
            ondelete="RESTRICT",
        ),
    )
    status_reason_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CalculationWorkerHeartbeat(CalculationRegistryBase):
    __tablename__ = "calculation_worker_heartbeat"
    __table_args__ = (
        CheckConstraint(
            "btrim(worker_id) <> '' AND worker_id = btrim(worker_id) "
            "AND btrim(worker_version) <> '' "
            "AND worker_version = btrim(worker_version)",
            name="ck_calculation_worker_nonblank_fields",
        ),
        CheckConstraint(
            "jsonb_typeof(supported_calculation_kinds) = 'array' "
            "AND jsonb_array_length(supported_calculation_kinds) > 0",
            name="ck_calculation_worker_supported_kinds",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object'",
            name="ck_calculation_worker_metadata",
        ),
        CheckConstraint(
            "heartbeat_at >= started_at",
            name="ck_calculation_worker_timestamps",
        ),
        Index("ix_calculation_worker_heartbeat_at", "heartbeat_at"),
    )

    worker_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    instance_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    worker_version: Mapped[str] = mapped_column(String(128), nullable=False)
    supported_calculation_kinds: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW_DEFAULT,
    )
    metadata_json: Mapped[dict[str, JsonValue]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=EMPTY_JSON_OBJECT_DEFAULT,
    )


Index(
    "ix_calculation_run_scope_as_of",
    CalculationRun.calculation_kind,
    CalculationRun.scope_kind,
    CalculationRun.scope_id,
    CalculationRun.effective_as_of.desc(),
)
Index(
    "ix_calculation_publication_scope_published",
    CalculationPublication.calculation_kind,
    CalculationPublication.scope_kind,
    CalculationPublication.scope_id,
    CalculationPublication.published_at.desc(),
)
