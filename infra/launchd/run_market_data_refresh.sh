#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <project-root> <python-bin> <lock-file> <external-env-root>" >&2
  exit 64
fi

PROJECT_ROOT="$(cd "$1" && pwd)"
PYTHON_BIN="$2"
LOCK_FILE="$3"
EXTERNAL_ENV_ROOT="$4"
SUMMARY_FILE="$PROJECT_ROOT/var/market-data-refresh-summary.json"
BACKEND_ROOT="$PROJECT_ROOT/apps/platform/backend"
REFRESH_SCRIPT="$BACKEND_ROOT/scripts/refresh_market_data_scheduled.py"
AUDIT_SCRIPT="$PROJECT_ROOT/infra/scripts/audit_live_data.py"
CHANNEL="${PORTFOLIO_OPS_LOCAL_REFRESH_CHANNEL:-all}"
RETRY_FAILED_ATTEMPTS="${PORTFOLIO_OPS_LOCAL_REFRESH_RETRY_FAILED_ATTEMPTS:-2}"
FAIL_ON_ITEM_FAILURE="${PORTFOLIO_OPS_LOCAL_REFRESH_FAIL_ON_ITEM_FAILURE:-true}"

source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
ENV_FILE="$(portfolio_ops_runtime_env_file platform "$EXTERNAL_ENV_ROOT")"
portfolio_ops_load_env_file "$ENV_FILE" PORTFOLIO_OPS_PLATFORM_

DATABASE_URL="${PORTFOLIO_OPS_LOCAL_DATABASE_URL:-}"
if [[ -z "$DATABASE_URL" ]]; then
  echo "PORTFOLIO_OPS_LOCAL_DATABASE_URL is required for scheduled refresh." >&2
  exit 64
fi
case "$DATABASE_URL" in
  postgresql://*|postgresql+psycopg://*) ;;
  *)
    echo "PORTFOLIO_OPS_LOCAL_DATABASE_URL must use postgresql:// or postgresql+psycopg://." >&2
    exit 64
    ;;
esac

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable is missing: $PYTHON_BIN" >&2
  exit 1
fi
if [[ ! -f "$REFRESH_SCRIPT" ]]; then
  echo "Scheduled refresh script is missing: $REFRESH_SCRIPT" >&2
  exit 1
fi
if [[ ! -f "$AUDIT_SCRIPT" ]]; then
  echo "Post-refresh data audit script is missing: $AUDIT_SCRIPT" >&2
  exit 1
fi
case "$CHANNEL" in
  all|email|tushare|projection) ;;
  *)
    echo "Invalid refresh channel: $CHANNEL" >&2
    exit 64
    ;;
esac
if [[ ! "$RETRY_FAILED_ATTEMPTS" =~ ^[0-9]+$ ]]; then
  echo "Refresh retry count must be a non-negative integer: $RETRY_FAILED_ATTEMPTS" >&2
  exit 64
fi
case "$FAIL_ON_ITEM_FAILURE" in
  true|false) ;;
  *)
    echo "PORTFOLIO_OPS_LOCAL_REFRESH_FAIL_ON_ITEM_FAILURE must be true or false." >&2
    exit 64
    ;;
esac

mkdir -p "$(dirname "$LOCK_FILE")"

export PYTHONUNBUFFERED=1
export PYTHONNOUSERSITE=1
export PYTHONPATH="$BACKEND_ROOT:$PROJECT_ROOT/packages/instrument-core/python"
export PORTFOLIO_OPS_PLATFORM_ENVIRONMENT=local
export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="$DATABASE_URL"
export PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry
export PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=platform
export PORTFOLIO_OPS_PLATFORM_FRONTEND_URL=http://127.0.0.1:5172
export PORTFOLIO_OPS_PLATFORM_WATCHLIST_URL=http://127.0.0.1:5173
export PORTFOLIO_OPS_PLATFORM_PORTFOLIO_URL=http://127.0.0.1:5174
export PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL=http://127.0.0.1:8000
export PORTFOLIO_OPS_PLATFORM_PORTFOLIO_API_URL=http://127.0.0.1:8001

refresh_arguments=(
  --channel "$CHANNEL"
  --updated-by launchd-scheduler
  --retry-failed-attempts "$RETRY_FAILED_ATTEMPTS"
  --lock-file "$LOCK_FILE"
  --summary-file "$SUMMARY_FILE"
  --require-downstream-success
  --json
)
if [[ "$FAIL_ON_ITEM_FAILURE" == "true" ]]; then
  refresh_arguments+=(--fail-on-item-failure)
fi

cd "$BACKEND_ROOT"
"$PYTHON_BIN" "$REFRESH_SCRIPT" "${refresh_arguments[@]}"
"$PYTHON_BIN" "$AUDIT_SCRIPT" --json
