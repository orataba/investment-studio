#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS launchd." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
LABEL_PREFIX="${LABEL_PREFIX:-com.orataba.investment-studio}"
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
LOG_DIR="${LOG_DIR:-$HOME/Library/Logs/investment-studio}"
ENV_ROOT="${INVESTMENT_STUDIO_LOCAL_ENV_ROOT:-$HOME/.config/orataba/secrets/investment-studio}"
BACKUP_ROOT="${INVESTMENT_STUDIO_INSTALL_BACKUP_DIR:-$HOME/Library/Application Support/investment-studio/backups}"
BUILD_FRONTENDS="${BUILD_FRONTENDS:-true}"
DATABASE_URL="${INVESTMENT_STUDIO_LOCAL_DATABASE_URL:-}"
REFRESH_HOUR="${INVESTMENT_STUDIO_LOCAL_REFRESH_HOUR:-21}"
REFRESH_MINUTE="${INVESTMENT_STUDIO_LOCAL_REFRESH_MINUTE:-0}"
REFRESH_RETRY_HOUR="${INVESTMENT_STUDIO_LOCAL_REFRESH_RETRY_HOUR:-23}"
REFRESH_RETRY_MINUTE="${INVESTMENT_STUDIO_LOCAL_REFRESH_RETRY_MINUTE:-0}"
HEALTH_ATTEMPTS="${INVESTMENT_STUDIO_INSTALL_HEALTH_ATTEMPTS:-30}"

PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
NPM_BIN="${NPM_BIN:-$(command -v npm || true)}"
MIGRATION_RUNNER="$PROJECT_ROOT/infra/scripts/migrate_all.sh"
AUDIT_RUNNER="$PROJECT_ROOT/infra/scripts/audit_live_data.py"
SNAPSHOT_REFRESH_RUNNER="$PROJECT_ROOT/apps/portfolio/backend/scripts/refresh_release_snapshots.py"
MARKET_DATA_REFRESH_RUNNER="$PROJECT_ROOT/shared-data/scripts/refresh_market_data_scheduled.py"
SERVICE_CONTROL="$SCRIPT_DIR/control_local_services.sh"
LOG_COMPACTOR="$SCRIPT_DIR/compact_local_logs.sh"
BACKUP_HELPER="$PROJECT_ROOT/infra/postgres/project_schema_backup.sh"

services=(
  home-api
  watchlist-api
  portfolio-api
  briefing-api
  home-web
  watchlist-web
  portfolio-web
  briefing-web
  market-data-refresh
  cn-market-data-refresh
  hk-market-data-refresh
  us-market-data-refresh
  cn-hk-reference-data-refresh
  us-reference-data-refresh
  market-sync
)

for executable in "$PYTHON_BIN" "$NODE_BIN"; do
  if [[ -z "$executable" || ! -x "$executable" ]]; then
    echo "Required executable is missing: ${executable:-<empty>}" >&2
    exit 1
  fi
done
if [[ "$BUILD_FRONTENDS" == "true" && ( -z "$NPM_BIN" || ! -x "$NPM_BIN" ) ]]; then
  echo "Required executable is missing: ${NPM_BIN:-<empty>}" >&2
  exit 1
fi

for required_executable in "$MIGRATION_RUNNER" "$SERVICE_CONTROL" "$LOG_COMPACTOR"; do
  if [[ ! -x "$required_executable" ]]; then
    echo "Required installer helper is missing or not executable: $required_executable" >&2
    exit 1
  fi
done
for required_file in \
  "$AUDIT_RUNNER" \
  "$MARKET_DATA_REFRESH_RUNNER" \
  "$SNAPSHOT_REFRESH_RUNNER" \
  "$SCRIPT_DIR/generate_local_service_plists.py" \
  "$PROJECT_ROOT/infra/scripts/install_market_pipeline.py" \
  "$SCRIPT_DIR/load_runtime_env.sh" \
  "$BACKUP_HELPER"; do
  if [[ ! -f "$required_file" ]]; then
    echo "Required installer helper is missing: $required_file" >&2
    exit 1
  fi
done
if [[ ! -x "$SCRIPT_DIR/run_market_data_refresh.sh" ]]; then
  echo "Missing scheduled refresh runner: $SCRIPT_DIR/run_market_data_refresh.sh" >&2
  exit 1
fi
if [[ ! "$REFRESH_HOUR" =~ ^[0-9]+$ || "$REFRESH_HOUR" -gt 23 ]]; then
  echo "INVESTMENT_STUDIO_LOCAL_REFRESH_HOUR must be an integer between 0 and 23." >&2
  exit 64
