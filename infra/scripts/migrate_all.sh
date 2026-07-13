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
CALLER_EXPECTED_DATABASE="${PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE:-}"

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
    # explicit caller decision.  Never let a repository-local .env file
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

for app in platform portfolio watchlist; do
  if [[ -n "$ENV_ROOT" ]]; then
    load_env_file "$ENV_ROOT/$app.env" true
  else
    load_env_file "$PROJECT_ROOT/apps/$app/backend/.env" false
  fi
done

# This authorization must come from the invoking process.  A repository or
# runtime .env file may contain connection settings, but it must never be able
# to authorize a PostgreSQL migration by itself.
if [[ -z "$CALLER_EXPECTED_DATABASE" ]]; then
  echo "Set PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE in the invoking process." >&2
  exit 64
fi
export PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE="$CALLER_EXPECTED_DATABASE"

if [[ -z "${PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL:-}" ]]; then
  if [[ -z "${PORTFOLIO_OPS_PLATFORM_DATABASE_URL:-}" ]]; then
    echo "Set PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL or PORTFOLIO_OPS_PLATFORM_DATABASE_URL." >&2
    exit 1
  fi
  export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL="$PORTFOLIO_OPS_PLATFORM_DATABASE_URL"
fi
export PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA="${PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA:-${PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA:-instrument_registry}}"

run_migration() {
  local label="$1"
  local migration_root="$2"
  local pythonpath="$3"

  if [[ ! -f "$migration_root/alembic.ini" ]]; then
    echo "Missing Alembic configuration for $label: $migration_root/alembic.ini" >&2
    exit 1
  fi

  echo "Applying $label migrations."
  (
    cd "$migration_root"
    PYTHONPATH="$pythonpath${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" -m alembic upgrade head
  )
}

INSTRUMENT_CORE_PYTHON="$PROJECT_ROOT/packages/instrument-core/python"
run_migration \
  "instrument registry" \
  "$PROJECT_ROOT/infra/instrument_registry" \
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
