from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.repositories.interfaces import RecalcJobRepository
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.recalc_job_ids import make_recalc_dedupe_key, make_recalc_job_id


class RecalcJobService:
    def __init__(self, repository: RecalcJobRepository) -> None:
        self.repository = repository

    def list_recent(self, session: Session, limit: int = 100) -> Sequence[RecalcJob]:
        return self.repository.list_recent(session, limit=limit)

    def get(self, session: Session, job_id: str) -> RecalcJob | None:
        return self.repository.get(session, job_id)


jobs = SQLAlchemyRecalcJobRepository()


def lock_instrument_configuration(session: Session, instrument_ids: list[str]) -> None:
    # Short edits must not wait on the lock held by a full calculation.
    if session.get_bind().dialect.name == "postgresql":
        for instrument_id in sorted(set(instrument_ids)):
            session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:instrument_id, 20260929))"),
                            {"instrument_id": instrument_id})


def queue_configuration_recalculation(session: Session, *, instrument_id: str,
                                      trigger_type: str, trigger_ref_type: str,
                                      trigger_ref_id: str | None,
                                      configuration_changed: bool = True) -> dict[str, str]:
    """Commit an edit and its successor job together, without blocking a worker."""
    lock_instrument_configuration(session, [instrument_id])
    pending = session.scalar(select(RecalcJob).where(RecalcJob.instrument_id == instrument_id,
        RecalcJob.job_type == "all", RecalcJob.job_status == "queued").limit(1).with_for_update(skip_locked=True))
    if pending is None:
        # A worker can hold a previous job's row and unique key while reading
        # old inputs. The successor needs its own identity; later edits coalesce.
        job_id = make_recalc_job_id()
        pending = jobs.create(session, recalc_job_id=job_id, job_type="all",
            instrument_id=instrument_id, trigger_type=trigger_type,
            trigger_ref_type=trigger_ref_type, trigger_ref_id=trigger_ref_id,
            job_status="queued", priority=100,
            dedupe_key=f"{make_recalc_dedupe_key(job_type='all', instrument_id=instrument_id)}:configuration:{job_id}",
            payload_json={"requested_by": trigger_type})
    if configuration_changed:
        # Coalescing into a membership/source-refresh job must still remember
        # that retained calculations no longer describe the saved settings.
        pending.payload_json = {**(pending.payload_json or {}), "configuration_changed": True}
    return {"recalc_job_id": pending.recalc_job_id, "job_status": "queued"}
