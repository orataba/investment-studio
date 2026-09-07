#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# -ne 1 || "$1" != "--confirm-destroy-project-schemas" ]]; then
  echo "Usage: INVESTMENT_STUDIO_LOCAL_DATABASE_URL=postgresql://user@host/database $0 --confirm-destroy-project-schemas" >&2
  echo "This command permanently deletes instrument_registry, data_ingestion (formerly platform), portfolio, and watchlist." >&2
  exit 64
fi

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
DATABASE_URL="${INVESTMENT_STUDIO_LOCAL_DATABASE_URL:-}"
MIGRATION_RUNNER="$PROJECT_ROOT/infra/scripts/migrate_all.sh"
SERVICE_CONTROL="$PROJECT_ROOT/infra/launchd/control_local_services.sh"
BACKUP_HELPER="$PROJECT_ROOT/infra/postgres/project_schema_backup.sh"
RUNTIME_ENV_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")/../launchd" && pwd)/load_runtime_env.sh"
LABEL_PREFIX="${LABEL_PREFIX:-com.orataba.investment-studio}"
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"

if [[ -z "$DATABASE_URL" ]]; then
  echo "INVESTMENT_STUDIO_LOCAL_DATABASE_URL must explicitly identify the database to rebuild." >&2
  exit 64
fi
case "$DATABASE_URL" in
  postgresql://*|postgresql+psycopg://*) ;;
  *)
    echo "INVESTMENT_STUDIO_LOCAL_DATABASE_URL must use postgresql:// or postgresql+psycopg://." >&2
    exit 64
    ;;
esac
if [[ ! -x "$MIGRATION_RUNNER" ]]; then
  echo "Missing migration runner: $MIGRATION_RUNNER" >&2
  exit 1
fi
if [[ ! -x "$SERVICE_CONTROL" ]]; then
  echo "Missing launchd service-control helper: $SERVICE_CONTROL" >&2
  exit 1
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "PYTHON_BIN is not executable: $PYTHON_BIN" >&2
  exit 1
fi
if [[ ! -f "$BACKUP_HELPER" ]]; then
  echo "Missing PostgreSQL connection helper: $BACKUP_HELPER" >&2
  exit 1
fi
if [[ ! -f "$RUNTIME_ENV_HELPER" ]]; then
  echo "Missing database URL validation helper: $RUNTIME_ENV_HELPER" >&2
  exit 1
fi
source "$RUNTIME_ENV_HELPER"
investment_studio_require_password_free_database_url "$DATABASE_URL"
source "$BACKUP_HELPER"

PSQL_BIN="${PSQL_BIN:-$(command -v psql || true)}"
if [[ -z "$PSQL_BIN" || ! -x "$PSQL_BIN" ]]; then
  echo "psql is required to rebuild local schemas." >&2
  exit 1
fi

CONNECTION_DIR="$(mktemp -d "${TMPDIR:-/tmp}/investment-studio-schema-rebuild-connection.XXXXXX")"
chmod 700 "$CONNECTION_DIR"
trap 'rm -rf "$CONNECTION_DIR"' EXIT
investment_studio_prepare_libpq_connection "$DATABASE_URL" "$CONNECTION_DIR"
LIBPQ_DATABASE_URL="$INVESTMENT_STUDIO_LIBPQ_DATABASE_URL"
LIBPQ_PASSFILE="$INVESTMENT_STUDIO_LIBPQ_PASSFILE"

target_identity="$(
  investment_studio_run_libpq_command "$LIBPQ_PASSFILE" \
    "$PSQL_BIN" "$LIBPQ_DATABASE_URL" \
    --no-password \
    --set ON_ERROR_STOP=1 \
    --tuples-only \
    --no-align \
    --command "SELECT current_database() || '|' || current_user"
)"
expected_identity="$INVESTMENT_STUDIO_LIBPQ_DATABASE_NAME|$INVESTMENT_STUDIO_LIBPQ_DATABASE_USER"
if [[ "$target_identity" != "$expected_identity" ]]; then
  echo "Could not verify the rebuild target identity." >&2
  rm -rf "$CONNECTION_DIR"
  exit 1
