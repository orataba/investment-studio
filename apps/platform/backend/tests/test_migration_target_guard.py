from __future__ import annotations

import sys
from pathlib import Path

import pytest


INFRA_PYTHON = Path(__file__).resolve().parents[4] / "infra" / "python"
if str(INFRA_PYTHON) not in sys.path:
    sys.path.insert(0, str(INFRA_PYTHON))

from portfolio_ops_infra.migration_target_guard import (  # noqa: E402
    require_expected_postgresql_database,
    verify_postgresql_connection_database,
)


class _ScalarResult:
    def __init__(self, value: str) -> None:
        self.value = value

    def scalar_one(self) -> str:
        return self.value


class _Connection:
    def __init__(self, database_name: str) -> None:
        self.database_name = database_name
        self.statement_text = ""

    def execute(self, statement: object, parameters: object) -> _ScalarResult:
        del parameters
        self.statement_text = str(statement)
        return _ScalarResult(self.database_name)


def test_sqlite_migrations_do_not_require_postgresql_authorization() -> None:
    assert (
        require_expected_postgresql_database(
            "sqlite+pysqlite:///:memory:",
            component="test",
            environment={},
        )
        is None
    )


def test_postgresql_migration_requires_process_target() -> None:
    with pytest.raises(RuntimeError, match="PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE"):
        require_expected_postgresql_database(
            "postgresql+psycopg://role:secret@127.0.0.1/portfolio_ops",
            component="test",
            environment={},
        )


def test_postgresql_migration_rejects_configured_target_mismatch() -> None:
    with pytest.raises(RuntimeError, match="does not match"):
        require_expected_postgresql_database(
            "postgresql+psycopg://role:secret@127.0.0.1/portfolio_ops",
            component="test",
            environment={"PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE": "rehearsal"},
        )


def test_postgresql_migration_verifies_the_live_connection() -> None:
    expected = require_expected_postgresql_database(
        "postgresql+psycopg://role:secret@127.0.0.1/rehearsal",
        component="test",
        environment={"PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE": "rehearsal"},
    )
    connection = _Connection("rehearsal")
    verify_postgresql_connection_database(
        connection,  # type: ignore[arg-type]
        expected_database=expected,
        component="test",
    )
    assert connection.statement_text == "SELECT current_database()"


def test_postgresql_migration_rejects_live_connection_mismatch() -> None:
    with pytest.raises(RuntimeError, match="connected database"):
        verify_postgresql_connection_database(
            _Connection("portfolio_ops"),  # type: ignore[arg-type]
            expected_database="rehearsal",
            component="test",
        )
