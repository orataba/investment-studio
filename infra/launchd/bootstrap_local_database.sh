#!/usr/bin/env bash
set -euo pipefail

DATABASE_NAME="${PORTFOLIO_OPS_LOCAL_DATABASE_NAME:-portfolio_ops}"
DATABASE_ROLE="${PORTFOLIO_OPS_LOCAL_DATABASE_ROLE:-portfolio_ops}"
DATABASE_PASSWORD="${PORTFOLIO_OPS_LOCAL_DATABASE_PASSWORD:-portfolio_ops}"
TEST_DATABASE_ROLE="${PORTFOLIO_OPS_LOCAL_TEST_DATABASE_ROLE:-portfolio_ops_test}"
TEST_DATABASE_PASSWORD="${PORTFOLIO_OPS_LOCAL_TEST_DATABASE_PASSWORD:-portfolio_ops_test}"

if [[ ! "$DATABASE_NAME" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "Invalid local database name: $DATABASE_NAME" >&2
  exit 64
fi
if [[ ! "$DATABASE_ROLE" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "Invalid local database role: $DATABASE_ROLE" >&2
  exit 64
fi
if [[ ! "$TEST_DATABASE_ROLE" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "Invalid local test database role: $TEST_DATABASE_ROLE" >&2
  exit 64
fi
if [[ "$DATABASE_ROLE" == "$TEST_DATABASE_ROLE" ]]; then
  echo "Runtime and test database roles must be different." >&2
  exit 64
fi
if [[ "$DATABASE_PASSWORD" == *"'"* || "$DATABASE_PASSWORD" == *$'\n'* ||
      "$TEST_DATABASE_PASSWORD" == *"'"* || "$TEST_DATABASE_PASSWORD" == *$'\n'* ]]; then
  echo "Local database passwords must not contain quotes or newlines." >&2
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
    --command "CREATE ROLE $DATABASE_ROLE LOGIN NOCREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '$DATABASE_PASSWORD'"
else
  "$PSQL_BIN" --host 127.0.0.1 --port 5432 --dbname postgres --no-password \
    --command "ALTER ROLE $DATABASE_ROLE WITH LOGIN NOCREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '$DATABASE_PASSWORD'" >/dev/null
fi

test_role_exists="$($PSQL_BIN --host 127.0.0.1 --port 5432 --dbname postgres --no-password --tuples-only --no-align \
  --command "SELECT 1 FROM pg_roles WHERE rolname = '$TEST_DATABASE_ROLE'")"
if [[ "$test_role_exists" != "1" ]]; then
  "$PSQL_BIN" --host 127.0.0.1 --port 5432 --dbname postgres --no-password \
    --command "CREATE ROLE $TEST_DATABASE_ROLE LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '$TEST_DATABASE_PASSWORD'"
else
  "$PSQL_BIN" --host 127.0.0.1 --port 5432 --dbname postgres --no-password \
    --command "ALTER ROLE $TEST_DATABASE_ROLE WITH LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '$TEST_DATABASE_PASSWORD'" >/dev/null
fi

database_exists="$($PSQL_BIN --host 127.0.0.1 --port 5432 --dbname postgres --no-password --tuples-only --no-align \
  --command "SELECT 1 FROM pg_database WHERE datname = '$DATABASE_NAME'")"
if [[ "$database_exists" != "1" ]]; then
  "$CREATEDB_BIN" --host 127.0.0.1 --port 5432 --owner "$DATABASE_ROLE" "$DATABASE_NAME"
else
  "$PSQL_BIN" --host 127.0.0.1 --port 5432 --dbname postgres --no-password \
    --command "ALTER DATABASE $DATABASE_NAME OWNER TO $DATABASE_ROLE" >/dev/null
fi

"$PSQL_BIN" --host 127.0.0.1 --port 5432 --dbname "$DATABASE_NAME" --no-password \
  --command "CREATE SCHEMA IF NOT EXISTS instrument_registry AUTHORIZATION $DATABASE_ROLE;
CREATE SCHEMA IF NOT EXISTS calculation_registry AUTHORIZATION $DATABASE_ROLE;
CREATE SCHEMA IF NOT EXISTS portfolio AUTHORIZATION $DATABASE_ROLE;
CREATE SCHEMA IF NOT EXISTS watchlist AUTHORIZATION $DATABASE_ROLE;
ALTER SCHEMA instrument_registry OWNER TO $DATABASE_ROLE;
ALTER SCHEMA calculation_registry OWNER TO $DATABASE_ROLE;
ALTER SCHEMA portfolio OWNER TO $DATABASE_ROLE;
ALTER SCHEMA watchlist OWNER TO $DATABASE_ROLE" >/dev/null

echo "Local PostgreSQL database '$DATABASE_NAME' and isolated test role '$TEST_DATABASE_ROLE' are ready."
