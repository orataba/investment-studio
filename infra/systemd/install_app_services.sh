#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
UNIT_PREFIX="${UNIT_PREFIX:-investment-studio}"
HOST="${HOST:-127.0.0.1}"
API_HOST="${API_HOST:-$HOST}"
WEB_HOST="${WEB_HOST:-$HOST}"
HOME_API_PORT="${HOME_API_PORT:-8102}"
WATCHLIST_API_PORT="${WATCHLIST_API_PORT:-8100}"
PORTFOLIO_API_PORT="${PORTFOLIO_API_PORT:-8101}"
BRIEFING_API_PORT="${BRIEFING_API_PORT:-8110}"
HOME_WEB_PORT="${HOME_WEB_PORT:-3100}"
WATCHLIST_WEB_PORT="${WATCHLIST_WEB_PORT:-3101}"
PORTFOLIO_WEB_PORT="${PORTFOLIO_WEB_PORT:-3102}"
BRIEFING_WEB_PORT="${BRIEFING_WEB_PORT:-3103}"
START_SERVICES="${START_SERVICES:-true}"
HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-60}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-true}"
ENV_ROOT="${ENV_ROOT:-}"
RUNTIME_ENV_HELPER="$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
if [[ ! -f "$RUNTIME_ENV_HELPER" ]]; then
  echo "Cannot find runtime environment helper: $RUNTIME_ENV_HELPER" >&2
  exit 1
fi
source "$RUNTIME_ENV_HELPER"
MANAGED_UNITS=(
  "$UNIT_PREFIX-home-api.service"
  "$UNIT_PREFIX-watchlist-api.service"
  "$UNIT_PREFIX-portfolio-api.service"
  "$UNIT_PREFIX-briefing-api.service"
  "$UNIT_PREFIX-home-web.service"
  "$UNIT_PREFIX-watchlist-web.service"
  "$UNIT_PREFIX-portfolio-web.service"
  "$UNIT_PREFIX-briefing-web.service"
)

DEFAULT_PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$DEFAULT_PYTHON_BIN" ]]; then
  DEFAULT_PYTHON_BIN="$(command -v python3)"
fi
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"

DEFAULT_NODE_BIN="$(command -v node)"
NODE_BIN="${NODE_BIN:-$DEFAULT_NODE_BIN}"

USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

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
if [[ "$RUN_MIGRATIONS" == "true" && ! -x "$PROJECT_ROOT/infra/scripts/audit_live_data.py" ]]; then
  echo "Cannot find executable infra/scripts/audit_live_data.py under PROJECT_ROOT: $PROJECT_ROOT" >&2
  exit 1
fi
if [[ "$RUN_MIGRATIONS" == "true" && ! -f "$PROJECT_ROOT/apps/portfolio/backend/scripts/refresh_release_snapshots.py" ]]; then
  echo "Cannot find Portfolio release snapshot refresh under PROJECT_ROOT: $PROJECT_ROOT" >&2
  exit 1
fi
if [[ "$RUN_MIGRATIONS" == "true" && ! -f "$PROJECT_ROOT/shared-data/scripts/refresh_release_catalogs.py" ]]; then
  echo "Cannot find Data release catalog refresh under PROJECT_ROOT: $PROJECT_ROOT" >&2
  exit 1
fi
if [[ "$RUN_MIGRATIONS" == "true" && ! -f "$PROJECT_ROOT/apps/watchlist/backend/scripts/refresh_release_watchlists.py" ]]; then
  echo "Cannot find Watchlist release directory reconciliation under PROJECT_ROOT: $PROJECT_ROOT" >&2
  exit 1
fi

if [[ -z "$ENV_ROOT" ]]; then
  echo "ENV_ROOT must explicitly name the external runtime environment directory." >&2
  exit 64
