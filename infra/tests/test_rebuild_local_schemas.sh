#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REBUILD_SCRIPT="$REPOSITORY_ROOT/infra/postgres/rebuild_local_schemas.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-rebuild-test.XXXXXX")"
REAL_PYTHON="$(command -v python3)"
trap 'rm -rf "$TEST_ROOT"' EXIT

PROJECT_ROOT="$TEST_ROOT/project"
MOCK_BIN="$TEST_ROOT/bin"
EVENT_LOG="$TEST_ROOT/events"
MIGRATION_ENV="$TEST_ROOT/migration-environment"
mkdir -p \
  "$PROJECT_ROOT/infra/scripts" \
  "$PROJECT_ROOT/infra/launchd" \
  "$PROJECT_ROOT/infra/postgres" \
  "$MOCK_BIN" \
  "$TEST_ROOT/tmp"
cp "$REPOSITORY_ROOT/infra/postgres/project_schema_backup.sh" \
  "$PROJECT_ROOT/infra/postgres/project_schema_backup.sh"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "psql" >> "$EVENT_LOG"' \
  'for argument in "$@"; do printf "|%s" "$argument" >> "$EVENT_LOG"; done' \
  'printf "\n" >> "$EVENT_LOG"' \
  'if [[ "$*" == *"current_database()"* ]]; then printf "%s\n" "portfolio_ops|portfolio_ops"; fi' \
  > "$MOCK_BIN/psql"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'action="$1"' \
  'state_file="$2"' \
  'printf "services:%s\n" "$action" >> "$EVENT_LOG"' \
  'if [[ "$action" == "stop" ]]; then printf "%s\n" platform-api portfolio-web > "$state_file"; fi' \
  > "$PROJECT_ROOT/infra/launchd/control_local_services.sh"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n" \
    "$PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL" \
    "$PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL" \
    "$PORTFOLIO_OPS_PLATFORM_DATABASE_URL" \
    "$PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL" \
    "$PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA" \
    "$PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA" \
    "$PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL" \
    "$PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL" \
    "$PORTFOLIO_OPS_WATCHLIST_DATABASE_URL" \
    "$PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL" > "$MIGRATION_ENV"' \
  'printf "migrate\n" >> "$EVENT_LOG"' \
  '[[ "${MIGRATION_FAIL:-false}" != "true" ]]' \
  > "$PROJECT_ROOT/infra/scripts/migrate_all.sh"

chmod +x \
  "$MOCK_BIN/psql" \
  "$PROJECT_ROOT/infra/launchd/control_local_services.sh" \
  "$PROJECT_ROOT/infra/scripts/migrate_all.sh"

DATABASE_URL='postgresql://portfolio_ops@127.0.0.1:5432/portfolio_ops'
PASSWORD_DATABASE_URL="${DATABASE_URL/portfolio_ops@/portfolio_ops:sensitive-password@}"
export EVENT_LOG MIGRATION_ENV

set +e
PORTFOLIO_OPS_LOCAL_DATABASE_URL="$PASSWORD_DATABASE_URL" \
  "$REBUILD_SCRIPT" > "$TEST_ROOT/unconfirmed.out" 2>&1
unconfirmed_status=$?
set -e
[[ $unconfirmed_status -eq 64 ]]
[[ ! -e "$EVENT_LOG" ]]
if grep -q 'sensitive-password' "$TEST_ROOT/unconfirmed.out"; then
  echo "Rebuild confirmation error exposed database credentials." >&2
  exit 1
fi

set +e
PROJECT_ROOT="$PROJECT_ROOT" \
PSQL_BIN="$MOCK_BIN/psql" \
PYTHON_BIN="$REAL_PYTHON" \
TMPDIR="$TEST_ROOT/tmp" \
PORTFOLIO_OPS_LOCAL_DATABASE_URL="$PASSWORD_DATABASE_URL" \
  "$REBUILD_SCRIPT" --confirm-destroy-project-schemas \
  > "$TEST_ROOT/password-url.out" 2>&1
password_url_status=$?
set -e
[[ $password_url_status -eq 64 ]]
grep -q 'must not contain passwords' "$TEST_ROOT/password-url.out"
[[ ! -e "$EVENT_LOG" ]]
if grep -q 'sensitive-password' "$TEST_ROOT/password-url.out"; then
  echo "Rebuild password rejection exposed database credentials." >&2
  exit 1
fi

PROJECT_ROOT="$PROJECT_ROOT" \
PSQL_BIN="$MOCK_BIN/psql" \
PYTHON_BIN="$REAL_PYTHON" \
TMPDIR="$TEST_ROOT/tmp" \
PORTFOLIO_OPS_LOCAL_DATABASE_URL="$DATABASE_URL" \
  "$REBUILD_SCRIPT" --confirm-destroy-project-schemas \
  > "$TEST_ROOT/success.out" 2>&1

grep -q '^services:stop$' "$EVENT_LOG"
grep -q '^services:start$' "$EVENT_LOG"
grep -q -- '--set|ON_ERROR_STOP=1' "$EVENT_LOG"
grep -q -- '--single-transaction' "$EVENT_LOG"
grep -q 'DROP SCHEMA IF EXISTS platform CASCADE' "$EVENT_LOG"
grep -q 'CREATE SCHEMA platform' "$EVENT_LOG"
expected_migration_env="$DATABASE_URL|$DATABASE_URL|$DATABASE_URL|$DATABASE_URL|instrument_registry|platform|$DATABASE_URL|$DATABASE_URL|$DATABASE_URL|$DATABASE_URL"
[[ "$(cat "$MIGRATION_ENV")" == "$expected_migration_env" ]]

: > "$EVENT_LOG"
set +e
PROJECT_ROOT="$PROJECT_ROOT" \
PSQL_BIN="$MOCK_BIN/psql" \
PYTHON_BIN="$REAL_PYTHON" \
TMPDIR="$TEST_ROOT/tmp" \
MIGRATION_FAIL=true \
PORTFOLIO_OPS_LOCAL_DATABASE_URL="$DATABASE_URL" \
  "$REBUILD_SCRIPT" --confirm-destroy-project-schemas \
  > "$TEST_ROOT/failure.out" 2>&1
failure_status=$?
set -e
[[ $failure_status -ne 0 ]]
grep -q '^services:stop$' "$EVENT_LOG"
if grep -q '^services:start$' "$EVENT_LOG"; then
  echo "Services restarted after a destructive rebuild failure." >&2
  exit 1
fi
grep -q 'managed services remain stopped' "$TEST_ROOT/failure.out"
echo "explicit, single-target schema rebuild safety test passed."
