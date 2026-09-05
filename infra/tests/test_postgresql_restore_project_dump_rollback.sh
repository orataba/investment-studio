#!/usr/bin/env bash
set -euo pipefail

# This is a real PostgreSQL integration test, not a command-mocking shell test.
REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/investment-studio-restore-rollback-test.XXXXXX")"
DATABASE_HOST="${INVESTMENT_STUDIO_TEST_DB_HOST:-127.0.0.1}"
DATABASE_PORT="${INVESTMENT_STUDIO_TEST_DB_PORT:-5432}"
DATABASE_USER="${INVESTMENT_STUDIO_TEST_DB_USER:-$(id -un)}"
DATABASE_PASSWORD="${INVESTMENT_STUDIO_TEST_DB_PASSWORD:-}"
TARGET_DATABASE="investment_studio_restore_target_$$"
SOURCE_DATABASE="investment_studio_restore_source_$$"
TARGET_DATABASE_URL="postgresql+psycopg://$DATABASE_USER@$DATABASE_HOST:$DATABASE_PORT/$TARGET_DATABASE"
escaped_database_password="${DATABASE_PASSWORD//\\/\\\\}"
escaped_database_password="${escaped_database_password//:/\\:}"
printf '%s:%s:*:%s:%s\n' \
  "$DATABASE_HOST" "$DATABASE_PORT" "$DATABASE_USER" "$escaped_database_password" \
  > "$TEST_ROOT/pgpass"
chmod 600 "$TEST_ROOT/pgpass"
export PGPASSFILE="$TEST_ROOT/pgpass"
unset PGPASSWORD

