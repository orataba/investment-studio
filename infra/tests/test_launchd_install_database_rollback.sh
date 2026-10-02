#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INSTALLER="$REPOSITORY_ROOT/infra/launchd/install_local_services.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/investment-studio-launchd-install-test.XXXXXX")"
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
    "$project_root/home/frontend/dist" \
    "$project_root/apps/watchlist/frontend/dist" \
    "$project_root/apps/portfolio/frontend/dist" \
    "$project_root/apps/briefing/frontend/dist" \
    "$project_root/shared-data/scripts" \
    "$project_root/apps/portfolio/backend/scripts" \
    "$project_root/apps/watchlist/backend/scripts" \
    "$mock_bin" \
    "$plist_root" \
    "$env_root" \
    "$case_root/logs" \
    "$case_root/backups" \
    "$case_root/tmp"
  chmod 700 "$env_root" "$case_root/backups" "$case_root/tmp"
  printf '%s\n' test.investment-studio.home-api test.investment-studio.portfolio-web > "$case_root/loaded"
  printf '%s\n' pre-install-record > "$case_root/database"
  : > "$env_root/home.env"
  chmod 600 "$env_root/home.env"
  printf '%s\n' 'INVESTMENT_STUDIO_AUTH_MODE=local' 'INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA=instrument_data' > "$env_root/data.env"
  chmod 600 "$env_root/data.env"
  touch "$env_root/briefing.env" "$env_root/market.env"
  chmod 600 "$env_root/briefing.env" "$env_root/market.env"
  touch \
    "$project_root/home/frontend/dist/index.html" \
    "$project_root/apps/watchlist/frontend/dist/index.html" \
    "$project_root/apps/portfolio/frontend/dist/index.html" \
    "$project_root/apps/briefing/frontend/dist/index.html"
  cp "$REPOSITORY_ROOT/infra/postgres/project_schema_backup.sh" \
    "$project_root/infra/postgres/project_schema_backup.sh"
  cp "$REPOSITORY_ROOT/infra/scripts/install_market_pipeline.py" \
    "$project_root/infra/scripts/install_market_pipeline.py"

  for service in \
    home-api watchlist-api portfolio-api briefing-api \
    home-web watchlist-web portfolio-web briefing-web market-data-refresh cn-market-data-refresh hk-market-data-refresh us-market-data-refresh cn-hk-reference-data-refresh us-reference-data-refresh market-sync; do
    printf 'old-%s\n' "$service" > "$plist_root/test.investment-studio.$service.plist"
    chmod 600 "$plist_root/test.investment-studio.$service.plist"
  done

  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'printf "migrate\n" >> "$EVENT_LOG"' \
    'printf "migrated\n" >> "$DATABASE_STATE_FILE"' \
    'platform_alembic_status=missing' \
    'if [[ -n "${INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL:-}" && "$INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL" == "${INVESTMENT_STUDIO_DATA_DATABASE_URL:-}" ]]; then platform_alembic_status=match; fi' \
    'printf "platform-migration-env:%s|%s\n" "$platform_alembic_status" "${INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA:-}" >> "$EVENT_LOG"' \
    'if [[ "${MIGRATION_FAIL:-false}" == "true" ]]; then exit 9; fi' \
    > "$project_root/infra/scripts/migrate_all.sh"
  printf '%s\n' \
    'from __future__ import annotations' \
    'import os' \
    'from pathlib import Path' \
    'with Path(os.environ["EVENT_LOG"]).open("a", encoding="utf-8") as handle:' \
    '    handle.write("catalog-refresh\n")' \
    'raise SystemExit(1 if os.environ.get("CATALOG_REFRESH_FAIL") == "true" else 0)' \
    > "$project_root/shared-data/scripts/refresh_release_catalogs.py"
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
  printf '%s\n' \
    'from __future__ import annotations' \
    'import os' \
    'from pathlib import Path' \
    'with Path(os.environ["EVENT_LOG"]).open("a", encoding="utf-8") as handle:' \
    '    handle.write("watchlist-refresh\n")' \
    'import sys' \
    'assert "--recover-interrupted" in sys.argv' \
    'raise SystemExit(1 if os.environ.get("WATCHLIST_REFRESH_FAIL") == "true" else 0)' \
    > "$project_root/apps/watchlist/backend/scripts/refresh_release_watchlists.py"

  printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" Darwin' > "$mock_bin/uname"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$mock_bin/node"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$mock_bin/npm"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$mock_bin/createdb"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$mock_bin/sleep"
  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf "health\n" >> "$EVENT_LOG"' \
    '[[ "${HEALTH_FAIL:-true}" != true ]]' \
    > "$mock_bin/curl"

  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'if [[ "$*" == *sensitive-password* ]]; then printf "credential-in-argv\n" >> "$EVENT_LOG"; fi' \
    'case "$*" in' \
    '  *pg_roles*) printf "%s\n" 1 ;;' \
    '  *pg_database*) printf "%s\n" 1 ;;' \
    '  *pg_namespace*) printf "%s\n" instrument_registry platform portfolio watchlist ;;' \
    '  # Real psql consumes the rollback stream; early exit can SIGPIPE pg_restore.' \
    '  *--single-transaction*) cat >/dev/null ;;' \
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
    'cp "$DATABASE_STATE_FILE" "$output"' \
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
    'archive="${@: -1}"' \
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
    'cp "$archive" "$DATABASE_STATE_FILE"' \
    > "$mock_bin/pg_restore"

  printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'printf "launchctl:%s\n" "$*" >> "$EVENT_LOG"' \
    'case "$1" in' \
    '  print) grep -Fxq "${2##*/}" "$LAUNCHCTL_LOADED_FILE"; exit $? ;;' \
    '  bootout)' \
    '    unit="${2##*/}"' \
    '    for phase in "$TMPDIR"/investment-studio-launchd-install.*/phase; do' \
    '      if [[ "$unit" == "${STOP_FAIL_UNIT:-}" && -f "$phase" ]]; then exit 1; fi' \
    '    done' \
    '    awk -v unit="$unit" '\''$0 != unit { print }'\'' "$LAUNCHCTL_LOADED_FILE" > "$LAUNCHCTL_LOADED_FILE.new"' \
    '    mv "$LAUNCHCTL_LOADED_FILE.new" "$LAUNCHCTL_LOADED_FILE"' \
    '    ;;' \
    '  bootstrap)' \
    '    unit="${3##*/}"; unit="${unit%.plist}"' \
    '    [[ "$unit" != "${BOOTSTRAP_FAIL_UNIT:-}" ]] || exit 1' \
    '    printf "%s\n" "$unit" >> "$LAUNCHCTL_LOADED_FILE"' \
    '    for phase in "$TMPDIR"/investment-studio-launchd-install.*/phase; do' \
    '      if [[ -f "$phase" ]] && grep -Fxq writers-may-have-resumed "$phase"; then' \
    '        printf "startup-write:%s\n" "$unit" >> "$DATABASE_STATE_FILE"' \
    '      fi' \
    '    done' \
    '    ;;' \
    'esac' \
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
  local database_url="${2:-postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio}"

  PATH="$mock_bin:$PATH" \
  PROJECT_ROOT="$project_root" \
  LABEL_PREFIX=test.investment-studio \
  LAUNCH_AGENTS_DIR="$case_root/LaunchAgents" \
  LOG_DIR="$case_root/logs" \
  INVESTMENT_STUDIO_LOCAL_ENV_ROOT="$case_root/env" \
  INVESTMENT_STUDIO_INSTALL_BACKUP_DIR="$case_root/backups" \
  INVESTMENT_STUDIO_LOCAL_DATABASE_URL="$database_url" \
  INVESTMENT_STUDIO_INSTALL_HEALTH_ATTEMPTS=1 \
  BUILD_FRONTENDS=false \
  PYTHON_BIN="$REAL_PYTHON" \
  NODE_BIN="$mock_bin/node" \
  NPM_BIN="$mock_bin/npm" \
  TMPDIR="$case_root/tmp" \
  DATABASE_STATE_FILE="$case_root/database" \
  LAUNCHCTL_LOADED_FILE="$case_root/loaded" \
    "$INSTALLER"
}