fi
investment_studio_reject_repository_env_files "$PROJECT_ROOT"
ENV_ROOT="$(investment_studio_resolve_external_env_root "$PROJECT_ROOT" "$ENV_ROOT")"
HOME_ENV_FILE="$ENV_ROOT/home.env"
DATA_ENV_FILE="$ENV_ROOT/data.env"
WATCHLIST_ENV_FILE="$ENV_ROOT/watchlist.env"
PORTFOLIO_ENV_FILE="$ENV_ROOT/portfolio.env"
BRIEFING_ENV_FILE="$ENV_ROOT/briefing.env"
MARKET_ENV_FILE="$ENV_ROOT/market.env"
investment_studio_validate_env_file \
  "$DATA_ENV_FILE" \
  INVESTMENT_STUDIO_DATA_ \
  INVESTMENT_STUDIO_INSTRUMENT_DATA_ \
  INVESTMENT_STUDIO_AUTH_
investment_studio_validate_env_file "$HOME_ENV_FILE" INVESTMENT_STUDIO_HOME_ INVESTMENT_STUDIO_AUTH_
investment_studio_validate_env_file "$WATCHLIST_ENV_FILE" INVESTMENT_STUDIO_WATCHLIST_ INVESTMENT_STUDIO_AUTH_
investment_studio_validate_env_file "$PORTFOLIO_ENV_FILE" INVESTMENT_STUDIO_PORTFOLIO_ INVESTMENT_STUDIO_AUTH_
investment_studio_validate_env_file "$BRIEFING_ENV_FILE" INVESTMENT_STUDIO_BRIEFING_ INVESTMENT_STUDIO_AUTH_
investment_studio_validate_env_file "$MARKET_ENV_FILE" INVESTMENT_STUDIO_MARKET_

