from __future__ import annotations

from threading import Event, Thread
import logging
import time

from watchlist_app.core.settings import get_settings
from watchlist_app.db.session import get_session_factory
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.canonical_recalc import CanonicalRecalcService
from watchlist_app.services.read_model_freshness import (
    reconcile_stale_instrument_read_models,
)


logger = logging.getLogger(__name__)
recalc_repository = SQLAlchemyRecalcJobRepository()
canonical_recalc_service = CanonicalRecalcService()
MAX_WORKER_ERROR_BACKOFF_SECONDS = 60.0


def _worker_error_backoff_seconds(
    consecutive_failures: int,
    *,
    poll_interval_seconds: float,
) -> float:
    exponent = max(0, min(consecutive_failures - 1, 6))
    return min(
        MAX_WORKER_ERROR_BACKOFF_SECONDS,
        max(1.0, poll_interval_seconds) * (2**exponent),
    )


def _should_log_worker_error(consecutive_failures: int) -> bool:
    return consecutive_failures > 0 and (
        consecutive_failures & (consecutive_failures - 1)
    ) == 0


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
        lease_token = str(record.lease_token or "")
        session.commit()

    with session_factory() as session:
        record = recalc_repository.get(session, job_id)
        if (
            record is None
            or record.job_status != "running"
            or record.lease_token != lease_token
        ):
            return False
        heartbeat_stop = Event()
        heartbeat_thread = None
        if session.get_bind().dialect.name == "postgresql":
            heartbeat_thread = Thread(
                target=_run_claimed_job_heartbeat,
                kwargs={
                    "job_id": job_id,
                    "lease_token": lease_token,
                    "stop_event": heartbeat_stop,
                    "interval_seconds": settings.recalc_worker_heartbeat_interval_seconds,
                },
                name=f"watchlist-recalc-heartbeat-{job_id}",
                daemon=True,
            )
            heartbeat_thread.start()
        try:
            canonical_recalc_service.execute_claimed_job(
                session,
                record=record,
                commit=True,
            )
        except Exception:
            logger.exception("Queued recalc job failed: %s", job_id)
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=max(1.0, settings.recalc_worker_heartbeat_interval_seconds))
        return True


def _run_claimed_job_heartbeat(
    *,
    job_id: str,
    lease_token: str,
    stop_event: Event,
    interval_seconds: float,
) -> None:
    session_factory = get_session_factory()
    while not stop_event.wait(interval_seconds):
        try:
            with session_factory() as session:
                if not recalc_repository.touch_heartbeat(session, job_id, lease_token):
                    session.rollback()
                    return
                session.commit()
        except Exception:
            logger.exception("Failed to update recalc heartbeat: %s", job_id)


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
    next_reconcile_at = 0.0
    reconcile_cursor: str | None = None
    consecutive_worker_failures = 0
    while not stop_event.is_set():
        now = time.monotonic()
        if now >= next_reconcile_at:
            try:
                reconciliation = reconcile_stale_instrument_read_models(
                    limit=settings.recalc_worker_reconcile_batch_size,
                    after_instrument_id=reconcile_cursor,
                )
                reconcile_cursor = reconciliation.next_cursor
                next_reconcile_at = time.monotonic() + (
                    settings.recalc_worker_reconcile_interval_seconds
                    if reconciliation.cycle_completed
                    else interval
                )
            except Exception:
                logger.exception("Watchlist source-generation reconciliation failed.")
                next_reconcile_at = (
                    time.monotonic()
                    + settings.recalc_worker_reconcile_interval_seconds
                )
        processed = False
        try:
            processed = process_next_recalc_job()
        except Exception:
            consecutive_worker_failures += 1
            backoff_seconds = _worker_error_backoff_seconds(
                consecutive_worker_failures,
                poll_interval_seconds=interval,
            )
            if _should_log_worker_error(consecutive_worker_failures):
                logger.exception(
                    "Watchlist recalc worker failed; retrying in %.1f seconds "
                    "(consecutive_failures=%s).",
                    backoff_seconds,
                    consecutive_worker_failures,
                )
            stop_event.wait(backoff_seconds)
            continue
        if consecutive_worker_failures:
            logger.info(
                "Watchlist recalc worker recovered after %s failures.",
                consecutive_worker_failures,
            )
            consecutive_worker_failures = 0
        if processed:
            continue
        wait_seconds = min(
            interval,
            max(0.0, next_reconcile_at - time.monotonic()),
        )
        stop_event.wait(wait_seconds)


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
