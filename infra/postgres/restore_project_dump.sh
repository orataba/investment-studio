#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BACKUP_HELPER="$PROJECT_ROOT/infra/postgres/project_schema_backup.sh"
if [[ ! -f "$BACKUP_HELPER" ]]; then
  echo "Missing project-schema backup helper: $BACKUP_HELPER" >&2
  exit 1
fi
source "$BACKUP_HELPER"
if [[ $# -ne 1 || -z "${1:-}" ]]; then
  echo "Usage: PORTFOLIO_OPS_LOCAL_DATABASE_URL=postgresql://user@host/database $0 /absolute/path/to/portfolio-operations-workbench.pgdump" >&2
  exit 2
fi
DUMP_PATH="$1"
if [[ "$DUMP_PATH" != /* ]]; then
  echo "Restore dump path must be absolute: $DUMP_PATH" >&2
  exit 2
fi
CHECKSUM_PATH="${PORTFOLIO_OPS_DUMP_CHECKSUM_PATH:-${DUMP_PATH%.pgdump}.sha256}"
DATABASE_URL="${PORTFOLIO_OPS_LOCAL_DATABASE_URL:-}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
MIGRATION_RUNNER="${PORTFOLIO_OPS_RESTORE_MIGRATION_RUNNER:-$PROJECT_ROOT/infra/scripts/migrate_all.sh}"
BACKUP_ROOT="${PORTFOLIO_OPS_RESTORE_BACKUP_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/portfolio-operations-workbench/postgres-backups}"
SERVICE_MANAGER_REQUESTED="${PORTFOLIO_OPS_RESTORE_SERVICE_MANAGER:-auto}"
ALLOW_REMOTE_RESTORE="${ALLOW_REMOTE_RESTORE:-false}"

SYSTEMD_UNIT_PREFIX="${UNIT_PREFIX:-portfolio-ops}"
SYSTEMD_UNITS=(
  "$SYSTEMD_UNIT_PREFIX-platform-api.service"
  "$SYSTEMD_UNIT_PREFIX-watchlist-api.service"
  "$SYSTEMD_UNIT_PREFIX-portfolio-api.service"
  "$SYSTEMD_UNIT_PREFIX-platform-web.service"
  "$SYSTEMD_UNIT_PREFIX-watchlist-web.service"
  "$SYSTEMD_UNIT_PREFIX-portfolio-web.service"
  "$SYSTEMD_UNIT_PREFIX-market-data-refresh.timer"
  "$SYSTEMD_UNIT_PREFIX-market-data-refresh.service"
)

PSQL_BIN=""
PG_RESTORE_BIN=""
LIBPQ_DATABASE_URL=""
LIBPQ_PASSFILE=""
DATABASE_HOST=""
DATABASE_PORT=""
DATABASE_NAME=""
DATABASE_USER=""
WORK_DIR=""
SERVICE_STATE_FILE=""
ACTIVE_SERVICE_MANAGER="none"
services_may_need_restart="false"
destructive_started="false"
database_ready="false"
backup_ready="false"
BACKUP_REFERENCE_PATH=""

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
        case "$unit" in
          "$SYSTEMD_UNIT_PREFIX"-platform-api.service|\
          "$SYSTEMD_UNIT_PREFIX"-watchlist-api.service|\
          "$SYSTEMD_UNIT_PREFIX"-portfolio-api.service|\
          "$SYSTEMD_UNIT_PREFIX"-platform-web.service|\
          "$SYSTEMD_UNIT_PREFIX"-watchlist-web.service|\
          "$SYSTEMD_UNIT_PREFIX"-portfolio-web.service|\
          "$SYSTEMD_UNIT_PREFIX"-market-data-refresh.timer|\
          "$SYSTEMD_UNIT_PREFIX"-market-data-refresh.service)
            active_units+=("$unit")
            ;;
          *)
            echo "Invalid systemd unit in restore state: $unit" >&2
            return 1
            ;;
        esac
      done < "$SERVICE_STATE_FILE"
      if [[ ${#active_units[@]} -gt 0 ]]; then
        systemctl --user start "${active_units[@]}"
      fi
      ;;
  esac
}

stop_all_managed_services() {
  local unit service
  case "$ACTIVE_SERVICE_MANAGER" in
    none)
      return 0
      ;;
    launchd)
      for service in \
        platform-api watchlist-api portfolio-api \
        platform-web watchlist-web portfolio-web market-data-refresh; do
        launchctl bootout \
          "gui/$UID/${LABEL_PREFIX:-com.orataba.portfolio-ops}.$service" \
          >/dev/null 2>&1 || true
      done
      ;;
    systemd)
      for unit in "${SYSTEMD_UNITS[@]}"; do
        systemctl --user stop "$unit" >/dev/null 2>&1 || true
      done
      ;;
  esac
}

rollback_database() {
  echo "Restore failed; rolling back the project schemas from $BACKUP_REFERENCE_PATH." >&2
  if portfolio_ops_restore_project_schema_backup \
    "$DATABASE_URL" \
    "${PORTFOLIO_OPS_PROJECT_SCHEMA_BACKUP_PATH:-}" \
    "${PORTFOLIO_OPS_PROJECT_SCHEMA_MANIFEST_PATH:-}"; then
    return 0
  fi
  echo "Automatic rollback failed. Preserve this recovery artifact: $BACKUP_REFERENCE_PATH" >&2
  return 1
}

cleanup_on_exit() {
  local status=$?
  local rollback_failed="false"
  local restart_failed="false"

  trap - EXIT HUP INT TERM
  set +e

  if [[ $status -ne 0 && "$destructive_started" == "true" && "$database_ready" != "true" ]]; then
    if [[ "$backup_ready" != "true" ]] || ! rollback_database; then
      rollback_failed="true"
    fi
  fi

  if [[ "$services_may_need_restart" == "true" && "$rollback_failed" != "true" ]]; then
    if ! start_managed_services; then
      restart_failed="true"
      stop_all_managed_services
      echo "Failed to restart one or more previously managed local services." >&2
    fi
  elif [[ "$services_may_need_restart" == "true" && "$rollback_failed" == "true" ]]; then
    echo "Managed services remain stopped because database rollback did not complete." >&2
  fi

  [[ -n "$WORK_DIR" ]] && rm -rf "$WORK_DIR/connection"

  if [[ "$rollback_failed" == "true" ]]; then
    status=70
  elif [[ "$restart_failed" == "true" && $status -eq 0 ]]; then
    status=1
  fi
  if [[ "$rollback_failed" == "true" || "$restart_failed" == "true" ]]; then
    echo "Restore recovery state retained at: $WORK_DIR" >&2
  elif [[ -n "$WORK_DIR" ]]; then
    rm -rf "$WORK_DIR"
  fi
  exit "$status"
}

trap cleanup_on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -z "$DATABASE_URL" ]]; then
  echo "PORTFOLIO_OPS_LOCAL_DATABASE_URL must explicitly identify the restore target." >&2
  exit 64
fi
case "$DATABASE_URL" in
  postgresql://*|postgresql+psycopg://*) ;;
  *)
    echo "PORTFOLIO_OPS_LOCAL_DATABASE_URL must use postgresql:// or postgresql+psycopg://." >&2
    exit 64
    ;;
esac

if [[ ! -f "$DUMP_PATH" ]]; then
  echo "Dump not found: $DUMP_PATH" >&2
  exit 1
fi
if [[ ! -f "$CHECKSUM_PATH" ]]; then
  echo "Checksum file not found: $CHECKSUM_PATH" >&2
  exit 1
fi
expected_checksum="$(awk 'NF { print $1; exit }' "$CHECKSUM_PATH")"
if [[ ! "$expected_checksum" =~ ^[0-9A-Fa-f]{64}$ ]]; then
  echo "Invalid SHA-256 checksum file: $CHECKSUM_PATH" >&2
  exit 1
fi
actual_checksum="$(portfolio_ops_sha256 "$DUMP_PATH")"
if [[ "$actual_checksum" != "$expected_checksum" ]]; then
  echo "SHA-256 mismatch for dump: $DUMP_PATH" >&2
  exit 1
fi
echo "Verified SHA-256 checksum for $DUMP_PATH"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "PYTHON_BIN is not executable: $PYTHON_BIN" >&2
  exit 1
fi
if [[ ! -x "$MIGRATION_RUNNER" ]]; then
  echo "Migration runner is not executable: $MIGRATION_RUNNER" >&2
  exit 1
fi

PSQL_BIN="$(portfolio_ops_find_postgres_binary psql || true)"
PG_RESTORE_BIN="$(portfolio_ops_find_postgres_binary pg_restore || true)"
if [[ -z "$PSQL_BIN" || -z "$PG_RESTORE_BIN" ]]; then
  echo "PostgreSQL psql and pg_restore are required for safe restore." >&2
  exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-restore.XXXXXX")"
chmod 700 "$WORK_DIR"
SERVICE_STATE_FILE="$WORK_DIR/service-state"
DUMP_LIST_PATH="$WORK_DIR/incoming-dump.list"
portfolio_ops_prepare_libpq_connection "$DATABASE_URL" "$WORK_DIR/connection"
LIBPQ_DATABASE_URL="$PORTFOLIO_OPS_LIBPQ_DATABASE_URL"
LIBPQ_PASSFILE="$PORTFOLIO_OPS_LIBPQ_PASSFILE"
DATABASE_HOST="$PORTFOLIO_OPS_LIBPQ_DATABASE_HOST"
DATABASE_PORT="$PORTFOLIO_OPS_LIBPQ_DATABASE_PORT"
DATABASE_NAME="$PORTFOLIO_OPS_LIBPQ_DATABASE_NAME"
DATABASE_USER="$PORTFOLIO_OPS_LIBPQ_DATABASE_USER"

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
  echo "Restore replaces the instrument_registry, platform, portfolio, and watchlist schemas." >&2
  echo "Verified target: $DATABASE_USER@$DATABASE_HOST:$DATABASE_PORT/$DATABASE_NAME" >&2
  echo "Re-run with CONFIRM_RESTORE=$expected_confirmation after confirming the target." >&2
  exit 64
fi

"$PG_RESTORE_BIN" --list "$DUMP_PATH" > "$DUMP_LIST_PATH"
for schema in "${PORTFOLIO_OPS_PROJECT_SCHEMAS[@]}"; do
  if ! grep -Eq "^[0-9]+; [0-9]+ [0-9]+ SCHEMA - ${schema} " "$DUMP_LIST_PATH"; then
    echo "Incoming dump is missing required schema: $schema" >&2
    exit 1
  fi
done

PSQL_CONNECTION_ARGS=(
  --dbname "$LIBPQ_DATABASE_URL"
)

target_identity="$(
  portfolio_ops_run_libpq_command "$LIBPQ_PASSFILE" \
    "$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" \
    --no-password \
    --set ON_ERROR_STOP=1 \
    --tuples-only \
    --no-align \
    --command "SELECT current_database() || '|' || current_user"
)"
if [[ "$target_identity" != "$DATABASE_NAME|$DATABASE_USER" ]]; then
  echo "Connected database identity does not match requested target: $target_identity" >&2
  exit 1
fi
echo "Validated restore target: $target_identity at $DATABASE_HOST:$DATABASE_PORT"

resolve_service_manager
services_may_need_restart="true"
stop_managed_services

portfolio_ops_run_libpq_command "$LIBPQ_PASSFILE" \
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
  portfolio_ops_run_libpq_command "$LIBPQ_PASSFILE" \
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
if [[ "$remaining_connections" != "0" ]]; then
  echo "Refusing restore while $remaining_connections other database connection(s) remain active." >&2
  exit 1
fi

safe_database_name="$(printf '%s' "$DATABASE_NAME" | tr -c 'A-Za-z0-9_.-' '_')"
portfolio_ops_create_project_schema_backup \
  "$DATABASE_URL" \
  "$BACKUP_ROOT" \
  "${safe_database_name}-pre-restore"
backup_ready="true"
BACKUP_REFERENCE_PATH="${PORTFOLIO_OPS_PROJECT_SCHEMA_BACKUP_PATH:-$PORTFOLIO_OPS_PROJECT_SCHEMA_MANIFEST_PATH}"

restore_schema_args=()
for schema in "${PORTFOLIO_OPS_PROJECT_SCHEMAS[@]}"; do
  restore_schema_args+=(--schema="$schema")
done
destructive_started="true"
(
  printf '%s\n' '
    DROP SCHEMA IF EXISTS watchlist CASCADE;
    DROP SCHEMA IF EXISTS portfolio CASCADE;
    DROP SCHEMA IF EXISTS platform CASCADE;
    DROP SCHEMA IF EXISTS instrument_registry CASCADE;
    CREATE SCHEMA instrument_registry;
    CREATE SCHEMA platform;
    CREATE SCHEMA portfolio;
    CREATE SCHEMA watchlist;
  '
  exec "$PG_RESTORE_BIN" \
    --file - \
    --no-owner \
    --no-acl \
    "${restore_schema_args[@]}" \
    "$DUMP_PATH"
) | portfolio_ops_run_libpq_command "$LIBPQ_PASSFILE" \
  "$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" \
    --no-password \
    --set ON_ERROR_STOP=1 \
    --single-transaction

export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA=instrument_registry
export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PLATFORM_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry
export PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=platform
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA=portfolio
export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA=watchlist

PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN" ENV_ROOT="" \
  "$MIGRATION_RUNNER"

schema_count="$(
  portfolio_ops_run_libpq_command "$LIBPQ_PASSFILE" \
    "$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" \
    --no-password \
    --set ON_ERROR_STOP=1 \
    --tuples-only \
    --no-align \
    --command "
      SELECT count(*)
      FROM pg_namespace
      WHERE nspname IN ('instrument_registry', 'platform', 'portfolio', 'watchlist');
    "
)"
if [[ "$schema_count" != "4" ]]; then
  echo "Post-restore validation failed: expected 4 project schemas, found $schema_count." >&2
  exit 1
fi

database_ready="true"
if ! start_managed_services; then
  echo "Database restore succeeded, but managed services did not restart cleanly." >&2
  exit 1
fi
services_may_need_restart="false"

echo "Project schemas were restored and upgraded successfully."
echo "Safety backup retained at: $BACKUP_REFERENCE_PATH"
