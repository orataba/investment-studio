from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from platform_app.services.market_data_outbox import (
    DEAD_LETTER_REQUEUE_DEFAULT_ADDITIONAL_ATTEMPTS,
    DEAD_LETTER_REQUEUE_MAX_ADDITIONAL_ATTEMPTS,
    MarketDataOutboxDeadLetterRequeueError,
)


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "requeue_market_data_outbox_dead_letter.py"
)
SPEC = importlib.util.spec_from_file_location(
    "requeue_market_data_outbox_dead_letter",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
dead_letter_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dead_letter_cli)

EVENT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_EVENT_ID = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 7, 14, 12, tzinfo=UTC)


def test_cli_requeues_confirmed_dead_event_with_bounded_default_budget(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []

    class FakeRepository:
        def __init__(self, session_factory: object) -> None:
            assert session_factory == "session-factory"

        def requeue_dead_event(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            return SimpleNamespace(
                event_id=EVENT_ID,
                attempt_count=8,
                previous_max_attempts=8,
                max_attempts=11,
                available_at=NOW,
                updated_at=NOW,
                last_error="Watchlist unavailable",
            )

    monkeypatch.setattr(
        dead_letter_cli, "get_session_factory", lambda: "session-factory"
    )
    monkeypatch.setattr(
        dead_letter_cli,
        "PostgresMarketDataOutboxRepository",
        FakeRepository,
    )

    assert (
        dead_letter_cli.main(["--event-id", EVENT_ID, "--confirm-event-id", EVENT_ID])
        == 0
    )

    assert calls == [
        {
            "event_id": EVENT_ID,
            "additional_attempts": DEAD_LETTER_REQUEUE_DEFAULT_ADDITIONAL_ATTEMPTS,
        }
    ]
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "additional_attempts": 3,
        "attempt_count": 8,
        "available_at": "2026-07-14T12:00:00+00:00",
        "event_id": EVENT_ID,
        "last_error_preserved": True,
        "max_attempts": 11,
        "previous_max_attempts": 8,
        "status": "requeued",
        "updated_at": "2026-07-14T12:00:00+00:00",
    }


def test_cli_fails_closed_before_repository_when_confirmation_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dead_letter_cli,
        "PostgresMarketDataOutboxRepository",
        lambda _session_factory: pytest.fail("repository must not be constructed"),
    )

    assert (
        dead_letter_cli.main(
            ["--event-id", EVENT_ID, "--confirm-event-id", OTHER_EVENT_ID]
        )
        == 2
    )


def test_cli_fails_closed_when_repository_rejects_non_dead_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRepository:
        def __init__(self, _session_factory: object) -> None:
            pass

        def requeue_dead_event(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise MarketDataOutboxDeadLetterRequeueError("event is not dead")

    monkeypatch.setattr(dead_letter_cli, "get_session_factory", lambda: object())
    monkeypatch.setattr(
        dead_letter_cli,
        "PostgresMarketDataOutboxRepository",
        FakeRepository,
    )

    assert (
        dead_letter_cli.main(["--event-id", EVENT_ID, "--confirm-event-id", EVENT_ID])
        == 1
    )


@pytest.mark.parametrize(
    "additional_attempts",
    ["0", str(DEAD_LETTER_REQUEUE_MAX_ADDITIONAL_ATTEMPTS + 1)],
)
def test_cli_rejects_retry_budget_outside_controlled_range(
    additional_attempts: str,
) -> None:
    with pytest.raises(SystemExit, match="2"):
        dead_letter_cli._parse_args(
            [
                "--event-id",
                EVENT_ID,
                "--confirm-event-id",
                EVENT_ID,
                "--additional-attempts",
                additional_attempts,
            ]
        )
