#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
UNIT_PREFIX="${UNIT_PREFIX:-portfolio-ops}"
HOST="${HOST:-127.0.0.1}"
API_HOST="${API_HOST:-$HOST}"
WEB_HOST="${WEB_HOST:-$HOST}"
PLATFORM_API_PORT="${PLATFORM_API_PORT:-8102}"
WATCHLIST_API_PORT="${WATCHLIST_API_PORT:-8100}"
PORTFOLIO_API_PORT="${PORTFOLIO_API_PORT:-8101}"
PLATFORM_WEB_PORT="${PLATFORM_WEB_PORT:-3100}"
WATCHLIST_WEB_PORT="${WATCHLIST_WEB_PORT:-3101}"
PORTFOLIO_WEB_PORT="${PORTFOLIO_WEB_PORT:-3102}"
START_SERVICES="${START_SERVICES:-true}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-true}"
ENV_ROOT="${ENV_ROOT:-}"
RELEASE_AS_OF_DATE="${PORTFOLIO_OPS_RELEASE_AS_OF_DATE:-}"
RUNTIME_HEALTH_ATTEMPTS="${PORTFOLIO_OPS_SYSTEMD_HEALTH_ATTEMPTS:-30}"
source "$PROJECT_ROOT/infra/service_inventory.sh"
source "$PROJECT_ROOT/infra/scripts/runtime_readiness.sh"
API_HEALTH_URL_HOST="$(
  portfolio_ops_runtime_health_host "${API_HEALTH_HOST:-$API_HOST}"
)"
WEB_HEALTH_URL_HOST="$(
  portfolio_ops_runtime_health_host "${WEB_HEALTH_HOST:-$WEB_HOST}"
)"
RUNTIME_UNITS=()
for service in "${PORTFOLIO_OPS_RUNTIME_SERVICE_NAMES[@]}"; do
  RUNTIME_UNITS+=("$UNIT_PREFIX-$service.service")
done
MANAGED_UNITS=()
for unit_suffix in "${PORTFOLIO_OPS_SYSTEMD_MANAGED_UNIT_SUFFIXES[@]}"; do
  MANAGED_UNITS+=("$UNIT_PREFIX-$unit_suffix")
done

DEFAULT_PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$DEFAULT_PYTHON_BIN" ]]; then
  DEFAULT_PYTHON_BIN="$(command -v python3)"
fi
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"

DEFAULT_NODE_BIN="$(command -v node)"
NODE_BIN="${NODE_BIN:-$DEFAULT_NODE_BIN}"

USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$USER_SYSTEMD_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "PYTHON_BIN is not executable: $PYTHON_BIN" >&2
  exit 1
fi

if [[ ! -x "$NODE_BIN" ]]; then
  echo "NODE_BIN is not executable: $NODE_BIN" >&2
  exit 1
fi

if [[ ! -f "$PROJECT_ROOT/deploy/serve_spa_proxy.mjs" ]]; then
  echo "Cannot find deploy/serve_spa_proxy.mjs under PROJECT_ROOT: $PROJECT_ROOT" >&2
  exit 1
fi

if [[ ! -x "$PROJECT_ROOT/infra/scripts/migrate_all.sh" ]]; then
  echo "Cannot find executable infra/scripts/migrate_all.sh under PROJECT_ROOT: $PROJECT_ROOT" >&2
  exit 1
fi
if [[ ! -x "$PROJECT_ROOT/infra/scripts/release_database.sh" ]]; then
  echo "Cannot find safe release orchestrator under PROJECT_ROOT: $PROJECT_ROOT" >&2
  exit 1
