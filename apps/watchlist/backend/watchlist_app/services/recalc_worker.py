from __future__ import annotations

from threading import Event, Thread
import logging
import time

from watchlist_app.core.settings import get_settings
from watchlist_app.db.session import get_session_factory
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.canonical_recalc import CanonicalRecalcService


logger = logging.getLogger(__name__)
recalc_repository = SQLAlchemyRecalcJobRepository()
canonical_recalc_service = CanonicalRecalcService()


def process_next_recalc_job() -> bool:
    session_factory = get_session_factory()
    settings = get_settings()

    with session_factory() as session:
        record = recalc_repository.claim_next_queued(
            session,
            running_timeout_seconds=settings.recalc_worker_running_job_timeout_seconds,
        )
        if record is None:
            return False
        job_id = record.recalc_job_id
        session.commit()

    with session_factory() as session:
        record = recalc_repository.get(session, job_id)
        if record is None or record.job_status != "running":
            return False
        try:
            canonical_recalc_service.execute_claimed_job(
                session,
                record=record,
                commit=True,
            )
        except Exception:
            logger.exception("Queued recalc job failed: %s", job_id)
        return True


def drain_recalc_jobs(*, max_jobs: int | None = None) -> int:
    processed = 0
    while max_jobs is None or processed < max_jobs:
        if not process_next_recalc_job():
            break
        processed += 1
    return processed


def run_recalc_worker_loop(
    *,
    stop_event: Event,
    poll_interval_seconds: float | None = None,
) -> None:
    settings = get_settings()
    interval = poll_interval_seconds or settings.recalc_worker_poll_interval_seconds
    while not stop_event.is_set():
        processed = False
        try:
            processed = process_next_recalc_job()
        except Exception:
            logger.exception("Watchlist recalc worker loop failed.")
        if processed:
            continue
        stop_event.wait(interval)


def start_recalc_worker(*, stop_event: Event | None = None) -> tuple[Thread, Event]:
    settings = get_settings()
    effective_stop_event = stop_event or Event()
    worker = Thread(
        target=run_recalc_worker_loop,
        kwargs={
            "stop_event": effective_stop_event,
            "poll_interval_seconds": settings.recalc_worker_poll_interval_seconds,
        },
        name="watchlist-recalc-worker",
        daemon=True,
    )
    worker.start()
    return worker, effective_stop_event
