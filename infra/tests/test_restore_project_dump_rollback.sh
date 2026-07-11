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

export PGPASSWORD="$DATABASE_PASSWORD"

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

FAILING_MIGRATION_RUNNER="$TEST_ROOT/failing-migration-runner"
printf '%s\n' '#!/usr/bin/env bash' 'exit 42' > "$FAILING_MIGRATION_RUNNER"
chmod +x "$FAILING_MIGRATION_RUNNER"

set +e
CONFIRM_RESTORE="$TARGET_DATABASE" \
PORTFOLIO_OPS_DB_HOST="$DATABASE_HOST" \
PORTFOLIO_OPS_DB_PORT="$DATABASE_PORT" \
PORTFOLIO_OPS_DB_NAME="$TARGET_DATABASE" \
PORTFOLIO_OPS_DB_USER="$DATABASE_USER" \
PORTFOLIO_OPS_DB_PASSWORD="$DATABASE_PASSWORD" \
PORTFOLIO_OPS_RESTORE_BACKUP_DIR="$TEST_ROOT/backups" \
PORTFOLIO_OPS_RESTORE_MIGRATION_RUNNER="$FAILING_MIGRATION_RUNNER" \
PORTFOLIO_OPS_RESTORE_SERVICE_MANAGER=none \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh" "$INCOMING_DUMP"
restore_status=$?
set -e

if [[ $restore_status -eq 0 ]]; then
  echo "Restore unexpectedly succeeded despite the injected migration failure." >&2
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

SUCCESSFUL_MIGRATION_RUNNER="$TEST_ROOT/successful-migration-runner"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$SUCCESSFUL_MIGRATION_RUNNER"
chmod +x "$SUCCESSFUL_MIGRATION_RUNNER"

CONFIRM_RESTORE="$TARGET_DATABASE" \
PORTFOLIO_OPS_DB_HOST="$DATABASE_HOST" \
PORTFOLIO_OPS_DB_PORT="$DATABASE_PORT" \
PORTFOLIO_OPS_DB_NAME="$TARGET_DATABASE" \
PORTFOLIO_OPS_DB_USER="$DATABASE_USER" \
PORTFOLIO_OPS_DB_PASSWORD="$DATABASE_PASSWORD" \
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

echo "restore failure rollback and success-path integration test passed."