fi
if [[ "$RUN_MIGRATIONS" == "true" ]]; then
  if [[ -z "$RELEASE_AS_OF_DATE" ]]; then
    echo "Set PORTFOLIO_OPS_RELEASE_AS_OF_DATE to an explicit YYYY-MM-DD." >&2
    exit 64
  fi
  if [[ -z "${PORTFOLIO_OPS_RELEASE_DATABASE_URL:-}" ]]; then
    echo "Set PORTFOLIO_OPS_RELEASE_DATABASE_URL explicitly for a database release." >&2
    exit 64
  fi
  if [[ -z "${CONFIRM_RELEASE:-}" ]]; then
    echo "Set CONFIRM_RELEASE to database@host:port after confirming the release target." >&2
    exit 64
  fi
fi

write_api_service() {
  local app="$1"
  local backend_rel="$2"
  local module="$3"
  local port="$4"
  local backend_root="$PROJECT_ROOT/$backend_rel"
  local env_file="$backend_root/.env"
  local service_file="$USER_SYSTEMD_DIR/$UNIT_PREFIX-$app-api.service"
  local pythonpath_value="$backend_root:$PROJECT_ROOT/packages/instrument-core/python"
  local worker_dependency=""

  if [[ "$app" == "portfolio" ]]; then
    pythonpath_value="$backend_root:$PROJECT_ROOT/packages/instrument-core/python:$PROJECT_ROOT/packages/calculation-core/python"
    worker_dependency=" $UNIT_PREFIX-portfolio-worker.service"
  fi
  if [[ "$app" == "watchlist" ]]; then
    worker_dependency=" $UNIT_PREFIX-watchlist-worker.service"
  fi
  if [[ "$app" == "platform" ]]; then
    worker_dependency=" $UNIT_PREFIX-platform-outbox-worker.service"
  fi

  if [[ -n "$ENV_ROOT" ]]; then
    env_file="$ENV_ROOT/$app.env"
  fi

  if [[ ! -f "$env_file" ]]; then
    echo "Missing environment file: $env_file" >&2
    exit 1
  fi

  cat > "$service_file" <<EOF
[Unit]
Description=Portfolio Operations $app API
After=network-online.target$worker_dependency
Wants=network-online.target$worker_dependency

[Service]
Type=simple
WorkingDirectory=$backend_root
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONPATH=$pythonpath_value
EnvironmentFile=$env_file
ExecStart=$PYTHON_BIN -m uvicorn $module --host $API_HOST --port $port
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
}

write_platform_outbox_worker_service() {
  local backend_root="$PROJECT_ROOT/apps/platform/backend"
  local env_file="$backend_root/.env"
  local service_file="$USER_SYSTEMD_DIR/$UNIT_PREFIX-platform-outbox-worker.service"
  local pythonpath_value="$backend_root:$PROJECT_ROOT/packages/instrument-core/python"

  if [[ -n "$ENV_ROOT" ]]; then
    env_file="$ENV_ROOT/platform.env"
  fi
  if [[ ! -f "$env_file" ]]; then
    echo "Missing environment file: $env_file" >&2
    exit 1
  fi

  cat > "$service_file" <<EOF
[Unit]
Description=Portfolio Operations Market Data Outbox Worker
After=network-online.target $UNIT_PREFIX-watchlist-api.service
Wants=network-online.target
Requires=$UNIT_PREFIX-watchlist-api.service

[Service]
Type=simple
WorkingDirectory=$backend_root
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONPATH=$pythonpath_value
EnvironmentFile=$env_file
Environment=PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL=http://127.0.0.1:$WATCHLIST_API_PORT
ExecStart=$PYTHON_BIN $backend_root/scripts/run_market_data_outbox_worker.py --worker-id-prefix systemd-platform-market-data-outbox
Restart=always
RestartSec=5
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
}

