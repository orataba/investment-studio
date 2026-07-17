#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS launchd." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
LABEL_PREFIX="${LABEL_PREFIX:-com.orataba.portfolio-ops}"
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
LOG_DIR="${LOG_DIR:-$HOME/Library/Logs/portfolio-operations-workbench}"
ENV_ROOT="${PORTFOLIO_OPS_LOCAL_ENV_ROOT:-$HOME/.config/orataba/secrets/portfolio-operations-workbench}"
BACKUP_ROOT="${PORTFOLIO_OPS_INSTALL_BACKUP_DIR:-$HOME/Library/Application Support/portfolio-operations-workbench/backups}"
BUILD_FRONTENDS="${BUILD_FRONTENDS:-true}"
DATABASE_URL="${PORTFOLIO_OPS_LOCAL_DATABASE_URL:-}"
REFRESH_HOUR="${PORTFOLIO_OPS_LOCAL_REFRESH_HOUR:-21}"
REFRESH_MINUTE="${PORTFOLIO_OPS_LOCAL_REFRESH_MINUTE:-0}"
HEALTH_ATTEMPTS="${PORTFOLIO_OPS_INSTALL_HEALTH_ATTEMPTS:-30}"

PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
NPM_BIN="${NPM_BIN:-$(command -v npm || true)}"
MIGRATION_RUNNER="$PROJECT_ROOT/infra/scripts/migrate_all.sh"
SERVICE_CONTROL="$SCRIPT_DIR/control_local_services.sh"
BACKUP_HELPER="$PROJECT_ROOT/infra/postgres/project_schema_backup.sh"

services=(
  platform-api
  watchlist-api
  portfolio-api
  platform-web
  watchlist-web
  portfolio-web
  market-data-refresh
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

for required_executable in "$MIGRATION_RUNNER" "$SERVICE_CONTROL"; do
  if [[ ! -x "$required_executable" ]]; then
    echo "Required installer helper is missing or not executable: $required_executable" >&2
    exit 1
  fi
done
for required_file in \
  "$SCRIPT_DIR/generate_local_service_plists.py" \
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
  echo "PORTFOLIO_OPS_LOCAL_REFRESH_HOUR must be an integer between 0 and 23." >&2
  exit 64
fi
if [[ ! "$REFRESH_MINUTE" =~ ^[0-9]+$ || "$REFRESH_MINUTE" -gt 59 ]]; then
  echo "PORTFOLIO_OPS_LOCAL_REFRESH_MINUTE must be an integer between 0 and 59." >&2
  exit 64
fi
if [[ "$BUILD_FRONTENDS" != "true" && "$BUILD_FRONTENDS" != "false" ]]; then
  echo "BUILD_FRONTENDS must be true or false." >&2
  exit 64
fi
if [[ -z "$DATABASE_URL" ]]; then
  echo "PORTFOLIO_OPS_LOCAL_DATABASE_URL must explicitly identify the existing local database." >&2
  exit 64
fi
case "$DATABASE_URL" in
  postgresql://*|postgresql+psycopg://*) ;;
  *)
    echo "PORTFOLIO_OPS_LOCAL_DATABASE_URL must use postgresql:// or postgresql+psycopg://." >&2
    exit 64
    ;;
esac
if [[ ! "$HEALTH_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "PORTFOLIO_OPS_INSTALL_HEALTH_ATTEMPTS must be a positive integer." >&2
  exit 64
fi

source "$SCRIPT_DIR/load_runtime_env.sh"
source "$BACKUP_HELPER"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
for env_spec in \
  platform:PORTFOLIO_OPS_PLATFORM_ \
  watchlist:PORTFOLIO_OPS_WATCHLIST_ \
  portfolio:PORTFOLIO_OPS_PORTFOLIO_; do
  app="${env_spec%%:*}"
  prefix="${env_spec#*:}"
  runtime_env_file="$(portfolio_ops_runtime_env_file "$app" "$ENV_ROOT")"
  if [[ "$app" == "platform" || -f "$runtime_env_file" ]]; then
    portfolio_ops_validate_env_file "$runtime_env_file" "$prefix"
  fi
done

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR" "$PROJECT_ROOT/var/watchlist-documents" \
  "$PROJECT_ROOT/var/portfolio-research-outputs"

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-launchd-install.XXXXXX")"
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
        || ! portfolio_ops_restore_project_schema_backup \
          "$DATABASE_URL" \
          "${PORTFOLIO_OPS_PROJECT_SCHEMA_BACKUP_PATH:-}" \
          "${PORTFOLIO_OPS_PROJECT_SCHEMA_MANIFEST_PATH:-}"; then
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

portfolio_ops_create_project_schema_backup \
  "$DATABASE_URL" \
  "$BACKUP_ROOT" \
  "portfolio-ops-pre-launchd-install"
backup_ready="true"

export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA=instrument_registry
export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry
export PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=platform
export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA=watchlist
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA=portfolio

database_mutated="true"
PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN" ENV_ROOT="" \
  "$MIGRATION_RUNNER"

if [[ "$BUILD_FRONTENDS" == "true" ]]; then
  for app in platform watchlist portfolio; do
    "$NPM_BIN" --prefix "$PROJECT_ROOT/apps/$app/frontend" ci
    "$NPM_BIN" --prefix "$PROJECT_ROOT/apps/$app/frontend" run build
  done
fi

for app in platform watchlist portfolio; do
  if [[ ! -f "$PROJECT_ROOT/apps/$app/frontend/dist/index.html" ]]; then
    echo "Missing frontend build: apps/$app/frontend/dist/index.html" >&2
    exit 1
  fi
done

definitions_touched="true"
PORTFOLIO_OPS_LOCAL_DATABASE_URL="$DATABASE_URL" \
  "$PYTHON_BIN" "$SCRIPT_DIR/generate_local_service_plists.py" \
  --project-root "$PROJECT_ROOT" \
  --python-bin "$PYTHON_BIN" \
  --node-bin "$NODE_BIN" \
  --label-prefix "$LABEL_PREFIX" \
  --launch-agents-dir "$LAUNCH_AGENTS_DIR" \
  --log-dir "$LOG_DIR" \
  --env-root "$ENV_ROOT" \
  --refresh-hour "$REFRESH_HOUR" \
  --refresh-minute "$REFRESH_MINUTE"

domain="gui/$UID"
for service in "${services[@]}"; do
  label="$LABEL_PREFIX.$service"
  plist="$LAUNCH_AGENTS_DIR/$label.plist"
  launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
  launchctl bootstrap "$domain" "$plist"
  launchctl enable "$domain/$label"
done

for service in platform-api watchlist-api portfolio-api platform-web watchlist-web portfolio-web; do
  launchctl kickstart -k "$domain/$LABEL_PREFIX.$service"
done

health_urls=(
  http://127.0.0.1:8002/api/health
  http://127.0.0.1:8000/api/health
  http://127.0.0.1:8001/api/health
  http://127.0.0.1:5172/
  http://127.0.0.1:5173/
  http://127.0.0.1:5174/
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
    echo "Portfolio Operations Workbench is running at http://127.0.0.1:5172"
    echo "Pre-migration backup retained at: ${PORTFOLIO_OPS_PROJECT_SCHEMA_BACKUP_PATH:-$PORTFOLIO_OPS_PROJECT_SCHEMA_MANIFEST_PATH}"
    exit 0
  fi
  [[ $attempt -eq $HEALTH_ATTEMPTS ]] || sleep 1
done

echo "Services were installed, but one or more health checks did not become ready." >&2
exit 1
