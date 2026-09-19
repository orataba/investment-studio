#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/investment-studio-systemd-transaction-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

PROJECT_ROOT="$TEST_ROOT/project"
ENV_ROOT="$TEST_ROOT/secure-env"
MOCK_BIN="$TEST_ROOT/bin"
mkdir -p \
  "$PROJECT_ROOT/shared-data" \
  "$PROJECT_ROOT/shared-data/scripts" \
  "$PROJECT_ROOT/apps/watchlist/backend" \
  "$PROJECT_ROOT/apps/watchlist/backend/scripts" \
  "$PROJECT_ROOT/apps/portfolio/backend" \
  "$PROJECT_ROOT/apps/briefing/backend" \
  "$PROJECT_ROOT/apps/portfolio/backend/scripts" \
  "$PROJECT_ROOT/home/frontend/dist" \
  "$PROJECT_ROOT/apps/watchlist/frontend/dist" \
  "$PROJECT_ROOT/apps/portfolio/frontend/dist" \
  "$PROJECT_ROOT/apps/briefing/frontend/dist" \
  "$PROJECT_ROOT/deploy" \
  "$PROJECT_ROOT/infra/launchd" \
  "$PROJECT_ROOT/infra/postgres" \
  "$PROJECT_ROOT/infra/scripts" \
  "$PROJECT_ROOT/shared-data/instruments/python" \
  "$ENV_ROOT" \
  "$MOCK_BIN"
touch \
  "$PROJECT_ROOT/home/frontend/dist/index.html" \
  "$PROJECT_ROOT/apps/watchlist/frontend/dist/index.html" \
  "$PROJECT_ROOT/apps/portfolio/frontend/dist/index.html" \
  "$PROJECT_ROOT/apps/briefing/frontend/dist/index.html" \
  "$PROJECT_ROOT/deploy/serve_spa_proxy.mjs"
cp "$REPOSITORY_ROOT/infra/launchd/load_runtime_env.sh" \
  "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "migrate\n" >> "$EVENT_LOG"' \
  '[[ "${MIGRATION_FAIL:-false}" != "true" ]]' \
  > "$PROJECT_ROOT/infra/scripts/migrate_all.sh"
chmod +x "$PROJECT_ROOT/infra/scripts/migrate_all.sh"

printf '%s\n' \
  '#!/usr/bin/env python3' \
  'from os import environ' \
  'from pathlib import Path' \
  'with Path(environ["EVENT_LOG"]).open("a", encoding="utf-8") as event_log:' \
  '    event_log.write("audit\n")' \
  'raise SystemExit(1 if environ.get("AUDIT_FAIL") == "true" else 0)' \
  > "$PROJECT_ROOT/infra/scripts/audit_live_data.py"
chmod +x "$PROJECT_ROOT/infra/scripts/audit_live_data.py"

printf '%s\n' \
  '#!/usr/bin/env python3' \
  'from os import environ' \
  'from pathlib import Path' \
  'with Path(environ["EVENT_LOG"]).open("a", encoding="utf-8") as event_log:' \
  '    event_log.write("catalog-refresh\n")' \
  'raise SystemExit(1 if environ.get("CATALOG_REFRESH_FAIL") == "true" else 0)' \
  > "$PROJECT_ROOT/shared-data/scripts/refresh_release_catalogs.py"

printf '%s\n' \
  '#!/usr/bin/env python3' \
  'from os import environ' \
  'from pathlib import Path' \
  'with Path(environ["EVENT_LOG"]).open("a", encoding="utf-8") as event_log:' \
  '    event_log.write("watchlist-refresh\n")' \
  'import sys' \
  'assert "--recover-interrupted" in sys.argv' \
  'raise SystemExit(1 if environ.get("WATCHLIST_REFRESH_FAIL") == "true" else 0)' \
  > "$PROJECT_ROOT/apps/watchlist/backend/scripts/refresh_release_watchlists.py"

printf '%s\n' \
  '#!/usr/bin/env python3' \
  'from os import environ' \
  'from pathlib import Path' \
  'with Path(environ["EVENT_LOG"]).open("a", encoding="utf-8") as event_log:' \
  '    event_log.write("snapshot-refresh\n")' \
  'raise SystemExit(1 if environ.get("SNAPSHOT_REFRESH_FAIL") == "true" else 0)' \
  > "$PROJECT_ROOT/apps/portfolio/backend/scripts/refresh_release_snapshots.py"