assert_old_plists_restored() {
  local case_root="$1"
  local service
  for service in \
    home-api watchlist-api portfolio-api briefing-api \
    home-web watchlist-web portfolio-web briefing-web market-data-refresh cn-market-data-refresh hk-market-data-refresh us-market-data-refresh cn-hk-reference-data-refresh us-reference-data-refresh market-sync; do
    [[ "$(cat "$case_root/LaunchAgents/test.investment-studio.$service.plist")" == "old-$service" ]]
  done
}

assert_forward_repair_preserved() {
  local case_root="$1" recovery
  test ! -s "$case_root/loaded"
  if grep -Eq '^restore$|^restore-failed$' "$case_root/events"; then
    echo "Launchd installer attempted to discard startup writes." >&2
    exit 1
  fi
  grep -q '^migrated$' "$case_root/database"
  grep -q '^startup-write:' "$case_root/database"
  if grep -q '^old-home-api$' "$case_root/LaunchAgents/test.investment-studio.home-api.plist"; then
    echo "Forward repair restored old LaunchAgent definitions." >&2
    exit 1
  fi
  recovery="$(sed -n 's/^Installer recovery state retained at: //p' "$case_root/output" | tail -n 1)"
  test -d "$recovery"
  grep -Fxq forward-repair-required "$recovery/phase"
  test -s "$recovery/database-backup-path"
  test -s "$recovery/database-manifest-path"
  grep -q 'automatic database and LaunchAgent rollback is disabled' "$case_root/output"
}

