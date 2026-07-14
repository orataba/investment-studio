from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from uuid import UUID


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from platform_app.db.session import get_session_factory  # noqa: E402
from platform_app.services.market_data_outbox import (  # noqa: E402
    DEAD_LETTER_REQUEUE_DEFAULT_ADDITIONAL_ATTEMPTS,
    DEAD_LETTER_REQUEUE_MAX_ADDITIONAL_ATTEMPTS,
    MarketDataOutboxDeadLetterRequeueError,
    PostgresMarketDataOutboxRepository,
)


LOGGER = logging.getLogger("portfolio_ops.market_data_outbox_dead_letter")


def _event_id(value: str) -> str:
    try:
        return str(UUID(value.strip()))
    except (AttributeError, ValueError) as error:
        raise argparse.ArgumentTypeError("event id must be a UUID") from error


def _additional_attempts(value: str) -> int:
    try:
        resolved = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "additional attempts must be an integer"
        ) from error
    if not 1 <= resolved <= DEAD_LETTER_REQUEUE_MAX_ADDITIONAL_ATTEMPTS:
        raise argparse.ArgumentTypeError(
            "additional attempts must be between 1 and "
            f"{DEAD_LETTER_REQUEUE_MAX_ADDITIONAL_ATTEMPTS}"
        )
    return resolved


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Requeue one dead instrument-registry market-data outbox event."
    )
    parser.add_argument("--event-id", required=True, type=_event_id)
    parser.add_argument(
        "--confirm-event-id",
        required=True,
        type=_event_id,
        help="must exactly match --event-id",
    )
    parser.add_argument(
        "--additional-attempts",
        type=_additional_attempts,
        default=DEAD_LETTER_REQUEUE_DEFAULT_ADDITIONAL_ATTEMPTS,
        help=(
            "bounded retry budget added to max_attempts "
            f"(default: {DEAD_LETTER_REQUEUE_DEFAULT_ADDITIONAL_ATTEMPTS}, "
            f"maximum: {DEAD_LETTER_REQUEUE_MAX_ADDITIONAL_ATTEMPTS})"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    args = _parse_args(argv)
    if args.confirm_event_id != args.event_id:
        LOGGER.error("--confirm-event-id must exactly match --event-id")
        return 2

    repository = PostgresMarketDataOutboxRepository(get_session_factory())
    try:
        result = repository.requeue_dead_event(
            event_id=args.event_id,
            additional_attempts=args.additional_attempts,
        )
    except (MarketDataOutboxDeadLetterRequeueError, ValueError) as error:
        LOGGER.error("Dead-letter requeue rejected: %s", error)
        return 1

    print(
        json.dumps(
            {
                "status": "requeued",
                "event_id": result.event_id,
                "attempt_count": result.attempt_count,
                "previous_max_attempts": result.previous_max_attempts,
                "max_attempts": result.max_attempts,
                "additional_attempts": args.additional_attempts,
                "available_at": result.available_at.isoformat(),
                "updated_at": result.updated_at.isoformat(),
                "last_error_preserved": result.last_error is not None,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