validate_database_contract() {
  unset \
    INVESTMENT_STUDIO_HOME_DATABASE_URL \
    INVESTMENT_STUDIO_DATA_DATABASE_URL \
    INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL \
    INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA \
    INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA \
    INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL \
    INVESTMENT_STUDIO_INSTRUMENT_DATA_ALEMBIC_DATABASE_URL \
    INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL \
    INVESTMENT_STUDIO_PORTFOLIO_ALEMBIC_DATABASE_URL \
    INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL \
    INVESTMENT_STUDIO_WATCHLIST_ALEMBIC_DATABASE_URL \
    INVESTMENT_STUDIO_BRIEFING_DATABASE_URL \
    INVESTMENT_STUDIO_BRIEFING_ALEMBIC_DATABASE_URL \
    INVESTMENT_STUDIO_MARKET_DATABASE_URL
  investment_studio_load_env_file "$HOME_ENV_FILE" INVESTMENT_STUDIO_HOME_ INVESTMENT_STUDIO_AUTH_
  investment_studio_load_env_file \
    "$DATA_ENV_FILE" \
    INVESTMENT_STUDIO_DATA_ \
    INVESTMENT_STUDIO_INSTRUMENT_DATA_ \
  INVESTMENT_STUDIO_AUTH_
  investment_studio_load_env_file "$WATCHLIST_ENV_FILE" INVESTMENT_STUDIO_WATCHLIST_ INVESTMENT_STUDIO_AUTH_
  investment_studio_load_env_file "$PORTFOLIO_ENV_FILE" INVESTMENT_STUDIO_PORTFOLIO_ INVESTMENT_STUDIO_AUTH_
  investment_studio_load_env_file "$BRIEFING_ENV_FILE" INVESTMENT_STUDIO_BRIEFING_ INVESTMENT_STUDIO_AUTH_
  investment_studio_load_env_file "$MARKET_ENV_FILE" INVESTMENT_STUDIO_MARKET_

  local required_variable required_value
  for required_variable in \
    INVESTMENT_STUDIO_HOME_DATABASE_URL \
    INVESTMENT_STUDIO_DATA_DATABASE_URL \
    INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL \
    INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL \
    INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL \
    INVESTMENT_STUDIO_BRIEFING_DATABASE_URL \
    INVESTMENT_STUDIO_MARKET_DATABASE_URL; do
    required_value="${!required_variable-}"
    if [[ -z "$required_value" ]]; then
      echo "External runtime environment is missing $required_variable." >&2
      return 1
    fi
  done

  if [[ -n "${INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA:-}" \
    && "$INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA" != "instrument_data" ]]; then
    echo "INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA must be instrument_data." >&2
    return 1
  fi
  if [[ -n "${INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA:-}" \
    && "$INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA" != "data_ingestion" ]]; then
    echo "INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA must be data_ingestion." >&2
    return 1
  fi

  "$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import os
from urllib.parse import parse_qs, unquote, urlsplit


REQUIRED_URLS = (
    "INVESTMENT_STUDIO_HOME_DATABASE_URL",
    "INVESTMENT_STUDIO_DATA_DATABASE_URL",
    "INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL",
    "INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL",
    "INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL",
    "INVESTMENT_STUDIO_BRIEFING_DATABASE_URL",
    "INVESTMENT_STUDIO_MARKET_DATABASE_URL",
)
OPTIONAL_URLS = (
    "INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL",
    "INVESTMENT_STUDIO_INSTRUMENT_DATA_ALEMBIC_DATABASE_URL",
    "INVESTMENT_STUDIO_PORTFOLIO_ALEMBIC_DATABASE_URL",
    "INVESTMENT_STUDIO_WATCHLIST_ALEMBIC_DATABASE_URL",
    "INVESTMENT_STUDIO_BRIEFING_ALEMBIC_DATABASE_URL",
)


def target(variable_name: str) -> tuple[str, int, str]:
    raw_value = os.environ[variable_name].strip()
    if "\n" in raw_value or "\r" in raw_value:
        raise SystemExit(f"{variable_name} must be a single-line PostgreSQL URL.")
    normalized = raw_value.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    if parsed.scheme != "postgresql" or not parsed.username or parsed.fragment:
        raise SystemExit(f"{variable_name} must be an explicit PostgreSQL URL.")
    if parsed.password is not None:
        raise SystemExit(f"{variable_name} must not contain a password; use a 0600 passfile.")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query.get("password"):
        raise SystemExit(f"{variable_name} must not contain a password query parameter.")
    authority_host = parsed.hostname or ""
    query_hosts = query.get("host", [])
    if authority_host and query_hosts:
        raise SystemExit(f"{variable_name} names its database host more than once.")
    host = unquote(query_hosts[-1] if query_hosts else authority_host)
    if not host:
        raise SystemExit(f"{variable_name} must name a database host or socket directory.")
    query_ports = query.get("port", [])
    if parsed.port is not None and query_ports:
        raise SystemExit(f"{variable_name} names its database port more than once.")
    port_text = query_ports[-1] if query_ports else str(parsed.port or 5432)
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise SystemExit(f"{variable_name} has an invalid database port.")
    database = unquote(parsed.path.removeprefix("/"))
    if not database or "/" in database:
        raise SystemExit(f"{variable_name} must name exactly one database.")
    return host, int(port_text), database


targets = {name: target(name) for name in REQUIRED_URLS}
for name in OPTIONAL_URLS:
    if os.getenv(name, "").strip():
        targets[name] = target(name)
canonical_target = targets[REQUIRED_URLS[0]]
mismatched = sorted(name for name, value in targets.items() if value != canonical_target)
if mismatched:
    raise SystemExit(
        "Every application and migration database URL must target the same PostgreSQL "
        "host, port, and database; mismatched variables: " + ", ".join(mismatched)
    )
print("Validated one password-free canonical PostgreSQL target for all managed services.")
PY
}

validate_database_contract
DATABASE_URL="$INVESTMENT_STUDIO_DATA_DATABASE_URL"
export INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA=instrument_data
export INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA=data_ingestion

INSTALL_WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/investment-studio-systemd-install.XXXXXX")"
chmod 700 "$INSTALL_WORK_DIR"
UNIT_OUTPUT_DIR="$INSTALL_WORK_DIR/staged-units"
UNIT_BACKUP_DIR="$INSTALL_WORK_DIR/previous-units"
ACTIVE_STATE_FILE="$INSTALL_WORK_DIR/active-units"
ENABLED_STATE_FILE="$INSTALL_WORK_DIR/enabled-units"
UNIT_PRESENCE_FILE="$INSTALL_WORK_DIR/unit-presence"
mkdir -p "$UNIT_OUTPUT_DIR" "$UNIT_BACKUP_DIR"
chmod 700 "$UNIT_OUTPUT_DIR" "$UNIT_BACKUP_DIR"
touch "$ACTIVE_STATE_FILE" "$ENABLED_STATE_FILE" "$UNIT_PRESENCE_FILE"
chmod 600 "$ACTIVE_STATE_FILE" "$ENABLED_STATE_FILE" "$UNIT_PRESENCE_FILE"