write_watchlist_worker_service() {
  local backend_root="$PROJECT_ROOT/apps/watchlist/backend"
  local env_file="$backend_root/.env"
  local service_file="$USER_SYSTEMD_DIR/$UNIT_PREFIX-watchlist-worker.service"
  local pythonpath_value="$backend_root:$PROJECT_ROOT/packages/instrument-core/python"

  if [[ -n "$ENV_ROOT" ]]; then
    env_file="$ENV_ROOT/watchlist.env"
  fi
  if [[ ! -f "$env_file" ]]; then
    echo "Missing environment file: $env_file" >&2
    exit 1
  fi

  cat > "$service_file" <<EOF
[Unit]
Description=Portfolio Operations Watchlist Recalc Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$backend_root
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONPATH=$pythonpath_value
EnvironmentFile=$env_file
ExecStart=$PYTHON_BIN -m watchlist_app.services.recalc_worker --worker-id-prefix systemd-watchlist-recalc
Restart=always
RestartSec=5
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
}

write_portfolio_worker_service() {
  local backend_root="$PROJECT_ROOT/apps/portfolio/backend"
  local env_file="$backend_root/.env"
  local service_file="$USER_SYSTEMD_DIR/$UNIT_PREFIX-portfolio-worker.service"
  local pythonpath_value="$backend_root:$PROJECT_ROOT/packages/instrument-core/python:$PROJECT_ROOT/packages/calculation-core/python"

  if [[ -n "$ENV_ROOT" ]]; then
    env_file="$ENV_ROOT/portfolio.env"
  fi
  if [[ ! -f "$env_file" ]]; then
    echo "Missing environment file: $env_file" >&2
    exit 1
  fi

  cat > "$service_file" <<EOF
[Unit]
Description=Portfolio Operations Portfolio Daily Calculation Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$backend_root
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONPATH=$pythonpath_value
EnvironmentFile=$env_file
ExecStart=$PYTHON_BIN -m portfolio_app.calculations.portfolio_daily.worker --worker-id-prefix systemd-portfolio-daily
Restart=always
RestartSec=5
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
}

write_web_service() {
  local app="$1"
  local frontend_rel="$2"
  local web_port="$3"
  local api_port="$4"
  local dist_root="$PROJECT_ROOT/$frontend_rel/dist"
  local service_file="$USER_SYSTEMD_DIR/$UNIT_PREFIX-$app-web.service"

  if [[ ! -f "$dist_root/index.html" ]]; then
    echo "Missing frontend build output: $dist_root/index.html" >&2
    exit 1
  fi

  cat > "$service_file" <<EOF
[Unit]
Description=Portfolio Operations $app Frontend
After=network-online.target $UNIT_PREFIX-$app-api.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_ROOT
Environment=NODE_ENV=production
ExecStart=$NODE_BIN $PROJECT_ROOT/deploy/serve_spa_proxy.mjs --host $WEB_HOST --port $web_port --dist $dist_root --api-target http://127.0.0.1:$api_port
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
}

write_watchlist_worker_service
write_api_service "watchlist" "apps/watchlist/backend" "watchlist_app.main:app" "$WATCHLIST_API_PORT"
write_platform_outbox_worker_service
write_api_service "platform" "apps/platform/backend" "platform_app.main:app" "$PLATFORM_API_PORT"
write_api_service "portfolio" "apps/portfolio/backend" "portfolio_app.main:app" "$PORTFOLIO_API_PORT"
write_portfolio_worker_service
write_web_service "platform" "apps/platform/frontend" "$PLATFORM_WEB_PORT" "$PLATFORM_API_PORT"
write_web_service "watchlist" "apps/watchlist/frontend" "$WATCHLIST_WEB_PORT" "$WATCHLIST_API_PORT"
write_web_service "portfolio" "apps/portfolio/frontend" "$PORTFOLIO_WEB_PORT" "$PORTFOLIO_API_PORT"

SERVICE_STATE_FILE="$(mktemp "${TMPDIR:-/tmp}/portfolio-ops-systemd-install-state.XXXXXX")"
services_stopped=false
leave_services_stopped_on_failure() {
  local exit_code=$?
  trap - EXIT
  if [[ $exit_code -ne 0 && "$services_stopped" == "true" ]]; then
    systemctl --user stop "${MANAGED_UNITS[@]}" >/dev/null 2>&1 || true
    echo "Install failed; managed services and refresh writers remain stopped for operator review." >&2
  fi
  rm -f "$SERVICE_STATE_FILE"
  exit "$exit_code"
}
trap leave_services_stopped_on_failure EXIT