PASSWORD_URL_CASE="$TEST_ROOT/password-url"
prepare_case "$PASSWORD_URL_CASE"
export EVENT_LOG="$PASSWORD_URL_CASE/events"
set +e
run_installer \
  "$PASSWORD_URL_CASE" \
  'postgresql+psycopg://investment_studio:sensitive-password@127.0.0.1:5432/investment_studio' \
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

MISSING_WATCHLIST_HELPER_CASE="$TEST_ROOT/missing-watchlist-helper"
prepare_case "$MISSING_WATCHLIST_HELPER_CASE"
rm "$MISSING_WATCHLIST_HELPER_CASE/project/apps/watchlist/backend/scripts/refresh_release_watchlists.py"
export EVENT_LOG="$MISSING_WATCHLIST_HELPER_CASE/events"
if run_installer "$MISSING_WATCHLIST_HELPER_CASE" > "$MISSING_WATCHLIST_HELPER_CASE/output" 2>&1; then
  echo "Installer accepted a missing Watchlist reconciliation helper." >&2
  exit 1
fi
grep -q 'Required installer helper is missing: .*refresh_release_watchlists.py' "$MISSING_WATCHLIST_HELPER_CASE/output"
[[ ! -e "$EVENT_LOG" ]]
assert_old_plists_restored "$MISSING_WATCHLIST_HELPER_CASE"

EARLY_STOP_CASE="$TEST_ROOT/early-stop-failure"
prepare_case "$EARLY_STOP_CASE"
rm -f "$EARLY_STOP_CASE/LaunchAgents/test.investment-studio.portfolio-web.plist"
export EVENT_LOG="$EARLY_STOP_CASE/events"
set +e
MIGRATION_FAIL=false FAIL_ROLLBACK=false run_installer "$EARLY_STOP_CASE" \
  > "$EARLY_STOP_CASE/output" 2>&1
early_stop_status=$?
set -e
[[ $early_stop_status -ne 0 ]]
grep -q 'Cannot safely stop test.investment-studio.portfolio-web' "$EARLY_STOP_CASE/output"
if grep -q 'launchctl:bootout\|launchctl:bootstrap\|^backup$\|^migrate$' "$EVENT_LOG"; then
  echo "Installer mutated service or database state after stop preflight failed." >&2
  exit 1
fi
[[ "$(cat "$EARLY_STOP_CASE/LaunchAgents/test.investment-studio.home-api.plist")" == "old-home-api" ]]
[[ ! -e "$EARLY_STOP_CASE/LaunchAgents/test.investment-studio.portfolio-web.plist" ]]

