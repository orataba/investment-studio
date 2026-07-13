from __future__ import annotations

import os
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url


EXPECTED_DATABASE_ENV = "PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE"


def require_expected_postgresql_database(
    database_url: str,
    *,
    component: str,
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """Fail before migration setup unless a PostgreSQL target is explicit.

    SQLite remains available for isolated migration tests. PostgreSQL migrations
    must carry a process-level expected database name; application ``.env``
    loading cannot silently manufacture this authorization.
    """

    resolved_url = make_url(database_url)
    if resolved_url.get_backend_name() != "postgresql":
        return None

    values = os.environ if environment is None else environment
    expected_database = str(values.get(EXPECTED_DATABASE_ENV) or "").strip()
    if not expected_database:
        raise RuntimeError(
            f"Refusing {component} PostgreSQL migration: export "
            f"{EXPECTED_DATABASE_ENV} with the exact target database name."
        )

    configured_database = str(resolved_url.database or "").strip()
    if not configured_database:
        raise RuntimeError(
            f"Refusing {component} PostgreSQL migration: the configured URL has "
            "no database name."
        )
    if configured_database != expected_database:
        raise RuntimeError(
            f"Refusing {component} PostgreSQL migration: configured database "
            f"'{configured_database}' does not match the explicitly expected "
            f"database '{expected_database}'."
        )
    return expected_database


def verify_postgresql_connection_database(
    connection: Connection,
    *,
    expected_database: str | None,
    component: str,
) -> None:
    """Re-check the server identity on the live connection before any DDL."""

    if expected_database is None:
        return
    connected_database = str(
        connection.execute(text("SELECT current_database()"), {}).scalar_one()
    )
    if connected_database != expected_database:
        raise RuntimeError(
            f"Refusing {component} PostgreSQL migration: connected database "
            f"'{connected_database}' does not match the explicitly expected "
            f"database '{expected_database}'."
        )
