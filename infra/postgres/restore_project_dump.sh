#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DUMP_PATH="${1:-$PROJECT_ROOT/data/migration/portfolio_ops_2026-07-09_current.pgdump}"
CHECKSUM_PATH="${PORTFOLIO_OPS_DUMP_CHECKSUM_PATH:-${DUMP_PATH%.pgdump}.sha256}"
DATABASE_HOST="${PORTFOLIO_OPS_DB_HOST:-127.0.0.1}"
DATABASE_PORT="${PORTFOLIO_OPS_DB_PORT:-5432}"
DATABASE_NAME="${PORTFOLIO_OPS_DB_NAME:-portfolio_ops}"
DATABASE_USER="${PORTFOLIO_OPS_DB_USER:-portfolio_ops}"
DATABASE_PASSWORD="${PORTFOLIO_OPS_DB_PASSWORD:-portfolio_ops}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
MIGRATION_RUNNER="${PORTFOLIO_OPS_RESTORE_MIGRATION_RUNNER:-$PROJECT_ROOT/infra/scripts/migrate_all.sh}"
POST_MIGRATION_GATE="${PORTFOLIO_OPS_RESTORE_POST_MIGRATION_GATE:-$PROJECT_ROOT/infra/scripts/post_migration_gate.sh}"
RESTORE_AS_OF_DATE="${PORTFOLIO_OPS_RESTORE_AS_OF_DATE:-}"
BACKUP_ROOT="${PORTFOLIO_OPS_RESTORE_BACKUP_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/portfolio-operations-workbench/postgres-backups}"
SERVICE_MANAGER_REQUESTED="${PORTFOLIO_OPS_RESTORE_SERVICE_MANAGER:-auto}"
ALLOW_UNVERIFIED_RESTORE="${ALLOW_UNVERIFIED_RESTORE:-false}"
ALLOW_REMOTE_RESTORE="${ALLOW_REMOTE_RESTORE:-false}"
ALLOW_ACTIVE_CONNECTIONS="${ALLOW_ACTIVE_CONNECTIONS:-false}"
RESTORE_HEALTH_ATTEMPTS="${PORTFOLIO_OPS_RESTORE_HEALTH_ATTEMPTS:-30}"

PROJECT_SCHEMAS=(instrument_registry calculation_registry portfolio watchlist)
REQUIRED_INCOMING_SCHEMAS=(instrument_registry portfolio watchlist)
SYSTEMD_UNIT_PREFIX="${UNIT_PREFIX:-portfolio-ops}"
LABEL_PREFIX="${LABEL_PREFIX:-com.orataba.portfolio-ops}"
source "$PROJECT_ROOT/infra/service_inventory.sh"
source "$PROJECT_ROOT/infra/scripts/runtime_readiness.sh"
SYSTEMD_UNITS=()
for unit_suffix in "${PORTFOLIO_OPS_SYSTEMD_MANAGED_UNIT_SUFFIXES[@]}"; do
  SYSTEMD_UNITS+=("$SYSTEMD_UNIT_PREFIX-$unit_suffix")
done

PSQL_BIN=""
PG_DUMP_BIN=""
PG_RESTORE_BIN=""
WORK_DIR=""
SERVICE_STATE_FILE=""
ACTIVE_SERVICE_MANAGER="none"
services_may_need_restart="false"
destructive_started="false"
database_ready="false"
backup_complete="false"
backup_has_schemas="false"
BACKUP_PATH=""
BACKUP_MANIFEST_PATH=""
BACKUP_REFERENCE_PATH=""

find_postgres_binary() {
  local binary="$1"
  if command -v "$binary" >/dev/null 2>&1; then
    command -v "$binary"
    return
  fi
  if command -v brew >/dev/null 2>&1; then
    local formula prefix
    for formula in postgresql@18 postgresql@17 postgresql@16 postgresql; do
      if prefix="$(brew --prefix "$formula" 2>/dev/null)" && [[ -x "$prefix/bin/$binary" ]]; then
        printf '%s\n' "$prefix/bin/$binary"
        return
      fi
    done
  fi
  return 1
}

