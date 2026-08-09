#!/usr/bin/env python3
"""Fail closed when release migration chains do not share one database target."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit


PRIMARY_VARIABLES = (
    "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL",
    "PORTFOLIO_OPS_PLATFORM_DATABASE_URL",
    "PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL",
    "PORTFOLIO_OPS_WATCHLIST_DATABASE_URL",
)
ALEMBIC_VARIABLES = (
    "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL",
    "PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL",
    "PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL",
    "PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL",
)
EXPECTED_DATABASE_VARIABLE = "PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE"


@dataclass(frozen=True)
class DatabaseTarget:
    host: str
    port: int
    database: str

    def safe_description(self) -> str:
        return f"host={self.host}, port={self.port}, database={self.database}"


def _target_from_url(variable: str, raw_url: str) -> DatabaseTarget:
    try:
        parsed = urlsplit(raw_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        hostname = parsed.hostname
        parsed_port = parsed.port
    except (TypeError, ValueError):
        raise ValueError(f"{variable} is not a valid PostgreSQL URL.") from None

    if not parsed.scheme.startswith("postgresql"):
        raise ValueError(f"{variable} must use a PostgreSQL URL.")
    if parsed.password is not None:
        raise ValueError(
            f"{variable} must not contain a password; use a 0600 .pgpass file."
        )

    query_host = query.get("host", [""])[0]
    host = unquote(hostname or query_host) or "local-socket"

    query_port = query.get("port", [""])[0]
    try:
        port = parsed_port or (int(query_port) if query_port else 5432)
    except ValueError:
        raise ValueError(f"{variable} has an invalid PostgreSQL port.") from None

    database = unquote(parsed.path.lstrip("/"))
    if not database:
        database = unquote(query.get("dbname", [""])[0])
    if not database:
        raise ValueError(f"{variable} must name a PostgreSQL database.")

    return DatabaseTarget(host=host, port=port, database=database)


def main() -> int:
    variables = (*PRIMARY_VARIABLES, *ALEMBIC_VARIABLES)
    targets: list[tuple[str, DatabaseTarget]] = []

    for variable in variables:
        raw_url = os.environ.get(variable, "")
        if not raw_url:
            if variable in PRIMARY_VARIABLES:
                print(f"Set {variable} explicitly before running migrations.", file=sys.stderr)
                return 1
            continue
        try:
            targets.append((variable, _target_from_url(variable, raw_url)))
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1

    canonical_variable, canonical_target = targets[0]
    mismatches = [
        (variable, target)
        for variable, target in targets[1:]
        if target != canonical_target
    ]
    if mismatches:
        print(
            "Migration targets do not identify one canonical PostgreSQL database.",
            file=sys.stderr,
        )
        print(
            f"{canonical_variable}: {canonical_target.safe_description()}",
            file=sys.stderr,
        )
        for variable, target in mismatches:
            print(f"{variable}: {target.safe_description()}", file=sys.stderr)
        return 1

    expected_database = os.environ.get(EXPECTED_DATABASE_VARIABLE, "")
    if expected_database and canonical_target.database != expected_database:
        print(
            f"Migration target database does not match {EXPECTED_DATABASE_VARIABLE}: "
            f"actual={canonical_target.database}, expected={expected_database}",
            file=sys.stderr,
        )
        return 1

    print(f"Validated canonical migration target: {canonical_target.safe_description()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
