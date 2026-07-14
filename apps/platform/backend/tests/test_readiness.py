from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from platform_app.core.settings import Settings
from platform_app.db.session import get_db_session
from platform_app.services import readiness as readiness_service


EXPECTED_HEAD = "instrument-registry-head"
READY_OUTBOX = readiness_service.OutboxReadinessSnapshot(
    fresh_successful_worker=True,
    no_dead_events=True,
    active_event_age_within_limit=True,
)


class _FakeSession:
    pass


@contextmanager
def _client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    import platform_app.main as main_module

    def override_session() -> Iterator[_FakeSession]:
        yield _FakeSession()

    main_module.app.dependency_overrides[get_db_session] = override_session
    monkeypatch.setattr(
        readiness_service,
        "expected_instrument_registry_migration_head",
        lambda: EXPECTED_HEAD,
    )
    try:
        with TestClient(main_module.app) as client:
            yield client
    finally:
        main_module.app.dependency_overrides.clear()


def _set_ready_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        readiness_service,
        "_read_instrument_registry_database_heads",
        lambda session: (EXPECTED_HEAD,),
    )


def test_readiness_is_healthy_for_current_migration_and_healthy_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_ready_database(monkeypatch)
    monkeypatch.setattr(
        readiness_service,
        "_read_outbox_readiness_snapshot",
        lambda session, **kwargs: READY_OUTBOX,
    )

    with _client(monkeypatch) as client:
        response = client.get("/api/readiness")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "database": {"status": "pass"},
            "migration_heads": {
                "status": "pass",
                "components": {"instrument_registry": "pass"},
            },
            "market_data_outbox_worker": {"status": "pass"},
            "market_data_outbox_dead_events": {"status": "pass"},
            "market_data_outbox_event_age": {"status": "pass"},
        },
    }


@pytest.mark.parametrize(
    ("snapshot", "failed_check"),
    [
        (
            readiness_service.OutboxReadinessSnapshot(False, True, True),
            "market_data_outbox_worker",
        ),
        (
            readiness_service.OutboxReadinessSnapshot(True, False, True),
            "market_data_outbox_dead_events",
        ),
        (
            readiness_service.OutboxReadinessSnapshot(True, True, False),
            "market_data_outbox_event_age",
        ),
    ],
)
def test_readiness_fails_closed_for_each_outbox_condition(
    monkeypatch: pytest.MonkeyPatch,
    snapshot: readiness_service.OutboxReadinessSnapshot,
    failed_check: str,
) -> None:
    _set_ready_database(monkeypatch)
    monkeypatch.setattr(
        readiness_service,
        "_read_outbox_readiness_snapshot",
        lambda session, **kwargs: snapshot,
    )

    with _client(monkeypatch) as client:
        response = client.get("/api/readiness")

    assert response.status_code == 503
    assert response.json()["checks"][failed_check] == {"status": "fail"}


def test_readiness_fails_closed_for_migration_head_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        readiness_service,
        "_read_instrument_registry_database_heads",
        lambda session: ("previous-head",),
    )
    monkeypatch.setattr(
        readiness_service,
        "_read_outbox_readiness_snapshot",
        lambda session, **kwargs: (_ for _ in ()).throw(
            AssertionError(
                "outbox tables must not be queried before migration readiness"
            )
        ),
    )

    with _client(monkeypatch) as client:
        response = client.get("/api/readiness")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == {"status": "pass"}
    assert response.json()["checks"]["migration_heads"] == {
        "status": "fail",
        "components": {"instrument_registry": "fail"},
    }


def test_readiness_does_not_return_database_exception_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        readiness_service,
        "_read_instrument_registry_database_heads",
        lambda session: (_ for _ in ()).throw(
            RuntimeError("postgresql://secret-user:secret-password@private-host/db")
        ),
    )

    with _client(monkeypatch) as client:
        response = client.get("/api/readiness")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == {"status": "fail"}
    assert "secret-user" not in response.text
    assert "secret-password" not in response.text
    assert "private-host" not in response.text


def test_readiness_uses_configured_worker_and_event_age_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        readiness_service,
        "expected_instrument_registry_migration_head",
        lambda: EXPECTED_HEAD,
    )
    _set_ready_database(monkeypatch)

    def capture_outbox_check(session, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return READY_OUTBOX

    monkeypatch.setattr(
        readiness_service,
        "_read_outbox_readiness_snapshot",
        capture_outbox_check,
    )

    report = readiness_service.check_platform_readiness(
        _FakeSession(),  # type: ignore[arg-type]
        Settings(
            market_data_outbox_worker_readiness_max_age_seconds=120,
            market_data_outbox_event_readiness_max_age_seconds=1_200,
        ),
    )

    assert report.ready is True
    assert captured == {
        "worker_max_age_seconds": 120,
        "event_max_age_seconds": 1_200,
    }


def test_outbox_age_query_uses_database_clock_and_event_creation_time() -> None:
    captured: dict[str, object] = {}

    class FakeResult:
        def mappings(self):  # type: ignore[no-untyped-def]
            return self

        def one(self) -> dict[str, bool]:
            return {
                "fresh_successful_worker": True,
                "no_dead_events": True,
                "active_event_age_within_limit": True,
            }

    class FakeSession:
        def execute(self, statement, parameters):  # type: ignore[no-untyped-def]
            captured["sql"] = str(statement)
            captured["parameters"] = parameters
            return FakeResult()

    snapshot = readiness_service._read_outbox_readiness_snapshot(
        FakeSession(),  # type: ignore[arg-type]
        worker_max_age_seconds=90,
        event_max_age_seconds=900,
    )

    assert snapshot == READY_OUTBOX
    assert "clock_timestamp()" in str(captured["sql"])
    assert "last_successful_poll_at IS NOT NULL" in str(captured["sql"])
    assert "last_successful_poll_at >= clock_timestamp()" in str(captured["sql"])
    assert "created_at < clock_timestamp()" in str(captured["sql"])
    assert captured["parameters"] == {
        "worker_max_age_seconds": 90,
        "event_max_age_seconds": 900,
    }


def test_source_migration_head_is_single_and_current() -> None:
    readiness_service.expected_instrument_registry_migration_head.cache_clear()
    try:
        assert (
            readiness_service.expected_instrument_registry_migration_head()
            == "20260714_0013"
        )
    finally:
        readiness_service.expected_instrument_registry_migration_head.cache_clear()


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("market_data_outbox_worker_readiness_max_age_seconds", 4),
        ("market_data_outbox_worker_readiness_max_age_seconds", 3_601),
        ("market_data_outbox_event_readiness_max_age_seconds", 59),
        ("market_data_outbox_event_readiness_max_age_seconds", 86_401),
    ],
)
def test_outbox_readiness_age_limits_are_bounded(
    field_name: str,
    invalid_value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field_name: invalid_value})


def test_event_age_limit_cannot_be_shorter_than_worker_age_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(
            market_data_outbox_worker_readiness_max_age_seconds=120,
            market_data_outbox_event_readiness_max_age_seconds=60,
        )