chmod +x "$PROJECT_ROOT/apps/portfolio/backend/scripts/refresh_release_snapshots.py"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'investment_studio_create_project_schema_backup() {' \
  '  local _database_url="$1" backup_root="$2" label="$3"' \
  '  mkdir -p "$backup_root"' \
  '  INVESTMENT_STUDIO_PROJECT_SCHEMA_BACKUP_PATH="$backup_root/$label-test.pgdump"' \
  '  INVESTMENT_STUDIO_PROJECT_SCHEMA_MANIFEST_PATH="$backup_root/$label-test.schemas"' \
  '  : > "$INVESTMENT_STUDIO_PROJECT_SCHEMA_BACKUP_PATH"' \
  '  printf "%s\n" instrument_registry platform portfolio watchlist > "$INVESTMENT_STUDIO_PROJECT_SCHEMA_MANIFEST_PATH"' \
  '  printf "backup\n" >> "$EVENT_LOG"' \
  '}' \
  'investment_studio_restore_project_schema_backup() {' \
  '  printf "database-restore\n" >> "$EVENT_LOG"' \
  '  [[ "${ROLLBACK_FAIL:-false}" != "true" ]]' \
  '}' \
  > "$PROJECT_ROOT/infra/postgres/project_schema_backup.sh"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  '[[ "$1" == "--user" ]] && shift' \
  'action="$1"' \
  'shift' \
  'printf "systemctl:%s %s\n" "$action" "$*" >> "$EVENT_LOG"' \
  'remove_active() {' \
  '  local unit="$1" temporary="$SYSTEMCTL_ACTIVE_FILE.tmp"' \
  '  awk -v unit="$unit" '\''$0 != unit { print }'\'' "$SYSTEMCTL_ACTIVE_FILE" > "$temporary"' \
  '  mv "$temporary" "$SYSTEMCTL_ACTIVE_FILE"' \
  '}' \
  'add_active() {' \
  '  local unit="$1"' \
  '  grep -Fxq "$unit" "$SYSTEMCTL_ACTIVE_FILE" || printf "%s\n" "$unit" >> "$SYSTEMCTL_ACTIVE_FILE"' \
  '}' \
  'remove_enabled() {' \
  '  local unit="$1" temporary="$SYSTEMCTL_ENABLED_FILE.tmp"' \
  '  awk -v unit="$unit" '\''$0 != unit { print }'\'' "$SYSTEMCTL_ENABLED_FILE" > "$temporary"' \
  '  mv "$temporary" "$SYSTEMCTL_ENABLED_FILE"' \
  '}' \
  'add_enabled() {' \
  '  local unit="$1"' \
  '  grep -Fxq "$unit" "$SYSTEMCTL_ENABLED_FILE" || printf "%s\n" "$unit" >> "$SYSTEMCTL_ENABLED_FILE"' \
  '}' \
  'case "$action" in' \
  '  is-active)' \
  '    [[ "${1:-}" == "--quiet" ]] && shift' \
  '    grep -Fxq "$1" "$SYSTEMCTL_ACTIVE_FILE"' \
  '    ;;' \
  '  is-enabled)' \
  '    if grep -Fxq "$1" "$SYSTEMCTL_ENABLED_FILE"; then echo enabled; else echo disabled; exit 1; fi' \
  '    ;;' \
  '  stop)' \
  '    for unit in "$@"; do remove_active "$unit"; done' \
  '    ;;' \
  '  start)' \
  '    for unit in "$@"; do add_active "$unit"; done' \
  '    ;;' \
  '  restart)' \
  '    for unit in "$@"; do' \
  '      if [[ "$unit" == "${START_FAIL_UNIT:-}" ]]; then exit 1; fi' \
  '      add_active "$unit"' \
  '    done' \
  '    ;;' \
  '  enable)' \
  '    [[ "${1:-}" == "--runtime" ]] && shift' \
  '    for unit in "$@"; do add_enabled "$unit"; done' \
  '    ;;' \
  '  disable)' \
  '    for unit in "$@"; do remove_enabled "$unit"; done' \
  '    ;;' \
  '  mask)' \
  '    [[ "${1:-}" == "--runtime" ]] && shift' \
  '    for unit in "$@"; do remove_enabled "$unit"; done' \
  '    ;;' \
  '  daemon-reload|status) ;;' \
  '  *) echo "Unexpected systemctl action: $action" >&2; exit 1 ;;' \
  'esac' \
  > "$MOCK_BIN/systemctl"
