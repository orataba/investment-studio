#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
RELEASE_DATABASE_URL="${PORTFOLIO_OPS_RELEASE_DATABASE_URL:-}"
if [[ -n "$RELEASE_DATABASE_URL" ]]; then
  parsed_database_target="$(
    "$PYTHON_BIN" - "$RELEASE_DATABASE_URL" <<'PY'
import sys
from urllib.parse import unquote, urlparse

parsed = urlparse(sys.argv[1].replace("postgresql+psycopg://", "postgresql://", 1))
values = (
    unquote(parsed.hostname or ""),
    str(parsed.port or 5432),
    unquote(parsed.path.lstrip("/")),
    unquote(parsed.username or ""),
    unquote(parsed.password or ""),
)
if any("|" in value or "\n" in value for value in values):
    raise SystemExit("Database URL fields contain an unsupported delimiter")
if not all(values[:4]):
    raise SystemExit("Release database URL must include host, database, and user")
print("|".join(values))
PY
  )"
  IFS='|' read -r DATABASE_HOST DATABASE_PORT DATABASE_NAME DATABASE_USER URL_DATABASE_PASSWORD \
    <<< "$parsed_database_target"
  # A URL may intentionally omit credentials so they never appear in process
  # arguments or logs. In that case, honor the separately supplied secret.
  DATABASE_PASSWORD="${URL_DATABASE_PASSWORD:-${PORTFOLIO_OPS_DB_PASSWORD:-}}"
else
  DATABASE_HOST="${PORTFOLIO_OPS_DB_HOST:-127.0.0.1}"
  DATABASE_PORT="${PORTFOLIO_OPS_DB_PORT:-5432}"
  DATABASE_NAME="${PORTFOLIO_OPS_DB_NAME:-portfolio_ops}"
  DATABASE_USER="${PORTFOLIO_OPS_DB_USER:-portfolio_ops}"
  DATABASE_PASSWORD="${PORTFOLIO_OPS_DB_PASSWORD:-portfolio_ops}"
fi
export PORTFOLIO_OPS_DB_HOST="$DATABASE_HOST"
export PORTFOLIO_OPS_DB_PORT="$DATABASE_PORT"
export PORTFOLIO_OPS_DB_NAME="$DATABASE_NAME"
export PORTFOLIO_OPS_DB_USER="$DATABASE_USER"
export PORTFOLIO_OPS_DB_PASSWORD="$DATABASE_PASSWORD"
MIGRATION_RUNNER="${PORTFOLIO_OPS_RELEASE_MIGRATION_RUNNER:-$SCRIPT_DIR/migrate_all.sh}"
POST_MIGRATION_GATE="${PORTFOLIO_OPS_RELEASE_POST_MIGRATION_GATE:-$SCRIPT_DIR/post_migration_gate.sh}"
AS_OF_DATE="${PORTFOLIO_OPS_RELEASE_AS_OF_DATE:-}"
BACKUP_ROOT="${PORTFOLIO_OPS_RELEASE_BACKUP_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/portfolio-operations-workbench/postgres-backups}"
SERVICE_MANAGER_REQUESTED="${PORTFOLIO_OPS_RELEASE_SERVICE_MANAGER:-auto}"
ALLOW_REMOTE_RELEASE="${ALLOW_REMOTE_RELEASE:-false}"
ALLOW_ACTIVE_CONNECTIONS="${ALLOW_ACTIVE_CONNECTIONS:-false}"
EXPECTED_SYSTEM_IDENTIFIER="${PORTFOLIO_OPS_RELEASE_EXPECTED_SYSTEM_IDENTIFIER:-}"
LABEL_PREFIX="${LABEL_PREFIX:-com.orataba.portfolio-ops}"
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
SYSTEMD_UNIT_PREFIX="${UNIT_PREFIX:-portfolio-ops}"
HEALTH_ATTEMPTS="${PORTFOLIO_OPS_RELEASE_HEALTH_ATTEMPTS:-30}"

PROJECT_SCHEMAS=(instrument_registry calculation_registry portfolio watchlist)
source "$PROJECT_ROOT/infra/service_inventory.sh"
source "$PROJECT_ROOT/infra/scripts/runtime_readiness.sh"
SYSTEMD_UNITS=()
for unit_suffix in "${PORTFOLIO_OPS_SYSTEMD_MANAGED_UNIT_SUFFIXES[@]}"; do
  SYSTEMD_UNITS+=("$SYSTEMD_UNIT_PREFIX-$unit_suffix")
