from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.postgres_test_database import (
    MigratedPostgresTemplate,
    validate_postgres_test_role,
)


@pytest.mark.no_database
@pytest.mark.parametrize(
    "forbidden_flag",
    (
        "rolsuper",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
        "has_pg_signal_backend",
    ),
)
def test_postgres_test_role_rejects_every_elevated_capability(
    forbidden_flag: str,
) -> None:
    role: dict[str, object] = {
        "role_name": "overprivileged_test_role",
        "rolcreatedb": True,
        "rolsuper": False,
        "rolcreaterole": False,
        "rolreplication": False,
        "rolbypassrls": False,
        "has_pg_signal_backend": False,
    }
    role[forbidden_flag] = True

    with pytest.raises(RuntimeError, match="forbidden elevated capabilities"):
        validate_postgres_test_role(role)


@pytest.mark.no_database
def test_postgres_test_role_requires_createdb() -> None:
    with pytest.raises(RuntimeError, match="must have CREATEDB"):
        validate_postgres_test_role(
            {
                "role_name": "underprivileged_test_role",
                "rolcreatedb": False,
                "rolsuper": False,
                "rolcreaterole": False,
                "rolreplication": False,
                "rolbypassrls": False,
                "has_pg_signal_backend": False,
            }
        )


@pytest.mark.no_database
@pytest.mark.parametrize("unverifiable_value", (None, 0, 1, "false"))
def test_postgres_test_role_fails_closed_when_capability_is_not_boolean(
    unverifiable_value: object,
) -> None:
    role: dict[str, object] = {
        "role_name": "unverifiable_test_role",
        "rolcreatedb": True,
        "rolsuper": False,
        "rolcreaterole": False,
        "rolreplication": False,
        "rolbypassrls": False,
        "has_pg_signal_backend": False,
    }
    role["rolsuper"] = unverifiable_value

    with pytest.raises(RuntimeError, match="fail-open permission check"):
        validate_postgres_test_role(role)


@pytest.mark.postgresql_integration
def test_migrated_template_clones_and_drops_ten_rounds_without_leaks(
    migrated_postgres_template: MigratedPostgresTemplate,
) -> None:
    for _ in range(10):
        with migrated_postgres_template.cloned_database() as database:
            engine = create_engine(database.url, poolclass=NullPool)
            try:
                with engine.connect() as connection:
                    assert connection.scalar(text("SELECT current_database()")) == database.name
                    assert connection.scalar(
                        text(
                            "SELECT count(*) FROM information_schema.schemata "
                            "WHERE schema_name IN "
                            "('instrument_registry', 'calculation_registry', 'portfolio')"
                        )
                    ) == 3
                    role = connection.execute(
                        text(
                            """
                            SELECT rolcreatedb, rolsuper, rolcreaterole,
                                   rolreplication, rolbypassrls,
                                   pg_has_role(
                                       current_user,
                                       'pg_signal_backend',
                                       'member'
                                   ) AS has_pg_signal_backend
                            FROM pg_roles
                            WHERE rolname = current_user
                            """
                        )
                    ).mappings().one()
                    assert dict(role) == {
                        "rolcreatedb": True,
                        "rolsuper": False,
                        "rolcreaterole": False,
                        "rolreplication": False,
                        "rolbypassrls": False,
                        "has_pg_signal_backend": False,
                    }
                    assert connection.scalar(
                        text(
                            """
                            SELECT count(*)
                            FROM calculation_registry.calculation_recompute_intent
                            WHERE status = 'pending'
                            """
                        )
                    ) == 0
                    assert connection.scalar(
                        text(
                            """
                            SELECT count(*)
                            FROM calculation_registry.calculation_run
                            WHERE status IN (
                                'capturing', 'queued', 'running', 'succeeded'
                            )
                            """
                        )
                    ) == 0
                    assert connection.scalar(
                        text(
                            """
                            SELECT count(*)
                            FROM calculation_registry.calculation_current_publication
                            WHERE calculation_kind = 'portfolio_daily'
                              AND scope_kind = 'portfolio'
                              AND scope_id = 'portfolio-ops'
                            """
                        )
                    ) == 1
                    assert connection.scalar(
                        text(
                            """
                            SELECT count(*)
                            FROM portfolio.portfolio_daily_transaction_input
                            WHERE instrument_id IS NULL
                            """
                        )
                    ) > 0
                    assert connection.scalar(
                        text(
                            """
                            SELECT count(*)
                            FROM portfolio.portfolio_daily_transaction_input
                            WHERE instrument_id IS NULL
                              AND instrument_snapshot_json IS NOT NULL
                            """
                        )
                    ) == 0
            finally:
                engine.dispose()

    assert migrated_postgres_template.databases_for_run() == []
