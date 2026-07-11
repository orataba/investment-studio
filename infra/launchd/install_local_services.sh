#!/usr/bin/env bash
set -euo pipefail

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
BUILD_FRONTENDS="${BUILD_FRONTENDS:-true}"
DATABASE_URL="${PORTFOLIO_OPS_LOCAL_DATABASE_URL:-postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops}"
REFRESH_HOUR="${PORTFOLIO_OPS_LOCAL_REFRESH_HOUR:-21}"
REFRESH_MINUTE="${PORTFOLIO_OPS_LOCAL_REFRESH_MINUTE:-0}"

PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
NPM_BIN="${NPM_BIN:-$(command -v npm || true)}"

for executable in "$PYTHON_BIN" "$NODE_BIN" "$NPM_BIN"; do
  if [[ -z "$executable" || ! -x "$executable" ]]; then
    echo "Required executable is missing: ${executable:-<empty>}" >&2
    exit 1
  fi
done

if [[ ! -x "$PROJECT_ROOT/infra/scripts/migrate_all.sh" ]]; then
  echo "Missing migration runner: $PROJECT_ROOT/infra/scripts/migrate_all.sh" >&2
  exit 1
fi
if [[ ! -f "$SCRIPT_DIR/generate_local_service_plists.py" ]]; then
  echo "Missing LaunchAgent plist generator: $SCRIPT_DIR/generate_local_service_plists.py" >&2
  exit 1
fi
if [[ ! -f "$SCRIPT_DIR/load_runtime_env.sh" ]]; then
  echo "Missing safe runtime environment loader: $SCRIPT_DIR/load_runtime_env.sh" >&2
  exit 1
fi
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

source "$SCRIPT_DIR/load_runtime_env.sh"
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

"$SCRIPT_DIR/bootstrap_local_database.sh"

SERVICE_STATE_FILE="$(mktemp "${TMPDIR:-/tmp}/portfolio-ops-launchd-install-state.XXXXXX")"
services_stopped=true
new_services_started=false
restore_previous_services_on_failure() {
  local exit_code=$?
  trap - EXIT
  if [[ $exit_code -ne 0 && "$services_stopped" == "true" ]]; then
    if [[ "$new_services_started" == "true" ]]; then
      local service
      for service in platform-api watchlist-api portfolio-api platform-web watchlist-web portfolio-web market-data-refresh; do
        launchctl bootout "gui/$UID/$LABEL_PREFIX.$service" >/dev/null 2>&1 || true
      done
    fi
    echo "Install failed; restoring the previously loaded launchd services." >&2
    LABEL_PREFIX="$LABEL_PREFIX" LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS_DIR" \
      "$SCRIPT_DIR/control_local_services.sh" start "$SERVICE_STATE_FILE" || \
      echo "Failed to restore one or more previous launchd services." >&2
  fi
  rm -f "$SERVICE_STATE_FILE"
  exit "$exit_code"
}
trap restore_previous_services_on_failure EXIT

# A running pre-upgrade worker does not understand a newly introduced database
# fencing protocol.  Stop every managed process before applying migrations.
LABEL_PREFIX="$LABEL_PREFIX" LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS_DIR" \
  "$SCRIPT_DIR/control_local_services.sh" stop "$SERVICE_STATE_FILE"

export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA=instrument_registry
export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry
export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA=watchlist
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA=portfolio
PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN" \
  "$PROJECT_ROOT/infra/scripts/migrate_all.sh"

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

"$PYTHON_BIN" "$SCRIPT_DIR/generate_local_service_plists.py" \
  --project-root "$PROJECT_ROOT" \
  --python-bin "$PYTHON_BIN" \
  --node-bin "$NODE_BIN" \
  --database-url "$DATABASE_URL" \
  --label-prefix "$LABEL_PREFIX" \
  --launch-agents-dir "$LAUNCH_AGENTS_DIR" \
  --log-dir "$LOG_DIR" \
  --env-root "$ENV_ROOT" \
  --refresh-hour "$REFRESH_HOUR" \
  --refresh-minute "$REFRESH_MINUTE"

domain="gui/$UID"
new_services_started=true
for service in platform-api watchlist-api portfolio-api platform-web watchlist-web portfolio-web market-data-refresh; do
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
for attempt in {1..30}; do
  healthy=true
  for url in "${health_urls[@]}"; do
    if ! curl --noproxy '*' --max-time 2 --fail --silent --output /dev/null "$url"; then
      healthy=false
      break
    fi
  done
  if [[ "$healthy" == "true" ]]; then
    services_stopped=false
    new_services_started=false
    rm -f "$SERVICE_STATE_FILE"
    trap - EXIT
    echo "Portfolio Operations Workbench is running at http://127.0.0.1:5172"
    exit 0
  fi
  sleep 1
done

echo "Services were installed, but one or more health checks did not become ready." >&2
"$SCRIPT_DIR/status_local_services.sh" || true
exit 1