cleanup() {
  dropdb --if-exists --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" "$TARGET_DATABASE" >/dev/null 2>&1 || true
  dropdb --if-exists --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" "$SOURCE_DATABASE" >/dev/null 2>&1 || true
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

createdb --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" "$TARGET_DATABASE"
createdb --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" "$SOURCE_DATABASE"

psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$TARGET_DATABASE" --set ON_ERROR_STOP=1 <<'SQL'
CREATE SCHEMA instrument_registry;
CREATE SCHEMA platform;
CREATE SCHEMA portfolio;
CREATE SCHEMA watchlist;
CREATE TABLE portfolio.restore_sentinel (value text PRIMARY KEY);
INSERT INTO portfolio.restore_sentinel VALUES ('original-data');
SQL

psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$SOURCE_DATABASE" --set ON_ERROR_STOP=1 <<'SQL'
CREATE SCHEMA instrument_registry;
CREATE SCHEMA platform;
CREATE SCHEMA portfolio;
CREATE SCHEMA watchlist;
CREATE TABLE portfolio.replacement_payload (value text PRIMARY KEY);
INSERT INTO portfolio.replacement_payload VALUES ('incoming-data');
SQL

INCOMING_DUMP="$TEST_ROOT/incoming.pgdump"
pg_dump \
  --host "$DATABASE_HOST" \
  --port "$DATABASE_PORT" \
  --username "$DATABASE_USER" \
  --dbname "$SOURCE_DATABASE" \
  --format custom \
  --schema instrument_registry \
  --schema platform \
  --schema portfolio \
  --schema watchlist \
  --file "$INCOMING_DUMP"
(cd "$TEST_ROOT" && shasum -a 256 "$(basename "$INCOMING_DUMP")" > incoming.sha256)

REAL_PG_RESTORE="$(command -v pg_restore)"
MOCK_BIN="$TEST_ROOT/bin"
LAUNCH_AGENTS_DIR="$TEST_ROOT/LaunchAgents"
EVENT_LOG="$TEST_ROOT/restore-events"
mkdir -p "$MOCK_BIN" "$LAUNCH_AGENTS_DIR"
touch "$LAUNCH_AGENTS_DIR/test.investment-studio.home-api.plist"

printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" Darwin' > "$MOCK_BIN/uname"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "launchctl:%s\n" "$*" >> "$EVENT_LOG"' \
  'if [[ "$1" == "print" ]]; then' \
  '  [[ "$2" == *.home-api ]]' \
  '  exit' \
  'fi' \
  'exit 0' \
  > "$MOCK_BIN/launchctl"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "pg_restore:%s\n" "$*" >> "$EVENT_LOG"' \
  'if [[ "${FAIL_HELPER_ROLLBACK:-false}" == "true" && "$*" == *-pre-restore-* && "$*" != *--list* ]]; then' \
  '  output=""' \
  '  while [[ $# -gt 0 ]]; do' \
  '    if [[ "$1" == "--file" ]]; then output="$2"; shift 2; else shift; fi' \
  '  done' \
  '  [[ -n "$output" ]]' \
  '  if [[ "$output" == "-" ]]; then' \
  '    printf "%s\n" "CREATE SCHEMA instrument_registry;" "CREATE SCHEMA platform;" "CREATE SCHEMA portfolio;" "CREATE SCHEMA watchlist;" "SELECT 1 / 0;"' \
  '  else' \
  '    printf "%s\n" "CREATE SCHEMA instrument_registry;" "CREATE SCHEMA platform;" "CREATE SCHEMA portfolio;" "CREATE SCHEMA watchlist;" "SELECT 1 / 0;" > "$output"' \
  '  fi' \
  '  printf "helper-rollback-sql-injected\n" >> "$EVENT_LOG"' \
  '  exit 0' \
  'fi' \
  'exec "$REAL_PG_RESTORE" "$@"' \
  > "$MOCK_BIN/pg_restore"
chmod +x "$MOCK_BIN/uname" "$MOCK_BIN/launchctl" "$MOCK_BIN/pg_restore"
export EVENT_LOG REAL_PG_RESTORE

FAILING_MIGRATION_RUNNER="$TEST_ROOT/failing-migration-runner"
printf '%s\n' '#!/usr/bin/env bash' 'exit 42' > "$FAILING_MIGRATION_RUNNER"
chmod +x "$FAILING_MIGRATION_RUNNER"

set +e
PATH="$MOCK_BIN:$PATH" \
CONFIRM_RESTORE="$TARGET_DATABASE" \
INVESTMENT_STUDIO_LOCAL_DATABASE_URL="$TARGET_DATABASE_URL" \
PYTHON_BIN="$(command -v python3)" \
INVESTMENT_STUDIO_RESTORE_BACKUP_DIR="$TEST_ROOT/backups" \
INVESTMENT_STUDIO_RESTORE_MIGRATION_RUNNER="$FAILING_MIGRATION_RUNNER" \
INVESTMENT_STUDIO_RESTORE_SERVICE_MANAGER=launchd \
LABEL_PREFIX=test.investment-studio \
LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS_DIR" \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh" "$INCOMING_DUMP" \
  > "$TEST_ROOT/failing-restore.out" 2>&1
restore_status=$?
set -e

if [[ $restore_status -ne 42 ]]; then
  cat "$TEST_ROOT/failing-restore.out" >&2
  echo "Restore did not preserve the injected migration failure after successful rollback: $restore_status" >&2
  exit 1
fi

sentinel="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$TARGET_DATABASE" \
    --tuples-only --no-align --command 'SELECT value FROM portfolio.restore_sentinel'
)"
replacement="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$TARGET_DATABASE" \
    --tuples-only --no-align --command "SELECT to_regclass('portfolio.replacement_payload') IS NULL"
)"

test "$sentinel" = "original-data"
test "$replacement" = "t"
test -n "$(find "$TEST_ROOT/backups" -name '*.pgdump' -type f -print -quit)"
test -n "$(find "$TEST_ROOT/backups" -name '*.pgdump.sha256' -type f -print -quit)"
test -n "$(find "$TEST_ROOT/backups" -name '*.schemas.sha256' -type f -print -quit)"

rollback_line="$(grep -n 'pg_restore:.*-pre-restore-' "$EVENT_LOG" | tail -n 1 | cut -d: -f1)"
restart_line="$(grep -n 'launchctl:bootstrap .*test.investment-studio.home-api.plist' "$EVENT_LOG" | tail -n 1 | cut -d: -f1)"
if [[ -z "$rollback_line" || -z "$restart_line" || "$rollback_line" -ge "$restart_line" ]]; then
  echo "The previous service set restarted before helper-based database rollback completed." >&2
  exit 1
fi

