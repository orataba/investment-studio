from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import re
from time import monotonic, sleep
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool


_DATABASE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _database_url(base_url: URL, database_name: str) -> str:
    return base_url.set(database=database_name).render_as_string(hide_password=False)


def _quoted_database_name(database_name: str) -> str:
    if not _DATABASE_NAME_PATTERN.fullmatch(database_name):
        raise ValueError(f"unsafe PostgreSQL database name: {database_name!r}")
    return f'"{database_name}"'


def _sqlstate(error: DBAPIError) -> str | None:
    value = getattr(error.orig, "sqlstate", None)
    return None if value is None else str(value)


def validate_postgres_test_role(role: dict[str, object]) -> None:
    role_name = str(role.get("role_name") or "<unknown>")
    capability_fields = (
        "rolcreatedb",
        "rolsuper",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
        "has_pg_signal_backend",
    )
    unverifiable = sorted(
        field for field in capability_fields if type(role.get(field)) is not bool
    )
    if unverifiable:
        raise RuntimeError(
            "PostgreSQL test role capabilities could not be verified; refusing "
            f"to run with a fail-open permission check: role={role_name!r}, "
            f"fields={unverifiable!r}."
        )
    if role.get("rolcreatedb") is not True:
        raise RuntimeError(
            "PostgreSQL test role must have CREATEDB so pytest can clone an "
            f"isolated migrated template (role={role_name!r})."
        )
    forbidden_capabilities = {
        "SUPERUSER": role.get("rolsuper") is True,
        "CREATEROLE": role.get("rolcreaterole") is True,
        "REPLICATION": role.get("rolreplication") is True,
        "BYPASSRLS": role.get("rolbypassrls") is True,
        "pg_signal_backend membership": role.get("has_pg_signal_backend") is True,
    }
    present = sorted(
        name for name, enabled in forbidden_capabilities.items() if enabled
    )
    if present:
        raise RuntimeError(
            "PostgreSQL test role has forbidden elevated capabilities: "
            f"role={role_name!r}, capabilities={present!r}."
        )


@dataclass(frozen=True, slots=True)
class PostgresTestDatabase:
    name: str
    url: str