is_local_database_host() {
  case "$DATABASE_HOST" in
    127.0.0.1|localhost|::1|/*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

resolve_service_manager() {
  case "$SERVICE_MANAGER_REQUESTED" in
    none)
      ACTIVE_SERVICE_MANAGER="none"
      ;;
    launchd)
      ACTIVE_SERVICE_MANAGER="launchd"
      ;;
    systemd)
      ACTIVE_SERVICE_MANAGER="systemd"
      ;;
    auto)
      if [[ "$(uname -s)" == "Darwin" ]] && command -v launchctl >/dev/null 2>&1; then
        ACTIVE_SERVICE_MANAGER="launchd"
      elif command -v systemctl >/dev/null 2>&1 \
        && systemctl --user show-environment >/dev/null 2>&1; then
        ACTIVE_SERVICE_MANAGER="systemd"
      else
        ACTIVE_SERVICE_MANAGER="none"
      fi
      ;;
    *)
      echo "Invalid PORTFOLIO_OPS_RESTORE_SERVICE_MANAGER: $SERVICE_MANAGER_REQUESTED" >&2
      exit 64
      ;;
  esac
}

stop_managed_services() {
  local unit
  local active_units=()
  : > "$SERVICE_STATE_FILE"
  chmod 600 "$SERVICE_STATE_FILE"

  case "$ACTIVE_SERVICE_MANAGER" in
    none)
      echo "No managed local app services detected; continuing without service control."
      ;;
    launchd)
      local launchd_helper="$PROJECT_ROOT/infra/launchd/control_local_services.sh"
      if [[ ! -x "$launchd_helper" ]]; then
        echo "Missing launchd service-control helper: $launchd_helper" >&2
        return 1
      fi
      "$launchd_helper" stop "$SERVICE_STATE_FILE"
      ;;
    systemd)
      for unit in "${SYSTEMD_UNITS[@]}"; do
        if systemctl --user is-active --quiet "$unit"; then
          printf '%s\n' "$unit" >> "$SERVICE_STATE_FILE"
          active_units+=("$unit")
        fi
      done
      if [[ ${#active_units[@]} -gt 0 ]]; then
        systemctl --user stop "${active_units[@]}"
      fi
      ;;
  esac
}

is_managed_systemd_unit() {
  local candidate="$1"
  local managed_unit
  for managed_unit in "${SYSTEMD_UNITS[@]}"; do
    if [[ "$candidate" == "$managed_unit" ]]; then
      return 0
    fi
  done
  return 1
}

start_managed_services() {
  local unit
  local active_units=()

  case "$ACTIVE_SERVICE_MANAGER" in
    none)
      return 0
      ;;
    launchd)
      "$PROJECT_ROOT/infra/launchd/control_local_services.sh" start "$SERVICE_STATE_FILE"
      ;;
    systemd)
      [[ -f "$SERVICE_STATE_FILE" ]] || return 0
      while IFS= read -r unit || [[ -n "$unit" ]]; do
        [[ -n "$unit" ]] || continue
        if ! is_managed_systemd_unit "$unit"; then
          echo "Invalid systemd unit in restore state: $unit" >&2
          return 1
        fi
        if [[ "$unit" == "$SYSTEMD_UNIT_PREFIX-market-data-refresh.service" ]]; then
          echo "The in-flight market-data refresh was fenced and was not replayed automatically."
          continue
        fi
        active_units+=("$unit")
      done < "$SERVICE_STATE_FILE"
      if [[ ${#active_units[@]} -gt 0 ]]; then
        systemctl --user start "${active_units[@]}"
      fi
      ;;
  esac
}

is_managed_launchd_service() {
  local candidate="$1"
  local service
  for service in "${PORTFOLIO_OPS_ALL_SERVICE_NAMES[@]}"; do
    if [[ "$candidate" == "$service" ]]; then
      return 0
    fi
  done
  return 1
}

stop_started_managed_services() {
  local item
  local stop_status=0

  [[ -f "$SERVICE_STATE_FILE" ]] || return 0
  case "$ACTIVE_SERVICE_MANAGER" in
    none)
      return 0
      ;;
    launchd)
      while IFS= read -r item || [[ -n "$item" ]]; do
        [[ -n "$item" ]] || continue
        if ! is_managed_launchd_service "$item"; then
          echo "Invalid launchd service in restore state: $item" >&2
          stop_status=1
          continue
        fi
        launchctl bootout "gui/$UID/$LABEL_PREFIX.$item" \
          >/dev/null 2>&1 || stop_status=1
      done < "$SERVICE_STATE_FILE"
      ;;
    systemd)
      while IFS= read -r item || [[ -n "$item" ]]; do
        [[ -n "$item" ]] || continue
        if ! is_managed_systemd_unit "$item"; then
          echo "Invalid systemd unit in restore state: $item" >&2
          stop_status=1
        fi
      done < "$SERVICE_STATE_FILE"
      if ! systemctl --user stop "${SYSTEMD_UNITS[@]}"; then
        stop_status=1
      fi
      ;;
  esac
  return "$stop_status"
}

drop_project_schemas() {
  "$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" \
    --no-password \
    --set ON_ERROR_STOP=1 \
    --single-transaction \
    --command '
      DROP SCHEMA IF EXISTS watchlist CASCADE;
      DROP SCHEMA IF EXISTS portfolio CASCADE;
      DROP SCHEMA IF EXISTS calculation_registry CASCADE;
      DROP SCHEMA IF EXISTS instrument_registry CASCADE;
    '
}

rollback_database() {
  local rollback_status=0
  if [[ "$backup_has_schemas" == "true" ]]; then
    echo "Restore failed; rolling back the project schemas from $BACKUP_PATH." >&2
  else
    echo "Restore failed; returning the project schemas to their previous empty state." >&2
  fi

  drop_project_schemas || rollback_status=1
  if [[ "$backup_has_schemas" == "true" && -f "$BACKUP_PATH" ]]; then
    "$PG_RESTORE_BIN" \
      --exit-on-error \
      --single-transaction \
      --no-owner \
      --no-acl \
      "${PG_RESTORE_CONNECTION_ARGS[@]}" \
      "$BACKUP_PATH" || rollback_status=1
  fi

  if [[ $rollback_status -eq 0 ]]; then
    echo "Previous project schemas were restored successfully." >&2
  else
    echo "Automatic rollback failed. Preserve this recovery artifact: $BACKUP_REFERENCE_PATH" >&2
  fi
  return "$rollback_status"
}

cleanup_on_exit() {
  local status=$?
  local rollback_failed="false"
  local restart_failed="false"

  trap - EXIT HUP INT TERM
  set +e

  if [[ $status -ne 0 && "$destructive_started" == "true" && "$database_ready" != "true" ]]; then
    if ! rollback_database; then
      rollback_failed="true"
    fi
  fi

  if [[ "$services_may_need_restart" == "true" && "$rollback_failed" != "true" ]]; then
    if ! start_managed_services; then
      restart_failed="true"
      echo "Failed to restart one or more previously managed local services." >&2
    fi
  elif [[ "$services_may_need_restart" == "true" && "$rollback_failed" == "true" ]]; then
    echo "Managed services remain stopped because database rollback did not complete." >&2
  fi

  if [[ "$backup_complete" != "true" ]]; then
    rm -f "$BACKUP_PATH" "$BACKUP_MANIFEST_PATH"
  fi
  [[ -n "$WORK_DIR" ]] && rm -rf "$WORK_DIR"

  if [[ "$rollback_failed" == "true" ]]; then
    status=70
  elif [[ "$restart_failed" == "true" && $status -eq 0 ]]; then
    status=1
  fi
  exit "$status"
}

trap cleanup_on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ ! "$DATABASE_PORT" =~ ^[0-9]+$ ]] || [[ "$DATABASE_PORT" -lt 1 || "$DATABASE_PORT" -gt 65535 ]]; then
  echo "Invalid database port: $DATABASE_PORT" >&2
  exit 64
fi

expected_confirmation="$DATABASE_NAME"
if ! is_local_database_host; then
  if [[ "$ALLOW_REMOTE_RESTORE" != "true" ]]; then
    echo "Refusing destructive restore to non-local host $DATABASE_HOST." >&2
    echo "Set ALLOW_REMOTE_RESTORE=true only after independently verifying the target." >&2
    exit 64
  fi
  expected_confirmation="$DATABASE_NAME@$DATABASE_HOST:$DATABASE_PORT"
fi
if [[ "${CONFIRM_RESTORE:-}" != "$expected_confirmation" ]]; then
  echo "Restore replaces the instrument_registry, calculation_registry, portfolio, and watchlist schemas." >&2
  echo "Verified target: $DATABASE_USER@$DATABASE_HOST:$DATABASE_PORT/$DATABASE_NAME" >&2
  echo "Re-run with CONFIRM_RESTORE=$expected_confirmation after confirming the target." >&2
  exit 64
fi

if [[ ! -f "$DUMP_PATH" ]]; then
  echo "Dump not found: $DUMP_PATH" >&2
  exit 1
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "PYTHON_BIN is not executable: $PYTHON_BIN" >&2
  exit 1
fi
if [[ ! -x "$MIGRATION_RUNNER" ]]; then
  echo "Migration runner is not executable: $MIGRATION_RUNNER" >&2
  exit 1
fi
if [[ ! -x "$POST_MIGRATION_GATE" ]]; then
  echo "Post-migration gate is not executable: $POST_MIGRATION_GATE" >&2
  exit 1
fi
if [[ -z "$RESTORE_AS_OF_DATE" ]]; then
  echo "Set PORTFOLIO_OPS_RESTORE_AS_OF_DATE to an explicit YYYY-MM-DD." >&2
  exit 64
fi

PSQL_BIN="$(find_postgres_binary psql || true)"
PG_DUMP_BIN="$(find_postgres_binary pg_dump || true)"
PG_RESTORE_BIN="$(find_postgres_binary pg_restore || true)"
if [[ -z "$PSQL_BIN" || -z "$PG_DUMP_BIN" || -z "$PG_RESTORE_BIN" ]]; then
  echo "PostgreSQL psql, pg_dump, and pg_restore are required for safe restore." >&2
  exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-restore.XXXXXX")"
chmod 700 "$WORK_DIR"
SERVICE_STATE_FILE="$WORK_DIR/service-state"
DUMP_LIST_PATH="$WORK_DIR/incoming-dump.list"

if [[ -f "$CHECKSUM_PATH" ]]; then
  expected_checksum="$(awk 'NF { print $1; exit }' "$CHECKSUM_PATH")"
  if [[ ! "$expected_checksum" =~ ^[0-9A-Fa-f]{64}$ ]]; then
    echo "Invalid SHA-256 checksum file: $CHECKSUM_PATH" >&2
    exit 1
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    actual_checksum="$(sha256sum "$DUMP_PATH" | awk '{ print $1 }')"
  else
    actual_checksum="$(shasum -a 256 "$DUMP_PATH" | awk '{ print $1 }')"
  fi
  if [[ "$actual_checksum" != "$expected_checksum" ]]; then
    echo "SHA-256 mismatch for dump: $DUMP_PATH" >&2
    exit 1
  fi
  echo "Verified SHA-256 checksum for $DUMP_PATH"
elif [[ "$ALLOW_UNVERIFIED_RESTORE" == "true" ]]; then
  echo "WARNING: restoring without a checksum because ALLOW_UNVERIFIED_RESTORE=true." >&2
else
  echo "Checksum file not found: $CHECKSUM_PATH" >&2
  echo "Set ALLOW_UNVERIFIED_RESTORE=true only for a separately verified dump." >&2
  exit 1
fi

"$PG_RESTORE_BIN" --list "$DUMP_PATH" > "$DUMP_LIST_PATH"
INCOMING_SCHEMAS=(instrument_registry portfolio watchlist)
for schema in "${REQUIRED_INCOMING_SCHEMAS[@]}"; do
  if ! grep -Eq "^[0-9]+; [0-9]+ [0-9]+ SCHEMA - ${schema} " "$DUMP_LIST_PATH"; then
    echo "Incoming dump is missing required schema: $schema" >&2
    exit 1
  fi
done
if grep -Eq "^[0-9]+; [0-9]+ [0-9]+ SCHEMA - calculation_registry " "$DUMP_LIST_PATH"; then
  INCOMING_SCHEMAS=(instrument_registry calculation_registry portfolio watchlist)
fi

export PGPASSWORD="$DATABASE_PASSWORD"
PSQL_CONNECTION_ARGS=(
  --host "$DATABASE_HOST"
  --port "$DATABASE_PORT"
  --username "$DATABASE_USER"
  --dbname "$DATABASE_NAME"
)
PG_RESTORE_CONNECTION_ARGS=(
  --host "$DATABASE_HOST"
  --port "$DATABASE_PORT"
  --username "$DATABASE_USER"
  --dbname "$DATABASE_NAME"
)

target_identity="$(
  "$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" \
    --no-password \
    --set ON_ERROR_STOP=1 \
    --tuples-only \
    --no-align \
    --command "SELECT current_database() || '|' || current_user"
)"
if [[ "${target_identity%%|*}" != "$DATABASE_NAME" ]]; then
  echo "Connected database identity does not match requested target: $target_identity" >&2
  exit 1
fi
echo "Validated restore target: $target_identity at $DATABASE_HOST:$DATABASE_PORT"

DATABASE_URL="$(
  DB_URL_USER="$DATABASE_USER" \
  DB_URL_PASSWORD="$DATABASE_PASSWORD" \
  DB_URL_HOST="$DATABASE_HOST" \
  DB_URL_PORT="$DATABASE_PORT" \
  DB_URL_DATABASE="$DATABASE_NAME" \
    "$PYTHON_BIN" - <<'PY'
import os
from urllib.parse import quote

user = quote(os.environ["DB_URL_USER"], safe="")
password = quote(os.environ["DB_URL_PASSWORD"], safe="")
host = os.environ["DB_URL_HOST"]
port = os.environ["DB_URL_PORT"]
database = quote(os.environ["DB_URL_DATABASE"], safe="")

if host.startswith("/"):
    print(
        f"postgresql+psycopg://{user}:{password}@/{database}"
        f"?host={quote(host, safe='')}&port={quote(port, safe='')}"
    )
else:
    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    print(f"postgresql+psycopg://{user}:{password}@{rendered_host}:{port}/{database}")
PY
)"

mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
safe_database_name="$(printf '%s' "$DATABASE_NAME" | tr -c 'A-Za-z0-9_.-' '_')"
backup_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_PATH="$BACKUP_ROOT/${safe_database_name}-pre-restore-${backup_timestamp}-$$.pgdump"
BACKUP_MANIFEST_PATH="$BACKUP_PATH.schemas"

resolve_service_manager
services_may_need_restart="true"
stop_managed_services

"$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" \
  --no-password \
  --set ON_ERROR_STOP=1 \
  --command "
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND backend_type = 'client backend'
      AND pid <> pg_backend_pid();
  " >/dev/null

remaining_connections="$(
  "$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" \
    --no-password \
    --set ON_ERROR_STOP=1 \
    --tuples-only \
    --no-align \
    --command "
      SELECT count(*)
      FROM pg_stat_activity
      WHERE datname = current_database()
        AND backend_type = 'client backend'
        AND pid <> pg_backend_pid();
    "
)"
if [[ "$remaining_connections" != "0" && "$ALLOW_ACTIVE_CONNECTIONS" != "true" ]]; then
  echo "Refusing restore while $remaining_connections other database connection(s) remain active." >&2
  echo "Stop them, or set ALLOW_ACTIVE_CONNECTIONS=true after assessing the write risk." >&2
  exit 1
fi

existing_schemas=()
EXISTING_SCHEMAS_PATH="$WORK_DIR/existing-schemas"
"$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" \
  --no-password \
  --set ON_ERROR_STOP=1 \
  --tuples-only \
  --no-align \
  --command "
    SELECT nspname
    FROM pg_namespace
    WHERE nspname IN ('instrument_registry', 'calculation_registry', 'portfolio', 'watchlist')
    ORDER BY CASE nspname
      WHEN 'instrument_registry' THEN 1
      WHEN 'calculation_registry' THEN 2
      WHEN 'portfolio' THEN 3
      WHEN 'watchlist' THEN 4
    END;
  " > "$EXISTING_SCHEMAS_PATH"
while IFS= read -r schema || [[ -n "$schema" ]]; do
  [[ -n "$schema" ]] || continue
  existing_schemas+=("$schema")
done < "$EXISTING_SCHEMAS_PATH"

: > "$BACKUP_MANIFEST_PATH"
chmod 600 "$BACKUP_MANIFEST_PATH"
if [[ ${#existing_schemas[@]} -gt 0 ]]; then
  backup_schema_args=()
  for schema in "${existing_schemas[@]}"; do
    printf '%s\n' "$schema" >> "$BACKUP_MANIFEST_PATH"
    backup_schema_args+=(--schema="$schema")
  done
  "$PG_DUMP_BIN" \
    --format=custom \
    --no-owner \
    --no-acl \
    "${PSQL_CONNECTION_ARGS[@]}" \
    "${backup_schema_args[@]}" \
    --file "$BACKUP_PATH"
  chmod 600 "$BACKUP_PATH"
  "$PG_RESTORE_BIN" --list "$BACKUP_PATH" >/dev/null
  backup_has_schemas="true"
  BACKUP_REFERENCE_PATH="$BACKUP_PATH"
else
  printf '%s\n' '# No project schemas existed before restore.' > "$BACKUP_MANIFEST_PATH"
  BACKUP_REFERENCE_PATH="$BACKUP_MANIFEST_PATH"
fi
backup_complete="true"
echo "Pre-restore backup completed: $BACKUP_REFERENCE_PATH"

destructive_started="true"
drop_project_schemas
"$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" \
  --no-password \
  --set ON_ERROR_STOP=1 \
  --single-transaction \
  --command '
    CREATE SCHEMA instrument_registry;
    CREATE SCHEMA calculation_registry;
    CREATE SCHEMA portfolio;
    CREATE SCHEMA watchlist;
  '

restore_schema_args=()
for schema in "${INCOMING_SCHEMAS[@]}"; do
  restore_schema_args+=(--schema="$schema")
done
"$PG_RESTORE_BIN" \
  --exit-on-error \
  --single-transaction \
  --no-owner \
  --no-acl \
  "${PG_RESTORE_CONNECTION_ARGS[@]}" \
  "${restore_schema_args[@]}" \
  "$DUMP_PATH"

export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA=instrument_registry
export PORTFOLIO_OPS_CALCULATION_REGISTRY_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_CALCULATION_REGISTRY_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_CALCULATION_REGISTRY_SCHEMA=calculation_registry
export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA=portfolio
export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA=watchlist
export PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE="$DATABASE_NAME"
export PORTFOLIO_OPS_LOCAL_DATABASE_URL="$DATABASE_URL"

PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN" ENV_ROOT="" \
  "$MIGRATION_RUNNER"

restore_gate_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
export PORTFOLIO_OPS_RELEASE_AUDIT_OUTPUT_PATH="$BACKUP_ROOT/${DATABASE_NAME}-restore-audit-${restore_gate_timestamp}-$$.json"
PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN" \
  "$POST_MIGRATION_GATE" "$RESTORE_AS_OF_DATE"

schema_count="$(
  "$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" \
    --no-password \
    --set ON_ERROR_STOP=1 \
    --tuples-only \
    --no-align \
    --command "
      SELECT count(*)
      FROM pg_namespace
      WHERE nspname IN ('instrument_registry', 'calculation_registry', 'portfolio', 'watchlist');
    "
)"
if [[ "$schema_count" != "4" ]]; then
  echo "Post-restore validation failed: expected 4 project schemas, found $schema_count." >&2
  exit 1
fi

database_ready="true"
if ! start_managed_services; then
  services_may_need_restart="false"
  if ! stop_started_managed_services; then
    echo "Service restart failed and one or more partially started services could not be stopped." >&2
  fi
  echo "Database restore succeeded, but managed services did not restart cleanly." >&2
  exit 1
fi
runtime_gate_failed="false"
if ! portfolio_ops_wait_for_service_state_readiness \
  "$SERVICE_STATE_FILE" "$SYSTEMD_UNIT_PREFIX" "$RESTORE_HEALTH_ATTEMPTS"; then
  runtime_gate_failed="true"
elif ! portfolio_ops_verify_portfolio_read_contract_for_service_state \
  "$SERVICE_STATE_FILE" "$SYSTEMD_UNIT_PREFIX" "$PYTHON_BIN"; then
  runtime_gate_failed="true"
fi
if [[ "$runtime_gate_failed" == "true" ]]; then
  services_may_need_restart="false"
  if ! stop_started_managed_services; then
    echo "Runtime validation failed and one or more restarted services could not be stopped." >&2
  fi
  echo "Database restore succeeded, but restarted services failed runtime validation and were stopped." >&2
  exit 1
fi
services_may_need_restart="false"

echo "Project schemas were restored and upgraded successfully."
echo "Safety backup retained at: $BACKUP_REFERENCE_PATH"
