from __future__ import annotations

import logging
from threading import Event, Lock, Thread

from portfolio_app.services.daily_snapshots import (
    _next_daily_snapshot_recalculation_candidate,
    _reconcile_materialized_source_generation_batch,
    _run_portfolio_daily_snapshot_recalculation_synchronously,
)


logger = logging.getLogger(__name__)


class DailySnapshotRecalculationWorker:
    """A single bounded thread that drains durable database-backed work."""

    def __init__(
        self,
        *,
        poll_seconds: float = 0.25,
        reconciliation_batch_size: int = 32,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("Daily snapshot worker poll_seconds must be positive.")
        if reconciliation_batch_size <= 0:
            raise ValueError(
                "Daily snapshot worker reconciliation_batch_size must be positive."
            )
        self._poll_seconds = poll_seconds
        self._reconciliation_batch_size = reconciliation_batch_size
        self._reconciliation_cursor: str | None = None
        self._stop_requested = Event()
        self._wake_requested = Event()
        self._thread = Thread(
            target=self._run,
            name="portfolio-daily-snapshot-worker",
            daemon=True,
        )

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()

    def wake(self) -> None:
        self._wake_requested.set()

    def stop(self, *, timeout_seconds: float) -> bool:
        self._stop_requested.set()
        self._wake_requested.set()
        self._thread.join(timeout=max(timeout_seconds, 0.0))
        return not self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop_requested.is_set():
            try:
                processed, self._reconciliation_cursor = (
                    _run_daily_snapshot_recalculation_worker_once(
                        reconciliation_after_portfolio_id=(
                            self._reconciliation_cursor
                        ),
                        reconciliation_batch_size=(
                            self._reconciliation_batch_size
                        ),
                    )
                )
            except Exception:
                # The synchronous kernel records a failed generation before
                # re-raising.  Keep the worker alive for unrelated portfolios.
                logger.exception("Portfolio daily snapshot recalculation failed.")
                processed = False
            if processed:
                continue
            self._wake_requested.wait(self._poll_seconds)
            self._wake_requested.clear()


def _run_daily_snapshot_recalculation_worker_once(
    *,
    reconciliation_after_portfolio_id: str | None,
    reconciliation_batch_size: int,
) -> tuple[bool, str | None]:
    portfolio_id = _next_daily_snapshot_recalculation_candidate()
    if portfolio_id is None:
        portfolio_id, reconciliation_after_portfolio_id = (
            _reconcile_materialized_source_generation_batch(
                after_portfolio_id=reconciliation_after_portfolio_id,
                batch_size=reconciliation_batch_size,
            )
        )
    if portfolio_id is None:
        return False, reconciliation_after_portfolio_id
    _run_portfolio_daily_snapshot_recalculation_synchronously(portfolio_id)
    return True, reconciliation_after_portfolio_id


def run_daily_snapshot_recalculation_worker_once() -> bool:
    processed, _cursor = _run_daily_snapshot_recalculation_worker_once(
        reconciliation_after_portfolio_id=None,
        reconciliation_batch_size=32,
    )
    return processed


_ACTIVE_WORKER: DailySnapshotRecalculationWorker | None = None
_ACTIVE_WORKER_GUARD = Lock()


def start_daily_snapshot_recalculation_worker(
    *,
    poll_seconds: float,
    reconciliation_batch_size: int,
) -> DailySnapshotRecalculationWorker:
    global _ACTIVE_WORKER
    with _ACTIVE_WORKER_GUARD:
        if _ACTIVE_WORKER is not None and _ACTIVE_WORKER.is_alive:
            return _ACTIVE_WORKER
        worker = DailySnapshotRecalculationWorker(
            poll_seconds=poll_seconds,
            reconciliation_batch_size=reconciliation_batch_size,
        )
        _ACTIVE_WORKER = worker
        worker.start()
        return worker


def wake_daily_snapshot_recalculation_worker() -> None:
    with _ACTIVE_WORKER_GUARD:
        worker = _ACTIVE_WORKER
    if worker is not None and worker.is_alive:
        worker.wake()


def stop_daily_snapshot_recalculation_worker(*, timeout_seconds: float) -> None:
    global _ACTIVE_WORKER
    with _ACTIVE_WORKER_GUARD:
        worker = _ACTIVE_WORKER
        if worker is None:
            return
        stopped = worker.stop(timeout_seconds=timeout_seconds)
        if not stopped:
            logger.error(
                "Portfolio daily snapshot worker did not stop within %.3f seconds.",
                timeout_seconds,
            )
            return
        if _ACTIVE_WORKER is worker:
            _ACTIVE_WORKER = None