fi
if [[ ! "$REFRESH_MINUTE" =~ ^[0-9]+$ || "$REFRESH_MINUTE" -gt 59 ]]; then
  echo "INVESTMENT_STUDIO_LOCAL_REFRESH_MINUTE must be an integer between 0 and 59." >&2
  exit 64
fi
if [[ ! "$REFRESH_RETRY_HOUR" =~ ^[0-9]+$ || "$REFRESH_RETRY_HOUR" -gt 23 ]]; then
  echo "INVESTMENT_STUDIO_LOCAL_REFRESH_RETRY_HOUR must be an integer between 0 and 23." >&2
  exit 64
fi
if [[ ! "$REFRESH_RETRY_MINUTE" =~ ^[0-9]+$ || "$REFRESH_RETRY_MINUTE" -gt 59 ]]; then
  echo "INVESTMENT_STUDIO_LOCAL_REFRESH_RETRY_MINUTE must be an integer between 0 and 59." >&2
  exit 64
fi
if (( 10#$REFRESH_RETRY_HOUR * 60 + 10#$REFRESH_RETRY_MINUTE \
    <= 10#$REFRESH_HOUR * 60 + 10#$REFRESH_MINUTE )); then
  echo "The refresh retry time must be later than the primary refresh time." >&2
  exit 64
fi
if [[ "$BUILD_FRONTENDS" != "true" && "$BUILD_FRONTENDS" != "false" ]]; then
  echo "BUILD_FRONTENDS must be true or false." >&2
  exit 64
fi
if [[ -z "$DATABASE_URL" ]]; then
  echo "INVESTMENT_STUDIO_LOCAL_DATABASE_URL must explicitly identify the existing local database." >&2
  exit 64
fi
case "$DATABASE_URL" in
  postgresql://*|postgresql+psycopg://*) ;;
  *)
    echo "INVESTMENT_STUDIO_LOCAL_DATABASE_URL must use postgresql:// or postgresql+psycopg://." >&2
    exit 64
    ;;
esac
if [[ ! "$HEALTH_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "INVESTMENT_STUDIO_INSTALL_HEALTH_ATTEMPTS must be a positive integer." >&2
  exit 64
fi

source "$SCRIPT_DIR/load_runtime_env.sh"
source "$BACKUP_HELPER"
investment_studio_require_password_free_database_url "$DATABASE_URL"
DATABASE_URL="$(investment_studio_sqlalchemy_database_url "$DATABASE_URL")"
investment_studio_reject_repository_env_files "$PROJECT_ROOT"
for env_spec in \
  home:INVESTMENT_STUDIO_HOME_ \
  data:INVESTMENT_STUDIO_DATA_ \
  watchlist:INVESTMENT_STUDIO_WATCHLIST_ \
  portfolio:INVESTMENT_STUDIO_PORTFOLIO_ \
  briefing:INVESTMENT_STUDIO_BRIEFING_ \
  market:INVESTMENT_STUDIO_MARKET_; do
  app="${env_spec%%:*}"
  prefix="${env_spec#*:}"
  runtime_env_file="$(investment_studio_runtime_env_file "$app" "$ENV_ROOT")"
  if [[ "$app" == "home" || "$app" == "data" || "$app" == "briefing" || "$app" == "market" || -f "$runtime_env_file" ]]; then
    if [[ "$app" == "data" ]]; then
      investment_studio_validate_env_file "$runtime_env_file" "$prefix" INVESTMENT_STUDIO_INSTRUMENT_DATA_ INVESTMENT_STUDIO_AUTH_
    else
      investment_studio_validate_env_file "$runtime_env_file" "$prefix" INVESTMENT_STUDIO_AUTH_
    fi
  fi
done

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR" \
  "${XDG_DATA_HOME:-$HOME/.local/share}/investment-studio/watchlist-documents" \
  "${XDG_DATA_HOME:-$HOME/.local/share}/investment-studio/portfolio-research-outputs" \
  "${XDG_STATE_HOME:-$HOME/.local/state}/investment-studio"

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/investment-studio-launchd-install.XXXXXX")"
chmod 700 "$WORK_DIR"
SERVICE_STATE_FILE="$WORK_DIR/service-state"
PLIST_BACKUP_DIR="$WORK_DIR/plists"
PLIST_MANIFEST="$WORK_DIR/existing-plists"
mkdir -p "$PLIST_BACKUP_DIR"
: > "$PLIST_MANIFEST"
chmod 600 "$PLIST_MANIFEST"

restart_required="false"
database_mutated="false"
backup_ready="false"
definitions_touched="false"

snapshot_existing_plists() {
  local service label plist
  for service in "${services[@]}"; do
    label="$LABEL_PREFIX.$service"
    plist="$LAUNCH_AGENTS_DIR/$label.plist"
    if [[ -f "$plist" ]]; then
      cp -p "$plist" "$PLIST_BACKUP_DIR/$label.plist"
      printf '%s\n' "$service" >> "$PLIST_MANIFEST"
    fi
  done
}

restore_plist_snapshot() {
  local service label plist
  local failed="false"
  for service in "${services[@]}"; do
    label="$LABEL_PREFIX.$service"
    plist="$LAUNCH_AGENTS_DIR/$label.plist"
    if grep -Fxq "$service" "$PLIST_MANIFEST"; then
      if ! cp -p "$PLIST_BACKUP_DIR/$label.plist" "$plist"; then
        failed="true"
      fi
    else
      if ! rm -f "$plist"; then
        failed="true"
      fi
    fi
  done
  [[ "$failed" == "false" ]]
}

stop_all_managed_services() {
  local service
  for service in "${services[@]}"; do
    launchctl bootout "gui/$UID/$LABEL_PREFIX.$service" >/dev/null 2>&1 || true
  done
}

cleanup_on_exit() {
  local status=$?
  local recovery_failed="false"
  local restart_failed="false"
  trap - EXIT HUP INT TERM
  set +e

  if [[ $status -ne 0 && "$restart_required" == "true" ]]; then
    if [[ "$definitions_touched" == "true" ]]; then
      stop_all_managed_services
    fi

    if [[ "$database_mutated" == "true" ]]; then
      if [[ "$backup_ready" != "true" ]] \
        || ! investment_studio_restore_project_schema_backup \
          "$DATABASE_URL" \
          "${INVESTMENT_STUDIO_PROJECT_SCHEMA_BACKUP_PATH:-}" \
          "${INVESTMENT_STUDIO_PROJECT_SCHEMA_MANIFEST_PATH:-}"; then
        recovery_failed="true"
        echo "Automatic database rollback failed." >&2
      fi
    fi

    if ! restore_plist_snapshot; then
      recovery_failed="true"
      echo "Automatic LaunchAgent definition rollback failed." >&2
    fi

    if [[ "$recovery_failed" != "true" ]]; then
      if ! LABEL_PREFIX="$LABEL_PREFIX" LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS_DIR" \
        "$SERVICE_CONTROL" start "$SERVICE_STATE_FILE"; then
        restart_failed="true"
        stop_all_managed_services
        echo "Previously loaded services could not be restored consistently and remain stopped." >&2
      fi
    else
      echo "Managed services remain stopped because rollback did not complete." >&2
    fi
  fi

  if [[ "$recovery_failed" == "true" || "$restart_failed" == "true" ]]; then
    echo "Installer recovery state retained at: $WORK_DIR" >&2
    [[ "$recovery_failed" == "true" ]] && status=70
  else
    rm -rf "$WORK_DIR"
  fi
  exit "$status"
}

trap cleanup_on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

snapshot_existing_plists
if ! LABEL_PREFIX="$LABEL_PREFIX" LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS_DIR" \
  "$SERVICE_CONTROL" stop "$SERVICE_STATE_FILE"; then
  if [[ -f "$SERVICE_STATE_FILE" ]]; then
    restart_required="true"
  fi
  exit 1
fi
restart_required="true"

"$LOG_COMPACTOR" "$LOG_DIR"

investment_studio_create_project_schema_backup \
  "$DATABASE_URL" \
  "$BACKUP_ROOT" \
  "investment-studio-pre-launchd-install"
backup_ready="true"

export INVESTMENT_STUDIO_HOME_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_INSTRUMENT_DATA_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA=instrument_data
export INVESTMENT_STUDIO_DATA_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA=instrument_data
export INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA=data_ingestion
export INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_WATCHLIST_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_WATCHLIST_DATABASE_SCHEMA=watchlist
export INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_PORTFOLIO_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_PORTFOLIO_DATABASE_SCHEMA=portfolio
export INVESTMENT_STUDIO_BRIEFING_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_BRIEFING_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_MARKET_DATABASE_URL="$DATABASE_URL"

database_mutated="true"
PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN" ENV_ROOT="" \
  "$MIGRATION_RUNNER"
data_env_file="$(investment_studio_runtime_env_file data "$ENV_ROOT")"
investment_studio_load_env_file "$data_env_file" INVESTMENT_STUDIO_DATA_ INVESTMENT_STUDIO_INSTRUMENT_DATA_ INVESTMENT_STUDIO_AUTH_
investment_studio_load_env_file "$ENV_ROOT/market.env" INVESTMENT_STUDIO_MARKET_
PYTHONPATH="$PROJECT_ROOT/shared-data:$PROJECT_ROOT/shared-data/instruments/python:$PROJECT_ROOT/shared-data/market${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" "$MARKET_DATA_REFRESH_RUNNER" \
    --channel fmp \
    --updated-by launchd-install \
    --no-downstream-refresh \
    --fail-on-item-failure
PYTHONPATH="$PROJECT_ROOT/apps/portfolio/backend:$PROJECT_ROOT/shared-data/instruments/python${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" "$SNAPSHOT_REFRESH_RUNNER" --recover-interrupted
INVESTMENT_STUDIO_LOCAL_DATABASE_URL="$DATABASE_URL" \
  "$PYTHON_BIN" "$AUDIT_RUNNER" --fail-on-warning

if [[ "$BUILD_FRONTENDS" == "true" ]]; then
  for frontend_root in "$PROJECT_ROOT/home/frontend" "$PROJECT_ROOT/apps/watchlist/frontend" "$PROJECT_ROOT/apps/portfolio/frontend" "$PROJECT_ROOT/apps/briefing/frontend"; do
    "$NPM_BIN" --prefix "$frontend_root" ci
    "$NPM_BIN" --prefix "$frontend_root" run build
  done
fi

for frontend_root in "$PROJECT_ROOT/home/frontend" "$PROJECT_ROOT/apps/watchlist/frontend" "$PROJECT_ROOT/apps/portfolio/frontend" "$PROJECT_ROOT/apps/briefing/frontend"; do
  if [[ ! -f "$frontend_root/dist/index.html" ]]; then
    echo "Missing frontend build: $frontend_root/dist/index.html" >&2
    exit 1
  fi
done

definitions_touched="true"
INVESTMENT_STUDIO_LOCAL_DATABASE_URL="$DATABASE_URL" \
  "$PYTHON_BIN" "$SCRIPT_DIR/generate_local_service_plists.py" \
  --project-root "$PROJECT_ROOT" \
  --python-bin "$PYTHON_BIN" \
  --node-bin "$NODE_BIN" \
  --label-prefix "$LABEL_PREFIX" \
  --launch-agents-dir "$LAUNCH_AGENTS_DIR" \
  --log-dir "$LOG_DIR" \
  --env-root "$ENV_ROOT" \
  --refresh-hour "$REFRESH_HOUR" \
  --refresh-minute "$REFRESH_MINUTE" \
  --refresh-retry-hour "$REFRESH_RETRY_HOUR" \
  --refresh-retry-minute "$REFRESH_RETRY_MINUTE"

"$PYTHON_BIN" "$PROJECT_ROOT/infra/scripts/install_market_pipeline.py" \
  --scheduler launchd --role replica --project-root "$PROJECT_ROOT" \
  --env-root "$ENV_ROOT" --python "$PYTHON_BIN" \
  --output-dir "$LAUNCH_AGENTS_DIR" --label-prefix "$LABEL_PREFIX"

domain="gui/$UID"
for service in "${services[@]}"; do
  label="$LABEL_PREFIX.$service"
  plist="$LAUNCH_AGENTS_DIR/$label.plist"
  launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
  launchctl bootstrap "$domain" "$plist"
  launchctl enable "$domain/$label"
done

for service in home-api watchlist-api portfolio-api briefing-api home-web watchlist-web portfolio-web briefing-web; do
  launchctl kickstart -k "$domain/$LABEL_PREFIX.$service"
done

health_urls=(
  http://127.0.0.1:8002/api/health
  http://127.0.0.1:8000/api/health
  http://127.0.0.1:8001/api/health
  http://127.0.0.1:8010/health
  http://127.0.0.1:5172/
  http://127.0.0.1:5173/
  http://127.0.0.1:5174/
  http://127.0.0.1:5175/
)
for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
  healthy="true"
  for url in "${health_urls[@]}"; do
    if ! curl --noproxy '*' --max-time 2 --fail --silent --output /dev/null "$url"; then
      healthy="false"
      break
    fi
  done
  if [[ "$healthy" == "true" ]]; then
    database_mutated="false"
    restart_required="false"
    rm -rf "$WORK_DIR"
    trap - EXIT HUP INT TERM
    echo "Investment Studio is running at http://127.0.0.1:5172"
    echo "Pre-migration backup retained at: ${INVESTMENT_STUDIO_PROJECT_SCHEMA_BACKUP_PATH:-$INVESTMENT_STUDIO_PROJECT_SCHEMA_MANIFEST_PATH}"
    exit 0
  fi
  [[ $attempt -eq $HEALTH_ATTEMPTS ]] || sleep 1
done

echo "Services were installed, but a health check did not become ready: $url" >&2
exit 1
