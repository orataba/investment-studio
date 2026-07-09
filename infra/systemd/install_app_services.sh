#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
UNIT_PREFIX="${UNIT_PREFIX:-portfolio-ops}"
HOST="${HOST:-0.0.0.0}"
API_HOST="${API_HOST:-$HOST}"
WEB_HOST="${WEB_HOST:-$HOST}"
PLATFORM_API_PORT="${PLATFORM_API_PORT:-8102}"
WATCHLIST_API_PORT="${WATCHLIST_API_PORT:-8100}"
PORTFOLIO_API_PORT="${PORTFOLIO_API_PORT:-8101}"
PLATFORM_WEB_PORT="${PLATFORM_WEB_PORT:-3100}"
WATCHLIST_WEB_PORT="${WATCHLIST_WEB_PORT:-3101}"
PORTFOLIO_WEB_PORT="${PORTFOLIO_WEB_PORT:-3102}"
START_SERVICES="${START_SERVICES:-true}"

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

write_api_service() {
  local app="$1"
  local backend_rel="$2"
  local module="$3"
  local port="$4"
  local backend_root="$PROJECT_ROOT/$backend_rel"
  local env_file="$backend_root/.env"
  local service_file="$USER_SYSTEMD_DIR/$UNIT_PREFIX-$app-api.service"
  local pythonpath_value="$backend_root:$PROJECT_ROOT/packages/instrument-core/python"

  if [[ ! -f "$env_file" ]]; then
    echo "Missing environment file: $env_file" >&2
    exit 1
  fi

  cat > "$service_file" <<EOF
[Unit]
Description=Portfolio Operations $app API
After=network-online.target
Wants=network-online.target

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

write_api_service "platform" "apps/platform/backend" "platform_app.main:app" "$PLATFORM_API_PORT"
write_api_service "watchlist" "apps/watchlist/backend" "watchlist_app.main:app" "$WATCHLIST_API_PORT"
write_api_service "portfolio" "apps/portfolio/backend" "portfolio_app.main:app" "$PORTFOLIO_API_PORT"
write_web_service "platform" "apps/platform/frontend" "$PLATFORM_WEB_PORT" "$PLATFORM_API_PORT"
write_web_service "watchlist" "apps/watchlist/frontend" "$WATCHLIST_WEB_PORT" "$WATCHLIST_API_PORT"
write_web_service "portfolio" "apps/portfolio/frontend" "$PORTFOLIO_WEB_PORT" "$PORTFOLIO_API_PORT"

systemctl --user daemon-reload
systemctl --user enable \
  "$UNIT_PREFIX-platform-api.service" \
  "$UNIT_PREFIX-platform-web.service" \
  "$UNIT_PREFIX-watchlist-api.service" \
  "$UNIT_PREFIX-watchlist-web.service" \
  "$UNIT_PREFIX-portfolio-api.service" \
  "$UNIT_PREFIX-portfolio-web.service"

if [[ "$START_SERVICES" == "true" ]]; then
  systemctl --user restart \
    "$UNIT_PREFIX-platform-api.service" \
    "$UNIT_PREFIX-watchlist-api.service" \
    "$UNIT_PREFIX-portfolio-api.service" \
    "$UNIT_PREFIX-platform-web.service" \
    "$UNIT_PREFIX-watchlist-web.service" \
    "$UNIT_PREFIX-portfolio-web.service"
fi

systemctl --user --no-pager --plain status \
  "$UNIT_PREFIX-platform-api.service" \
  "$UNIT_PREFIX-watchlist-api.service" \
  "$UNIT_PREFIX-portfolio-api.service" \
  "$UNIT_PREFIX-platform-web.service" \
  "$UNIT_PREFIX-watchlist-web.service" \
  "$UNIT_PREFIX-portfolio-web.service" || true
