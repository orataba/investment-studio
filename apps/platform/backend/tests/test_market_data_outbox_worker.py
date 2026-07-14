from __future__ import annotations

from datetime import UTC, datetime
from threading import Event

import pytest

from platform_app.services.market_data_outbox import (
    MarketDataOutboxEvent,
    MarketDataOutboxWorker,
    MarketDataOutboxWorkerConfig,
)


NOW = datetime(2026, 7, 14, 10, tzinfo=UTC)


class _FakeRepository:
    def __init__(self, events: list[MarketDataOutboxEvent]) -> None:
        self.events = events
        self.registered = False
        self.stopped = False
        self.delivered: list[str] = []
        self.failed: list[tuple[str, str, datetime]] = []
        self.successful_polls = 0
        self.poll_failures: list[str] = []
        self.claim_error: Exception | None = None

    def register_worker(self, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        self.registered = True

    def claim(self, **_kwargs) -> list[MarketDataOutboxEvent]:  # type: ignore[no-untyped-def]
        if self.claim_error is not None:
            raise self.claim_error
        events, self.events = self.events, []
        return events

    def mark_delivered(self, event, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        self.delivered.append(event.event_id)

    def mark_failed(  # type: ignore[no-untyped-def]
        self,
        event,
        *,
        retry_at,
        error,
        **_kwargs,
    ) -> str:
        status = "dead" if event.attempt_count >= event.max_attempts else "pending"
        self.failed.append((event.event_id, error, retry_at))
        return status

    def record_poll_success(self, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        self.successful_polls += 1

    def record_poll_failure(  # type: ignore[no-untyped-def]
        self,
        *,
        error: str,
        **_kwargs,
    ) -> None:
        self.poll_failures.append(error)

    def stop_worker(self, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        self.stopped = True


def _event(event_id: str, *, attempt: int, maximum: int) -> MarketDataOutboxEvent:
    return MarketDataOutboxEvent(
        event_id=event_id,
        event_type="watchlist_market_data_refresh_requested",
        instrument_id=f"instrument-{event_id}",
        quote_revision_id=f"revision-{event_id}",
        attempt_count=attempt,
        max_attempts=maximum,
    )


def test_worker_delivers_retries_and_dead_letters_without_hiding_failures() -> None:
    repository = _FakeRepository(
        [
            _event("success", attempt=1, maximum=3),
            _event("retry", attempt=2, maximum=3),
            _event("dead", attempt=3, maximum=3),
        ]
    )

    def sender(*, event_id: str, instrument_id: str) -> None:
        del instrument_id
        if event_id != "success":
            raise RuntimeError(f"failed {event_id}")

    worker = MarketDataOutboxWorker(
        repository=repository,
        sender=sender,
        worker_id="worker-1",
        config=MarketDataOutboxWorkerConfig(
            retry_base_seconds=5,
            retry_max_seconds=20,
        ),
        clock=lambda: NOW,
        hostname="test-host",
        process_id=123,
    )
    worker.register()

    result = worker.run_once()

    assert result.claimed_count == 3
    assert result.delivered_count == 1
    assert result.retry_count == 1
    assert result.dead_count == 1
    assert repository.delivered == ["success"]
    assert [item[0] for item in repository.failed] == ["retry", "dead"]
    assert repository.failed[0][2] == NOW.replace(second=10)
    assert repository.failed[1][2] == NOW.replace(second=20)
    assert repository.successful_polls == 1
    assert repository.poll_failures == []


def test_worker_records_unhandled_poll_failure_and_reraises() -> None:
    repository = _FakeRepository([])
    repository.claim_error = RuntimeError("database unavailable")
    worker = MarketDataOutboxWorker(
        repository=repository,
        sender=lambda **_kwargs: None,
        worker_id="worker-failing-poll",
        clock=lambda: NOW,
        hostname="test-host",
        process_id=123,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        worker.run_once()

    assert repository.successful_polls == 0
    assert repository.poll_failures == ["RuntimeError: database unavailable"]


def test_retry_backoff_is_exponential_and_bounded() -> None:
    config = MarketDataOutboxWorkerConfig(
        retry_base_seconds=5,
        retry_max_seconds=60,
    )

    assert [config.retry_delay_seconds(attempt) for attempt in (1, 2, 3, 10)] == [
        5,
        10,
        20,
        60,
    ]


def test_worker_rejects_delivery_timeout_that_can_outlive_lease() -> None:
    with pytest.raises(ValueError, match="reserve at least five seconds"):
        MarketDataOutboxWorkerConfig(
            lease_seconds=10,
            delivery_timeout_seconds=6,
        )


def test_worker_run_forever_registers_and_stops_gracefully() -> None:
    repository = _FakeRepository([_event("success", attempt=1, maximum=3)])
    stop_event = Event()

    def sender(**_kwargs) -> None:  # type: ignore[no-untyped-def]
        stop_event.set()

    worker = MarketDataOutboxWorker(
        repository=repository,
        sender=sender,
        worker_id="worker-graceful",
        clock=lambda: NOW,
        hostname="test-host",
        process_id=123,
    )

    worker.run_forever(stop_event)

    assert repository.registered is True
    assert repository.stopped is True
    assert repository.delivered == ["success"]
