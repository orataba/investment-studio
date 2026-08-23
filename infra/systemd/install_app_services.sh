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
RUNTIME_ENV_HELPER="$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
if [[ ! -f "$RUNTIME_ENV_HELPER" ]]; then
  echo "Cannot find runtime environment helper: $RUNTIME_ENV_HELPER" >&2
  exit 1
fi
source "$RUNTIME_ENV_HELPER"
MANAGED_UNITS=(
  "$UNIT_PREFIX-platform-api.service"
  "$UNIT_PREFIX-watchlist-api.service"
  "$UNIT_PREFIX-portfolio-api.service"
  "$UNIT_PREFIX-platform-web.service"
  "$UNIT_PREFIX-watchlist-web.service"
  "$UNIT_PREFIX-portfolio-web.service"
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
if [[ "$RUN_MIGRATIONS" == "true" && ! -f "$PROJECT_ROOT/apps/platform/backend/scripts/refresh_release_catalogs.py" ]]; then
  echo "Cannot find Platform release catalog refresh under PROJECT_ROOT: $PROJECT_ROOT" >&2
  exit 1
fi

if [[ -z "$ENV_ROOT" ]]; then
  echo "ENV_ROOT must explicitly name the external runtime environment directory." >&2
  exit 64
fi
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
ENV_ROOT="$(portfolio_ops_resolve_external_env_root "$PROJECT_ROOT" "$ENV_ROOT")"
PLATFORM_ENV_FILE="$ENV_ROOT/platform.env"
WATCHLIST_ENV_FILE="$ENV_ROOT/watchlist.env"
PORTFOLIO_ENV_FILE="$ENV_ROOT/portfolio.env"
portfolio_ops_validate_env_file \
  "$PLATFORM_ENV_FILE" \
  PORTFOLIO_OPS_PLATFORM_ \
  PORTFOLIO_OPS_INSTRUMENT_REGISTRY_
portfolio_ops_validate_env_file "$WATCHLIST_ENV_FILE" PORTFOLIO_OPS_WATCHLIST_
portfolio_ops_validate_env_file "$PORTFOLIO_ENV_FILE" PORTFOLIO_OPS_PORTFOLIO_

validate_database_contract() {
  unset \
    PORTFOLIO_OPS_PLATFORM_DATABASE_URL \
    PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL \
    PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA \
    PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA \
    PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL \
    PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL \
    PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL \
    PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL \
    PORTFOLIO_OPS_WATCHLIST_DATABASE_URL \
    PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL
  portfolio_ops_load_env_file \
    "$PLATFORM_ENV_FILE" \
    PORTFOLIO_OPS_PLATFORM_ \
    PORTFOLIO_OPS_INSTRUMENT_REGISTRY_
  portfolio_ops_load_env_file "$WATCHLIST_ENV_FILE" PORTFOLIO_OPS_WATCHLIST_
  portfolio_ops_load_env_file "$PORTFOLIO_ENV_FILE" PORTFOLIO_OPS_PORTFOLIO_

  local required_variable required_value
  for required_variable in \
    PORTFOLIO_OPS_PLATFORM_DATABASE_URL \
    PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL \
    PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL \
    PORTFOLIO_OPS_WATCHLIST_DATABASE_URL; do
    required_value="${!required_variable-}"
    if [[ -z "$required_value" ]]; then
      echo "External runtime environment is missing $required_variable." >&2
      return 1
    fi
  done

  if [[ -n "${PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA:-}" \
    && "$PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA" != "instrument_registry" ]]; then
    echo "PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA must be instrument_registry." >&2
    return 1
  fi
  if [[ -n "${PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA:-}" \
    && "$PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA" != "platform" ]]; then
    echo "PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA must be platform." >&2
    return 1
  fi

  "$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import os
from urllib.parse import parse_qs, unquote, urlsplit


REQUIRED_URLS = (
    "PORTFOLIO_OPS_PLATFORM_DATABASE_URL",
    "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL",
    "PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL",
    "PORTFOLIO_OPS_WATCHLIST_DATABASE_URL",
)
OPTIONAL_URLS = (
    "PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL",
    "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL",
    "PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL",
    "PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL",
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
DATABASE_URL="$PORTFOLIO_OPS_PLATFORM_DATABASE_URL"
export PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry
export PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=platform

INSTALL_WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-systemd-install.XXXXXX")"
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

REFRESH_SERVICE_UNIT="$UNIT_PREFIX-market-data-refresh.service"
REFRESH_TIMER_UNIT="$UNIT_PREFIX-market-data-refresh.timer"
WRITER_UNITS=(
  "$REFRESH_TIMER_UNIT"
  "$REFRESH_SERVICE_UNIT"
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
  local pythonpath_value="$backend_root:$PROJECT_ROOT/packages/instrument-core/python"
  local escaped_env_file schema_environment_lines="" exec_start_prefix=""
  escaped_env_file="$(printf '%q' "$env_file")"
  if [[ "$app" == "platform" ]]; then
    schema_environment_lines=$'Environment=PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry\nEnvironment=PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=platform'
    exec_start_prefix='/usr/bin/env PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=platform '
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
EnvironmentFile=$escaped_env_file
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
  for unit in "${MANAGED_UNITS[@]}" "$REFRESH_SERVICE_UNIT" "$REFRESH_TIMER_UNIT"; do
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
  for unit in "$REFRESH_SERVICE_UNIT" "$REFRESH_TIMER_UNIT"; do
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
      if [[ "$recovery_failed" == "false" ]] && ! portfolio_ops_restore_project_schema_backup \
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

write_api_service "platform" "apps/platform/backend" "platform_app.main:app" "$PLATFORM_API_PORT"
write_api_service "watchlist" "apps/watchlist/backend" "watchlist_app.main:app" "$WATCHLIST_API_PORT"
write_api_service "portfolio" "apps/portfolio/backend" "portfolio_app.main:app" "$PORTFOLIO_API_PORT"
write_web_service "platform" "apps/platform/frontend" "$PLATFORM_WEB_PORT" "$PLATFORM_API_PORT"
write_web_service "watchlist" "apps/watchlist/frontend" "$WATCHLIST_WEB_PORT" "$WATCHLIST_API_PORT"
write_web_service "portfolio" "apps/portfolio/frontend" "$PORTFOLIO_WEB_PORT" "$PORTFOLIO_API_PORT"

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
  SYSTEMD_BACKUP_ROOT="${PORTFOLIO_OPS_SYSTEMD_BACKUP_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/portfolio-operations-workbench/postgres-backups}"
  portfolio_ops_create_project_schema_backup \
    "$DATABASE_URL" "$SYSTEMD_BACKUP_ROOT" "portfolio-ops-pre-systemd-install"
  backup_path="$PORTFOLIO_OPS_PROJECT_SCHEMA_BACKUP_PATH"
  manifest_path="$PORTFOLIO_OPS_PROJECT_SCHEMA_MANIFEST_PATH"
  backup_created=true
  database_mutation_started=true
  echo "Applying release migrations."
  PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN" ENV_ROOT="$ENV_ROOT" \
    "$PROJECT_ROOT/infra/scripts/migrate_all.sh"
  echo "Refreshing release-required FMP security catalogs."
  PYTHONPATH="$PROJECT_ROOT/apps/platform/backend:$PROJECT_ROOT/packages/instrument-core/python${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" "$PROJECT_ROOT/apps/platform/backend/scripts/refresh_release_catalogs.py"
  echo "Refreshing release-invalidated Portfolio snapshots."
  PYTHONPATH="$PROJECT_ROOT/apps/portfolio/backend:$PROJECT_ROOT/packages/instrument-core/python${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" "$PROJECT_ROOT/apps/portfolio/backend/scripts/refresh_release_snapshots.py"
  echo "Running the read-only live-data integrity gate."
  PORTFOLIO_OPS_LOCAL_DATABASE_URL="$DATABASE_URL" \
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