if [[ "$RUN_MIGRATIONS" == "true" ]]; then
  : > "$SERVICE_STATE_FILE"
  chmod 600 "$SERVICE_STATE_FILE"
  for unit in "${MANAGED_UNITS[@]}"; do
    if systemctl --user is-active --quiet "$unit"; then
      printf '%s\n' "$unit" >> "$SERVICE_STATE_FILE"
    fi
  done
  services_stopped=true
  while IFS= read -r unit || [[ -n "$unit" ]]; do
    [[ -n "$unit" ]] || continue
    systemctl --user stop "$unit"
  done < "$SERVICE_STATE_FILE"
  echo "Managed services and refresh writers stopped; applying the safe database release."
  CONFIRM_RELEASE="$CONFIRM_RELEASE" \
  PORTFOLIO_OPS_RELEASE_DATABASE_URL="$PORTFOLIO_OPS_RELEASE_DATABASE_URL" \
  PORTFOLIO_OPS_RELEASE_AS_OF_DATE="$RELEASE_AS_OF_DATE" \
  PORTFOLIO_OPS_RELEASE_SERVICE_MANAGER=none \
  PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN" ENV_ROOT="$ENV_ROOT" \
    "$PROJECT_ROOT/infra/scripts/release_database.sh"
fi

systemctl --user daemon-reload
systemctl --user enable "${RUNTIME_UNITS[@]}"

if [[ "$START_SERVICES" == "true" ]]; then
  services_stopped=true
  systemctl --user restart "${RUNTIME_UNITS[@]}"
  runtime_urls=(
    "http://$API_HEALTH_URL_HOST:$PLATFORM_API_PORT/api/readiness"
    "http://$API_HEALTH_URL_HOST:$WATCHLIST_API_PORT/api/readiness"
    "http://$API_HEALTH_URL_HOST:$PORTFOLIO_API_PORT/api/readiness"
    "http://$WEB_HEALTH_URL_HOST:$PLATFORM_WEB_PORT/"
    "http://$WEB_HEALTH_URL_HOST:$WATCHLIST_WEB_PORT/"
    "http://$WEB_HEALTH_URL_HOST:$PORTFOLIO_WEB_PORT/"
  )
  runtime_gate_failed="false"
  if ! portfolio_ops_wait_for_runtime_urls \
    "$RUNTIME_HEALTH_ATTEMPTS" "${runtime_urls[@]}"; then
    runtime_gate_failed="true"
  elif ! portfolio_ops_verify_portfolio_read_contract \
    "$PYTHON_BIN" "http://$API_HEALTH_URL_HOST:$PORTFOLIO_API_PORT"; then
    runtime_gate_failed="true"
  fi
  if [[ "$runtime_gate_failed" == "true" ]]; then
    echo "Managed services failed post-start runtime validation." >&2
    systemctl --user --no-pager --plain status "${MANAGED_UNITS[@]}" || true
    exit 1
  fi
fi
if [[ "$RUN_MIGRATIONS" == "true" ]] \
  && grep -Fxq "$UNIT_PREFIX-market-data-refresh.timer" "$SERVICE_STATE_FILE"; then
  systemctl --user start "$UNIT_PREFIX-market-data-refresh.timer"
fi
if [[ "$RUN_MIGRATIONS" == "true" ]] \
  && grep -Fxq "$UNIT_PREFIX-market-data-refresh.service" "$SERVICE_STATE_FILE"; then
  echo "The in-flight market-data refresh was fenced and was not replayed automatically."
fi

services_stopped=false
rm -f "$SERVICE_STATE_FILE"
trap - EXIT

systemctl --user --no-pager --plain status "${MANAGED_UNITS[@]}" || true