REFRESH_SERVICE_UNITS=()
REFRESH_TIMER_UNITS=()
for refresh_name in market-data-refresh cn-market-data-refresh hk-market-data-refresh \
  us-market-data-refresh cn-hk-reference-data-refresh us-reference-data-refresh; do
  REFRESH_SERVICE_UNITS+=("$UNIT_PREFIX-$refresh_name.service")
  REFRESH_TIMER_UNITS+=("$UNIT_PREFIX-$refresh_name.timer")
done
for briefing_period in daily weekly; do
  REFRESH_SERVICE_UNITS+=("$UNIT_PREFIX-briefing-$briefing_period.service")
  REFRESH_TIMER_UNITS+=("$UNIT_PREFIX-briefing-$briefing_period.timer")
done
for market_action in daily weekly crypto publish sync registered-prices-cn registered-prices-hk registered-prices-us registered-prices-eu; do
  REFRESH_SERVICE_UNITS+=("$UNIT_PREFIX-market-$market_action.service")
  REFRESH_TIMER_UNITS+=("$UNIT_PREFIX-market-$market_action.timer")
done
WRITER_UNITS=(
  "${REFRESH_TIMER_UNITS[@]}"
  "${REFRESH_SERVICE_UNITS[@]}"
  "${MANAGED_UNITS[@]}"
)

state_captured=false
backup_created=false
database_mutation_started=false
units_published=false
backup_path=""
manifest_path=""

write_api_service() {
  local app="$1"
  local backend_rel="$2"
  local module="$3"
  local port="$4"
  local backend_root="$PROJECT_ROOT/$backend_rel"
  local env_file="$ENV_ROOT/$app.env"
  local service_file="$UNIT_OUTPUT_DIR/$UNIT_PREFIX-$app-api.service"
  local pythonpath_value="$PROJECT_ROOT/packages/identity:$backend_root:$PROJECT_ROOT/shared-data/instruments/python"
  local escaped_env_file schema_environment_lines="" exec_start_prefix="" market_environment_line=""
  escaped_env_file="$(printf '%q' "$env_file")"
  if [[ "$app" == "home" ]]; then
    pythonpath_value="$backend_root"
  fi
  if [[ "$app" == "briefing" || "$app" == "watchlist" || "$app" == "portfolio" ]]; then
    pythonpath_value="$pythonpath_value:$PROJECT_ROOT/shared-data/market"
    market_environment_line="EnvironmentFile=$(printf '%q' "$MARKET_ENV_FILE")"
    schema_environment_lines="Environment=INVESTMENT_STUDIO_SECRET_ROOT=$(printf '%q' "$ENV_ROOT")"
  fi

  cat > "$service_file" <<EOF
[Unit]
Description=Investment Studio $app API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$backend_root
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONPATH=$pythonpath_value
EnvironmentFile=$escaped_env_file
$market_environment_line
$schema_environment_lines
ExecStart=$exec_start_prefix$PYTHON_BIN -m uvicorn $module --host $API_HOST --port $port
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
  chmod 600 "$service_file"
}

write_web_service() {
  local app="$1"
  local frontend_rel="$2"
  local web_port="$3"
  local api_port="$4"
  local dist_root="$PROJECT_ROOT/$frontend_rel/dist"
  local service_file="$UNIT_OUTPUT_DIR/$UNIT_PREFIX-$app-web.service"

  if [[ ! -f "$dist_root/index.html" ]]; then
    echo "Missing frontend build output: $dist_root/index.html" >&2
    exit 1
  fi

  cat > "$service_file" <<EOF
[Unit]
Description=Investment Studio $app Frontend
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
  chmod 600 "$service_file"
}

