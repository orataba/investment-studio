from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Interval, bindparam, exists, func, select, update
from sqlalchemy.orm import Session

from watchlist_app.db.models.recalc import RecalcWorkerRegistration


class RecalcWorkerRegistrationError(RuntimeError):
    pass


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


class SQLAlchemyRecalcWorkerRepository:
    def register(
        self,
        session: Session,
        *,
        worker_id: str,
        instance_id: str,
        worker_version: str,
        metadata_json: dict[str, object],
    ) -> RecalcWorkerRegistration:
        now = _database_now(session)
        record = session.get(RecalcWorkerRegistration, worker_id)
        if record is None:
            record = RecalcWorkerRegistration(
                worker_id=worker_id,
                instance_id=instance_id,
                worker_version=worker_version,
                started_at=now,
                last_heartbeat_at=now,
                last_successful_poll_at=None,
                last_poll_error=None,
                worker_state="running",
                stopped_at=None,
                metadata_json=metadata_json,
            )
            session.add(record)
            session.flush()
            return record
        if record.instance_id != instance_id:
            raise RecalcWorkerRegistrationError(
                f"Worker id '{worker_id}' is already registered to another instance."
            )
        record.worker_version = worker_version
        record.last_heartbeat_at = now
        record.last_successful_poll_at = None
        record.last_poll_error = None
        record.worker_state = "running"
        record.stopped_at = None
        record.metadata_json = metadata_json
        session.flush()
        return record

    def touch_heartbeat(
        self,
        session: Session,
        *,
        worker_id: str,
        instance_id: str,
    ) -> bool:
        now = _database_now(session)
        result = session.execute(
            update(RecalcWorkerRegistration)
            .where(
                RecalcWorkerRegistration.worker_id == worker_id,
                RecalcWorkerRegistration.instance_id == instance_id,
                RecalcWorkerRegistration.worker_state == "running",
            )
            .values(last_heartbeat_at=now)
        )
        return int(result.rowcount or 0) == 1

    def record_poll_success(
        self,
        session: Session,
        *,
        worker_id: str,
        instance_id: str,
    ) -> bool:
        now = _database_now(session)
        result = session.execute(
            update(RecalcWorkerRegistration)
            .where(
                RecalcWorkerRegistration.worker_id == worker_id,
                RecalcWorkerRegistration.instance_id == instance_id,
                RecalcWorkerRegistration.worker_state == "running",
            )
            .values(
                last_heartbeat_at=now,
                last_successful_poll_at=now,
                last_poll_error=None,
            )
        )
        return int(result.rowcount or 0) == 1

    def record_poll_failure(
        self,
        session: Session,
        *,
        worker_id: str,
        instance_id: str,
        error_message: str,
    ) -> bool:
        now = _database_now(session)
        result = session.execute(
            update(RecalcWorkerRegistration)
            .where(
                RecalcWorkerRegistration.worker_id == worker_id,
                RecalcWorkerRegistration.instance_id == instance_id,
                RecalcWorkerRegistration.worker_state == "running",
            )
            .values(
                last_heartbeat_at=now,
                last_poll_error=error_message[:4000],
            )
        )
        return int(result.rowcount or 0) == 1

    def stop_worker(
        self,
        session: Session,
        *,
        worker_id: str,
        instance_id: str,
    ) -> bool:
        now = _database_now(session)
        result = session.execute(
            update(RecalcWorkerRegistration)
            .where(
                RecalcWorkerRegistration.worker_id == worker_id,
                RecalcWorkerRegistration.instance_id == instance_id,
                RecalcWorkerRegistration.worker_state == "running",
            )
            .values(
                worker_state="stopped",
                stopped_at=now,
                last_heartbeat_at=now,
            )
        )
        return int(result.rowcount or 0) == 1

    def has_fresh_worker(
        self,
        session: Session,
        *,
        max_age: timedelta,
    ) -> bool:
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive.")
        if session.get_bind().dialect.name == "postgresql":
            max_age_parameter = bindparam(
                "recalc_worker_max_age",
                value=max_age,
                type_=Interval(),
            )
            freshness_threshold = func.clock_timestamp() - max_age_parameter
        else:
            freshness_threshold = _database_now(session) - max_age
        return bool(
            session.scalar(
                select(
                    exists().where(
                        RecalcWorkerRegistration.last_heartbeat_at
                        >= freshness_threshold,
                        RecalcWorkerRegistration.worker_state == "running",
                        RecalcWorkerRegistration.last_successful_poll_at.is_not(None),
                        RecalcWorkerRegistration.last_successful_poll_at
                        >= freshness_threshold,
                    )
                )
            )
        )


__all__ = [
    "RecalcWorkerRegistrationError",
    "SQLAlchemyRecalcWorkerRepository",
]
