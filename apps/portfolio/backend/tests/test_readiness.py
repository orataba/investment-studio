from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest
from fastapi.testclient import TestClient

from portfolio_app.core.settings import Settings
from portfolio_app.db.session import get_db_session
from portfolio_app.services import readiness as readiness_service


pytestmark = pytest.mark.no_database

EXPECTED_HEADS = {
    "portfolio": "portfolio-head",
    "calculation_registry": "calculation-head",
    "instrument_registry": "instrument-head",
}


class _FakeSession:
    pass


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import portfolio_app.main as main_module

    def override_session() -> Iterator[_FakeSession]:
        yield _FakeSession()

    main_module.app.dependency_overrides[get_db_session] = override_session
    monkeypatch.setattr(
        readiness_service,
        "expected_migration_heads",
        lambda: EXPECTED_HEADS,
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
        readiness_service.LifecycleRepository,
        "has_fresh_worker",
        lambda session, *, calculation_kind, max_age: True,
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
                    "portfolio": "pass",
                    "calculation_registry": "pass",
                    "instrument_registry": "pass",
                },
            },
            "portfolio_daily_worker": {"status": "pass"},
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
        readiness_service.LifecycleRepository,
        "has_fresh_worker",
        lambda session, *, calculation_kind, max_age: False,
    )

    with _client(monkeypatch) as client:
        response = client.get("/api/readiness")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["portfolio_daily_worker"] == {
        "status": "fail"
    }


def test_readiness_returns_503_for_any_migration_head_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mismatched_heads = dict(EXPECTED_HEADS)
    mismatched_heads["portfolio"] = "previous-portfolio-head"
    monkeypatch.setattr(
        readiness_service,
        "_read_database_heads",
        lambda session, *, components: _database_heads(mismatched_heads),
    )
    monkeypatch.setattr(
        readiness_service.LifecycleRepository,
        "has_fresh_worker",
        lambda session, *, calculation_kind, max_age: True,
    )

    with _client(monkeypatch) as client:
        response = client.get("/api/readiness")

    assert response.status_code == 503
    assert response.json()["checks"]["migration_heads"] == {
        "status": "fail",
        "components": {
            "portfolio": "fail",
            "calculation_registry": "pass",
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
            RuntimeError("postgresql://secret-user:secret-password@private-host/db")
        ),
    )

    with _client(monkeypatch) as client:
        response = client.get("/api/readiness")

    assert response.status_code == 503
    payload = response.text
    assert "secret-user" not in payload
    assert "secret-password" not in payload
    assert "private-host" not in payload


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


def test_source_migration_heads_are_single_and_current() -> None:
    readiness_service.expected_migration_heads.cache_clear()
    try:
        assert readiness_service.expected_migration_heads() == {
            "portfolio": "20260714_0042",
            "calculation_registry": "20260714_0001",
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

    def capture_worker_check(session, *, calculation_kind, max_age):
        captured["calculation_kind"] = calculation_kind
        captured["max_age"] = max_age
        return True

    monkeypatch.setattr(
        readiness_service.LifecycleRepository,
        "has_fresh_worker",
        capture_worker_check,
    )

    report = readiness_service.check_portfolio_readiness(
        _FakeSession(),  # type: ignore[arg-type]
        Settings(calculation_worker_readiness_max_age_seconds=123),
    )

    assert report.ready is True
    assert captured == {
        "calculation_kind": "portfolio_daily",
        "max_age": readiness_service.timedelta(seconds=123),
    }
