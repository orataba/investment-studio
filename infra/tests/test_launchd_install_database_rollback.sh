#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="$REPOSITORY_ROOT/infra/launchd/install_local_services.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-launchd-install-test.XXXXXX")"
REAL_PYTHON="$(command -v python3)"
trap 'rm -rf "$TEST_ROOT"' EXIT

prepare_case() {
  local case_root="$1"
  local project_root="$case_root/project"
  local mock_bin="$case_root/bin"
  local plist_root="$case_root/LaunchAgents"
  local env_root="$case_root/env"
  local service

  mkdir -p \
    "$project_root/infra/scripts" \
    "$project_root/infra/postgres" \
    "$project_root/apps/platform/frontend/dist" \
    "$project_root/apps/watchlist/frontend/dist" \
    "$project_root/apps/portfolio/frontend/dist" \
    "$project_root/apps/platform/backend/scripts" \
    "$project_root/apps/portfolio/backend/scripts" \
    "$mock_bin" \
    "$plist_root" \
    "$env_root" \
    "$case_root/logs" \
    "$case_root/backups" \
    "$case_root/tmp"
  chmod 700 "$env_root" "$case_root/backups" "$case_root/tmp"
  : > "$env_root/platform.env"
  chmod 600 "$env_root/platform.env"
  touch \
    "$project_root/apps/platform/frontend/dist/index.html" \
    "$project_root/apps/watchlist/frontend/dist/index.html" \
    "$project_root/apps/portfolio/frontend/dist/index.html"
  cp "$REPOSITORY_ROOT/infra/postgres/project_schema_backup.sh" \
    "$project_root/infra/postgres/project_schema_backup.sh"

  for service in \
    platform-api watchlist-api portfolio-api \
    platform-web watchlist-web portfolio-web market-data-refresh; do
    printf 'old-%s\n' "$service" > "$plist_root/test.portfolio-ops.$service.plist"
    chmod 600 "$plist_root/test.portfolio-ops.$service.plist"
  done

  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'printf "migrate\n" >> "$EVENT_LOG"' \
    'platform_alembic_status=missing' \
    'if [[ -n "${PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL:-}" && "$PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL" == "${PORTFOLIO_OPS_PLATFORM_DATABASE_URL:-}" ]]; then platform_alembic_status=match; fi' \
    'printf "platform-migration-env:%s|%s\n" "$platform_alembic_status" "${PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA:-}" >> "$EVENT_LOG"' \
    'if [[ "${MIGRATION_FAIL:-false}" == "true" ]]; then exit 9; fi' \
    > "$project_root/infra/scripts/migrate_all.sh"
  printf '%s\n' \
    'from __future__ import annotations' \
    'import os' \
    'import sys' \
    'from pathlib import Path' \
    'with Path(os.environ["EVENT_LOG"]).open("a", encoding="utf-8") as handle:' \
    '    handle.write("market-refresh:" + " ".join(sys.argv[1:]) + "\n")' \
    > "$project_root/apps/platform/backend/scripts/refresh_market_data_scheduled.py"
  printf '%s\n' \
    'from __future__ import annotations' \
    'import os' \
    'from pathlib import Path' \
    'with Path(os.environ["EVENT_LOG"]).open("a", encoding="utf-8") as handle:' \
    '    handle.write("audit\n")' \
    > "$project_root/infra/scripts/audit_live_data.py"
  printf '%s\n' \
    'from __future__ import annotations' \
    'import os' \
    'from pathlib import Path' \
    'with Path(os.environ["EVENT_LOG"]).open("a", encoding="utf-8") as handle:' \
    '    handle.write("snapshot-refresh\n")' \
    'raise SystemExit(1 if os.environ.get("SNAPSHOT_REFRESH_FAIL") == "true" else 0)' \
    > "$project_root/apps/portfolio/backend/scripts/refresh_release_snapshots.py"

  printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" Darwin' > "$mock_bin/uname"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$mock_bin/node"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$mock_bin/npm"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$mock_bin/createdb"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$mock_bin/sleep"
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf "health\n" >> "$EVENT_LOG"' \
    'exit 1' \
    > "$mock_bin/curl"

  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'if [[ "$*" == *sensitive-password* ]]; then printf "credential-in-argv\n" >> "$EVENT_LOG"; fi' \
    'case "$*" in' \
    '  *pg_roles*) printf "%s\n" 1 ;;' \
    '  *pg_database*) printf "%s\n" 1 ;;' \
    '  *pg_namespace*) printf "%s\n" instrument_registry platform portfolio watchlist ;;' \
    '  *"DROP SCHEMA"*) printf "drop-schemas\n" >> "$EVENT_LOG" ;;' \
    'esac' \
    'exit 0' \
    > "$mock_bin/psql"

  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'if [[ "$*" == *sensitive-password* ]]; then printf "credential-in-argv\n" >> "$EVENT_LOG"; fi' \
    'printf "pg_dump:%s\n" "$*" >> "$EVENT_LOG"' \
    'output=""' \
    'while [[ $# -gt 0 ]]; do' \
    '  if [[ "$1" == "--file" ]]; then output="$2"; shift 2; else shift; fi' \
    'done' \
    '[[ -n "$output" ]]' \
    'printf "%s\n" verified-custom-archive > "$output"' \
    'printf "backup\n" >> "$EVENT_LOG"' \
    > "$mock_bin/pg_dump"

  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'if [[ "$*" == *sensitive-password* ]]; then printf "credential-in-argv\n" >> "$EVENT_LOG"; fi' \
    'if [[ "$1" == "--list" ]]; then' \
    '  printf "%s\n" "1; 0 0 SCHEMA - instrument_registry owner" "2; 0 0 SCHEMA - platform owner" "3; 0 0 SCHEMA - portfolio owner" "4; 0 0 SCHEMA - watchlist owner"' \
    '  exit 0' \
    'fi' \
    'output=""' \
    'while [[ $# -gt 0 ]]; do' \
    '  if [[ "$1" == "--file" ]]; then output="$2"; shift 2; else shift; fi' \
    'done' \
    'if [[ "${FAIL_ROLLBACK:-false}" == "true" ]]; then' \
    '  printf "restore-failed\n" >> "$EVENT_LOG"' \
    '  exit 12' \
    'fi' \
    'if [[ "$output" == "-" ]]; then' \
    '  printf "%s\n" "CREATE SCHEMA instrument_registry;" "CREATE SCHEMA platform;" "CREATE SCHEMA portfolio;" "CREATE SCHEMA watchlist;"' \
    'elif [[ -n "$output" ]]; then' \
    '  printf "%s\n" "CREATE SCHEMA instrument_registry;" "CREATE SCHEMA platform;" "CREATE SCHEMA portfolio;" "CREATE SCHEMA watchlist;" > "$output"' \
    'fi' \
    'printf "restore\n" >> "$EVENT_LOG"' \
    > "$mock_bin/pg_restore"

  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'printf "launchctl:%s\n" "$*" >> "$EVENT_LOG"' \
    'if [[ "$1" == "print" ]]; then' \
    '  case "$2" in' \
    '    *.platform-api|*.portfolio-web) exit 0 ;;' \
    '    *) exit 1 ;;' \
    '  esac' \
    'fi' \
    'exit 0' \
    > "$mock_bin/launchctl"

  chmod +x \
    "$project_root/infra/scripts/migrate_all.sh" \
    "$mock_bin/uname" \
    "$mock_bin/node" \
    "$mock_bin/npm" \
    "$mock_bin/createdb" \
    "$mock_bin/sleep" \
    "$mock_bin/curl" \
    "$mock_bin/psql" \
    "$mock_bin/pg_dump" \
    "$mock_bin/pg_restore" \
    "$mock_bin/launchctl"
}

