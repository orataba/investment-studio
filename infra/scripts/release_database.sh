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

PROJECT_SCHEMAS=(instrument_registry portfolio watchlist)
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
            echo "Invalid unit in release state: $unit" >&2
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

force_stop_managed_services() {
  case "$ACTIVE_SERVICE_MANAGER" in
    none) return 0 ;;
    launchd)
      local service
      for service in platform-api watchlist-api portfolio-api platform-web watchlist-web portfolio-web market-data-refresh; do
        launchctl bootout "gui/$UID/$LABEL_PREFIX.$service" >/dev/null 2>&1 || true
      done
      ;;
    systemd)
      systemctl --user stop "${SYSTEMD_UNITS[@]}" >/dev/null 2>&1 || true
      ;;
  esac
}

health_urls_for_previous_services() {
  local item
  while IFS= read -r item || [[ -n "$item" ]]; do
    case "$item" in
      platform-api) printf '%s\n' 'http://127.0.0.1:8002/api/health' ;;
      watchlist-api) printf '%s\n' 'http://127.0.0.1:8000/api/health' ;;
      portfolio-api) printf '%s\n' 'http://127.0.0.1:8001/api/health' ;;
      platform-web) printf '%s\n' 'http://127.0.0.1:5172/' ;;
      watchlist-web) printf '%s\n' 'http://127.0.0.1:5173/' ;;
      portfolio-web) printf '%s\n' 'http://127.0.0.1:5174/' ;;
      "$SYSTEMD_UNIT_PREFIX-platform-api.service") printf '%s\n' 'http://127.0.0.1:8102/api/health' ;;
      "$SYSTEMD_UNIT_PREFIX-watchlist-api.service") printf '%s\n' 'http://127.0.0.1:8100/api/health' ;;
      "$SYSTEMD_UNIT_PREFIX-portfolio-api.service") printf '%s\n' 'http://127.0.0.1:8101/api/health' ;;
      "$SYSTEMD_UNIT_PREFIX-platform-web.service") printf '%s\n' 'http://127.0.0.1:3100/' ;;
      "$SYSTEMD_UNIT_PREFIX-watchlist-web.service") printf '%s\n' 'http://127.0.0.1:3101/' ;;
      "$SYSTEMD_UNIT_PREFIX-portfolio-web.service") printf '%s\n' 'http://127.0.0.1:3102/' ;;
    esac
  done < "$SERVICE_STATE_FILE"
}

wait_for_health() {
  local urls=() url attempt healthy
  while IFS= read -r url || [[ -n "$url" ]]; do
    [[ -n "$url" ]] && urls+=("$url")
  done < <(health_urls_for_previous_services)
  if [[ ${#urls[@]} -eq 0 ]]; then
    echo "No previously active HTTP services require a health check."
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required for post-release health checks." >&2
    return 1
  fi
  for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt += 1)); do
    healthy="true"
    for url in "${urls[@]}"; do
      if ! curl --noproxy '*' --max-time 2 --fail --silent --output /dev/null "$url"; then
        healthy="false"
        break
      fi
    done
    if [[ "$healthy" == "true" ]]; then
      echo "All previously active HTTP services passed health checks."
      return 0
    fi
    sleep 1
  done
  echo "One or more post-release health checks did not become ready." >&2
  return 1
}

portfolio_api_base_for_previous_services() {
  local item
  while IFS= read -r item || [[ -n "$item" ]]; do
    case "$item" in
      portfolio-api)
        printf '%s\n' 'http://127.0.0.1:8001'
        return 0
        ;;
      "$SYSTEMD_UNIT_PREFIX-portfolio-api.service")
        printf '%s\n' 'http://127.0.0.1:8101'
        return 0
        ;;
    esac
  done < "$SERVICE_STATE_FILE"
}

