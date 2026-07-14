from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from watchlist_app.db.base import Base


class RecalcJob(Base):
    __tablename__ = "recalc_job"
    __table_args__ = (
        Index(
            "uq_recalc_job_open_dedupe_key",
            "dedupe_key",
            unique=True,
            sqlite_where=text("job_status IN ('queued', 'running')"),
            postgresql_where=text("job_status IN ('queued', 'running')"),
        ),
        Index(
            "uq_recalc_job_source_event_identity",
            "trigger_ref_type",
            "trigger_ref_id",
            "instrument_id",
            "job_type",
            unique=True,
            sqlite_where=text(
                "trigger_ref_type IS NOT NULL AND TRIM(trigger_ref_type) <> '' "
                "AND trigger_ref_id IS NOT NULL AND TRIM(trigger_ref_id) <> ''"
            ),
            postgresql_where=text(
                "trigger_ref_type IS NOT NULL AND TRIM(trigger_ref_type) <> '' "
                "AND trigger_ref_id IS NOT NULL AND TRIM(trigger_ref_id) <> ''"
            ),
        ),
        Index(
            "idx_recalc_job_claim",
            "job_status",
            "available_at",
            "priority",
            "enqueued_at",
        ),
        Index(
            "uq_recalc_job_running_instrument",
            "instrument_id",
            unique=True,
            sqlite_where=text("job_status = 'running'"),
            postgresql_where=text("job_status = 'running'"),
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 "
            "AND attempt_count <= max_attempts",
            name="attempt_budget",
        ),
        CheckConstraint(
            "claimed_generation IS NULL OR claimed_generation > 0",
            name="claimed_generation",
        ),
    )

    recalc_job_id: Mapped[str] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(nullable=False)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        nullable=False,
    )
    trigger_type: Mapped[str] = mapped_column(nullable=False)
    trigger_ref_type: Mapped[str | None]
    trigger_ref_id: Mapped[str | None]
    job_status: Mapped[str] = mapped_column(nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    dedupe_key: Mapped[str] = mapped_column(nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default=text("3"),
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    claimed_generation: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None]


class RecalcWorkerRegistration(Base):
    __tablename__ = "recalc_worker_registration"
    __table_args__ = (
        Index(
            "idx_recalc_worker_registration_last_heartbeat",
            "last_heartbeat_at",
        ),
        CheckConstraint(
            "worker_state IN ('running', 'stopped')",
            name="state",
        ),
        CheckConstraint(
            "(worker_state = 'running' AND stopped_at IS NULL) OR "
            "(worker_state = 'stopped' AND stopped_at IS NOT NULL)",
            name="lifecycle",
        ),
    )

    worker_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    worker_version: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_successful_poll_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    last_poll_error: Mapped[str | None] = mapped_column(String(4000))
    worker_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="running",
        server_default=text("'running'"),
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )


class RecalcSourceEventInbox(Base):
    __tablename__ = "recalc_source_event_inbox"
    __table_args__ = (
        UniqueConstraint(
            "trigger_ref_type",
            "trigger_ref_id",
            "instrument_id",
            "job_type",
            name="uq_recalc_source_event_inbox_identity",
        ),
        Index(
            "idx_recalc_source_event_inbox_pending",
            "instrument_id",
            "job_type",
            "consumed_at",
            "generation",
        ),
        CheckConstraint(
            "TRIM(trigger_ref_type) <> '' AND TRIM(trigger_ref_id) <> '' "
            "AND TRIM(instrument_id) <> '' AND TRIM(job_type) <> ''",
            name="identity",
        ),
        CheckConstraint(
            "disposition IN ('recalc', 'ignored')",
            name="disposition",
        ),
        CheckConstraint(
            "(disposition = 'recalc' AND generation IS NOT NULL "
            "AND generation > 0) OR "
            "(disposition = 'ignored' AND generation IS NULL "
            "AND consumed_at IS NOT NULL)",
            name="lifecycle",
        ),
    )

    source_event_inbox_id: Mapped[str] = mapped_column(String, primary_key=True)
    trigger_ref_type: Mapped[str] = mapped_column(String, nullable=False)
    trigger_ref_id: Mapped[str] = mapped_column(String, nullable=False)
    instrument_id: Mapped[str] = mapped_column(String, nullable=False)
    job_type: Mapped[str] = mapped_column(String, nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    generation: Mapped[int | None] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecalcInvalidationState(Base):
    __tablename__ = "recalc_invalidation_state"
    __table_args__ = (
        Index(
            "idx_recalc_invalidation_state_pending",
            "requested_generation",
            "completed_generation",
        ),
        CheckConstraint(
            "requested_generation >= 0 AND completed_generation >= 0 "
            "AND completed_generation <= requested_generation",
            name="generation_order",
        ),
    )

    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_detail.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    job_type: Mapped[str] = mapped_column(String, primary_key=True)
    requested_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    completed_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