fi
echo "Verified rebuild target: $target_identity"

SERVICE_STATE_FILE="$(mktemp "${TMPDIR:-/tmp}/investment-studio-schema-rebuild-state.XXXXXX")"
restart_required="false"
mutation_started="false"

stop_all_managed_services() {
  local service
  for service in home-api watchlist-api portfolio-api home-web watchlist-web portfolio-web market-data-refresh cn-market-data-refresh hk-market-data-refresh us-market-data-refresh cn-hk-reference-data-refresh us-reference-data-refresh; do
    launchctl bootout "gui/$UID/$LABEL_PREFIX.$service" >/dev/null 2>&1 || true
  done
}

cleanup_on_exit() {
  local status=$?
  local restart_failed="false"
  trap - EXIT HUP INT TERM
  set +e
  rm -rf "$CONNECTION_DIR"

  if [[ "$restart_required" == "true" ]]; then
    if [[ $status -ne 0 && "$mutation_started" == "true" ]]; then
      echo "Schema rebuild failed after destructive work began; managed services remain stopped." >&2
      echo "Repair or rerun the rebuild, then restore the saved service set from: $SERVICE_STATE_FILE" >&2
      exit "$status"
    fi

    if ! LABEL_PREFIX="$LABEL_PREFIX" LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS_DIR" \
      "$SERVICE_CONTROL" start "$SERVICE_STATE_FILE"; then
      restart_failed="true"
      stop_all_managed_services
      echo "Managed services could not be restored consistently and remain stopped." >&2
    fi
  fi

  if [[ "$restart_failed" == "true" ]]; then
    echo "Saved service set retained at: $SERVICE_STATE_FILE" >&2
    exit 1
  fi
  rm -f "$SERVICE_STATE_FILE"
  exit "$status"
}

trap cleanup_on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

restart_required="true"
LABEL_PREFIX="$LABEL_PREFIX" LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS_DIR" \
  "$SERVICE_CONTROL" stop "$SERVICE_STATE_FILE"

mutation_started="true"
investment_studio_run_libpq_command "$LIBPQ_PASSFILE" \
  "$PSQL_BIN" "$LIBPQ_DATABASE_URL" \
  --no-password \
  --set ON_ERROR_STOP=1 \
  --single-transaction \
  --command '
    DROP SCHEMA IF EXISTS watchlist CASCADE;
    DROP SCHEMA IF EXISTS portfolio CASCADE;
    DROP SCHEMA IF EXISTS data_ingestion CASCADE;
    DROP SCHEMA IF EXISTS platform CASCADE;
    DROP SCHEMA IF EXISTS instrument_data CASCADE;
    DROP SCHEMA IF EXISTS instrument_registry CASCADE;
    CREATE SCHEMA instrument_registry;
    CREATE SCHEMA platform;
    CREATE SCHEMA portfolio;
    CREATE SCHEMA watchlist;
  '

# Every migration chain receives the exact URL that psql just used. ENV_ROOT is
# intentionally empty so no dotenv source can redirect an individual chain.
export INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_INSTRUMENT_DATA_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA=instrument_data
export INVESTMENT_STUDIO_DATA_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA=instrument_data
export INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA=data_ingestion
export INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_PORTFOLIO_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_PORTFOLIO_DATABASE_SCHEMA=portfolio
export INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_WATCHLIST_ALEMBIC_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_WATCHLIST_DATABASE_SCHEMA=watchlist

PROJECT_ROOT="$PROJECT_ROOT" PYTHON_BIN="$PYTHON_BIN" ENV_ROOT="" \
  "$MIGRATION_RUNNER"

mutation_started="false"
echo "Local project schemas rebuilt successfully."