MIGRATION_CASE="$TEST_ROOT/migration-failure"
prepare_case "$MIGRATION_CASE"
export EVENT_LOG="$MIGRATION_CASE/events"
set +e
MIGRATION_FAIL=true FAIL_ROLLBACK=false run_installer \
  "$MIGRATION_CASE" \
  'postgresql://investment_studio@127.0.0.1:5432/investment_studio' \
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
grep -q '^platform-migration-env:match|data_ingestion$' "$EVENT_LOG"
grep -q '^pg_dump:.*--schema=platform' "$EVENT_LOG"
grep -q '^restore$' "$EVENT_LOG"
restore_line="$(grep -n '^restore$' "$EVENT_LOG" | cut -d: -f1)"
restart_line="$(grep -n 'launchctl:bootstrap .*test.investment-studio.home-api.plist' "$EVENT_LOG" | tail -n 1 | cut -d: -f1)"
[[ "$restore_line" -lt "$restart_line" ]]
assert_old_plists_restored "$MIGRATION_CASE"
grep -Fxq pre-install-record "$MIGRATION_CASE/database"
test "$(wc -l < "$MIGRATION_CASE/database" | tr -d ' ')" -eq 1
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

CATALOG_REFRESH_CASE="$TEST_ROOT/catalog-refresh-failure"
prepare_case "$CATALOG_REFRESH_CASE"
export EVENT_LOG="$CATALOG_REFRESH_CASE/events"
if CATALOG_REFRESH_FAIL=true FAIL_ROLLBACK=false run_installer "$CATALOG_REFRESH_CASE" \
  > "$CATALOG_REFRESH_CASE/output" 2>&1; then
  echo "Installer accepted a failed catalog refresh." >&2
  exit 1
fi
grep -q '^backup$' "$EVENT_LOG"
grep -q '^migrate$' "$EVENT_LOG"
grep -q '^catalog-refresh$' "$EVENT_LOG"
if grep -q '^watchlist-refresh$\|^snapshot-refresh$\|^audit$' "$EVENT_LOG"; then
  echo "Installer continued after a failed catalog refresh." >&2
  exit 1
fi
grep -q '^restore$' "$EVENT_LOG"
restore_line="$(grep -n '^restore$' "$EVENT_LOG" | cut -d: -f1)"
restart_line="$(grep -n 'launchctl:bootstrap .*test.investment-studio.home-api.plist' "$EVENT_LOG" | tail -n 1 | cut -d: -f1)"
[[ "$restore_line" -lt "$restart_line" ]]
assert_old_plists_restored "$CATALOG_REFRESH_CASE"

WATCHLIST_REFRESH_CASE="$TEST_ROOT/watchlist-refresh-failure"
prepare_case "$WATCHLIST_REFRESH_CASE"
export EVENT_LOG="$WATCHLIST_REFRESH_CASE/events"
if WATCHLIST_REFRESH_FAIL=true FAIL_ROLLBACK=false run_installer "$WATCHLIST_REFRESH_CASE" \
  > "$WATCHLIST_REFRESH_CASE/output" 2>&1; then
  echo "Installer accepted a failed Watchlist reconciliation." >&2
  exit 1
fi
grep -q '^backup$' "$EVENT_LOG"
grep -q '^migrate$' "$EVENT_LOG"
grep -q '^catalog-refresh$' "$EVENT_LOG"
grep -q '^watchlist-refresh$' "$EVENT_LOG"
if grep -q '^snapshot-refresh$\|^audit$' "$EVENT_LOG"; then
  echo "Installer continued after a failed Watchlist reconciliation." >&2
  exit 1
fi
grep -q '^restore$' "$EVENT_LOG"
restore_line="$(grep -n '^restore$' "$EVENT_LOG" | cut -d: -f1)"
restart_line="$(grep -n 'launchctl:bootstrap .*test.investment-studio.home-api.plist' "$EVENT_LOG" | tail -n 1 | cut -d: -f1)"
[[ "$restore_line" -lt "$restart_line" ]]
assert_old_plists_restored "$WATCHLIST_REFRESH_CASE"

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
grep -q '^catalog-refresh$' "$EVENT_LOG"
grep -q '^watchlist-refresh$' "$EVENT_LOG"
grep -q '^snapshot-refresh$' "$EVENT_LOG"
if grep -q '^audit$' "$EVENT_LOG"; then
  echo "Installer ran the audit after a failed snapshot refresh." >&2
  exit 1