SUCCESSFUL_MIGRATION_RUNNER="$TEST_ROOT/successful-migration-runner"
MIGRATION_ENV="$TEST_ROOT/migration-environment"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'platform_alembic_status=missing' \
  'if [[ -n "${INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL:-}" && "$INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL" == "${INVESTMENT_STUDIO_DATA_DATABASE_URL:-}" ]]; then platform_alembic_status=match; fi' \
  'printf "%s|%s\n" "$platform_alembic_status" "${INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA:-}" > "$MIGRATION_ENV"' \
  'psql "${INVESTMENT_STUDIO_DATA_DATABASE_URL/+psycopg/}" --no-password --set ON_ERROR_STOP=1 --command "ALTER SCHEMA platform RENAME TO data_ingestion; ALTER SCHEMA instrument_registry RENAME TO instrument_data"' \
  > "$SUCCESSFUL_MIGRATION_RUNNER"
chmod +x "$SUCCESSFUL_MIGRATION_RUNNER"
export MIGRATION_ENV

CONFIRM_RESTORE="$TARGET_DATABASE" \
INVESTMENT_STUDIO_LOCAL_DATABASE_URL="$TARGET_DATABASE_URL" \
PYTHON_BIN="$(command -v python3)" \
INVESTMENT_STUDIO_RESTORE_BACKUP_DIR="$TEST_ROOT/backups" \
INVESTMENT_STUDIO_RESTORE_MIGRATION_RUNNER="$SUCCESSFUL_MIGRATION_RUNNER" \
INVESTMENT_STUDIO_RESTORE_SERVICE_MANAGER=none \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh" "$INCOMING_DUMP"

test "$(cat "$MIGRATION_ENV")" = "match|data_ingestion"

incoming_value="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$TARGET_DATABASE" \
    --tuples-only --no-align --command 'SELECT value FROM portfolio.replacement_payload'
)"
original_removed="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$TARGET_DATABASE" \
    --tuples-only --no-align --command "SELECT to_regclass('portfolio.restore_sentinel') IS NULL"
)"

test "$incoming_value" = "incoming-data"
test "$original_removed" = "t"

: > "$EVENT_LOG"
set +e
PATH="$MOCK_BIN:$PATH" \
FAIL_HELPER_ROLLBACK=true \
CONFIRM_RESTORE="$TARGET_DATABASE" \
INVESTMENT_STUDIO_LOCAL_DATABASE_URL="$TARGET_DATABASE_URL" \
PYTHON_BIN="$(command -v python3)" \
INVESTMENT_STUDIO_RESTORE_BACKUP_DIR="$TEST_ROOT/backups" \
INVESTMENT_STUDIO_RESTORE_MIGRATION_RUNNER="$FAILING_MIGRATION_RUNNER" \
INVESTMENT_STUDIO_RESTORE_SERVICE_MANAGER=launchd \
LABEL_PREFIX=test.investment-studio \
LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS_DIR" \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh" "$INCOMING_DUMP" \
  > "$TEST_ROOT/rollback-failure.out" 2>&1
rollback_failure_status=$?
set -e

test "$rollback_failure_status" -eq 70
grep -q '^helper-rollback-sql-injected$' "$EVENT_LOG"
if awk 'seen && /launchctl:bootstrap/ { found=1 } /^helper-rollback-sql-injected$/ { seen=1 } END { exit found ? 0 : 1 }' "$EVENT_LOG"; then
  echo "Managed services restarted after helper-based database rollback failed." >&2
  exit 1
fi
grep -q 'Managed services remain stopped because database rollback did not complete' \
  "$TEST_ROOT/rollback-failure.out"
grep -q 'Restore recovery state retained at:' "$TEST_ROOT/rollback-failure.out"

preserved_value="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$TARGET_DATABASE" \
    --tuples-only --no-align --command 'SELECT value FROM portfolio.replacement_payload'
)"
preserved_schema_count="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$TARGET_DATABASE" \
    --tuples-only --no-align --command "SELECT count(*) FROM pg_namespace WHERE nspname IN ('instrument_registry', 'platform', 'portfolio', 'watchlist')"
)"
# Migration never ran, and the failed atomic rollback must preserve the incoming
# dump's legacy schema names. Use test so Bash 3.2 also honors errexit here.
test "$preserved_value" = "incoming-data"
test "$preserved_schema_count" = "4"

echo "restore failure rollback and success-path integration test passed."
