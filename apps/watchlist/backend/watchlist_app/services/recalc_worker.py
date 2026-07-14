"""Durable Watchlist recalc worker process."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import logging
import os
import signal
import socket
from threading import Event, Thread
from types import FrameType
from uuid import uuid4

from watchlist_app.core.settings import Settings, get_settings
from watchlist_app.db.session import get_session_factory
from watchlist_app.repositories.sqlalchemy.recalc_jobs import (
    SQLAlchemyRecalcJobRepository,
)
from watchlist_app.repositories.sqlalchemy.recalc_workers import (
    SQLAlchemyRecalcWorkerRepository,
)
from watchlist_app.services.canonical_recalc import CanonicalRecalcService


LOGGER = logging.getLogger(__name__)
WORKER_VERSION = "watchlist-recalc-worker.v1"

recalc_repository = SQLAlchemyRecalcJobRepository()
recalc_worker_repository = SQLAlchemyRecalcWorkerRepository()
canonical_recalc_service = CanonicalRecalcService()


@dataclass(frozen=True, slots=True)
class RecalcWorkerRegistration:
    worker_id: str
    instance_id: str
    worker_version: str
    metadata_json: dict[str, object]


def process_next_recalc_job() -> bool:
    """Claim and execute at most one job; retained as the focused test seam."""

    session_factory = get_session_factory()
    settings = get_settings()

    with session_factory() as session:
        record = recalc_repository.claim_next_queued(
            session,
            running_timeout_seconds=(
                settings.recalc_worker_running_job_timeout_seconds
            ),
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
                    "interval_seconds": (
                        settings.recalc_worker_heartbeat_interval_seconds
                    ),
                },
                name=f"watchlist-recalc-job-heartbeat-{job_id}",
                daemon=True,
            )
            heartbeat_thread.start()
        failure: Exception | None = None
        try:
            canonical_recalc_service.execute_claimed_job(
                session,
                record=record,
                commit=True,
            )
        except Exception as error:
            failure = error
            LOGGER.exception("Queued recalc job failed: %s", job_id)
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(
                    timeout=max(
                        1.0,
                        settings.recalc_worker_heartbeat_interval_seconds,
                    )
                )
        if failure is not None:
            with session_factory() as failure_session:
                failed_record = recalc_repository.get(failure_session, job_id)
                if failed_record is None:
                    raise RuntimeError(
                        f"Recalc job disappeared during failure handling: {job_id}"
                    ) from failure
                next_status = recalc_repository.reschedule_after_failure(
                    failure_session,
                    failed_record,
                    lease_token=lease_token,
                    error_message=f"{type(failure).__name__}: {failure}",
                    retry_delay_seconds=settings.recalc_retry_delay_seconds(
                        failed_record.attempt_count
                    ),
                )
                if next_status is None:
                    failure_session.rollback()
                    raise RuntimeError(
                        f"Recalc lease lost during failure handling: {job_id}"
                    ) from failure
                failure_session.commit()
            if next_status == "failed":
                LOGGER.error("Queued recalc job dead-lettered: %s", job_id)
            else:
                LOGGER.warning("Queued recalc job scheduled for retry: %s", job_id)
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
                if not recalc_repository.touch_heartbeat(
                    session,
                    job_id,
                    lease_token,
                ):
                    session.rollback()
                    return
                session.commit()
        except Exception:
            LOGGER.exception("Failed to update recalc job heartbeat: %s", job_id)


def drain_recalc_jobs(*, max_jobs: int | None = None) -> int:
    """Drain queued jobs synchronously; retained for deterministic maintenance/tests."""

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
    process_once: Callable[[], bool] | None = None,
) -> None:
    """Run the polling loop until stopped; retained as the loop-level test seam."""

    settings = get_settings()
    interval = (
        poll_interval_seconds
        if poll_interval_seconds is not None
        else settings.recalc_worker_poll_interval_seconds
    )
    if interval <= 0:
        raise ValueError("poll_interval_seconds must be positive.")
    processor = process_once or process_next_recalc_job
    while not stop_event.is_set():
        processed = False
        try:
            processed = processor()
        except Exception:
            LOGGER.exception("Watchlist recalc worker loop failed.")
        if processed:
            continue
        stop_event.wait(interval)


class _WorkerHeartbeatSupervisor:
    """Persist process liveness even when no recalc job is available."""

    def __init__(
        self,
        *,
        registration: RecalcWorkerRegistration,
        interval_seconds: float,
        process_stop_event: Event,
    ) -> None:
        self._registration = registration
        self._interval_seconds = interval_seconds
        self._process_stop_event = process_stop_event
        self._stop_event = Event()
        self._thread = Thread(
            target=self._run,
            name="watchlist-recalc-worker-heartbeat",
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        session_factory = get_session_factory()
        with session_factory() as session:
            recalc_worker_repository.register(
                session,
                worker_id=self._registration.worker_id,
                instance_id=self._registration.instance_id,
                worker_version=self._registration.worker_version,
                metadata_json=self._registration.metadata_json,
            )
            session.commit()
        self._thread.start()
        self._started = True

    def close(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        self._thread.join(timeout=max(1.0, self._interval_seconds + 1.0))
        self._started = False

    def _heartbeat(self) -> bool:
        session_factory = get_session_factory()
        with session_factory() as session:
            touched = recalc_worker_repository.touch_heartbeat(
                session,
                worker_id=self._registration.worker_id,
                instance_id=self._registration.instance_id,
            )
            if not touched:
                session.rollback()
                return False
            session.commit()
            return True

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                if self._heartbeat():
                    continue
                LOGGER.error(
                    "Watchlist recalc worker registration was lost: %s",
                    self._registration.worker_id,
                )
                self._process_stop_event.set()
                return
            except Exception:
                LOGGER.exception("Watchlist recalc worker heartbeat failed.")


class RecalcWorkerProcess:
    def __init__(
        self,
        *,
        settings: Settings,
        registration: RecalcWorkerRegistration,
        stop_event: Event,
    ) -> None:
        self.settings = settings
        self.registration = registration
        self.stop_event = stop_event
        self._heartbeat = _WorkerHeartbeatSupervisor(
            registration=registration,
            interval_seconds=settings.recalc_worker_heartbeat_interval_seconds,
            process_stop_event=stop_event,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._heartbeat.start()
        self._started = True

    def close(self) -> None:
        if self._started:
            self._heartbeat.close()
            session_factory = get_session_factory()
            with session_factory() as session:
                stopped = recalc_worker_repository.stop_worker(
                    session,
                    worker_id=self.registration.worker_id,
                    instance_id=self.registration.instance_id,
                )
                if stopped:
                    session.commit()
                else:
                    session.rollback()
                    LOGGER.warning(
                        "Watchlist recalc worker registration was already stopped: %s",
                        self.registration.worker_id,
                    )
            self._started = False

    def run_once(self) -> bool:
        try:
            processed = process_next_recalc_job()
        except Exception as error:
            self._record_poll_failure(error)
            raise
        self._record_poll_success()
        return processed

    def _record_poll_success(self) -> None:
        session_factory = get_session_factory()
        with session_factory() as session:
            if not recalc_worker_repository.record_poll_success(
                session,
                worker_id=self.registration.worker_id,
                instance_id=self.registration.instance_id,
            ):
                session.rollback()
                raise RuntimeError("Watchlist recalc worker registration was lost.")
            session.commit()

    def _record_poll_failure(self, error: Exception) -> None:
        session_factory = get_session_factory()
        with session_factory() as session:
            if not recalc_worker_repository.record_poll_failure(
                session,
                worker_id=self.registration.worker_id,
                instance_id=self.registration.instance_id,
                error_message=f"{type(error).__name__}: {error}",
            ):
                session.rollback()
                raise RuntimeError("Watchlist recalc worker registration was lost.")
            session.commit()

    def run_forever(self) -> None:
        run_recalc_worker_loop(
            stop_event=self.stop_event,
            poll_interval_seconds=self.settings.recalc_worker_poll_interval_seconds,
            process_once=self.run_once,
        )


def _registration(worker_id_prefix: str | None = None) -> RecalcWorkerRegistration:
    instance_id = uuid4().hex
    host = socket.gethostname().strip() or "unknown-host"
    prefix = (worker_id_prefix or f"{host}:{os.getpid()}").strip()
    if not prefix:
        prefix = f"{host}:{os.getpid()}"
    suffix = f":{instance_id[:12]}"
    prefix_limit = 255 - len(suffix)
    return RecalcWorkerRegistration(
        worker_id=f"{prefix[:prefix_limit]}{suffix}",
        instance_id=instance_id,
        worker_version=WORKER_VERSION,
        metadata_json={"host": host, "pid": os.getpid()},
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Watchlist recalc worker")
    parser.add_argument(
        "--worker-id-prefix",
        help="stable operator label; a unique process-instance suffix is always added",
    )
    parser.add_argument("--once", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _parse_args(argv)
    stop_event = Event()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    worker = RecalcWorkerProcess(
        settings=get_settings(),
        registration=_registration(args.worker_id_prefix),
        stop_event=stop_event,
    )
    worker.start()
    try:
        if args.once:
            worker.run_once()
        else:
            worker.run_forever()
    finally:
        worker.close()
    return 0


__all__ = [
    "RecalcWorkerProcess",
    "RecalcWorkerRegistration",
    "drain_recalc_jobs",
    "main",
    "process_next_recalc_job",
    "run_recalc_worker_loop",
]


if __name__ == "__main__":
    raise SystemExit(main())
