#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-restore-rollback-test.XXXXXX")"
DATABASE_HOST="${PORTFOLIO_OPS_TEST_DB_HOST:-127.0.0.1}"
DATABASE_PORT="${PORTFOLIO_OPS_TEST_DB_PORT:-5432}"
DATABASE_USER="${PORTFOLIO_OPS_TEST_DB_USER:-$(id -un)}"
DATABASE_PASSWORD="${PORTFOLIO_OPS_TEST_DB_PASSWORD:-}"
TARGET_DATABASE="portfolio_ops_restore_target_$$"
SOURCE_DATABASE="portfolio_ops_restore_source_$$"
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
CREATE SCHEMA portfolio;
CREATE SCHEMA watchlist;
CREATE TABLE portfolio.restore_sentinel (value text PRIMARY KEY);
INSERT INTO portfolio.restore_sentinel VALUES ('original-data');
SQL

psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$SOURCE_DATABASE" --set ON_ERROR_STOP=1 <<'SQL'
CREATE SCHEMA instrument_registry;
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
  --schema portfolio \
  --schema watchlist \
  --file "$INCOMING_DUMP"
(cd "$TEST_ROOT" && shasum -a 256 "$(basename "$INCOMING_DUMP")" > incoming.sha256)

REAL_PG_RESTORE="$(command -v pg_restore)"
MOCK_BIN="$TEST_ROOT/bin"
LAUNCH_AGENTS_DIR="$TEST_ROOT/LaunchAgents"
EVENT_LOG="$TEST_ROOT/restore-events"
mkdir -p "$MOCK_BIN" "$LAUNCH_AGENTS_DIR"
touch "$LAUNCH_AGENTS_DIR/test.portfolio-ops.platform-api.plist"

printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" Darwin' > "$MOCK_BIN/uname"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "launchctl:%s\n" "$*" >> "$EVENT_LOG"' \
  'if [[ "$1" == "print" ]]; then' \
  '  [[ "$2" == *.platform-api ]]' \
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
  '  printf "%s\n" "CREATE SCHEMA instrument_registry;" "CREATE SCHEMA portfolio;" "CREATE SCHEMA watchlist;" "SELECT 1 / 0;" > "$output"' \
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
PORTFOLIO_OPS_LOCAL_DATABASE_URL="$TARGET_DATABASE_URL" \
PYTHON_BIN="$(command -v python3)" \
PORTFOLIO_OPS_RESTORE_BACKUP_DIR="$TEST_ROOT/backups" \
PORTFOLIO_OPS_RESTORE_MIGRATION_RUNNER="$FAILING_MIGRATION_RUNNER" \
PORTFOLIO_OPS_RESTORE_SERVICE_MANAGER=launchd \
LABEL_PREFIX=test.portfolio-ops \
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

[[ "$sentinel" == "original-data" ]]
[[ "$replacement" == "t" ]]
test -n "$(find "$TEST_ROOT/backups" -name '*.pgdump' -type f -print -quit)"
test -n "$(find "$TEST_ROOT/backups" -name '*.pgdump.sha256' -type f -print -quit)"
test -n "$(find "$TEST_ROOT/backups" -name '*.schemas.sha256' -type f -print -quit)"

rollback_line="$(grep -n 'pg_restore:.*-pre-restore-' "$EVENT_LOG" | tail -n 1 | cut -d: -f1)"
restart_line="$(grep -n 'launchctl:bootstrap .*test.portfolio-ops.platform-api.plist' "$EVENT_LOG" | tail -n 1 | cut -d: -f1)"
if [[ -z "$rollback_line" || -z "$restart_line" || "$rollback_line" -ge "$restart_line" ]]; then
  echo "The previous service set restarted before helper-based database rollback completed." >&2
  exit 1
fi

SUCCESSFUL_MIGRATION_RUNNER="$TEST_ROOT/successful-migration-runner"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$SUCCESSFUL_MIGRATION_RUNNER"
chmod +x "$SUCCESSFUL_MIGRATION_RUNNER"

CONFIRM_RESTORE="$TARGET_DATABASE" \
PORTFOLIO_OPS_LOCAL_DATABASE_URL="$TARGET_DATABASE_URL" \
PYTHON_BIN="$(command -v python3)" \
PORTFOLIO_OPS_RESTORE_BACKUP_DIR="$TEST_ROOT/backups" \
PORTFOLIO_OPS_RESTORE_MIGRATION_RUNNER="$SUCCESSFUL_MIGRATION_RUNNER" \
PORTFOLIO_OPS_RESTORE_SERVICE_MANAGER=none \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh" "$INCOMING_DUMP"

incoming_value="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$TARGET_DATABASE" \
    --tuples-only --no-align --command 'SELECT value FROM portfolio.replacement_payload'
)"
original_removed="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$TARGET_DATABASE" \
    --tuples-only --no-align --command "SELECT to_regclass('portfolio.restore_sentinel') IS NULL"
)"

[[ "$incoming_value" == "incoming-data" ]]
[[ "$original_removed" == "t" ]]

: > "$EVENT_LOG"
set +e
PATH="$MOCK_BIN:$PATH" \
FAIL_HELPER_ROLLBACK=true \
CONFIRM_RESTORE="$TARGET_DATABASE" \
PORTFOLIO_OPS_LOCAL_DATABASE_URL="$TARGET_DATABASE_URL" \
PYTHON_BIN="$(command -v python3)" \
PORTFOLIO_OPS_RESTORE_BACKUP_DIR="$TEST_ROOT/backups" \
PORTFOLIO_OPS_RESTORE_MIGRATION_RUNNER="$FAILING_MIGRATION_RUNNER" \
PORTFOLIO_OPS_RESTORE_SERVICE_MANAGER=launchd \
LABEL_PREFIX=test.portfolio-ops \
LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS_DIR" \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh" "$INCOMING_DUMP" \
  > "$TEST_ROOT/rollback-failure.out" 2>&1
rollback_failure_status=$?
set -e

[[ $rollback_failure_status -eq 70 ]]
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
    --tuples-only --no-align --command "SELECT count(*) FROM pg_namespace WHERE nspname IN ('instrument_registry', 'portfolio', 'watchlist')"
)"
[[ "$preserved_value" == "incoming-data" ]]
[[ "$preserved_schema_count" == "3" ]]

echo "restore failure rollback and success-path integration test passed."
