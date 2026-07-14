from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import os
import socket
from threading import Event
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


LOGGER = logging.getLogger("portfolio_ops.market_data_outbox")

DEAD_LETTER_REQUEUE_DEFAULT_ADDITIONAL_ATTEMPTS = 3
DEAD_LETTER_REQUEUE_MAX_ADDITIONAL_ATTEMPTS = 8


class MarketDataOutboxError(RuntimeError):
    pass


class MarketDataOutboxLeaseLostError(MarketDataOutboxError):
    pass


class MarketDataOutboxDeadLetterRequeueError(MarketDataOutboxError):
    pass


@dataclass(frozen=True)
class MarketDataOutboxEvent:
    event_id: str
    event_type: str
    instrument_id: str
    quote_revision_id: str | None
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True)
class MarketDataOutboxWorkerConfig:
    batch_size: int = 1
    lease_seconds: int = 60
    poll_interval_seconds: float = 1.0
    retry_base_seconds: int = 5
    retry_max_seconds: int = 900
    delivery_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.batch_size != 1:
            raise ValueError(
                "batch_size must be 1 unless explicit lease renewal is implemented"
            )
        if self.lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.retry_base_seconds < 1:
            raise ValueError("retry_base_seconds must be positive")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError(
                "retry_max_seconds must be greater than or equal to retry_base_seconds"
            )
        if self.delivery_timeout_seconds <= 0:
            raise ValueError("delivery_timeout_seconds must be positive")
        if self.lease_seconds < self.delivery_timeout_seconds + 5:
            raise ValueError(
                "lease_seconds must reserve at least five seconds beyond "
                "delivery_timeout_seconds"
            )

    def retry_delay_seconds(self, attempt_count: int) -> int:
        exponent = min(max(attempt_count - 1, 0), 30)
        return min(self.retry_max_seconds, self.retry_base_seconds * (2**exponent))


@dataclass(frozen=True)
class MarketDataOutboxBatchResult:
    claimed_count: int
    delivered_count: int
    retry_count: int
    dead_count: int


@dataclass(frozen=True)
class MarketDataOutboxDeadLetterRequeueResult:
    event_id: str
    attempt_count: int
    previous_max_attempts: int
    max_attempts: int
    available_at: datetime
    updated_at: datetime
    last_error: str | None


class MarketDataOutboxRepository(Protocol):
    def register_worker(
        self,
        *,
        worker_id: str,
        hostname: str,
        process_id: int,
        now: datetime,
    ) -> None: ...

    def claim(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        batch_size: int,
    ) -> list[MarketDataOutboxEvent]: ...

    def mark_delivered(
        self,
        event: MarketDataOutboxEvent,
        *,
        worker_id: str,
        now: datetime,
    ) -> None: ...

    def mark_failed(
        self,
        event: MarketDataOutboxEvent,
        *,
        worker_id: str,
        now: datetime,
        retry_at: datetime,
        error: str,
    ) -> str: ...

    def record_poll_success(self, *, worker_id: str, now: datetime) -> None: ...

    def record_poll_failure(
        self,
        *,
        worker_id: str,
        now: datetime,
        error: str,
    ) -> None: ...

    def stop_worker(self, *, worker_id: str, now: datetime) -> None: ...


class PostgresMarketDataOutboxRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _require_postgresql(session: Session) -> None:
        if session.get_bind().dialect.name != "postgresql":
            raise MarketDataOutboxError(
                "The market-data outbox worker requires PostgreSQL SKIP LOCKED semantics"
            )

    def register_worker(
        self,
        *,
        worker_id: str,
        hostname: str,
        process_id: int,
        now: datetime,
    ) -> None:
        with self._session_factory() as session:
            self._require_postgresql(session)
            session.execute(
                text(
                    """
                    INSERT INTO market_data_outbox_worker_heartbeat (
                        worker_id, hostname, process_id, worker_state,
                        started_at, heartbeat_at, last_claimed_at,
                        last_delivered_at, last_successful_poll_at,
                        last_poll_error, stopped_at
                    ) VALUES (
                        :worker_id, :hostname, :process_id, 'running',
                        :now, :now, NULL, NULL, NULL, NULL, NULL
                    )
                    ON CONFLICT (worker_id) DO UPDATE
                    SET hostname = EXCLUDED.hostname,
                        process_id = EXCLUDED.process_id,
                        worker_state = 'running',
                        started_at = EXCLUDED.started_at,
                        heartbeat_at = EXCLUDED.heartbeat_at,
                        last_claimed_at = NULL,
                        last_delivered_at = NULL,
                        last_successful_poll_at = NULL,
                        last_poll_error = NULL,
                        stopped_at = NULL
                    """
                ),
                {
                    "worker_id": worker_id,
                    "hostname": hostname,
                    "process_id": process_id,
                    "now": now,
                },
            )
            session.commit()

    def claim(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        batch_size: int,
    ) -> list[MarketDataOutboxEvent]:
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        with self._session_factory() as session:
            self._require_postgresql(session)
            session.execute(
                text(
                    """
                    UPDATE market_data_outbox_event
                    SET status = 'dead',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        dead_at = :now,
                        updated_at = :now,
                        last_error = COALESCE(
                            last_error,
                            'lease expired after final delivery attempt'
                        )
                    WHERE status = 'processing'
                      AND lease_expires_at <= :now
                      AND attempt_count >= max_attempts
                    """
                ),
                {"now": now},
            )
            rows = (
                session.execute(
                    text(
                        """
                        WITH candidates AS (
                            SELECT event_id
                            FROM market_data_outbox_event
                            WHERE (
                                status = 'pending'
                                AND available_at <= :now
                                AND attempt_count < max_attempts
                            ) OR (
                                status = 'processing'
                                AND lease_expires_at <= :now
                                AND attempt_count < max_attempts
                            )
                            ORDER BY available_at, created_at, event_id
                            FOR UPDATE SKIP LOCKED
                            LIMIT :batch_size
                        )
                        UPDATE market_data_outbox_event AS event
                        SET status = 'processing',
                            attempt_count = event.attempt_count + 1,
                            lease_owner = :worker_id,
                            lease_expires_at = :lease_expires_at,
                            updated_at = :now,
                            delivered_at = NULL,
                            dead_at = NULL
                        FROM candidates
                        WHERE event.event_id = candidates.event_id
                        RETURNING
                            event.event_id,
                            event.event_type,
                            event.instrument_id,
                            event.quote_revision_id,
                            event.attempt_count,
                            event.max_attempts
                        """
                    ),
                    {
                        "worker_id": worker_id,
                        "now": now,
                        "lease_expires_at": lease_expires_at,
                        "batch_size": batch_size,
                    },
                )
                .mappings()
                .all()
            )
            heartbeat = session.execute(
                text(
                    """
                    UPDATE market_data_outbox_worker_heartbeat
                    SET heartbeat_at = :now,
                        last_claimed_at = CASE
                            WHEN :claimed_count > 0 THEN :now
                            ELSE last_claimed_at
                        END
                    WHERE worker_id = :worker_id
                      AND worker_state = 'running'
                    """
                ),
                {
                    "worker_id": worker_id,
                    "now": now,
                    "claimed_count": len(rows),
                },
            )
            if heartbeat.rowcount != 1:
                raise MarketDataOutboxError(
                    f"Outbox worker {worker_id!r} is not registered as running"
                )
            session.commit()
        return [
            MarketDataOutboxEvent(
                event_id=str(row["event_id"]),
                event_type=str(row["event_type"]),
                instrument_id=str(row["instrument_id"]),
                quote_revision_id=(
                    None
                    if row["quote_revision_id"] is None
                    else str(row["quote_revision_id"])
                ),
                attempt_count=int(row["attempt_count"]),
                max_attempts=int(row["max_attempts"]),
            )
            for row in rows
        ]

    def mark_delivered(
        self,
        event: MarketDataOutboxEvent,
        *,
        worker_id: str,
        now: datetime,
    ) -> None:
        with self._session_factory() as session:
            self._require_postgresql(session)
            result = session.execute(
                text(
                    """
                    UPDATE market_data_outbox_event
                    SET status = 'delivered',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_error = NULL,
                        updated_at = :now,
                        delivered_at = :now,
                        dead_at = NULL
                    WHERE event_id = :event_id
                      AND status = 'processing'
                      AND lease_owner = :worker_id
                    """
                ),
                {
                    "event_id": event.event_id,
                    "worker_id": worker_id,
                    "now": now,
                },
            )
            if result.rowcount != 1:
                raise MarketDataOutboxLeaseLostError(
                    f"Delivery lease was lost for outbox event {event.event_id}"
                )
            session.execute(
                text(
                    """
                    UPDATE market_data_outbox_worker_heartbeat
                    SET heartbeat_at = :now,
                        last_delivered_at = :now
                    WHERE worker_id = :worker_id
                      AND worker_state = 'running'
                    """
                ),
                {"worker_id": worker_id, "now": now},
            )
            session.commit()

    def mark_failed(
        self,
        event: MarketDataOutboxEvent,
        *,
        worker_id: str,
        now: datetime,
        retry_at: datetime,
        error: str,
    ) -> str:
        terminal = event.attempt_count >= event.max_attempts
        next_status = "dead" if terminal else "pending"
        with self._session_factory() as session:
            self._require_postgresql(session)
            result = session.execute(
                text(
                    """
                    UPDATE market_data_outbox_event
                    SET status = :next_status,
                        available_at = CASE
                            WHEN :terminal THEN available_at
                            ELSE :retry_at
                        END,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_error = :error,
                        updated_at = :now,
                        delivered_at = NULL,
                        dead_at = CASE WHEN :terminal THEN :now ELSE NULL END
                    WHERE event_id = :event_id
                      AND status = 'processing'
                      AND lease_owner = :worker_id
                    """
                ),
                {
                    "event_id": event.event_id,
                    "worker_id": worker_id,
                    "next_status": next_status,
                    "terminal": terminal,
                    "retry_at": retry_at,
                    "error": error[:4000],
                    "now": now,
                },
            )
            if result.rowcount != 1:
                raise MarketDataOutboxLeaseLostError(
                    f"Failure lease was lost for outbox event {event.event_id}"
                )
            session.execute(
                text(
                    """
                    UPDATE market_data_outbox_worker_heartbeat
                    SET heartbeat_at = :now
                    WHERE worker_id = :worker_id
                      AND worker_state = 'running'
                    """
                ),
                {"worker_id": worker_id, "now": now},
            )
            session.commit()
        return next_status

    def record_poll_success(self, *, worker_id: str, now: datetime) -> None:
        with self._session_factory() as session:
            self._require_postgresql(session)
            result = session.execute(
                text(
                    """
                    UPDATE market_data_outbox_worker_heartbeat
                    SET heartbeat_at = :now,
                        last_successful_poll_at = :now,
                        last_poll_error = NULL
                    WHERE worker_id = :worker_id
                      AND worker_state = 'running'
                    """
                ),
                {"worker_id": worker_id, "now": now},
            )
            if result.rowcount != 1:
                raise MarketDataOutboxError(
                    "Cannot record successful poll for unregistered outbox worker "
                    f"{worker_id!r}"
                )
            session.commit()

    def record_poll_failure(
        self,
        *,
        worker_id: str,
        now: datetime,
        error: str,
    ) -> None:
        with self._session_factory() as session:
            self._require_postgresql(session)
            result = session.execute(
                text(
                    """
                    UPDATE market_data_outbox_worker_heartbeat
                    SET heartbeat_at = :now,
                        last_poll_error = :error
                    WHERE worker_id = :worker_id
                      AND worker_state = 'running'
                    """
                ),
                {
                    "worker_id": worker_id,
                    "now": now,
                    "error": error[:4000],
                },
            )
            if result.rowcount != 1:
                raise MarketDataOutboxError(
                    "Cannot record failed poll for unregistered outbox worker "
                    f"{worker_id!r}"
                )
            session.commit()

    def stop_worker(self, *, worker_id: str, now: datetime) -> None:
        with self._session_factory() as session:
            self._require_postgresql(session)
            result = session.execute(
                text(
                    """
                    UPDATE market_data_outbox_worker_heartbeat
                    SET worker_state = 'stopped',
                        heartbeat_at = :now,
                        stopped_at = :now
                    WHERE worker_id = :worker_id
                      AND worker_state = 'running'
                    """
                ),
                {"worker_id": worker_id, "now": now},
            )
            if result.rowcount != 1:
                raise MarketDataOutboxError(
                    f"Outbox worker {worker_id!r} is not registered as running"
                )
            session.commit()

    def requeue_dead_event(
        self,
        *,
        event_id: str,
        additional_attempts: int = DEAD_LETTER_REQUEUE_DEFAULT_ADDITIONAL_ATTEMPTS,
    ) -> MarketDataOutboxDeadLetterRequeueResult:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must not be blank")
        if not (
            1 <= additional_attempts <= DEAD_LETTER_REQUEUE_MAX_ADDITIONAL_ATTEMPTS
        ):
            raise ValueError(
                "additional_attempts must be between 1 and "
                f"{DEAD_LETTER_REQUEUE_MAX_ADDITIONAL_ATTEMPTS}"
            )

        with self._session_factory() as session:
            self._require_postgresql(session)
            row = (
                session.execute(
                    text(
                        """
                        WITH database_clock AS MATERIALIZED (
                            SELECT clock_timestamp() AS now
                        )
                        UPDATE market_data_outbox_event AS event
                        SET status = 'pending',
                            max_attempts = event.max_attempts + :additional_attempts,
                            available_at = database_clock.now,
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            updated_at = database_clock.now,
                            delivered_at = NULL,
                            dead_at = NULL
                        FROM database_clock
                        WHERE event.event_id = :event_id
                          AND event.status = 'dead'
                        RETURNING
                            event.event_id,
                            event.attempt_count,
                            event.max_attempts,
                            event.available_at,
                            event.updated_at,
                            event.last_error
                        """
                    ),
                    {
                        "event_id": normalized_event_id,
                        "additional_attempts": additional_attempts,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                session.rollback()
                raise MarketDataOutboxDeadLetterRequeueError(
                    "Outbox event does not exist or is not dead; no state was changed: "
                    f"{normalized_event_id}"
                )
            session.commit()

        max_attempts = int(row["max_attempts"])
        return MarketDataOutboxDeadLetterRequeueResult(
            event_id=str(row["event_id"]),
            attempt_count=int(row["attempt_count"]),
            previous_max_attempts=max_attempts - additional_attempts,
            max_attempts=max_attempts,
            available_at=row["available_at"],
            updated_at=row["updated_at"],
            last_error=(None if row["last_error"] is None else str(row["last_error"])),
        )


class MarketDataOutboxWorker:
    def __init__(
        self,
        *,
        repository: MarketDataOutboxRepository,
        sender: Callable[..., object],
        worker_id: str,
        config: MarketDataOutboxWorkerConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        hostname: str | None = None,
        process_id: int | None = None,
    ) -> None:
        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id:
            raise ValueError("worker_id must not be blank")
        self._repository = repository
        self._sender = sender
        self.worker_id = normalized_worker_id
        self.config = config or MarketDataOutboxWorkerConfig()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.hostname = (hostname or socket.gethostname()).strip() or "unknown"
        self.process_id = os.getpid() if process_id is None else process_id
        if self.process_id <= 0:
            raise ValueError("process_id must be positive")

    def register(self) -> None:
        self._repository.register_worker(
            worker_id=self.worker_id,
            hostname=self.hostname,
            process_id=self.process_id,
            now=self._clock(),
        )

    def run_once(self) -> MarketDataOutboxBatchResult:
        try:
            events = self._repository.claim(
                worker_id=self.worker_id,
                now=self._clock(),
                lease_seconds=self.config.lease_seconds,
                batch_size=self.config.batch_size,
            )
            delivered_count = 0
            retry_count = 0
            dead_count = 0
            for event in events:
                try:
                    self._sender(
                        event_id=event.event_id,
                        instrument_id=event.instrument_id,
                    )
                except Exception as error:
                    failed_at = self._clock()
                    retry_at = failed_at + timedelta(
                        seconds=self.config.retry_delay_seconds(event.attempt_count)
                    )
                    next_status = self._repository.mark_failed(
                        event,
                        worker_id=self.worker_id,
                        now=failed_at,
                        retry_at=retry_at,
                        error=f"{type(error).__name__}: {error}",
                    )
                    if next_status == "dead":
                        dead_count += 1
                        LOGGER.error(
                            "Market-data outbox event dead-lettered event_id=%s attempt=%s",
                            event.event_id,
                            event.attempt_count,
                        )
                    else:
                        retry_count += 1
                        LOGGER.warning(
                            "Market-data outbox delivery failed event_id=%s attempt=%s retry_at=%s",
                            event.event_id,
                            event.attempt_count,
                            retry_at.isoformat(),
                        )
                else:
                    self._repository.mark_delivered(
                        event,
                        worker_id=self.worker_id,
                        now=self._clock(),
                    )
                    delivered_count += 1
            result = MarketDataOutboxBatchResult(
                claimed_count=len(events),
                delivered_count=delivered_count,
                retry_count=retry_count,
                dead_count=dead_count,
            )
            self._repository.record_poll_success(
                worker_id=self.worker_id,
                now=self._clock(),
            )
            return result
        except Exception as error:
            try:
                self._repository.record_poll_failure(
                    worker_id=self.worker_id,
                    now=self._clock(),
                    error=f"{type(error).__name__}: {error}",
                )
            except Exception:
                LOGGER.exception(
                    "Failed to persist outbox worker poll error worker_id=%s",
                    self.worker_id,
                )
            raise

    def run_forever(self, stop_event: Event) -> None:
        self.register()
        try:
            while not stop_event.is_set():
                result = self.run_once()
                if result.claimed_count == 0:
                    stop_event.wait(self.config.poll_interval_seconds)
        finally:
            self._repository.stop_worker(
                worker_id=self.worker_id,
                now=self._clock(),
            )
