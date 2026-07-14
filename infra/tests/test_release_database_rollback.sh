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
PGPASSFILE="$TEST_ROOT/empty-pgpass"
: > "$PGPASSFILE"
chmod 600 "$PGPASSFILE"
export PGPASSFILE

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
  'psql --host "$PORTFOLIO_OPS_DB_HOST" --port "$PORTFOLIO_OPS_DB_PORT" --username "$PORTFOLIO_OPS_DB_USER" --dbname "$PORTFOLIO_OPS_DB_NAME" --set ON_ERROR_STOP=1 --command "CREATE SCHEMA IF NOT EXISTS calculation_registry; UPDATE portfolio.release_sentinel SET value = '\''migration-complete'\''"' \
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
  local migration_runner="$1" gate_runner="$2" service_manager="${3:-none}"
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
  PORTFOLIO_OPS_RELEASE_SERVICE_MANAGER="$service_manager" \
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
partial_calculation_schema="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" \
    --dbname "$TARGET_DATABASE" --tuples-only --no-align \
    --command "SELECT to_regnamespace('calculation_registry') IS NULL"
)"
[[ "$partial_calculation_schema" == "t" ]]

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
gate_calculation_schema="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" \
    --dbname "$TARGET_DATABASE" --tuples-only --no-align \
    --command "SELECT to_regnamespace('calculation_registry') IS NULL"
)"
[[ "$gate_calculation_schema" == "t" ]]

SYSTEMCTL_CALLS="$TEST_ROOT/systemctl-fence-calls"
MOCK_SYSTEMD_BIN="$TEST_ROOT/systemd-bin"
mkdir -p "$MOCK_SYSTEMD_BIN"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$SYSTEMCTL_CALLS"' \
  'if [[ "$*" == "--user is-active --quiet portfolio-ops-portfolio-api.service" ]]; then exit 0; fi' \
  'if [[ "$*" == "--user is-active portfolio-ops-portfolio-api.service" ]]; then printf "active\n"; exit 0; fi' \
  'if [[ "${1:-}" == "--user" && "${2:-}" == "is-active" ]]; then printf "inactive\n"; exit 3; fi' \
  'exit 0' \
  > "$MOCK_SYSTEMD_BIN/systemctl"
chmod +x "$MOCK_SYSTEMD_BIN/systemctl"
export SYSTEMCTL_CALLS

set +e
PATH="$MOCK_SYSTEMD_BIN:$PATH" \
  run_release "$PARTIAL_MIGRATION_RUNNER" "$SUCCESSFUL_GATE" systemd \
  > "$TEST_ROOT/unfenced-rollback.out" 2>&1
unfenced_status=$?
set -e
if [[ $unfenced_status -eq 0 ]]; then
  echo "Release unexpectedly succeeded when the managed-writer fence failed." >&2
  exit 1
fi
[[ "$unfenced_status" -eq 70 ]]
grep -Fq 'AUTOMATIC ROLLBACK SKIPPED' "$TEST_ROOT/unfenced-rollback.out"
grep -Fq -- '--user stop portfolio-ops-platform-api.service' "$SYSTEMCTL_CALLS"
unfenced_value="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" \
    --dbname "$TARGET_DATABASE" --tuples-only --no-align \
    --command 'SELECT value FROM portfolio.release_sentinel'
)"
[[ "$unfenced_value" == "partial-migration" ]]
psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" \
  --dbname "$TARGET_DATABASE" --set ON_ERROR_STOP=1 \
  --command "UPDATE portfolio.release_sentinel SET value = 'original-data'" >/dev/null

SYSTEMCTL_CALLS="$TEST_ROOT/systemctl-no-replay-calls"
: > "$SYSTEMCTL_CALLS"
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
PATH="$MOCK_SYSTEMD_BIN:$PATH" \
  run_release "$SUCCESSFUL_MIGRATION_RUNNER" "$SUCCESSFUL_GATE" systemd
grep -Fxq -- '--user start portfolio-ops-market-data-refresh.timer' "$SYSTEMCTL_CALLS"
if grep -Fq -- '--user start portfolio-ops-market-data-refresh.service' "$SYSTEMCTL_CALLS"; then
  echo "Release replayed the fenced systemd refresh oneshot." >&2
  exit 1
fi
released_value="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" \
    --dbname "$TARGET_DATABASE" --tuples-only --no-align \
    --command 'SELECT value FROM portfolio.release_sentinel'
)"
[[ "$released_value" == "migration-complete" ]]
released_calculation_schema="$(
  psql --host "$DATABASE_HOST" --port "$DATABASE_PORT" --username "$DATABASE_USER" \
    --dbname "$TARGET_DATABASE" --tuples-only --no-align \
    --command "SELECT to_regnamespace('calculation_registry') IS NOT NULL"
)"
[[ "$released_calculation_schema" == "t" ]]

backup_count="$(find "$TEST_ROOT/backups" -name '*.pgdump' -type f | wc -l | tr -d ' ')"
checksum_count="$(find "$TEST_ROOT/backups" -name '*.pgdump.sha256' -type f | wc -l | tr -d ' ')"
[[ "$backup_count" -ge 4 ]]
[[ "$checksum_count" == "$backup_count" ]]

echo "release rollback, verified fencing, no-replay, and success test passed."
