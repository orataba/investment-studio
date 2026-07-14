#!/usr/bin/env bash
set -euo pipefail

DATABASE_NAME="${PORTFOLIO_OPS_LOCAL_DATABASE_NAME:-portfolio_ops}"
DATABASE_ROLE="${PORTFOLIO_OPS_LOCAL_DATABASE_ROLE:-portfolio_ops}"
DATABASE_PASSWORD="${PORTFOLIO_OPS_LOCAL_DATABASE_PASSWORD:-portfolio_ops}"

if [[ ! "$DATABASE_NAME" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "Invalid local database name: $DATABASE_NAME" >&2
  exit 64
fi
if [[ ! "$DATABASE_ROLE" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "Invalid local database role: $DATABASE_ROLE" >&2
  exit 64
fi
if [[ "$DATABASE_PASSWORD" == *"'"* || "$DATABASE_PASSWORD" == *$'\n'* ]]; then
  echo "Local database password must not contain quotes or newlines." >&2
  exit 64
fi

find_postgres_binary() {
  local binary="$1"
  if command -v "$binary" >/dev/null 2>&1; then
    command -v "$binary"
    return
  fi
  if command -v brew >/dev/null 2>&1; then
    local formula prefix
    for formula in postgresql@18 postgresql@17 postgresql@16 postgresql; do
      if prefix="$(brew --prefix "$formula" 2>/dev/null)" && [[ -x "$prefix/bin/$binary" ]]; then
        printf '%s\n' "$prefix/bin/$binary"
        return
      fi
    done
  fi
  return 1
}

PSQL_BIN="$(find_postgres_binary psql || true)"
CREATEDB_BIN="$(find_postgres_binary createdb || true)"
if [[ -z "$PSQL_BIN" || -z "$CREATEDB_BIN" ]]; then
  echo "PostgreSQL client tools are missing. Install PostgreSQL before installing the local services." >&2
  exit 1
fi

if ! "$PSQL_BIN" --host 127.0.0.1 --port 5432 --dbname postgres --no-password --command 'SELECT 1' >/dev/null 2>&1; then
  echo "PostgreSQL is not accepting local connections on 127.0.0.1:5432." >&2
  exit 1
fi

role_exists="$($PSQL_BIN --host 127.0.0.1 --port 5432 --dbname postgres --no-password --tuples-only --no-align \
  --command "SELECT 1 FROM pg_roles WHERE rolname = '$DATABASE_ROLE'")"
if [[ "$role_exists" != "1" ]]; then
  "$PSQL_BIN" --host 127.0.0.1 --port 5432 --dbname postgres --no-password \
    --command "CREATE ROLE $DATABASE_ROLE LOGIN PASSWORD '$DATABASE_PASSWORD'"
else
  "$PSQL_BIN" --host 127.0.0.1 --port 5432 --dbname postgres --no-password \
    --command "ALTER ROLE $DATABASE_ROLE WITH LOGIN PASSWORD '$DATABASE_PASSWORD'" >/dev/null
fi

database_exists="$($PSQL_BIN --host 127.0.0.1 --port 5432 --dbname postgres --no-password --tuples-only --no-align \
  --command "SELECT 1 FROM pg_database WHERE datname = '$DATABASE_NAME'")"
if [[ "$database_exists" != "1" ]]; then
  "$CREATEDB_BIN" --host 127.0.0.1 --port 5432 --owner "$DATABASE_ROLE" "$DATABASE_NAME"
fi

echo "Local PostgreSQL database '$DATABASE_NAME' is ready."