class MigratedPostgresTemplate:
    """One migrated database copied into isolated per-test databases.

    The owning connection role needs CREATEDB, but no superuser,
    CREATEROLE, pg_signal_backend, or schema-bypass privileges.  A template is
    immutable after migration and baseline seeding, and disallows direct
    connections, so every test starts from the exact same PostgreSQL catalog
    and canonical application state.
    """

    def __init__(
        self,
        base_url: str,
        migrate: Callable[[str, str], None],
    ) -> None:
        resolved_url = make_url(base_url)
        if resolved_url.get_backend_name() != "postgresql":
            raise RuntimeError(
                "Portfolio database tests require PostgreSQL; received "
                f"{resolved_url.get_backend_name()!r}."
            )
        if not resolved_url.username:
            raise RuntimeError(
                "Portfolio database tests require an explicit PostgreSQL test role."
            )

        self._base_url = resolved_url
        self._run_token = uuid4().hex[:12]
        self._template_name = f"portfolio_ops_pytest_tpl_{self._run_token}"
        self._admin_engine = create_engine(
            _database_url(resolved_url, "postgres"),
            isolation_level="AUTOCOMMIT",
            poolclass=NullPool,
        )
        self._closed = False

        try:
            self._assert_test_role_permissions()
            self._create_empty_database(self._template_name)
            migrate(
                _database_url(self._base_url, self._template_name),
                self._template_name,
            )
            with self._admin_engine.connect() as connection:
                connection.execute(
                    text(
                        "ALTER DATABASE "
                        f"{_quoted_database_name(self._template_name)} "
                        "WITH ALLOW_CONNECTIONS false"
                    )
                )
            self._wait_for_no_backends(self._template_name)
        except BaseException:
            self._drop_database(self._template_name)
            self._admin_engine.dispose()
            self._closed = True
            raise

    @property
    def template_name(self) -> str:
        return self._template_name

    @property
    def run_token(self) -> str:
        return self._run_token

    def _assert_test_role_permissions(self) -> None:
        try:
            with self._admin_engine.connect() as connection:
                role = connection.execute(
                    text(
                        """
                        SELECT current_user AS role_name,
                               rolcreatedb,
                               rolsuper,
                               rolcreaterole,
                               rolreplication,
                               rolbypassrls,
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
        except DBAPIError as error:
            raise RuntimeError(
                "PostgreSQL test server is unavailable or the configured test "
                "role cannot connect to the postgres maintenance database."
            ) from error
        validate_postgres_test_role(dict(role))

    def _create_empty_database(self, database_name: str) -> None:
        with self._admin_engine.connect() as connection:
            connection.execute(
                text(f"CREATE DATABASE {_quoted_database_name(database_name)}")
            )

    def _wait_for_no_backends(self, database_name: str) -> None:
        deadline = monotonic() + 10.0
        while True:
            with self._admin_engine.connect() as connection:
                active_count = int(
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = :database_name"
                        ),
                        {"database_name": database_name},
                    )
                    or 0
                )
            if active_count == 0:
                return
            if monotonic() >= deadline:
                raise RuntimeError(
                    "migrated PostgreSQL template still has active backends: "
                    f"database={database_name!r}"
                )
            sleep(0.05)

    def create_clone(self) -> PostgresTestDatabase:
        if self._closed:
            raise RuntimeError("PostgreSQL test template is already closed.")
        database_name = f"portfolio_ops_pytest_{self._run_token}_{uuid4().hex[:10]}"
        statement = text(
            f"CREATE DATABASE {_quoted_database_name(database_name)} "
            f"WITH TEMPLATE {_quoted_database_name(self._template_name)}"
        )
        deadline = monotonic() + 10.0
        while True:
            try:
                with self._admin_engine.connect() as connection:
                    connection.execute(statement)
                break
            except DBAPIError as error:
                if _sqlstate(error) != "55006" or monotonic() >= deadline:
                    raise
                sleep(0.05)
        return PostgresTestDatabase(
            name=database_name,
            url=_database_url(self._base_url, database_name),
        )

    def _active_backend_details(self, database_name: str) -> list[dict[str, object]]:
        with self._admin_engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT pid, usename, application_name, backend_type, state
                        FROM pg_stat_activity
                        WHERE datname = :database_name
                        ORDER BY pid
                        """
                    ),
                    {"database_name": database_name},
                ).mappings().all()
            ]

    def _drop_database(self, database_name: str) -> None:
        statement = text(
            f"DROP DATABASE IF EXISTS {_quoted_database_name(database_name)}"
        )
        deadline = monotonic() + 10.0
        while True:
            try:
                with self._admin_engine.connect() as connection:
                    connection.execute(statement)
                return
            except DBAPIError as error:
                if _sqlstate(error) != "55006":
                    raise
                if monotonic() >= deadline:
                    active_backends = self._active_backend_details(database_name)
                    raise RuntimeError(
                        "temporary PostgreSQL database still has active backends "
                        "after application engine disposal: "
                        f"database={database_name!r}, "
                        f"backends={active_backends!r}"
                    ) from error
                sleep(0.05)

    def drop_clone(self, database: PostgresTestDatabase) -> None:
        expected_prefix = f"portfolio_ops_pytest_{self._run_token}_"
        if not database.name.startswith(expected_prefix):
            raise ValueError(
                "refusing to drop a database not owned by this pytest run: "
                f"{database.name!r}"
            )
        self._drop_database(database.name)

    @contextmanager
    def cloned_database(self) -> Iterator[PostgresTestDatabase]:
        database = self.create_clone()
        try:
            yield database
        finally:
            self.drop_clone(database)

    def databases_for_run(self) -> list[str]:
        prefix = f"portfolio_ops_pytest_{self._run_token}_"
        with self._admin_engine.connect() as connection:
            return list(
                connection.scalars(
                    text(
                        "SELECT datname FROM pg_database "
                        "WHERE left(datname, length(:prefix)) = :prefix "
                        "ORDER BY datname"
                    ),
                    {"prefix": prefix},
                )
            )

    def close(self) -> None:
        if self._closed:
            return
        try:
            leaked_clones = self.databases_for_run()
            for database_name in leaked_clones:
                self._drop_database(database_name)
            self._drop_database(self._template_name)
            if leaked_clones:
                raise RuntimeError(
                    "pytest PostgreSQL clone cleanup invariant failed; owned "
                    "databases were removed during session teardown: "
                    f"databases={leaked_clones!r}"
                )
        finally:
            self._admin_engine.dispose()
            self._closed = True
