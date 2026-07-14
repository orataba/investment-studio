from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import signal
from threading import Event
import time

import pytest
from sqlalchemy import update

from watchlist_app.core.settings import Settings
from watchlist_app.db.models.recalc import RecalcWorkerRegistration as WorkerModel
from watchlist_app.db.session import get_session_factory
from watchlist_app.repositories.sqlalchemy.recalc_workers import (
    RecalcWorkerRegistrationError,
    SQLAlchemyRecalcWorkerRepository,
)
from watchlist_app.services import recalc_worker


def test_fastapi_runtime_does_not_start_a_recalc_worker() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "watchlist_app"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert "start_recalc_worker" not in source
    assert "lifespan" not in source


def test_registration_suffix_remains_unique_when_prefix_is_long() -> None:
    first = recalc_worker._registration("operator-" * 40)
    second = recalc_worker._registration("operator-" * 40)

    assert len(first.worker_id) <= 255
    assert len(first.worker_id.rsplit(":", 1)[-1]) == 12
    assert first.worker_id != second.worker_id
    assert first.instance_id != second.instance_id


def test_loop_seam_stops_without_starting_a_background_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_event = Event()
    calls = 0

    def no_job() -> bool:
        nonlocal calls
        calls += 1
        stop_event.set()
        return False

    monkeypatch.setattr(recalc_worker, "process_next_recalc_job", no_job)

    recalc_worker.run_recalc_worker_loop(
        stop_event=stop_event,
        poll_interval_seconds=0.001,
    )

    assert calls == 1


def test_registration_repository_rejects_worker_id_reuse_and_checks_freshness(
    client,
) -> None:
    del client
    repository = SQLAlchemyRecalcWorkerRepository()
    registration = recalc_worker._registration("repository-test")
    session_factory = get_session_factory()

    with session_factory() as session:
        repository.register(
            session,
            worker_id=registration.worker_id,
            instance_id=registration.instance_id,
            worker_version=registration.worker_version,
            metadata_json=registration.metadata_json,
        )
        session.commit()
        assert not repository.has_fresh_worker(
            session,
            max_age=timedelta(seconds=30),
        )
        assert repository.record_poll_success(
            session,
            worker_id=registration.worker_id,
            instance_id=registration.instance_id,
        )
        session.commit()
        assert repository.has_fresh_worker(
            session,
            max_age=timedelta(seconds=30),
        )

        session.execute(
            update(WorkerModel)
            .where(WorkerModel.worker_id == registration.worker_id)
            .values(last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=5))
        )
        session.commit()
        assert not repository.has_fresh_worker(
            session,
            max_age=timedelta(seconds=30),
        )

        with pytest.raises(RecalcWorkerRegistrationError):
            repository.register(
                session,
                worker_id=registration.worker_id,
                instance_id="different-worker-instance",
                worker_version=registration.worker_version,
                metadata_json={},
            )


def test_worker_readiness_requires_successful_poll_not_only_heartbeat(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del client
    registration = recalc_worker._registration("poll-readiness-test")
    stop_event = Event()
    worker = recalc_worker.RecalcWorkerProcess(
        settings=Settings(
            recalc_worker_heartbeat_interval_seconds=0.01,
            recalc_worker_running_job_timeout_seconds=1,
            recalc_worker_readiness_max_age_seconds=0.1,
        ),
        registration=registration,
        stop_event=stop_event,
    )
    repository = SQLAlchemyRecalcWorkerRepository()
    session_factory = get_session_factory()

    worker.start()
    try:
        monkeypatch.setattr(
            recalc_worker,
            "process_next_recalc_job",
            lambda: (_ for _ in ()).throw(RuntimeError("poll exploded")),
        )
        with pytest.raises(RuntimeError, match="poll exploded"):
            worker.run_once()

        with session_factory() as session:
            record = session.get(WorkerModel, registration.worker_id)
            assert record is not None
            assert record.last_successful_poll_at is None
            assert "poll exploded" in str(record.last_poll_error)
            assert not repository.has_fresh_worker(
                session,
                max_age=timedelta(seconds=30),
            )

        monkeypatch.setattr(recalc_worker, "process_next_recalc_job", lambda: False)
        assert worker.run_once() is False
        with session_factory() as session:
            record = session.get(WorkerModel, registration.worker_id)
            assert record is not None
            assert record.last_successful_poll_at is not None
            assert record.last_poll_error is None
            assert repository.has_fresh_worker(
                session,
                max_age=timedelta(seconds=30),
            )
    finally:
        stop_event.set()
        worker.close()

    with session_factory() as session:
        stopped = session.get(WorkerModel, registration.worker_id)
        assert stopped is not None
        assert stopped.worker_state == "stopped"
        assert stopped.stopped_at is not None
        assert not repository.has_fresh_worker(
            session,
            max_age=timedelta(seconds=30),
        )


def test_idle_worker_updates_persistent_heartbeat(client) -> None:
    del client
    registration = recalc_worker._registration("idle-heartbeat-test")
    stop_event = Event()
    settings = Settings(
        recalc_worker_heartbeat_interval_seconds=0.01,
        recalc_worker_running_job_timeout_seconds=1,
        recalc_worker_readiness_max_age_seconds=0.1,
    )
    worker = recalc_worker.RecalcWorkerProcess(
        settings=settings,
        registration=registration,
        stop_event=stop_event,
    )
    session_factory = get_session_factory()

    worker.start()
    try:
        with session_factory() as session:
            initial = session.get(WorkerModel, registration.worker_id)
            assert initial is not None
            initial_heartbeat = initial.last_heartbeat_at

        deadline = time.monotonic() + 1
        observed = initial_heartbeat
        while time.monotonic() < deadline and observed <= initial_heartbeat:
            time.sleep(0.01)
            with session_factory() as session:
                refreshed = session.get(WorkerModel, registration.worker_id)
                assert refreshed is not None
                observed = refreshed.last_heartbeat_at

        assert observed > initial_heartbeat
    finally:
        stop_event.set()
        worker.close()

    with session_factory() as session:
        stopped = session.get(WorkerModel, registration.worker_id)
        assert stopped is not None
        assert stopped.worker_state == "stopped"
        assert stopped.stopped_at is not None


def test_cli_installs_signal_handlers_and_closes_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers: dict[signal.Signals, object] = {}
    calls: list[str] = []

    def capture_handler(signum, handler):
        handlers[signum] = handler

    class FakeWorker:
        def __init__(self, *, settings, registration, stop_event):
            del settings, registration
            self.stop_event = stop_event

        def start(self) -> None:
            calls.append("start")

        def run_once(self) -> bool:
            calls.append("once")
            return False

        def run_forever(self) -> None:
            calls.append("forever")
            handler = handlers[signal.SIGTERM]
            assert callable(handler)
            handler(signal.SIGTERM, None)
            assert self.stop_event.is_set()

        def close(self) -> None:
            calls.append("close")

    monkeypatch.setattr(recalc_worker.signal, "signal", capture_handler)
    monkeypatch.setattr(recalc_worker, "RecalcWorkerProcess", FakeWorker)

    assert recalc_worker.main(["--worker-id-prefix", "cli-test"]) == 0
    assert signal.SIGINT in handlers
    assert signal.SIGTERM in handlers
    assert calls == ["start", "forever", "close"]