chmod +x "$MOCK_BIN/systemctl"

printf '%s\n' '#!/usr/bin/env bash' \
  'printf "health:%s\n" "${@: -1}" >> "$EVENT_LOG"' \
  '[[ "${HEALTH_FAIL:-false}" != true ]]' > "$MOCK_BIN/curl"
chmod +x "$MOCK_BIN/curl"

chmod 700 "$ENV_ROOT"
CANONICAL_URL='postgresql+psycopg://investment_studio@127.0.0.1:5432/investment_studio'
printf '%s\n' \
  "INVESTMENT_STUDIO_DATA_DATABASE_URL=$CANONICAL_URL" \
  "INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL=$CANONICAL_URL" \
  > "$ENV_ROOT/data.env"
printf '%s\n' "INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL=$CANONICAL_URL" \
  > "$ENV_ROOT/watchlist.env"
printf '%s\n' "INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL=$CANONICAL_URL" \
  > "$ENV_ROOT/portfolio.env"
printf '%s\n' "INVESTMENT_STUDIO_BRIEFING_DATABASE_URL=$CANONICAL_URL" > "$ENV_ROOT/briefing.env"
printf '%s\n' "INVESTMENT_STUDIO_MARKET_DATABASE_URL=$CANONICAL_URL" > "$ENV_ROOT/market.env"
chmod 600 "$ENV_ROOT/data.env" "$ENV_ROOT/watchlist.env" "$ENV_ROOT/portfolio.env" "$ENV_ROOT/briefing.env" "$ENV_ROOT/market.env"
  printf '%s\n' "INVESTMENT_STUDIO_HOME_DATABASE_URL=$CANONICAL_URL" > "$ENV_ROOT/home.env"
  chmod 600 "$ENV_ROOT/home.env"

MANAGED_UNITS=(
  investment-studio-home-api.service
  investment-studio-watchlist-api.service
  investment-studio-portfolio-api.service
  investment-studio-briefing-api.service
  investment-studio-home-web.service
  investment-studio-watchlist-web.service
  investment-studio-portfolio-web.service
  investment-studio-briefing-web.service
)
ORIGINAL_ACTIVE_UNITS=(
  investment-studio-home-api.service
  investment-studio-market-data-refresh.timer
  investment-studio-us-reference-data-refresh.timer
  investment-studio-us-reference-data-refresh.service
  investment-studio-market-sync.timer
  investment-studio-market-sync.service
  investment-studio-market-daily.timer
  investment-studio-briefing-daily.timer
)
RETIRED_UNITS=(investment-studio-us-reference-data-refresh.timer investment-studio-us-reference-data-refresh.service)
ORIGINAL_ENABLED_UNITS=(
  investment-studio-us-reference-data-refresh.timer
  investment-studio-home-api.service
  investment-studio-watchlist-api.service
)

prepare_case() {
  local case_root="$1" unit
  rm -rf "$case_root"
  mkdir -p "$case_root/config/systemd/user" "$case_root/tmp" "$case_root/backups"
  : > "$case_root/events"
  printf '%s\n' "${ORIGINAL_ACTIVE_UNITS[@]}" > "$case_root/active"
  printf '%s\n' "${ORIGINAL_ENABLED_UNITS[@]}" > "$case_root/enabled"
  for unit in "${MANAGED_UNITS[@]}" "${RETIRED_UNITS[@]}"; do
    if [[ "$unit" == "investment-studio-watchlist-web.service" ]]; then
      continue
    fi
    printf 'previous-unit:%s\n' "$unit" > "$case_root/config/systemd/user/$unit"
  done
}

