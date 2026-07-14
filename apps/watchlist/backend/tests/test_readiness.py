from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest
from fastapi.testclient import TestClient

from watchlist_app.core.settings import Settings
from watchlist_app.db.session import get_db_session
from watchlist_app.services import readiness as readiness_service


EXPECTED_HEADS = {
    "watchlist": "watchlist-head",
    "instrument_registry": "instrument-head",
}


class _FakeSession:
    pass


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import watchlist_app.main as main_module

    def override_session() -> Iterator[_FakeSession]:
        yield _FakeSession()

    main_module.app.dependency_overrides[get_db_session] = override_session
    monkeypatch.setattr(
        readiness_service,
        "expected_migration_heads",
        lambda: EXPECTED_HEADS,
    )
    monkeypatch.setattr(
        readiness_service.recalc_invalidation_repository,
        "has_unserviceable_pending_invalidation",
        lambda session: False,
    )
    return TestClient(main_module.app)


def _database_heads(
    heads: Mapping[str, str],
) -> Mapping[str, tuple[str, ...]]:
    return {component: (head,) for component, head in heads.items()}


def test_readiness_is_healthy_when_database_heads_and_worker_are_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        readiness_service,
        "_read_database_heads",
        lambda session, *, components: _database_heads(EXPECTED_HEADS),
    )
    monkeypatch.setattr(
        readiness_service.recalc_worker_repository,
        "has_fresh_worker",
        lambda session, *, max_age: True,
    )
    monkeypatch.setattr(
        readiness_service.recalc_job_repository,
        "has_terminal_source_event_failure",
        lambda session: False,
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
                "components": {
                    "watchlist": "pass",
                    "instrument_registry": "pass",
                },
            },
            "recalc_worker": {"status": "pass"},
            "recalc_source_event_dead_letters": {"status": "pass"},
            "recalc_invalidation_serviceability": {"status": "pass"},
        },
    }


def test_readiness_returns_503_for_stale_or_missing_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        readiness_service,
        "_read_database_heads",
        lambda session, *, components: _database_heads(EXPECTED_HEADS),
    )
    monkeypatch.setattr(
        readiness_service.recalc_worker_repository,
        "has_fresh_worker",
        lambda session, *, max_age: False,
    )
    monkeypatch.setattr(
        readiness_service.recalc_job_repository,
        "has_terminal_source_event_failure",
        lambda session: False,
    )

    with _client(monkeypatch) as client:
        response = client.get("/api/readiness")

    assert response.status_code == 503
    assert response.json()["checks"]["recalc_worker"] == {"status": "fail"}


def test_readiness_returns_503_for_any_migration_head_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mismatched_heads = dict(EXPECTED_HEADS)
    mismatched_heads["watchlist"] = "previous-watchlist-head"
    monkeypatch.setattr(
        readiness_service,
        "_read_database_heads",
        lambda session, *, components: _database_heads(mismatched_heads),
    )
    monkeypatch.setattr(
        readiness_service.recalc_worker_repository,
        "has_fresh_worker",
        lambda session, *, max_age: True,
    )
    monkeypatch.setattr(
        readiness_service.recalc_job_repository,
        "has_terminal_source_event_failure",
        lambda session: False,
    )

    with _client(monkeypatch) as client:
        response = client.get("/api/readiness")

    assert response.status_code == 503
    assert response.json()["checks"]["migration_heads"] == {
        "status": "fail",
        "components": {
            "watchlist": "fail",
            "instrument_registry": "pass",
        },
    }


def test_readiness_does_not_return_database_exception_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        readiness_service,
        "_read_database_heads",
        lambda session, *, components: (_ for _ in ()).throw(
            RuntimeError(
                "postgresql://secret-user:secret-password@private-host/db"
            )
        ),
    )

    with _client(monkeypatch) as client:
        response = client.get("/api/readiness")

    assert response.status_code == 503
    assert "secret-user" not in response.text
    assert "secret-password" not in response.text
    assert "private-host" not in response.text


def test_health_remains_database_independent_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        readiness_service,
        "_read_database_heads",
        lambda session, *, components: (_ for _ in ()).throw(
            AssertionError("health must not inspect the database")
        ),
    )

    with _client(monkeypatch) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_fails_for_terminal_source_event_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        readiness_service,
        "_read_database_heads",
        lambda session, *, components: _database_heads(EXPECTED_HEADS),
    )
    monkeypatch.setattr(
        readiness_service.recalc_worker_repository,
        "has_fresh_worker",
        lambda session, *, max_age: True,
    )
    monkeypatch.setattr(
        readiness_service.recalc_job_repository,
        "has_terminal_source_event_failure",
        lambda session: True,
    )

    with _client(monkeypatch) as client:
        response = client.get("/api/readiness")

    assert response.status_code == 503
    assert response.json()["checks"]["recalc_source_event_dead_letters"] == {
        "status": "fail"
    }


def test_source_migration_heads_are_single_and_current() -> None:
    readiness_service.expected_migration_heads.cache_clear()
    try:
        assert readiness_service.expected_migration_heads() == {
            "watchlist": "20260714_0034",
            "instrument_registry": "20260714_0013",
        }
    finally:
        readiness_service.expected_migration_heads.cache_clear()


def test_worker_readiness_uses_configured_max_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        readiness_service,
        "expected_migration_heads",
        lambda: EXPECTED_HEADS,
    )
    monkeypatch.setattr(
        readiness_service,
        "_read_database_heads",
        lambda session, *, components: _database_heads(EXPECTED_HEADS),
    )
    monkeypatch.setattr(
        readiness_service.recalc_job_repository,
        "has_terminal_source_event_failure",
        lambda session: False,
    )
    monkeypatch.setattr(
        readiness_service.recalc_invalidation_repository,
        "has_unserviceable_pending_invalidation",
        lambda session: False,
    )

    def capture_worker_check(session, *, max_age):
        captured["max_age"] = max_age
        return True

    monkeypatch.setattr(
        readiness_service.recalc_worker_repository,
        "has_fresh_worker",
        capture_worker_check,
    )

    report = readiness_service.check_watchlist_readiness(
        _FakeSession(),  # type: ignore[arg-type]
        Settings(recalc_worker_readiness_max_age_seconds=123),
    )

    assert report.ready is True
    assert captured == {
        "max_age": readiness_service.timedelta(seconds=123),
    }