capture_previous_state() {
  local unit target_file enabled_state
  : > "$ACTIVE_STATE_FILE"
  : > "$ENABLED_STATE_FILE"
  : > "$UNIT_PRESENCE_FILE"
  for unit in "${WRITER_UNITS[@]}"; do
    if systemctl --user is-active --quiet "$unit"; then
      printf '%s\n' "$unit" >> "$ACTIVE_STATE_FILE"
    fi
  done
  for unit in "${MANAGED_UNITS[@]}"; do
    target_file="$USER_SYSTEMD_DIR/$unit"
    enabled_state="$(systemctl --user is-enabled "$unit" 2>/dev/null || true)"
    case "$enabled_state" in
      enabled|enabled-runtime|disabled|masked|masked-runtime|static|indirect|generated|not-found|"") ;;
      *)
        echo "Cannot safely preserve unsupported systemd enablement state for $unit: $enabled_state" >&2
        return 1
        ;;
    esac
    printf '%s\t%s\n' "$unit" "$enabled_state" >> "$ENABLED_STATE_FILE"
    if [[ -f "$target_file" ]]; then
      cp -p "$target_file" "$UNIT_BACKUP_DIR/$unit"
      printf '%s\tpresent\n' "$unit" >> "$UNIT_PRESENCE_FILE"
    else
      printf '%s\tmissing\n' "$unit" >> "$UNIT_PRESENCE_FILE"
    fi
  done
  state_captured=true
}

ensure_writer_units_stopped() {
  local unit stop_failed=false
  for unit in "${WRITER_UNITS[@]}"; do
    if ! systemctl --user stop "$unit" >/dev/null 2>&1; then
      if systemctl --user is-active --quiet "$unit"; then
        echo "Failed to stop database-writing unit: $unit" >&2
        stop_failed=true
      fi
    fi
  done
  for unit in "${WRITER_UNITS[@]}"; do
    if systemctl --user is-active --quiet "$unit"; then
      echo "Database-writing unit remained active after stop: $unit" >&2
      stop_failed=true
    fi
  done
  [[ "$stop_failed" == "false" ]]
}

restore_unit_files_and_enablement() {
  local unit previous_presence enabled_state target_file temporary_file
  while IFS=$'\t' read -r unit previous_presence || [[ -n "$unit" ]]; do
    [[ -n "$unit" ]] || continue
    target_file="$USER_SYSTEMD_DIR/$unit"
    if [[ "$previous_presence" == "present" ]]; then
      temporary_file="$USER_SYSTEMD_DIR/.$unit.rollback.$$"
      install -m 0644 "$UNIT_BACKUP_DIR/$unit" "$temporary_file"
      mv -f "$temporary_file" "$target_file"
    elif [[ "$previous_presence" == "missing" ]]; then
      rm -f "$target_file"
    else
      echo "Invalid prior unit presence state for $unit: $previous_presence" >&2
      return 1
    fi
  done < "$UNIT_PRESENCE_FILE"

  systemctl --user daemon-reload
  while IFS=$'\t' read -r unit enabled_state || [[ -n "$unit" ]]; do
    [[ -n "$unit" ]] || continue
    case "$enabled_state" in
      enabled) systemctl --user enable "$unit" >/dev/null ;;
      enabled-runtime) systemctl --user enable --runtime "$unit" >/dev/null ;;
      disabled|not-found|"") systemctl --user disable "$unit" >/dev/null 2>&1 || true ;;
      masked) systemctl --user mask "$unit" >/dev/null ;;
      masked-runtime) systemctl --user mask --runtime "$unit" >/dev/null ;;
      static|indirect|generated) ;;
      *)
        echo "Invalid prior enablement state for $unit: $enabled_state" >&2
        return 1
        ;;
    esac
  done < "$ENABLED_STATE_FILE"
}

restore_previous_active_units() {
  local unit restore_failed=false
  ensure_writer_units_stopped || return 1
  for unit in "${MANAGED_UNITS[@]}" "${REFRESH_SERVICE_UNITS[@]}" "${REFRESH_TIMER_UNITS[@]}"; do
    if ! grep -Fxq "$unit" "$ACTIVE_STATE_FILE"; then
      continue
    fi
    if ! systemctl --user start "$unit"; then
      echo "Failed to restore previously active systemd unit: $unit" >&2
      restore_failed=true
      break
    fi
  done
  if [[ "$restore_failed" == "true" ]]; then
    ensure_writer_units_stopped || true
    return 1
  fi
}