run_case() {
  local case_root="$1"
  EVENT_LOG="$case_root/events" \
  SYSTEMCTL_ACTIVE_FILE="$case_root/active" \
  SYSTEMCTL_ENABLED_FILE="$case_root/enabled" \
  HOME="$case_root/home" \
  XDG_CONFIG_HOME="$case_root/config" \
  TMPDIR="$case_root/tmp" \
  PATH="$MOCK_BIN:$PATH" \
  PROJECT_ROOT="$PROJECT_ROOT" \
  PYTHON_BIN="$(command -v python3)" \
  NODE_BIN="/usr/bin/true" \
  ENV_ROOT="$ENV_ROOT" \
  RUN_MIGRATIONS=true \
  START_SERVICES=true \
  HEALTH_ATTEMPTS=1 \
  INVESTMENT_STUDIO_SYSTEMD_BACKUP_ROOT="$case_root/backups" \
    "$REPOSITORY_ROOT/infra/systemd/install_app_services.sh"
}

assert_original_state_restored() {
  local case_root="$1" unit
  diff -u \
    <(printf '%s\n' "${ORIGINAL_ACTIVE_UNITS[@]}" | sort) \
    <(sort "$case_root/active")
  diff -u \
    <(printf '%s\n' "${ORIGINAL_ENABLED_UNITS[@]}" | sort) \
    <(sort "$case_root/enabled")
  for unit in "${MANAGED_UNITS[@]}" "${RETIRED_UNITS[@]}"; do
    if [[ "$unit" == "investment-studio-watchlist-web.service" ]]; then
      test ! -e "$case_root/config/systemd/user/$unit"
    else
      grep -Fxq "previous-unit:$unit" "$case_root/config/systemd/user/$unit"
    fi
  done
}

SUCCESS_CASE="$TEST_ROOT/success"
prepare_case "$SUCCESS_CASE"
run_case "$SUCCESS_CASE" > "$SUCCESS_CASE/output" 2>&1
diff -u \
  <(printf '%s\n' "${MANAGED_UNITS[@]}" investment-studio-market-data-refresh.timer investment-studio-market-sync.timer investment-studio-market-sync.service investment-studio-market-daily.timer investment-studio-briefing-daily.timer | sort) \
  <(sort "$SUCCESS_CASE/active")
diff -u \
  <(printf '%s\n' "${MANAGED_UNITS[@]}" | sort) \
  <(sort "$SUCCESS_CASE/enabled")
for unit in "${RETIRED_UNITS[@]}"; do
  test ! -e "$SUCCESS_CASE/config/systemd/user/$unit"
done
for unit in "${MANAGED_UNITS[@]}"; do
  test -s "$SUCCESS_CASE/config/systemd/user/$unit"
  if grep -Fq 'previous-unit:' "$SUCCESS_CASE/config/systemd/user/$unit"; then
    echo "Successful systemd install retained an old unit: $unit" >&2
    exit 1
  fi
done
test -f "$SUCCESS_CASE/backups/investment-studio-pre-systemd-install-test.pgdump"
grep -q 'Safety backup retained at:' "$SUCCESS_CASE/output"
backup_line="$(grep -n '^backup$' "$SUCCESS_CASE/events" | head -n 1 | cut -d: -f1)"
migration_line="$(grep -n '^migrate$' "$SUCCESS_CASE/events" | head -n 1 | cut -d: -f1)"
catalog_refresh_line="$(grep -n '^catalog-refresh$' "$SUCCESS_CASE/events" | head -n 1 | cut -d: -f1)"
watchlist_refresh_line="$(grep -n '^watchlist-refresh$' "$SUCCESS_CASE/events" | head -n 1 | cut -d: -f1)"
snapshot_refresh_line="$(grep -n '^snapshot-refresh$' "$SUCCESS_CASE/events" | head -n 1 | cut -d: -f1)"
audit_line="$(grep -n '^audit$' "$SUCCESS_CASE/events" | head -n 1 | cut -d: -f1)"
restart_line="$(grep -n 'systemctl:restart' "$SUCCESS_CASE/events" | head -n 1 | cut -d: -f1)"
if [[ -z "$backup_line" || -z "$migration_line" || -z "$catalog_refresh_line" || -z "$watchlist_refresh_line" || -z "$snapshot_refresh_line" || -z "$audit_line" || -z "$restart_line" \
  || "$backup_line" -ge "$migration_line" \
  || "$migration_line" -ge "$catalog_refresh_line" \
  || "$catalog_refresh_line" -ge "$watchlist_refresh_line" \
  || "$watchlist_refresh_line" -ge "$snapshot_refresh_line" \
  || "$snapshot_refresh_line" -ge "$audit_line" \
  || "$audit_line" -ge "$restart_line" ]]; then
  echo "Systemd install ordering was not backup, migration, catalog refresh, Watchlist reconciliation, snapshot refresh, audit, then restart." >&2
  exit 1
