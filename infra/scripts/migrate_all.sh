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
  for app in data portfolio watchlist; do
    load_env_file "$ENV_ROOT/$app.env" true
  done
fi

for required_database_variable in \
  INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL \
  INVESTMENT_STUDIO_DATA_DATABASE_URL \
  INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL \
  INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL; do
  if [[ -z "${!required_database_variable:-}" ]]; then
    echo "Set $required_database_variable explicitly before running migrations." >&2
    exit 1
  fi
done
export INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA="${INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA:-instrument_data}"

"$PYTHON_BIN" "$SCRIPT_DIR/validate_migration_targets.py"

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

registry_revision_is_descendant() {
  local current_output="$1"
  local target_revision="$2"

  PYTHONPATH="$INSTRUMENT_CORE_PYTHON${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" "$SCRIPT_DIR/alembic_revision_is_descendant.py" \
      --migration-root "$REGISTRY_MIGRATION_ROOT" \
      --current-output "$current_output" \
      --target "$target_revision"
}

INSTRUMENT_CORE_PYTHON="$PROJECT_ROOT/shared-data/instruments/python"
HOME_BACKEND="$PROJECT_ROOT/shared-data"
REGISTRY_MIGRATION_ROOT="$PROJECT_ROOT/shared-data/instruments"
REGISTRY_NAV_LEDGER_REVISION="20260715_0012"
registry_current_revision="$(
  current_migration_revision \
    "instrument registry" \
    "$REGISTRY_MIGRATION_ROOT" \
    "$INSTRUMENT_CORE_PYTHON"
)"
if registry_revision_is_descendant "$registry_current_revision" "$REGISTRY_NAV_LEDGER_REVISION"; then
  echo "Instrument registry NAV contract is already applied; prerequisite phase is complete."
else
  run_migration \
    "instrument registry prerequisite" \
    "$REGISTRY_MIGRATION_ROOT" \
    "$INSTRUMENT_CORE_PYTHON" \
    "20260715_0011"
  # Registry 0012 consumes the evidence snapshot under its historical schema
  # name. Finish that contract before the ingestion schema is renamed in 0008.
  run_migration \
    "data ingestion prerequisite" \
    "$HOME_BACKEND" \
    "$HOME_BACKEND:$INSTRUMENT_CORE_PYTHON" \
    "20260823_0007"
fi
# Historical app migrations reference instrument_registry explicitly. Complete
# them before the final shared-schema rename; never downgrade a renamed DB.
registry_target="20260902_0029"
if registry_revision_is_descendant "$registry_current_revision" "20260904_0030"; then
  registry_target="head"
fi
run_migration \
  "shared asset data prerequisite" \
  "$REGISTRY_MIGRATION_ROOT" \
  "$INSTRUMENT_CORE_PYTHON" \
  "$registry_target"
run_migration \
  "data ingestion" \
  "$HOME_BACKEND" \
  "$HOME_BACKEND:$INSTRUMENT_CORE_PYTHON"
run_migration \
  "portfolio" \
  "$PROJECT_ROOT/apps/portfolio/backend" \
  "$PROJECT_ROOT/apps/portfolio/backend:$INSTRUMENT_CORE_PYTHON"
run_migration \
  "watchlist" \
  "$PROJECT_ROOT/apps/watchlist/backend" \
  "$PROJECT_ROOT/apps/watchlist/backend:$INSTRUMENT_CORE_PYTHON"

run_migration "shared asset data" "$REGISTRY_MIGRATION_ROOT" "$INSTRUMENT_CORE_PYTHON"

echo "All release migrations completed successfully."