publish_staged_units() {
  local unit target_file temporary_file
  mkdir -p "$USER_SYSTEMD_DIR"
  units_published=true
  for unit in "${MANAGED_UNITS[@]}"; do
    target_file="$USER_SYSTEMD_DIR/$unit"
    temporary_file="$USER_SYSTEMD_DIR/.$unit.install.$$"
    install -m 0644 "$UNIT_OUTPUT_DIR/$unit" "$temporary_file"
    mv -f "$temporary_file" "$target_file"
  done
}

restore_refresh_writer_state() {
  local unit
  for unit in "${REFRESH_SERVICE_UNITS[@]}" "${REFRESH_TIMER_UNITS[@]}"; do
    if grep -Fxq "$unit" "$ACTIVE_STATE_FILE"; then
      systemctl --user start "$unit"
    fi
  done
}

recover_failed_install() {
  local original_exit_code=$?
  local recovery_failed=false
  trap - EXIT HUP INT TERM
  set +e

  if [[ "$original_exit_code" -ne 0 && "$state_captured" == "true" ]]; then
    echo "Systemd installation failed; restoring the prior database, units, and service state." >&2
    if ! ensure_writer_units_stopped; then
      recovery_failed=true
    fi
    if [[ "$database_mutation_started" == "true" && "$backup_created" == "true" ]]; then
      if [[ "$recovery_failed" == "false" ]] && ! investment_studio_restore_project_schema_backup \
        "$DATABASE_URL" "$backup_path" "$manifest_path"; then
        recovery_failed=true
      fi
    fi
    if [[ "$units_published" == "true" ]] && ! restore_unit_files_and_enablement; then
      recovery_failed=true
    fi
    if [[ "$recovery_failed" == "false" ]] && ! restore_previous_active_units; then
      recovery_failed=true
    fi
  fi

  if [[ "$recovery_failed" == "true" ]]; then
    ensure_writer_units_stopped || true
    echo "Automatic recovery failed; managed writers remain stopped." >&2
    echo "Recovery state retained at: $INSTALL_WORK_DIR" >&2
    exit 1
  fi

  rm -rf "$INSTALL_WORK_DIR"
  exit "$original_exit_code"
}
trap recover_failed_install EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

write_api_service "home" "home/backend" "home_api.main:app" "$HOME_API_PORT"
write_api_service "watchlist" "apps/watchlist/backend" "watchlist_app.main:app" "$WATCHLIST_API_PORT"
write_api_service "portfolio" "apps/portfolio/backend" "portfolio_app.main:app" "$PORTFOLIO_API_PORT"
write_api_service "briefing" "apps/briefing/backend" "briefing_app.main:app" "$BRIEFING_API_PORT"
write_web_service "home" "home/frontend" "$HOME_WEB_PORT" "$HOME_API_PORT"
write_web_service "watchlist" "apps/watchlist/frontend" "$WATCHLIST_WEB_PORT" "$WATCHLIST_API_PORT"
write_web_service "portfolio" "apps/portfolio/frontend" "$PORTFOLIO_WEB_PORT" "$PORTFOLIO_API_PORT"
write_web_service "briefing" "apps/briefing/frontend" "$BRIEFING_WEB_PORT" "$BRIEFING_API_PORT"

capture_previous_state

