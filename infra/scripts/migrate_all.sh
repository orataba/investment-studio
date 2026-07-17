#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
ENV_ROOT="${ENV_ROOT:-}"

DEFAULT_PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$DEFAULT_PYTHON_BIN" ]]; then
  DEFAULT_PYTHON_BIN="$(command -v python3 || true)"
fi
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "PYTHON_BIN is not executable: $PYTHON_BIN" >&2
  exit 1
fi

load_env_file() {
  local env_file="$1"
  local required="$2"

  if [[ ! -f "$env_file" ]]; then
    if [[ "$required" == "true" ]]; then
      echo "Missing environment file: $env_file" >&2
      exit 1
    fi
    return
  fi

  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    local line="${raw_line%$'\r'}"
    if [[ "$line" =~ ^[[:space:]]*$ || "$line" =~ ^[[:space:]]*# ]]; then
      continue
    fi
    if [[ "$line" != *"="* ]]; then
      echo "Invalid environment entry in $env_file: $line" >&2
      exit 1
    fi

    local key="${line%%=*}"
    local value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      echo "Invalid environment key in $env_file: $key" >&2
      exit 1
    fi

    # Values that are already present in the process environment are an
    # explicit caller decision. Never let an optional external env file
    # redirect a release migration to a different database.
    if [[ ${!key+x} ]]; then
      continue
    fi

    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ ${#value} -ge 2 ]]; then
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
    fi
    export "$key=$value"
  done < "$env_file"
}

if [[ -n "$ENV_ROOT" ]]; then
  for app in platform portfolio watchlist; do
    load_env_file "$ENV_ROOT/$app.env" true
  done
fi

for required_database_variable in \
  PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL \
  PORTFOLIO_OPS_PLATFORM_DATABASE_URL \
  PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL \
  PORTFOLIO_OPS_WATCHLIST_DATABASE_URL; do
  if [[ -z "${!required_database_variable:-}" ]]; then
    echo "Set $required_database_variable explicitly before running migrations." >&2
    exit 1
  fi
done
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA="${PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA:-instrument_registry}"

run_migration() {
  local label="$1"
  local migration_root="$2"
  local pythonpath="$3"
  local revision="${4:-head}"

  if [[ ! -f "$migration_root/alembic.ini" ]]; then
    echo "Missing Alembic configuration for $label: $migration_root/alembic.ini" >&2
    exit 1
  fi

  echo "Applying $label migrations."
  (
    cd "$migration_root"
    PYTHONPATH="$pythonpath${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" -m alembic upgrade "$revision"
  )
}

current_migration_revision() {
  local label="$1"
  local migration_root="$2"
  local pythonpath="$3"

  if [[ ! -f "$migration_root/alembic.ini" ]]; then
    echo "Missing Alembic configuration for $label: $migration_root/alembic.ini" >&2
    exit 1
  fi

  (
    cd "$migration_root"
    PYTHONPATH="$pythonpath${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" -m alembic current
  )
}

INSTRUMENT_CORE_PYTHON="$PROJECT_ROOT/packages/instrument-core/python"
PLATFORM_BACKEND="$PROJECT_ROOT/apps/platform/backend"
REGISTRY_MIGRATION_ROOT="$PROJECT_ROOT/infra/instrument_registry"
REGISTRY_NAV_LEDGER_REVISION="20260715_0012"
REGISTRY_NAV_CONTRACT_REVISION="20260716_0013"
REGISTRY_NAV_CASH_RETURN_REVISION="20260717_0014"
REGISTRY_PRICE_BAR_REVISION="20260717_0015"
registry_current_revision="$(
  current_migration_revision \
    "instrument registry" \
    "$REGISTRY_MIGRATION_ROOT" \
    "$INSTRUMENT_CORE_PYTHON"
)"
if [[ "$registry_current_revision" == *"$REGISTRY_NAV_LEDGER_REVISION"* ]] || \
   [[ "$registry_current_revision" == *"$REGISTRY_NAV_CONTRACT_REVISION"* ]] || \
   [[ "$registry_current_revision" == *"$REGISTRY_NAV_CASH_RETURN_REVISION"* ]] || \
   [[ "$registry_current_revision" == *"$REGISTRY_PRICE_BAR_REVISION"* ]]; then
  echo "Instrument registry NAV contract is already applied; prerequisite phase is complete."
else
  run_migration \
    "instrument registry prerequisite" \
    "$REGISTRY_MIGRATION_ROOT" \
    "$INSTRUMENT_CORE_PYTHON" \
    "20260715_0011"
fi
run_migration \
  "platform" \
  "$PLATFORM_BACKEND" \
  "$PLATFORM_BACKEND:$INSTRUMENT_CORE_PYTHON"
run_migration \
  "instrument registry NAV contract" \
  "$REGISTRY_MIGRATION_ROOT" \
  "$INSTRUMENT_CORE_PYTHON"
run_migration \
  "portfolio" \
  "$PROJECT_ROOT/apps/portfolio/backend" \
  "$PROJECT_ROOT/apps/portfolio/backend:$INSTRUMENT_CORE_PYTHON"
run_migration \
  "watchlist" \
  "$PROJECT_ROOT/apps/watchlist/backend" \
  "$PROJECT_ROOT/apps/watchlist/backend:$INSTRUMENT_CORE_PYTHON"

echo "All release migrations completed successfully."