run_installer() {
  local case_root="$1"
  local project_root="$case_root/project"
  local mock_bin="$case_root/bin"
  local database_url="${2:-postgresql+psycopg://portfolio_ops@127.0.0.1:5432/portfolio_ops}"

  PATH="$mock_bin:$PATH" \
  PROJECT_ROOT="$project_root" \
  LABEL_PREFIX=test.portfolio-ops \
  LAUNCH_AGENTS_DIR="$case_root/LaunchAgents" \
  LOG_DIR="$case_root/logs" \
  PORTFOLIO_OPS_LOCAL_ENV_ROOT="$case_root/env" \
  PORTFOLIO_OPS_INSTALL_BACKUP_DIR="$case_root/backups" \
  PORTFOLIO_OPS_LOCAL_DATABASE_URL="$database_url" \
  PORTFOLIO_OPS_INSTALL_HEALTH_ATTEMPTS=1 \
  BUILD_FRONTENDS=false \
  PYTHON_BIN="$REAL_PYTHON" \
  NODE_BIN="$mock_bin/node" \
  NPM_BIN="$mock_bin/npm" \
  TMPDIR="$case_root/tmp" \
    "$INSTALLER"
}

assert_old_plists_restored() {
  local case_root="$1"
  local service
  for service in \
    platform-api watchlist-api portfolio-api \
    platform-web watchlist-web portfolio-web market-data-refresh; do
    [[ "$(cat "$case_root/LaunchAgents/test.portfolio-ops.$service.plist")" == "old-$service" ]]
  done
}