if [[ "$RUN_MIGRATIONS" == "true" ]]; then
  ensure_writer_units_stopped
  echo "Managed database writers stopped; creating a verified schema backup."
  BACKUP_HELPER="$PROJECT_ROOT/infra/postgres/project_schema_backup.sh"
  if [[ ! -f "$BACKUP_HELPER" ]]; then
    echo "Cannot find project-schema backup helper: $BACKUP_HELPER" >&2
    exit 1
  fi
  source "$BACKUP_HELPER"
  SYSTEMD_BACKUP_ROOT="${INVESTMENT_STUDIO_SYSTEMD_BACKUP_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/investment-studio/postgres-backups}"
  investment_studio_create_project_schema_backup \
    "$DATABASE_URL" "$SYSTEMD_BACKUP_ROOT" "investment-studio-pre-systemd-install"
  backup_path="$INVESTMENT_STUDIO_PROJECT_SCHEMA_BACKUP_PATH"
  manifest_path="$INVESTMENT_STUDIO_PROJECT_SCHEMA_MANIFEST_PATH"
  backup_created=true
  database_mutation_started=true
  echo "Applying release migrations."
  PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN" ENV_ROOT="$ENV_ROOT" \
    "$PROJECT_ROOT/infra/scripts/migrate_all.sh"
  echo "Refreshing release-required FMP security catalogs."
  PYTHONPATH="$PROJECT_ROOT/shared-data:$PROJECT_ROOT/shared-data/instruments/python${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" "$PROJECT_ROOT/shared-data/scripts/refresh_release_catalogs.py"
  echo "Reconciling release-required Watchlist system directories."
  PYTHONPATH="$PROJECT_ROOT/apps/watchlist/backend:$PROJECT_ROOT/packages/identity:$PROJECT_ROOT/shared-data/instruments/python:$PROJECT_ROOT/shared-data/market${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" "$PROJECT_ROOT/apps/watchlist/backend/scripts/refresh_release_watchlists.py"
  echo "Refreshing release-invalidated Portfolio snapshots."
  PYTHONPATH="$PROJECT_ROOT/apps/portfolio/backend:$PROJECT_ROOT/shared-data/instruments/python${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" "$PROJECT_ROOT/apps/portfolio/backend/scripts/refresh_release_snapshots.py" \
      --recover-interrupted
  echo "Running the read-only live-data integrity gate."
  INVESTMENT_STUDIO_LOCAL_DATABASE_URL="$DATABASE_URL" \
    "$PYTHON_BIN" "$PROJECT_ROOT/infra/scripts/audit_live_data.py" --fail-on-warning
fi

publish_staged_units
systemctl --user daemon-reload
systemctl --user enable "${MANAGED_UNITS[@]}"

if [[ "$START_SERVICES" == "true" ]]; then
  systemctl --user restart "${MANAGED_UNITS[@]}"
  for unit in "${MANAGED_UNITS[@]}"; do
    if ! systemctl --user is-active --quiet "$unit"; then
      echo "Managed systemd unit failed its active-state gate: $unit" >&2
      exit 1
    fi
  done
  health_urls=(
    "http://127.0.0.1:$HOME_API_PORT/api/health"
    "http://127.0.0.1:$WATCHLIST_API_PORT/api/health"
    "http://127.0.0.1:$PORTFOLIO_API_PORT/api/health"
    "http://127.0.0.1:$BRIEFING_API_PORT/health"
    "http://127.0.0.1:$HOME_WEB_PORT/"
    "http://127.0.0.1:$WATCHLIST_WEB_PORT/"
    "http://127.0.0.1:$PORTFOLIO_WEB_PORT/"
    "http://127.0.0.1:$BRIEFING_WEB_PORT/"
  )
  healthy=false
  for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
    healthy=true
    for url in "${health_urls[@]}"; do
      if ! curl --noproxy '*' --max-time 2 --fail --silent --output /dev/null "$url"; then
        healthy=false
        break
      fi
    done
    [[ "$healthy" == false ]] || break
    [[ $attempt -eq $HEALTH_ATTEMPTS ]] || sleep 1
  done
  if [[ "$healthy" == false ]]; then
    echo "Managed systemd service failed its readiness gate: $url" >&2
    exit 1
  fi
fi

if [[ "$RUN_MIGRATIONS" == "true" ]]; then
  restore_refresh_writer_state
fi

rm -rf "$INSTALL_WORK_DIR"
trap - EXIT HUP INT TERM

if [[ "$backup_created" == "true" ]]; then
  echo "Safety backup retained at: ${backup_path:-$manifest_path}"
fi
systemctl --user --no-pager --plain status "${MANAGED_UNITS[@]}" || true