fi
grep -q '^restore$' "$EVENT_LOG"
assert_old_plists_restored "$SNAPSHOT_REFRESH_CASE"

HEALTH_CASE="$TEST_ROOT/health-failure"
prepare_case "$HEALTH_CASE"
export EVENT_LOG="$HEALTH_CASE/events"
if HEALTH_FAIL=true run_installer "$HEALTH_CASE" > "$HEALTH_CASE/output" 2>&1; then
  echo "Launchd installer accepted an injected readiness failure." >&2
  exit 1
fi
assert_forward_repair_preserved "$HEALTH_CASE"
grep -q '^migrate$' "$EVENT_LOG"
grep -q '^catalog-refresh$' "$EVENT_LOG"
grep -q '^audit$' "$EVENT_LOG"
grep -q '^health$' "$EVENT_LOG"
previous_line=0
for stage in backup migrate catalog-refresh watchlist-refresh snapshot-refresh audit health; do
  stage_line="$(grep -n "^$stage\(:\|$\)" "$EVENT_LOG" | head -n 1 | cut -d: -f1)"
  if [[ -z "$stage_line" || "$stage_line" -le "$previous_line" ]]; then
    echo "Installer did not execute backup, migration, catalog refresh, Watchlist reconciliation, snapshot refresh, audit and health checks in order." >&2
    exit 1
  fi
  previous_line="$stage_line"
done

BOOTSTRAP_CASE="$TEST_ROOT/partial-bootstrap-failure"
prepare_case "$BOOTSTRAP_CASE"
export EVENT_LOG="$BOOTSTRAP_CASE/events"
if BOOTSTRAP_FAIL_UNIT=test.investment-studio.portfolio-api \
  run_installer "$BOOTSTRAP_CASE" > "$BOOTSTRAP_CASE/output" 2>&1; then
  echo "Launchd installer accepted an injected partial bootstrap failure." >&2
  exit 1
fi
assert_forward_repair_preserved "$BOOTSTRAP_CASE"

STOP_CASE="$TEST_ROOT/post-start-stop-failure"
prepare_case "$STOP_CASE"
export EVENT_LOG="$STOP_CASE/events"
if STOP_FAIL_UNIT=test.investment-studio.home-api \
  run_installer "$STOP_CASE" > "$STOP_CASE/output" 2>&1; then
  echo "Launchd installer accepted a failed stop after startup." >&2
  exit 1
fi
grep -Fxq test.investment-studio.home-api "$STOP_CASE/loaded"
grep -q '^startup-write:' "$STOP_CASE/database"
if grep -Eq '^restore$|^restore-failed$' "$STOP_CASE/events"; then
  echo "Launchd installer replayed a backup while a new writer remained loaded." >&2
  exit 1
fi
grep -q 'Some managed writers could not be stopped; stop them manually before repair' "$STOP_CASE/output"
grep -q 'Installer recovery state retained at:' "$STOP_CASE/output"

SUCCESS_CASE="$TEST_ROOT/success"
prepare_case "$SUCCESS_CASE"
export EVENT_LOG="$SUCCESS_CASE/events"
HEALTH_FAIL=false run_installer "$SUCCESS_CASE" > "$SUCCESS_CASE/output" 2>&1
grep -q '^startup-write:' "$SUCCESS_CASE/database"
grep -q 'Investment Studio is running at' "$SUCCESS_CASE/output"
test -z "$(find "$SUCCESS_CASE/tmp" -name 'investment-studio-launchd-install.*' -type d -print -quit)"

ROLLBACK_CASE="$TEST_ROOT/rollback-failure"
prepare_case "$ROLLBACK_CASE"
export EVENT_LOG="$ROLLBACK_CASE/events"
set +e
MIGRATION_FAIL=true FAIL_ROLLBACK=true run_installer "$ROLLBACK_CASE" \
  > "$ROLLBACK_CASE/output" 2>&1
rollback_status=$?
set -e
[[ $rollback_status -eq 70 ]]
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