PASSWORD_URL_CASE="$TEST_ROOT/password-url"
prepare_case "$PASSWORD_URL_CASE"
export EVENT_LOG="$PASSWORD_URL_CASE/events"
set +e
run_installer \
  "$PASSWORD_URL_CASE" \
  'postgresql+psycopg://portfolio_ops:sensitive-password@127.0.0.1:5432/portfolio_ops' \
  > "$PASSWORD_URL_CASE/output" 2>&1
password_url_status=$?
set -e
[[ $password_url_status -eq 64 ]]
grep -q 'must not contain passwords' "$PASSWORD_URL_CASE/output"
if grep -q 'sensitive-password' "$PASSWORD_URL_CASE/output"; then
  echo "Launchd password rejection exposed database credentials." >&2
  exit 1
fi
[[ ! -e "$EVENT_LOG" ]]

EARLY_STOP_CASE="$TEST_ROOT/early-stop-failure"
prepare_case "$EARLY_STOP_CASE"
rm -f "$EARLY_STOP_CASE/LaunchAgents/test.portfolio-ops.portfolio-web.plist"
export EVENT_LOG="$EARLY_STOP_CASE/events"
set +e
MIGRATION_FAIL=false FAIL_ROLLBACK=false run_installer "$EARLY_STOP_CASE" \
  > "$EARLY_STOP_CASE/output" 2>&1
early_stop_status=$?
set -e
[[ $early_stop_status -ne 0 ]]
grep -q 'Cannot safely stop test.portfolio-ops.portfolio-web' "$EARLY_STOP_CASE/output"
if grep -q 'launchctl:bootout\|launchctl:bootstrap\|^backup$\|^migrate$' "$EVENT_LOG"; then
  echo "Installer mutated service or database state after stop preflight failed." >&2
  exit 1
fi
[[ "$(cat "$EARLY_STOP_CASE/LaunchAgents/test.portfolio-ops.platform-api.plist")" == "old-platform-api" ]]
[[ ! -e "$EARLY_STOP_CASE/LaunchAgents/test.portfolio-ops.portfolio-web.plist" ]]

MIGRATION_CASE="$TEST_ROOT/migration-failure"
prepare_case "$MIGRATION_CASE"
export EVENT_LOG="$MIGRATION_CASE/events"
set +e
MIGRATION_FAIL=true FAIL_ROLLBACK=false run_installer \
  "$MIGRATION_CASE" \
  'postgresql://portfolio_ops@127.0.0.1:5432/portfolio_ops' \
  > "$MIGRATION_CASE/output" 2>&1
migration_status=$?
set -e
[[ $migration_status -eq 9 ]]
grep -q '^backup$' "$EVENT_LOG"
grep -q '^migrate$' "$EVENT_LOG"
if grep -q '^audit$' "$EVENT_LOG"; then
  echo "Installer ran the data audit after a failed migration." >&2
  exit 1