verify_portfolio_read_contract() {
  local base_url
  base_url="$(portfolio_api_base_for_previous_services)"
  if [[ -z "$base_url" ]]; then
    echo "No previously active Portfolio API requires a read-contract smoke test."
    return 0
  fi
  "$PYTHON_BIN" - "$base_url" <<'PY'
from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, build_opener


base_url = sys.argv[1].rstrip("/")
opener = build_opener(ProxyHandler({}))


def get_json(path: str) -> object:
    url = f"{base_url}{path}"
    try:
        with opener.open(url, timeout=10) as response:
            if response.status != 200:
                raise SystemExit(f"Portfolio read smoke returned HTTP {response.status}: {url}")
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Portfolio read smoke failed for {url}: {exc}") from exc


portfolios = get_json("/api/portfolios")
if not isinstance(portfolios, list):
    raise SystemExit("Portfolio list response is not a JSON array")

transaction_count = 0
history_count = 0
for portfolio in portfolios:
    if not isinstance(portfolio, dict) or not isinstance(portfolio.get("portfolio_id"), str):
        raise SystemExit("Portfolio list contains an invalid portfolio identity")
    portfolio_id = quote(portfolio["portfolio_id"], safe="")
    transaction_response = get_json(f"/api/portfolios/{portfolio_id}/transactions")
    if not isinstance(transaction_response, dict) or not isinstance(
        transaction_response.get("transactions"), list
    ):
        raise SystemExit("Portfolio transaction response has an invalid envelope")
    transactions = transaction_response["transactions"]
    transaction_count += len(transactions)
    for transaction in transactions:
        if not isinstance(transaction, dict) or not isinstance(
            transaction.get("transaction_id"), str
        ):
            raise SystemExit("Portfolio transaction response contains an invalid identity")
        transaction_id = quote(transaction["transaction_id"], safe="")
        history = get_json(
            f"/api/portfolios/{portfolio_id}/transactions/{transaction_id}/revisions"
        )
        if not isinstance(history, dict) or not isinstance(history.get("revisions"), list):
            raise SystemExit("Transaction revision history response has an invalid envelope")
        revisions = history["revisions"]
        if not revisions:
            raise SystemExit("Transaction revision history is unexpectedly empty")
        history_count += len(revisions)

print(
    "Portfolio read-contract smoke passed: "
    f"{len(portfolios)} portfolios, {transaction_count} current transactions, "
    f"{history_count} revisions."
)
PY
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
  local list_path="$WORK_DIR/$(basename "$dump_path").list"
  local schema
  "$PG_RESTORE_BIN" --list "$dump_path" > "$list_path"
  for schema in "${PROJECT_SCHEMAS[@]}"; do
    if ! grep -Eq "^[0-9]+; [0-9]+ [0-9]+ SCHEMA - ${schema} " "$list_path"; then
      echo "Backup is missing required schema: $schema" >&2
      return 1
    fi
  done
}

create_verified_backup() {
  local label="$1" path_variable="$2" checksum_variable="$3"
  local timestamp backup_path checksum_path checksum
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup_path="$BACKUP_ROOT/${DATABASE_NAME}-${label}-${timestamp}-$$.pgdump"
  checksum_path="$backup_path.sha256"
  "$PG_DUMP_BIN" \
    --format=custom \
    --no-owner \
    --no-acl \
    "${PSQL_CONNECTION_ARGS[@]}" \
    --schema=instrument_registry \
    --schema=portfolio \
    --schema=watchlist \
    --file "$backup_path"
  chmod 600 "$backup_path"
  verify_dump "$backup_path"
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
  verify_dump "$PRE_BACKUP_PATH"
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
  local status=$? rollback_failed="false" restart_failed="false"
  trap - EXIT HUP INT TERM
  set +e
  if [[ $status -ne 0 && "$services_stopped" == "true" ]]; then
    force_stop_managed_services
  fi
  if [[ $status -ne 0 && "$database_mutation_started" == "true" && "$release_complete" != "true" ]]; then
    if ! restore_pre_release_backup; then
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
schema_count="$(
  "$PSQL_BIN" "${PSQL_CONNECTION_ARGS[@]}" --no-password --set ON_ERROR_STOP=1 \
    --tuples-only --no-align --command "
      SELECT count(*) FROM pg_namespace
      WHERE nspname IN ('instrument_registry', 'portfolio', 'watchlist');
    "
)"
if [[ "$schema_count" != "3" ]]; then
  echo "Release requires all three project schemas; found $schema_count." >&2
  exit 1
fi

create_verified_backup pre-release PRE_BACKUP_PATH PRE_BACKUP_CHECKSUM_PATH

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
echo "Applying all three migration chains."
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

create_verified_backup post-release POST_BACKUP_PATH POST_BACKUP_CHECKSUM_PATH

start_previously_active_services
wait_for_health
verify_portfolio_read_contract
release_complete="true"
services_stopped="false"

echo "Database release completed successfully."
echo "Pre-release recovery point: $PRE_BACKUP_PATH"
echo "Post-release recovery point: $POST_BACKUP_PATH"
echo "Audit evidence: $PORTFOLIO_OPS_RELEASE_AUDIT_OUTPUT_PATH"
