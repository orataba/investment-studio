from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import signal
import socket
import sys
from threading import Event
from uuid import uuid4


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from platform_app.db.session import get_session_factory  # noqa: E402
from platform_app.services.market_data_outbox import (  # noqa: E402
    MarketDataOutboxWorker,
    MarketDataOutboxWorkerConfig,
    PostgresMarketDataOutboxRepository,
)
from platform_app.services.watchlist_recalc_sender import (  # noqa: E402
    send_watchlist_market_data_recalc,
)


LOGGER = logging.getLogger("portfolio_ops.market_data_outbox_worker")


def _worker_id(
    worker_id_prefix: str | None = None,
    *,
    instance_id: str | None = None,
    hostname: str | None = None,
    process_id: int | None = None,
) -> str:
    resolved_instance_id = instance_id or uuid4().hex
    host = (hostname or socket.gethostname()).strip() or "unknown-host"
    pid = os.getpid() if process_id is None else process_id
    prefix = (worker_id_prefix or f"{host}:{pid}").strip() or f"{host}:{pid}"
    suffix = f":{resolved_instance_id[:12]}"
    prefix_limit = 255 - len(suffix)
    return f"{prefix[:prefix_limit]}{suffix}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deliver instrument-registry market-data outbox events."
    )
    parser.add_argument(
        "--worker-id-prefix",
        help="stable operator label; a unique process-instance suffix is always added",
    )
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--poll-interval-seconds", type=float, default=1.0)
    parser.add_argument("--retry-base-seconds", type=int, default=5)
    parser.add_argument("--retry-max-seconds", type=int, default=900)
    parser.add_argument("--http-timeout-seconds", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    args = _parse_args()
    if args.http_timeout_seconds <= 0:
        LOGGER.error("--http-timeout-seconds must be positive")
        return 2
    try:
        config = MarketDataOutboxWorkerConfig(
            lease_seconds=args.lease_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            retry_base_seconds=args.retry_base_seconds,
            retry_max_seconds=args.retry_max_seconds,
            delivery_timeout_seconds=args.http_timeout_seconds,
        )
    except ValueError as error:
        LOGGER.error("Invalid worker configuration: %s", error)
        return 2

    worker_id = _worker_id(args.worker_id_prefix)
    stop_event = Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("Graceful worker stop requested signal=%s", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    repository = PostgresMarketDataOutboxRepository(get_session_factory())

    def sender(*, event_id: str, instrument_id: str) -> object:
        return send_watchlist_market_data_recalc(
            event_id=event_id,
            instrument_id=instrument_id,
            timeout_seconds=config.delivery_timeout_seconds,
        )

    worker = MarketDataOutboxWorker(
        repository=repository,
        sender=sender,
        worker_id=worker_id,
        config=config,
    )
    LOGGER.info("Market-data outbox worker starting worker_id=%s", worker_id)
    try:
        worker.run_forever(stop_event)
    except Exception:
        LOGGER.exception("Market-data outbox worker stopped unexpectedly")
        return 1
    LOGGER.info("Market-data outbox worker stopped worker_id=%s", worker_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
