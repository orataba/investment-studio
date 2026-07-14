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

SUCCESSFUL_POST_MIGRATION_GATE="$TEST_ROOT/successful-post-migration-gate"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  '[[ "$1" == "2026-07-13" ]]' \
  'printf '\''{"status":"passed","failed_count":0,"warning_count":0,"checks":[]}'\'' > "$PORTFOLIO_OPS_RELEASE_AUDIT_OUTPUT_PATH"' \
  > "$SUCCESSFUL_POST_MIGRATION_GATE"
chmod +x "$SUCCESSFUL_POST_MIGRATION_GATE"

set +e
CONFIRM_RESTORE="$TARGET_DATABASE" \
PORTFOLIO_OPS_DB_HOST="$DATABASE_HOST" \
PORTFOLIO_OPS_DB_PORT="$DATABASE_PORT" \
PORTFOLIO_OPS_DB_NAME="$TARGET_DATABASE" \
PORTFOLIO_OPS_DB_USER="$DATABASE_USER" \
PORTFOLIO_OPS_DB_PASSWORD="$DATABASE_PASSWORD" \
PORTFOLIO_OPS_RESTORE_BACKUP_DIR="$TEST_ROOT/backups" \
PORTFOLIO_OPS_RESTORE_MIGRATION_RUNNER="$FAILING_MIGRATION_RUNNER" \
PORTFOLIO_OPS_RESTORE_POST_MIGRATION_GATE="$SUCCESSFUL_POST_MIGRATION_GATE" \
PORTFOLIO_OPS_RESTORE_AS_OF_DATE=2026-07-13 \
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
rollback_calculation_schema="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$TARGET_DATABASE" \
    --tuples-only --no-align --command "SELECT to_regnamespace('calculation_registry') IS NULL"
)"

[[ "$sentinel" == "original-data" ]]
[[ "$replacement" == "t" ]]
[[ "$rollback_calculation_schema" == "t" ]]
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
PORTFOLIO_OPS_RESTORE_POST_MIGRATION_GATE="$SUCCESSFUL_POST_MIGRATION_GATE" \
PORTFOLIO_OPS_RESTORE_AS_OF_DATE=2026-07-13 \
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
upgraded_calculation_schema="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" --dbname "$TARGET_DATABASE" \
    --tuples-only --no-align --command "SELECT to_regnamespace('calculation_registry') IS NOT NULL"
)"

[[ "$incoming_value" == "incoming-data" ]]
[[ "$original_removed" == "t" ]]
[[ "$upgraded_calculation_schema" == "t" ]]

psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" \
  --dbname "$SOURCE_DATABASE" --set ON_ERROR_STOP=1 <<'SQL'
CREATE SCHEMA calculation_registry;
CREATE TABLE calculation_registry.restore_marker (value text PRIMARY KEY);
INSERT INTO calculation_registry.restore_marker VALUES ('calculation-data');
SQL

FOUR_SCHEMA_DUMP="$TEST_ROOT/incoming-four-schema.pgdump"
pg_dump \
  --host "$DATABASE_HOST" \
  --port "$DATABASE_PORT" \
  --username "$DATABASE_USER" \
  --dbname "$SOURCE_DATABASE" \
  --format custom \
  --schema instrument_registry \
  --schema calculation_registry \
  --schema portfolio \
  --schema watchlist \
  --file "$FOUR_SCHEMA_DUMP"
(cd "$TEST_ROOT" && shasum -a 256 "$(basename "$FOUR_SCHEMA_DUMP")" > incoming-four-schema.sha256)

PORTFOLIO_OPS_DUMP_CHECKSUM_PATH="$TEST_ROOT/incoming-four-schema.sha256" \
CONFIRM_RESTORE="$TARGET_DATABASE" \
PORTFOLIO_OPS_DB_HOST="$DATABASE_HOST" \
PORTFOLIO_OPS_DB_PORT="$DATABASE_PORT" \
PORTFOLIO_OPS_DB_NAME="$TARGET_DATABASE" \
PORTFOLIO_OPS_DB_USER="$DATABASE_USER" \
PORTFOLIO_OPS_DB_PASSWORD="$DATABASE_PASSWORD" \
PORTFOLIO_OPS_RESTORE_BACKUP_DIR="$TEST_ROOT/backups" \
PORTFOLIO_OPS_RESTORE_MIGRATION_RUNNER="$SUCCESSFUL_MIGRATION_RUNNER" \
PORTFOLIO_OPS_RESTORE_POST_MIGRATION_GATE="$SUCCESSFUL_POST_MIGRATION_GATE" \
PORTFOLIO_OPS_RESTORE_AS_OF_DATE=2026-07-13 \
PORTFOLIO_OPS_RESTORE_SERVICE_MANAGER=none \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh" "$FOUR_SCHEMA_DUMP"

calculation_value="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" \
    --dbname "$TARGET_DATABASE" --tuples-only --no-align \
    --command 'SELECT value FROM calculation_registry.restore_marker'
)"
[[ "$calculation_value" == "calculation-data" ]]

