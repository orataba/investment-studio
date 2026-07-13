#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-release-test.XXXXXX")"
DATABASE_HOST="${PORTFOLIO_OPS_TEST_DB_HOST:-127.0.0.1}"
DATABASE_PORT="${PORTFOLIO_OPS_TEST_DB_PORT:-5432}"
DATABASE_USER="${PORTFOLIO_OPS_TEST_DB_USER:-$(id -un)}"
DATABASE_PASSWORD="${PORTFOLIO_OPS_TEST_DB_PASSWORD:-}"
TARGET_DATABASE="portfolio_ops_release_target_$$"
PYTHON_BIN="${PYTHON_BIN:-$REPOSITORY_ROOT/.venv/bin/python}"

export PGPASSWORD="$DATABASE_PASSWORD"

cleanup() {
  dropdb --if-exists --host "$DATABASE_HOST" --port "$DATABASE_PORT" \
    --username "$DATABASE_USER" "$TARGET_DATABASE" >/dev/null 2>&1 || true
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

createdb --host "$DATABASE_HOST" --port "$DATABASE_PORT" \
  --username "$DATABASE_USER" "$TARGET_DATABASE"
psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" \
  --username "$DATABASE_USER" --dbname "$TARGET_DATABASE" \
  --set ON_ERROR_STOP=1 <<'SQL'
CREATE SCHEMA instrument_registry;
CREATE SCHEMA portfolio;
CREATE SCHEMA watchlist;
CREATE TABLE portfolio.release_sentinel (value text PRIMARY KEY);
INSERT INTO portfolio.release_sentinel VALUES ('original-data');
SQL

PARTIAL_MIGRATION_RUNNER="$TEST_ROOT/partial-migration-runner"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'psql --host "$PORTFOLIO_OPS_DB_HOST" --port "$PORTFOLIO_OPS_DB_PORT" --username "$PORTFOLIO_OPS_DB_USER" --dbname "$PORTFOLIO_OPS_DB_NAME" --set ON_ERROR_STOP=1 --command "UPDATE portfolio.release_sentinel SET value = '\''partial-migration'\''"' \
  'exit 42' \
  > "$PARTIAL_MIGRATION_RUNNER"

SUCCESSFUL_MIGRATION_RUNNER="$TEST_ROOT/successful-migration-runner"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'psql --host "$PORTFOLIO_OPS_DB_HOST" --port "$PORTFOLIO_OPS_DB_PORT" --username "$PORTFOLIO_OPS_DB_USER" --dbname "$PORTFOLIO_OPS_DB_NAME" --set ON_ERROR_STOP=1 --command "UPDATE portfolio.release_sentinel SET value = '\''migration-complete'\''"' \
  > "$SUCCESSFUL_MIGRATION_RUNNER"

FAILING_GATE="$TEST_ROOT/failing-gate"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf '\''{"status":"failed","failed_count":1,"warning_count":0,"checks":[]}'\'' > "$PORTFOLIO_OPS_RELEASE_AUDIT_OUTPUT_PATH"' \
  'exit 43' \
  > "$FAILING_GATE"

SUCCESSFUL_GATE="$TEST_ROOT/successful-gate"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  '[[ "$1" == "2026-07-13" ]]' \
  'printf '\''{"status":"passed","failed_count":0,"warning_count":0,"checks":[]}'\'' > "$PORTFOLIO_OPS_RELEASE_AUDIT_OUTPUT_PATH"' \
  > "$SUCCESSFUL_GATE"
chmod +x \
  "$PARTIAL_MIGRATION_RUNNER" \
  "$SUCCESSFUL_MIGRATION_RUNNER" \
  "$FAILING_GATE" \
  "$SUCCESSFUL_GATE"

run_release() {
  local migration_runner="$1" gate_runner="$2"
  CONFIRM_RELEASE="$TARGET_DATABASE@$DATABASE_HOST:$DATABASE_PORT" \
  PORTFOLIO_OPS_DB_HOST="$DATABASE_HOST" \
  PORTFOLIO_OPS_DB_PORT="$DATABASE_PORT" \
  PORTFOLIO_OPS_DB_NAME="$TARGET_DATABASE" \
  PORTFOLIO_OPS_DB_USER="$DATABASE_USER" \
  PORTFOLIO_OPS_DB_PASSWORD="$DATABASE_PASSWORD" \
  PORTFOLIO_OPS_RELEASE_DATABASE_URL="postgresql+psycopg://$DATABASE_USER@$DATABASE_HOST:$DATABASE_PORT/$TARGET_DATABASE" \
  PORTFOLIO_OPS_RELEASE_AS_OF_DATE=2026-07-13 \
  PORTFOLIO_OPS_RELEASE_BACKUP_DIR="$TEST_ROOT/backups" \
  PORTFOLIO_OPS_RELEASE_MIGRATION_RUNNER="$migration_runner" \
  PORTFOLIO_OPS_RELEASE_POST_MIGRATION_GATE="$gate_runner" \
  PORTFOLIO_OPS_RELEASE_SERVICE_MANAGER=none \
  PYTHON_BIN="$PYTHON_BIN" \
    "$REPOSITORY_ROOT/infra/scripts/release_database.sh"
}

set +e
run_release "$PARTIAL_MIGRATION_RUNNER" "$SUCCESSFUL_GATE"
partial_status=$?
set -e
if [[ $partial_status -eq 0 ]]; then
  echo "Release unexpectedly succeeded after an injected partial migration failure." >&2
  exit 1
fi
partial_value="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" \
    --dbname "$TARGET_DATABASE" --tuples-only --no-align \
    --command 'SELECT value FROM portfolio.release_sentinel'
)"
[[ "$partial_value" == "original-data" ]]

set +e
run_release "$SUCCESSFUL_MIGRATION_RUNNER" "$FAILING_GATE"
gate_status=$?
set -e
if [[ $gate_status -eq 0 ]]; then
  echo "Release unexpectedly succeeded after an injected post-migration gate failure." >&2
  exit 1
fi
gate_value="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" \
    --dbname "$TARGET_DATABASE" --tuples-only --no-align \
    --command 'SELECT value FROM portfolio.release_sentinel'
)"
[[ "$gate_value" == "original-data" ]]

run_release "$SUCCESSFUL_MIGRATION_RUNNER" "$SUCCESSFUL_GATE"
released_value="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" \
    --dbname "$TARGET_DATABASE" --tuples-only --no-align \
    --command 'SELECT value FROM portfolio.release_sentinel'
)"
[[ "$released_value" == "migration-complete" ]]

backup_count="$(find "$TEST_ROOT/backups" -name '*.pgdump' -type f | wc -l | tr -d ' ')"
checksum_count="$(find "$TEST_ROOT/backups" -name '*.pgdump.sha256' -type f | wc -l | tr -d ' ')"
[[ "$backup_count" -ge 4 ]]
[[ "$checksum_count" == "$backup_count" ]]

echo "release partial-migration rollback, gate rollback, and success test passed."
