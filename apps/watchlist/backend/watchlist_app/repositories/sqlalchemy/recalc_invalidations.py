from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from watchlist_app.db.models.recalc import (
    RecalcInvalidationState,
    RecalcJob,
    RecalcSourceEventInbox,
)


def _database_now(session: Session) -> datetime:
    expression = (
        func.clock_timestamp()
        if session.get_bind().dialect.name == "postgresql"
        else func.current_timestamp()
    )
    value = session.scalar(select(expression))
    if not isinstance(value, datetime):
        raise RuntimeError("Database did not return a valid current timestamp.")
    return value


class SQLAlchemyRecalcInvalidationRepository:
    def get_source_event(
        self,
        session: Session,
        *,
        trigger_ref_type: str,
        trigger_ref_id: str,
        instrument_id: str,
        job_type: str,
    ) -> RecalcSourceEventInbox | None:
        return session.scalar(
            select(RecalcSourceEventInbox).where(
                RecalcSourceEventInbox.trigger_ref_type == trigger_ref_type,
                RecalcSourceEventInbox.trigger_ref_id == trigger_ref_id,
                RecalcSourceEventInbox.instrument_id == instrument_id,
                RecalcSourceEventInbox.job_type == job_type,
            )
        )

    def record_supported_source_event(
        self,
        session: Session,
        *,
        trigger_ref_type: str,
        trigger_ref_id: str,
        instrument_id: str,
        job_type: str,
    ) -> RecalcSourceEventInbox:
        now = _database_now(session)
        dialect_name = session.get_bind().dialect.name
        values = {
            "instrument_id": instrument_id,
            "job_type": job_type,
            "requested_generation": 1,
            "completed_generation": 0,
            "updated_at": now,
        }
        if dialect_name == "postgresql":
            statement = postgresql_insert(RecalcInvalidationState).values(**values)
        elif dialect_name == "sqlite":
            statement = sqlite_insert(RecalcInvalidationState).values(**values)
        else:  # pragma: no cover - runtime supports PostgreSQL; tests use SQLite.
            raise RuntimeError(
                f"Unsupported recalc invalidation database dialect: {dialect_name}"
            )
        generation = session.scalar(
            statement.on_conflict_do_update(
                index_elements=[
                    RecalcInvalidationState.instrument_id,
                    RecalcInvalidationState.job_type,
                ],
                set_={
                    "requested_generation": (
                        RecalcInvalidationState.requested_generation + 1
                    ),
                    "updated_at": now,
                },
            ).returning(RecalcInvalidationState.requested_generation)
        )
        if not isinstance(generation, int) or generation < 1:
            raise RuntimeError("Failed to allocate recalc invalidation generation.")
        record = RecalcSourceEventInbox(
            source_event_inbox_id=uuid4().hex,
            trigger_ref_type=trigger_ref_type,
            trigger_ref_id=trigger_ref_id,
            instrument_id=instrument_id,
            job_type=job_type,
            disposition="recalc",
            generation=generation,
            received_at=now,
            consumed_at=None,
        )
        session.add(record)
        session.flush()
        return record

    def record_ignored_source_event(
        self,
        session: Session,
        *,
        trigger_ref_type: str,
        trigger_ref_id: str,
        instrument_id: str,
        job_type: str,
    ) -> RecalcSourceEventInbox:
        now = _database_now(session)
        record = RecalcSourceEventInbox(
            source_event_inbox_id=uuid4().hex,
            trigger_ref_type=trigger_ref_type,
            trigger_ref_id=trigger_ref_id,
            instrument_id=instrument_id,
            job_type=job_type,
            disposition="ignored",
            generation=None,
            received_at=now,
            consumed_at=now,
        )
        session.add(record)
        session.flush()
        return record

    def get_state(
        self,
        session: Session,
        *,
        instrument_id: str,
        job_type: str,
        for_update: bool = False,
    ) -> RecalcInvalidationState | None:
        statement = select(RecalcInvalidationState).where(
            RecalcInvalidationState.instrument_id == instrument_id,
            RecalcInvalidationState.job_type == job_type,
        )
        if for_update and session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update()
        return session.scalar(statement)

    def capture_claim_generation(
        self,
        session: Session,
        *,
        instrument_id: str,
        job_type: str,
    ) -> int | None:
        state = self.get_state(
            session,
            instrument_id=instrument_id,
            job_type=job_type,
            for_update=True,
        )
        if state is None or state.requested_generation <= state.completed_generation:
            return None
        return state.requested_generation

    def complete_claimed_generation(
        self,
        session: Session,
        *,
        instrument_id: str,
        job_type: str,
        claimed_generation: int | None,
    ) -> int | None:
        state = self.get_state(
            session,
            instrument_id=instrument_id,
            job_type=job_type,
            for_update=True,
        )
        if state is None:
            if claimed_generation is None:
                return None
            raise RuntimeError(
                "Claimed recalc invalidation generation has no persistent state."
            )
        if claimed_generation is None:
            if state.requested_generation > state.completed_generation:
                return state.requested_generation
            return None
        now = _database_now(session)
        completed_generation = max(
            state.completed_generation,
            min(claimed_generation, state.requested_generation),
        )
        state.completed_generation = completed_generation
        state.updated_at = now
        session.execute(
            update(RecalcSourceEventInbox)
            .where(
                RecalcSourceEventInbox.instrument_id == instrument_id,
                RecalcSourceEventInbox.job_type == job_type,
                RecalcSourceEventInbox.disposition == "recalc",
                RecalcSourceEventInbox.generation.is_not(None),
                RecalcSourceEventInbox.generation <= completed_generation,
                RecalcSourceEventInbox.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        session.flush()
        if state.requested_generation > completed_generation:
            return state.requested_generation
        return None

    def has_unresolved_failed_job(
        self,
        session: Session,
        *,
        instrument_id: str,
        job_type: str,
    ) -> bool:
        state = self.get_state(
            session,
            instrument_id=instrument_id,
            job_type=job_type,
        )
        if state is None:
            return False
        return bool(
            session.scalar(
                select(
                    exists().where(
                        RecalcJob.instrument_id == instrument_id,
                        RecalcJob.job_type == job_type,
                        RecalcJob.job_status == "failed",
                        RecalcJob.claimed_generation.is_not(None),
                        RecalcJob.claimed_generation > state.completed_generation,
                    )
                )
            )
        )

    def has_unserviceable_pending_invalidation(self, session: Session) -> bool:
        serviceable_job = exists().where(
            RecalcJob.instrument_id == RecalcInvalidationState.instrument_id,
            RecalcJob.job_type == RecalcInvalidationState.job_type,
            or_(
                and_(
                    RecalcJob.job_status == "queued",
                    RecalcJob.attempt_count < RecalcJob.max_attempts,
                ),
                RecalcJob.job_status == "running",
            ),
        )
        return bool(
            session.scalar(
                select(
                    exists().where(
                        RecalcInvalidationState.requested_generation
                        > RecalcInvalidationState.completed_generation,
                        ~serviceable_job,
                    )
                )
            )
        )


__all__ = ["SQLAlchemyRecalcInvalidationRepository"]