fi
grep -q '^platform-migration-env:match|platform$' "$EVENT_LOG"
grep -q '^pg_dump:.*--schema=platform' "$EVENT_LOG"
grep -q '^restore$' "$EVENT_LOG"
restore_line="$(grep -n '^restore$' "$EVENT_LOG" | cut -d: -f1)"
restart_line="$(grep -n 'launchctl:bootstrap .*test.portfolio-ops.platform-api.plist' "$EVENT_LOG" | tail -n 1 | cut -d: -f1)"
[[ "$restore_line" -lt "$restart_line" ]]
assert_old_plists_restored "$MIGRATION_CASE"
test -n "$(find "$MIGRATION_CASE/backups" -name '*.pgdump' -type f -print -quit)"
test -n "$(find "$MIGRATION_CASE/backups" -name '*.pgdump.sha256' -type f -print -quit)"
test -n "$(find "$MIGRATION_CASE/backups" -name '*.schemas.sha256' -type f -print -quit)"
if grep -q 'sensitive-password' "$MIGRATION_CASE/output"; then
  echo "Launchd migration failure output exposed database credentials." >&2
  exit 1
fi
if grep -q '^credential-in-argv$' "$EVENT_LOG"; then
  echo "Launchd migration put database credentials in a child-process argument." >&2
  exit 1
fi

SNAPSHOT_REFRESH_CASE="$TEST_ROOT/snapshot-refresh-failure"
prepare_case "$SNAPSHOT_REFRESH_CASE"
export EVENT_LOG="$SNAPSHOT_REFRESH_CASE/events"
set +e
SNAPSHOT_REFRESH_FAIL=true FAIL_ROLLBACK=false run_installer "$SNAPSHOT_REFRESH_CASE" \
  > "$SNAPSHOT_REFRESH_CASE/output" 2>&1
snapshot_refresh_status=$?
set -e
[[ $snapshot_refresh_status -ne 0 ]]
grep -q '^backup$' "$EVENT_LOG"
grep -q '^migrate$' "$EVENT_LOG"
grep -q '^market-refresh:--channel fmp --updated-by launchd-install --no-downstream-refresh --fail-on-item-failure$' "$EVENT_LOG"
grep -q '^snapshot-refresh$' "$EVENT_LOG"
if grep -q '^audit$' "$EVENT_LOG"; then
  echo "Installer ran the audit after a failed snapshot refresh." >&2
  exit 1
fi
grep -q '^restore$' "$EVENT_LOG"
assert_old_plists_restored "$SNAPSHOT_REFRESH_CASE"

ROLLBACK_CASE="$TEST_ROOT/rollback-failure"
prepare_case "$ROLLBACK_CASE"
export EVENT_LOG="$ROLLBACK_CASE/events"
set +e
MIGRATION_FAIL=false FAIL_ROLLBACK=true run_installer "$ROLLBACK_CASE" \
  > "$ROLLBACK_CASE/output" 2>&1
rollback_status=$?
set -e
[[ $rollback_status -eq 70 ]]
grep -q '^migrate$' "$EVENT_LOG"
grep -q '^market-refresh:--channel fmp --updated-by launchd-install --no-downstream-refresh --fail-on-item-failure$' "$EVENT_LOG"
grep -q '^audit$' "$EVENT_LOG"
grep -q '^health$' "$EVENT_LOG"
grep -q '^restore-failed$' "$EVENT_LOG"
if awk 'seen && /launchctl:bootstrap/ { found=1 } /^restore-failed$/ { seen=1 } END { exit found ? 0 : 1 }' "$EVENT_LOG"; then
  echo "Previously loaded services restarted after database rollback failed." >&2
  exit 1
fi
grep -q 'Managed services remain stopped because rollback did not complete' "$ROLLBACK_CASE/output"
assert_old_plists_restored "$ROLLBACK_CASE"
if grep -q 'sensitive-password' "$ROLLBACK_CASE/output"; then
  echo "Launchd rollback failure output exposed database credentials." >&2
  exit 1
fi
if grep -q '^credential-in-argv$' "$EVENT_LOG"; then
  echo "Launchd rollback put database credentials in a child-process argument." >&2
  exit 1
fi

echo "launchd install database rollback ordering and fail-closed service test passed."