fi

MIGRATION_CASE="$TEST_ROOT/migration-failure"
prepare_case "$MIGRATION_CASE"
set +e
MIGRATION_FAIL=true run_case "$MIGRATION_CASE" > "$MIGRATION_CASE/output" 2>&1
migration_status=$?
set -e
if [[ $migration_status -eq 0 ]]; then
  echo "The systemd installer accepted an injected migration failure." >&2
  exit 1
fi
assert_original_state_restored "$MIGRATION_CASE"
grep -q '^backup$' "$MIGRATION_CASE/events"
grep -q '^migrate$' "$MIGRATION_CASE/events"
grep -q '^database-restore$' "$MIGRATION_CASE/events"

CATALOG_REFRESH_CASE="$TEST_ROOT/catalog-refresh-failure"
prepare_case "$CATALOG_REFRESH_CASE"
set +e
CATALOG_REFRESH_FAIL=true run_case "$CATALOG_REFRESH_CASE" > "$CATALOG_REFRESH_CASE/output" 2>&1
catalog_refresh_status=$?
set -e
if [[ $catalog_refresh_status -eq 0 ]]; then
  echo "The systemd installer accepted an injected catalog refresh failure." >&2
  exit 1
fi
assert_original_state_restored "$CATALOG_REFRESH_CASE"
grep -q '^backup$' "$CATALOG_REFRESH_CASE/events"
grep -q '^migrate$' "$CATALOG_REFRESH_CASE/events"
grep -q '^catalog-refresh$' "$CATALOG_REFRESH_CASE/events"
if grep -q '^watchlist-refresh$\|^snapshot-refresh$\|^audit$' "$CATALOG_REFRESH_CASE/events"; then
  echo "The systemd installer continued after a failed catalog refresh." >&2
  exit 1
fi
grep -q '^database-restore$' "$CATALOG_REFRESH_CASE/events"

WATCHLIST_REFRESH_CASE="$TEST_ROOT/watchlist-refresh-failure"
prepare_case "$WATCHLIST_REFRESH_CASE"
if WATCHLIST_REFRESH_FAIL=true run_case "$WATCHLIST_REFRESH_CASE" > "$WATCHLIST_REFRESH_CASE/output" 2>&1; then
  echo "The systemd installer accepted an injected Watchlist reconciliation failure." >&2
  exit 1
fi
assert_original_state_restored "$WATCHLIST_REFRESH_CASE"
grep -q '^catalog-refresh$' "$WATCHLIST_REFRESH_CASE/events"
grep -q '^watchlist-refresh$' "$WATCHLIST_REFRESH_CASE/events"
if grep -q '^snapshot-refresh$\|^audit$' "$WATCHLIST_REFRESH_CASE/events"; then
  echo "The systemd installer continued after a failed Watchlist reconciliation." >&2
  exit 1
fi
grep -q '^database-restore$' "$WATCHLIST_REFRESH_CASE/events"

SNAPSHOT_REFRESH_CASE="$TEST_ROOT/snapshot-refresh-failure"
prepare_case "$SNAPSHOT_REFRESH_CASE"
set +e
SNAPSHOT_REFRESH_FAIL=true run_case "$SNAPSHOT_REFRESH_CASE" > "$SNAPSHOT_REFRESH_CASE/output" 2>&1
snapshot_refresh_status=$?
set -e
if [[ $snapshot_refresh_status -eq 0 ]]; then
  echo "The systemd installer accepted an injected snapshot refresh failure." >&2
  exit 1
