#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-db-bootstrap-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

MOCK_BIN="$TEST_ROOT/bin"
CALL_LOG="$TEST_ROOT/postgres-calls"
mkdir -p "$MOCK_BIN"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "psql %s\n" "$*" >> "$CALL_LOG"' \
  'if [[ "$*" == *"SELECT 1 FROM pg_roles"* || "$*" == *"SELECT 1 FROM pg_database"* ]]; then' \
  '  printf "%s\n" 1' \
  'fi' \
  > "$MOCK_BIN/psql"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "createdb %s\n" "$*" >> "$CALL_LOG"' \
  > "$MOCK_BIN/createdb"
chmod +x "$MOCK_BIN/psql" "$MOCK_BIN/createdb"

export CALL_LOG
PATH="$MOCK_BIN:$PATH" \
PORTFOLIO_OPS_LOCAL_DATABASE_NAME=workbench_db \
PORTFOLIO_OPS_LOCAL_DATABASE_ROLE=workbench_runtime \
PORTFOLIO_OPS_LOCAL_DATABASE_PASSWORD=runtime_password \
PORTFOLIO_OPS_LOCAL_TEST_DATABASE_ROLE=workbench_test \
PORTFOLIO_OPS_LOCAL_TEST_DATABASE_PASSWORD=test_password \
  "$REPOSITORY_ROOT/infra/launchd/bootstrap_local_database.sh"

grep -q 'ALTER ROLE workbench_runtime WITH LOGIN NOCREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS' "$CALL_LOG"
grep -q 'ALTER ROLE workbench_test WITH LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS' "$CALL_LOG"
grep -q 'ALTER DATABASE workbench_db OWNER TO workbench_runtime' "$CALL_LOG"
grep -q 'CREATE SCHEMA IF NOT EXISTS instrument_registry AUTHORIZATION workbench_runtime' "$CALL_LOG"
grep -q 'ALTER SCHEMA instrument_registry OWNER TO workbench_runtime' "$CALL_LOG"
grep -q 'ALTER SCHEMA portfolio OWNER TO workbench_runtime' "$CALL_LOG"
grep -q 'ALTER SCHEMA watchlist OWNER TO workbench_runtime' "$CALL_LOG"

echo "local database least-privilege bootstrap test passed."