SYSTEMCTL_CALLS="$TEST_ROOT/systemctl-no-replay-calls"
MOCK_SYSTEMD_BIN="$TEST_ROOT/systemd-bin"
mkdir -p "$MOCK_SYSTEMD_BIN"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$SYSTEMCTL_CALLS"' \
  'if [[ "$*" == "--user is-active --quiet portfolio-ops-market-data-refresh.timer" ]]; then exit 0; fi' \
  'if [[ "$*" == "--user is-active --quiet portfolio-ops-market-data-refresh.service" ]]; then exit 0; fi' \
  'if [[ "${1:-}" == "--user" && "${2:-}" == "is-active" ]]; then exit 3; fi' \
  'exit 0' \
  > "$MOCK_SYSTEMD_BIN/systemctl"
chmod +x "$MOCK_SYSTEMD_BIN/systemctl"
export SYSTEMCTL_CALLS

PATH="$MOCK_SYSTEMD_BIN:$PATH" \
PORTFOLIO_OPS_DUMP_CHECKSUM_PATH="$TEST_ROOT/incoming-four-schema.sha256" \
CONFIRM_RESTORE="$TARGET_DATABASE" \
PORTFOLIO_OPS_DB_HOST="$DATABASE_HOST" \
PORTFOLIO_OPS_DB_PORT="$DATABASE_PORT" \
PORTFOLIO_OPS_DB_NAME="$TARGET_DATABASE" \
PORTFOLIO_OPS_DB_USER="$DATABASE_USER" \
PORTFOLIO_OPS_DB_PASSWORD="$DATABASE_PASSWORD" \
PORTFOLIO_OPS_RESTORE_BACKUP_DIR="$TEST_ROOT/backups" \
PORTFOLIO_OPS_RESTORE_MIGRATION_RUNNER="$SUCCESSFUL_MIGRATION_RUNNER" \
PORTFOLIO_OPS_RESTORE_POST_MIGRATION_GATE="$SUCCESSFUL_POST_MIGRATION_GATE" \
PORTFOLIO_OPS_RESTORE_AS_OF_DATE=2026-07-13 \
PORTFOLIO_OPS_RESTORE_SERVICE_MANAGER=systemd \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh" "$FOUR_SCHEMA_DUMP"
grep -Fxq -- '--user start portfolio-ops-market-data-refresh.timer' "$SYSTEMCTL_CALLS"
if grep -Fq -- '--user start portfolio-ops-market-data-refresh.service' "$SYSTEMCTL_CALLS"; then
  echo "Restore replayed the fenced systemd refresh oneshot." >&2
  exit 1
fi

SYSTEMCTL_CALLS="$TEST_ROOT/systemctl-start-failure-calls"
: > "$SYSTEMCTL_CALLS"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$SYSTEMCTL_CALLS"' \
  'if [[ "$*" == "--user is-active --quiet portfolio-ops-portfolio-api.service" ]]; then exit 0; fi' \
  'if [[ "${1:-}" == "--user" && "${2:-}" == "is-active" ]]; then exit 3; fi' \
  'if [[ "${1:-}" == "--user" && "${2:-}" == "start" ]]; then exit 1; fi' \
  'exit 0' \
  > "$MOCK_SYSTEMD_BIN/systemctl"
chmod +x "$MOCK_SYSTEMD_BIN/systemctl"

set +e
PATH="$MOCK_SYSTEMD_BIN:$PATH" \
PORTFOLIO_OPS_DUMP_CHECKSUM_PATH="$TEST_ROOT/incoming-four-schema.sha256" \
CONFIRM_RESTORE="$TARGET_DATABASE" \
PORTFOLIO_OPS_DB_HOST="$DATABASE_HOST" \
PORTFOLIO_OPS_DB_PORT="$DATABASE_PORT" \
PORTFOLIO_OPS_DB_NAME="$TARGET_DATABASE" \
PORTFOLIO_OPS_DB_USER="$DATABASE_USER" \
PORTFOLIO_OPS_DB_PASSWORD="$DATABASE_PASSWORD" \
PORTFOLIO_OPS_RESTORE_BACKUP_DIR="$TEST_ROOT/backups" \
PORTFOLIO_OPS_RESTORE_MIGRATION_RUNNER="$SUCCESSFUL_MIGRATION_RUNNER" \
PORTFOLIO_OPS_RESTORE_POST_MIGRATION_GATE="$SUCCESSFUL_POST_MIGRATION_GATE" \
PORTFOLIO_OPS_RESTORE_AS_OF_DATE=2026-07-13 \
PORTFOLIO_OPS_RESTORE_SERVICE_MANAGER=systemd \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh" "$FOUR_SCHEMA_DUMP" \
  > "$TEST_ROOT/start-failure.out" 2>&1
start_failure_status=$?
set -e
if [[ $start_failure_status -eq 0 ]]; then
  echo "Restore unexpectedly succeeded after an injected service-start failure." >&2
  exit 1
fi
[[ "$(grep -c '^--user start ' "$SYSTEMCTL_CALLS")" -eq 1 ]]
[[ "$(grep -c '^--user stop ' "$SYSTEMCTL_CALLS")" -eq 2 ]]
grep -Fxq -- '--user stop portfolio-ops-portfolio-api.service' "$SYSTEMCTL_CALLS"
grep -Fq -- '--user stop portfolio-ops-platform-api.service portfolio-ops-watchlist-api.service' "$SYSTEMCTL_CALLS"
grep -Fq 'managed services did not restart cleanly' "$TEST_ROOT/start-failure.out"

echo "restore rollback, four-schema, no-replay, and start-failure lifecycle test passed."