fi
assert_original_state_restored "$SNAPSHOT_REFRESH_CASE"
grep -q '^backup$' "$SNAPSHOT_REFRESH_CASE/events"
grep -q '^migrate$' "$SNAPSHOT_REFRESH_CASE/events"
grep -q '^snapshot-refresh$' "$SNAPSHOT_REFRESH_CASE/events"
if grep -q '^audit$' "$SNAPSHOT_REFRESH_CASE/events"; then
  echo "The systemd installer ran the audit after a failed snapshot refresh." >&2
  exit 1
fi
grep -q '^database-restore$' "$SNAPSHOT_REFRESH_CASE/events"

AUDIT_CASE="$TEST_ROOT/audit-failure"
prepare_case "$AUDIT_CASE"
set +e
AUDIT_FAIL=true run_case "$AUDIT_CASE" > "$AUDIT_CASE/output" 2>&1
audit_status=$?
set -e
if [[ $audit_status -eq 0 ]]; then
  echo "The systemd installer accepted an injected post-migration audit failure." >&2
  exit 1
fi
assert_original_state_restored "$AUDIT_CASE"
grep -q '^backup$' "$AUDIT_CASE/events"
grep -q '^migrate$' "$AUDIT_CASE/events"
grep -q '^audit$' "$AUDIT_CASE/events"
grep -q '^database-restore$' "$AUDIT_CASE/events"

RESTART_CASE="$TEST_ROOT/restart-failure"
prepare_case "$RESTART_CASE"
set +e
START_FAIL_UNIT=investment-studio-portfolio-api.service \
  run_case "$RESTART_CASE" > "$RESTART_CASE/output" 2>&1
restart_status=$?
set -e
if [[ $restart_status -eq 0 ]]; then
  echo "The systemd installer accepted an injected restart failure." >&2
  exit 1
fi
assert_original_state_restored "$RESTART_CASE"
restart_line="$(grep -n 'systemctl:restart' "$RESTART_CASE/events" | head -n 1 | cut -d: -f1)"
database_restore_line="$(grep -n '^database-restore$' "$RESTART_CASE/events" | head -n 1 | cut -d: -f1)"
restored_api_line="$(grep -n 'systemctl:start investment-studio-home-api.service' "$RESTART_CASE/events" | tail -n 1 | cut -d: -f1)"
restored_timer_line="$(grep -n 'systemctl:start investment-studio-market-data-refresh.timer' "$RESTART_CASE/events" | tail -n 1 | cut -d: -f1)"
if [[ -z "$restart_line" || -z "$database_restore_line" || -z "$restored_api_line" || -z "$restored_timer_line" \
  || "$restart_line" -ge "$database_restore_line" \
  || "$database_restore_line" -ge "$restored_api_line" \
  || "$restored_api_line" -ge "$restored_timer_line" ]]; then
  echo "Systemd rollback ordering did not restore DB, API, then refresh timer." >&2
  exit 1
fi

ROLLBACK_CASE="$TEST_ROOT/rollback-failure"
HEALTH_CASE="$TEST_ROOT/health-failure"
prepare_case "$HEALTH_CASE"
if HEALTH_FAIL=true run_case "$HEALTH_CASE" > "$HEALTH_CASE/output" 2>&1; then
  echo "The systemd installer accepted an active service with a failed HTTP health check." >&2
  exit 1
fi
assert_original_state_restored "$HEALTH_CASE"
grep -q '^database-restore$' "$HEALTH_CASE/events"
grep -q 'failed its readiness gate: http://127.0.0.1:8102/api/health' "$HEALTH_CASE/output"

prepare_case "$ROLLBACK_CASE"
set +e
ROLLBACK_FAIL=true \
START_FAIL_UNIT=investment-studio-portfolio-api.service \
  run_case "$ROLLBACK_CASE" > "$ROLLBACK_CASE/output" 2>&1
rollback_status=$?
set -e
if [[ $rollback_status -eq 0 ]]; then
  echo "The systemd installer hid an injected database rollback failure." >&2
  exit 1
fi
test ! -s "$ROLLBACK_CASE/active"
grep -q 'Automatic recovery failed; managed writers remain stopped.' "$ROLLBACK_CASE/output"
grep -q 'Recovery state retained at:' "$ROLLBACK_CASE/output"

echo "systemd app install transaction and fail-closed rollback test passed."