done

ACTIVE_SERVICE_MANAGER=none
WORK_DIR=""
SERVICE_STATE_FILE=""
PSQL_BIN=""
PG_DUMP_BIN=""
PG_RESTORE_BIN=""
PRE_BACKUP_PATH=""
PRE_BACKUP_CHECKSUM_PATH=""
POST_BACKUP_PATH=""
POST_BACKUP_CHECKSUM_PATH=""
database_mutation_started="false"
release_complete="false"
services_stopped="false"

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
    127.0.0.1|localhost|::1|/*) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_service_manager() {
  case "$SERVICE_MANAGER_REQUESTED" in
    none|launchd|systemd)
      ACTIVE_SERVICE_MANAGER="$SERVICE_MANAGER_REQUESTED"
      ;;
    auto)
      if [[ "$(uname -s)" == "Darwin" ]] && command -v launchctl >/dev/null 2>&1; then
        ACTIVE_SERVICE_MANAGER=launchd
      elif command -v systemctl >/dev/null 2>&1 \
        && systemctl --user show-environment >/dev/null 2>&1; then
        ACTIVE_SERVICE_MANAGER=systemd
      else
        ACTIVE_SERVICE_MANAGER=none
      fi
      ;;
    *)
      echo "Invalid PORTFOLIO_OPS_RELEASE_SERVICE_MANAGER: $SERVICE_MANAGER_REQUESTED" >&2
      exit 64
      ;;
  esac
}

stop_managed_services() {
  : > "$SERVICE_STATE_FILE"
  chmod 600 "$SERVICE_STATE_FILE"
  case "$ACTIVE_SERVICE_MANAGER" in
    none)
      echo "No service manager selected; caller is responsible for fencing every writer."
      ;;
    launchd)
      LABEL_PREFIX="$LABEL_PREFIX" LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS_DIR" \
        "$PROJECT_ROOT/infra/launchd/control_local_services.sh" stop "$SERVICE_STATE_FILE"
      ;;
    systemd)
      local unit active_units=()
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
  services_stopped="true"
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

start_previously_active_services() {
  case "$ACTIVE_SERVICE_MANAGER" in
    none) return 0 ;;
    launchd)
      LABEL_PREFIX="$LABEL_PREFIX" LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS_DIR" \
        "$PROJECT_ROOT/infra/launchd/control_local_services.sh" start "$SERVICE_STATE_FILE"
      ;;
    systemd)
      local unit active_units=()
      while IFS= read -r unit || [[ -n "$unit" ]]; do
        [[ -n "$unit" ]] || continue
        if ! is_managed_systemd_unit "$unit"; then
          echo "Invalid unit in release state: $unit" >&2
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

force_stop_managed_services() {
  local stop_status=0
  case "$ACTIVE_SERVICE_MANAGER" in
    none) return 0 ;;
    launchd)
      local service
      for service in "${PORTFOLIO_OPS_ALL_SERVICE_NAMES[@]}"; do
        if launchctl print "gui/$UID/$LABEL_PREFIX.$service" >/dev/null 2>&1 \
          && ! launchctl bootout "gui/$UID/$LABEL_PREFIX.$service" >/dev/null 2>&1; then
          stop_status=1
        fi
      done
      ;;
    systemd)
      if ! systemctl --user stop "${SYSTEMD_UNITS[@]}" >/dev/null 2>&1; then
        stop_status=1
      fi
      ;;
  esac
  return "$stop_status"
}

managed_services_are_inactive() {
  local service unit state
  case "$ACTIVE_SERVICE_MANAGER" in
    none)
      return 0
      ;;
    launchd)
      if ! launchctl print "gui/$UID" >/dev/null 2>&1; then
        echo "Cannot verify the launchd user domain while fencing managed services." >&2
        return 1
      fi
      for service in "${PORTFOLIO_OPS_ALL_SERVICE_NAMES[@]}"; do
        if launchctl print "gui/$UID/$LABEL_PREFIX.$service" >/dev/null 2>&1; then
          echo "Managed service is still loaded: $LABEL_PREFIX.$service" >&2
          return 1
        fi
      done
      ;;
    systemd)
      for unit in "${SYSTEMD_UNITS[@]}"; do
        state="$(systemctl --user is-active "$unit" 2>/dev/null || true)"
        case "$state" in
          inactive|failed|unknown) ;;
          *)
            echo "Managed service is not inactive: $unit ($state)" >&2
            return 1
            ;;
        esac
      done
      ;;
  esac
  return 0
}

sha256_file() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{ print $1 }'
  else
    shasum -a 256 "$path" | awk '{ print $1 }'
  fi
}

verify_dump() {
  local dump_path="$1"
  shift
  local list_path="$WORK_DIR/$(basename "$dump_path").list"
  local schema
  "$PG_RESTORE_BIN" --list "$dump_path" > "$list_path"
  for schema in "$@"; do
    if ! grep -Eq "^[0-9]+; [0-9]+ [0-9]+ SCHEMA - ${schema} " "$list_path"; then
      echo "Backup is missing required schema: $schema" >&2
      return 1
    fi
  done
}

create_verified_backup() {
  local label="$1" path_variable="$2" checksum_variable="$3"
  shift 3
  local schemas=("$@")
  local schema_args=()
  local schema timestamp backup_path checksum_path checksum
  if [[ ${#schemas[@]} -eq 0 ]]; then
    echo "Cannot create a project backup without an explicit schema set." >&2
    return 1
  fi
  for schema in "${schemas[@]}"; do
    schema_args+=(--schema="$schema")
  done
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup_path="$BACKUP_ROOT/${DATABASE_NAME}-${label}-${timestamp}-$$.pgdump"
  checksum_path="$backup_path.sha256"
  "$PG_DUMP_BIN" \
    --format=custom \
    --no-owner \
    --no-acl \
    "${PSQL_CONNECTION_ARGS[@]}" \
    "${schema_args[@]}" \
    --file "$backup_path"
  chmod 600 "$backup_path"
  verify_dump "$backup_path" "${schemas[@]}"
  checksum="$(sha256_file "$backup_path")"
  printf '%s  %s\n' "$checksum" "$(basename "$backup_path")" > "$checksum_path"
  chmod 600 "$checksum_path"
  if [[ "$(sha256_file "$backup_path")" != "$checksum" ]]; then
    echo "Backup checksum verification failed: $backup_path" >&2
    return 1
  fi
  printf -v "$path_variable" '%s' "$backup_path"
  printf -v "$checksum_variable" '%s' "$checksum_path"
  echo "Verified $label backup: $backup_path"
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

restore_pre_release_backup() {
  local expected_checksum actual_checksum
  if [[ ! -f "$PRE_BACKUP_PATH" || ! -f "$PRE_BACKUP_CHECKSUM_PATH" ]]; then
    echo "Cannot roll back: the verified pre-release backup is unavailable." >&2
    return 1
  fi
  expected_checksum="$(awk 'NF { print $1; exit }' "$PRE_BACKUP_CHECKSUM_PATH")"
  actual_checksum="$(sha256_file "$PRE_BACKUP_PATH")"
  if [[ "$expected_checksum" != "$actual_checksum" ]]; then
    echo "Cannot roll back: pre-release backup checksum mismatch." >&2
    return 1
  fi
  verify_dump "$PRE_BACKUP_PATH" "${PRE_RELEASE_SCHEMAS[@]}"
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
  drop_project_schemas
  "$PG_RESTORE_BIN" \
    --exit-on-error \
    --single-transaction \
    --no-owner \
    --no-acl \
    "${PG_RESTORE_CONNECTION_ARGS[@]}" \
    "$PRE_BACKUP_PATH"
  echo "Database rolled back from: $PRE_BACKUP_PATH" >&2
}

cleanup_on_exit() {
  local status=$? rollback_failed="false" restart_failed="false" fence_failed="false"
  trap - EXIT HUP INT TERM
  set +e
  if [[ $status -ne 0 && "$services_stopped" == "true" ]]; then
    if ! force_stop_managed_services; then
      echo "One or more managed-service stop commands failed; verifying the fence." >&2
    fi
  fi
  if [[ $status -ne 0 && "$database_mutation_started" == "true" && "$release_complete" != "true" ]]; then
    if ! managed_services_are_inactive; then
      fence_failed="true"
      echo "AUTOMATIC ROLLBACK SKIPPED. Managed writer inactivity could not be verified." >&2
      echo "Recovery artifact: $PRE_BACKUP_PATH" >&2
    elif ! restore_pre_release_backup; then
      rollback_failed="true"
      echo "AUTOMATIC ROLLBACK FAILED. All managed services remain stopped." >&2
      echo "Recovery artifact: $PRE_BACKUP_PATH" >&2
    else
      echo "Release failed after database mutation. The database was restored, but services remain stopped for operator review." >&2
    fi
  elif [[ $status -ne 0 && "$services_stopped" == "true" ]]; then
    if ! start_previously_active_services; then
      restart_failed="true"
      echo "Release failed before database mutation and previous services could not be restored." >&2
    fi
  fi
  [[ -n "$WORK_DIR" ]] && rm -rf "$WORK_DIR"
  if [[ "$rollback_failed" == "true" || "$fence_failed" == "true" ]]; then
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
if [[ ! "$HEALTH_ATTEMPTS" =~ ^[0-9]+$ ]] || [[ "$HEALTH_ATTEMPTS" -lt 1 || "$HEALTH_ATTEMPTS" -gt 120 ]]; then
  echo "PORTFOLIO_OPS_RELEASE_HEALTH_ATTEMPTS must be between 1 and 120." >&2
  exit 64
fi
if [[ -z "$AS_OF_DATE" ]]; then
  echo "Set PORTFOLIO_OPS_RELEASE_AS_OF_DATE to an explicit YYYY-MM-DD." >&2
  exit 64
fi
expected_confirmation="$DATABASE_NAME@$DATABASE_HOST:$DATABASE_PORT"
if [[ "${CONFIRM_RELEASE:-}" != "$expected_confirmation" ]]; then
  echo "Release target: $DATABASE_USER@$DATABASE_HOST:$DATABASE_PORT/$DATABASE_NAME" >&2
  echo "Re-run with CONFIRM_RELEASE=$expected_confirmation after independently confirming the target." >&2
  exit 64
fi
if ! is_local_database_host && [[ "$ALLOW_REMOTE_RELEASE" != "true" ]]; then
  echo "Refusing a remote database release without ALLOW_REMOTE_RELEASE=true." >&2
  exit 64
fi
for executable in "$PYTHON_BIN" "$MIGRATION_RUNNER" "$POST_MIGRATION_GATE"; do
  if [[ ! -x "$executable" ]]; then
    echo "Required release executable is missing: $executable" >&2
    exit 1
  fi
done

PSQL_BIN="$(find_postgres_binary psql || true)"
PG_DUMP_BIN="$(find_postgres_binary pg_dump || true)"
PG_RESTORE_BIN="$(find_postgres_binary pg_restore || true)"
if [[ -z "$PSQL_BIN" || -z "$PG_DUMP_BIN" || -z "$PG_RESTORE_BIN" ]]; then
  echo "PostgreSQL psql, pg_dump, and pg_restore are required." >&2
  exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-release.XXXXXX")"
chmod 700 "$WORK_DIR"
SERVICE_STATE_FILE="$WORK_DIR/service-state"
mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"

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

target_database="$(
  "$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" --no-password --set ON_ERROR_STOP=1 \
    --tuples-only --no-align --command 'SELECT current_database()'
)"
if [[ "$target_database" != "$DATABASE_NAME" ]]; then
  echo "Connected database does not match release target: $target_database" >&2
  exit 1
fi
system_identifier="$(
  "$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" --no-password --set ON_ERROR_STOP=1 \
    --tuples-only --no-align --command 'SELECT system_identifier FROM pg_control_system()' 2>/dev/null \
    || true
)"
if [[ -n "$EXPECTED_SYSTEM_IDENTIFIER" && "$system_identifier" != "$EXPECTED_SYSTEM_IDENTIFIER" ]]; then
  echo "PostgreSQL system identifier does not match the confirmed deployment target." >&2
  exit 1
fi
echo "Validated release target: $DATABASE_USER@$DATABASE_HOST:$DATABASE_PORT/$target_database"
if [[ -n "$system_identifier" ]]; then
  echo "PostgreSQL system identifier: $system_identifier"
fi

resolve_service_manager
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
  "$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" --no-password --set ON_ERROR_STOP=1 \
    --tuples-only --no-align --command "
      SELECT count(*)
      FROM pg_stat_activity
      WHERE datname = current_database()
        AND backend_type = 'client backend'
        AND pid <> pg_backend_pid();
    "
)"
if [[ "$remaining_connections" != "0" && "$ALLOW_ACTIVE_CONNECTIONS" != "true" ]]; then
  echo "Refusing release while $remaining_connections other database connection(s) remain active." >&2
  exit 1
fi
EXISTING_SCHEMAS_PATH="$WORK_DIR/existing-project-schemas"
"$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" --no-password --set ON_ERROR_STOP=1 \
  --tuples-only --no-align --command "
    SELECT nspname FROM pg_namespace
    WHERE nspname IN ('instrument_registry', 'calculation_registry', 'portfolio', 'watchlist')
    ORDER BY CASE nspname
      WHEN 'instrument_registry' THEN 1
      WHEN 'calculation_registry' THEN 2
      WHEN 'portfolio' THEN 3
      WHEN 'watchlist' THEN 4
    END;
  " > "$EXISTING_SCHEMAS_PATH"
PRE_RELEASE_SCHEMAS=()
while IFS= read -r schema || [[ -n "$schema" ]]; do
  [[ -n "$schema" ]] && PRE_RELEASE_SCHEMAS+=("$schema")
done < "$EXISTING_SCHEMAS_PATH"
schema_count="$(
  "$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" --no-password --set ON_ERROR_STOP=1 \
    --tuples-only --no-align --command "
      SELECT count(*) FROM pg_namespace
      WHERE nspname IN ('instrument_registry', 'portfolio', 'watchlist');
    "
)"
if [[ "$schema_count" != "3" ]]; then
  echo "Release requires the three legacy core schemas; found $schema_count." >&2
  exit 1
fi

create_verified_backup \
  pre-release PRE_BACKUP_PATH PRE_BACKUP_CHECKSUM_PATH \
  "${PRE_RELEASE_SCHEMAS[@]}"

DATABASE_URL="$(
  DB_URL_USER="$DATABASE_USER" DB_URL_PASSWORD="$DATABASE_PASSWORD" \
  DB_URL_HOST="$DATABASE_HOST" DB_URL_PORT="$DATABASE_PORT" DB_URL_DATABASE="$DATABASE_NAME" \
    "$PYTHON_BIN" - <<'PY'
import os
from urllib.parse import quote

user = quote(os.environ["DB_URL_USER"], safe="")
password = quote(os.environ["DB_URL_PASSWORD"], safe="")
host = os.environ["DB_URL_HOST"]
port = os.environ["DB_URL_PORT"]
database = quote(os.environ["DB_URL_DATABASE"], safe="")
rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
print(f"postgresql+psycopg://{user}:{password}@{rendered_host}:{port}/{database}")
PY
)"
export PORTFOLIO_OPS_LOCAL_DATABASE_URL="$DATABASE_URL"
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

database_mutation_started="true"
echo "Applying all four migration chains."
PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN" ENV_ROOT="" "$MIGRATION_RUNNER"

audit_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
export PORTFOLIO_OPS_RELEASE_AUDIT_OUTPUT_PATH="$BACKUP_ROOT/${DATABASE_NAME}-release-audit-${audit_timestamp}-$$.json"
PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN" "$POST_MIGRATION_GATE" "$AS_OF_DATE"
"$PYTHON_BIN" - "$PORTFOLIO_OPS_RELEASE_AUDIT_OUTPUT_PATH" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"Release gate did not retain valid audit evidence at {path}") from exc
if (
    payload.get("status") != "passed"
    or payload.get("failed_count") != 0
    or payload.get("warning_count") != 0
):
    raise SystemExit("Retained audit evidence is not a zero-failure, zero-warning pass")
PY

create_verified_backup \
  post-release POST_BACKUP_PATH POST_BACKUP_CHECKSUM_PATH \
  "${PROJECT_SCHEMAS[@]}"

start_previously_active_services
portfolio_ops_wait_for_service_state_readiness \
  "$SERVICE_STATE_FILE" "$SYSTEMD_UNIT_PREFIX" "$HEALTH_ATTEMPTS"
portfolio_ops_verify_portfolio_read_contract_for_service_state \
  "$SERVICE_STATE_FILE" "$SYSTEMD_UNIT_PREFIX" "$PYTHON_BIN"
release_complete="true"
services_stopped="false"

echo "Database release completed successfully."
echo "Pre-release recovery point: $PRE_BACKUP_PATH"
echo "Post-release recovery point: $POST_BACKUP_PATH"
echo "Audit evidence: $PORTFOLIO_OPS_RELEASE_AUDIT_OUTPUT_PATH"
