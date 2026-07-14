#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 && $# -ne 5 ]]; then
  echo "Usage: $0 <service> <project-root> <python-bin> <node-bin> [external-env-root]" >&2
  exit 64
fi

SERVICE="$1"
PROJECT_ROOT="$(cd "$2" && pwd)"
PYTHON_BIN="$3"
NODE_BIN="$4"
EXTERNAL_ENV_ROOT="${5:-${PORTFOLIO_OPS_LOCAL_ENV_ROOT:-$HOME/.config/orataba/secrets/portfolio-operations-workbench}}"
DATABASE_URL="${PORTFOLIO_OPS_LOCAL_DATABASE_URL:-postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops}"

# Load only namespaced dotenv assignments as data.  This preserves the current
# external secret file without copying secrets into a plist or evaluating shell
# syntax contained in a value.
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
case "$SERVICE" in
  platform-api|platform-outbox-worker|watchlist-api|watchlist-worker|portfolio-api|portfolio-worker)
    portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
    ;;
esac
case "$SERVICE" in
  platform-api|platform-outbox-worker)
    ENV_FILE="$(portfolio_ops_runtime_env_file platform "$EXTERNAL_ENV_ROOT")"
    portfolio_ops_load_env_file "$ENV_FILE" PORTFOLIO_OPS_PLATFORM_
    ;;
  watchlist-api|watchlist-worker)
    ENV_FILE="$(portfolio_ops_runtime_env_file watchlist "$EXTERNAL_ENV_ROOT")"
    if [[ -f "$ENV_FILE" ]]; then
      portfolio_ops_load_env_file "$ENV_FILE" PORTFOLIO_OPS_WATCHLIST_
    fi
    ;;
  portfolio-api|portfolio-worker)
    ENV_FILE="$(portfolio_ops_runtime_env_file portfolio "$EXTERNAL_ENV_ROOT")"
    if [[ -f "$ENV_FILE" ]]; then
      portfolio_ops_load_env_file "$ENV_FILE" PORTFOLIO_OPS_PORTFOLIO_
    fi
    ;;
esac

export PYTHONUNBUFFERED=1
export PYTHONNOUSERSITE=1
export PYTHONPATH="$PROJECT_ROOT/packages/instrument-core/python"

case "$SERVICE" in
  platform-api)
    export PYTHONPATH="$PROJECT_ROOT/apps/platform/backend:$PYTHONPATH"
    export PORTFOLIO_OPS_PLATFORM_ENVIRONMENT=local
    export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry
    export PORTFOLIO_OPS_PLATFORM_FRONTEND_URL=http://127.0.0.1:5172
    export PORTFOLIO_OPS_PLATFORM_WATCHLIST_URL=http://127.0.0.1:5173
    export PORTFOLIO_OPS_PLATFORM_PORTFOLIO_URL=http://127.0.0.1:5174
    export PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL=http://127.0.0.1:8000
    export PORTFOLIO_OPS_PLATFORM_CORS_ORIGINS='["http://127.0.0.1:5172","http://localhost:5172"]'
    cd "$PROJECT_ROOT/apps/platform/backend"
    exec "$PYTHON_BIN" -m uvicorn platform_app.main:app --host 127.0.0.1 --port 8002
    ;;
  platform-outbox-worker)
    export PYTHONPATH="$PROJECT_ROOT/apps/platform/backend:$PROJECT_ROOT/packages/instrument-core/python"
    export PORTFOLIO_OPS_PLATFORM_ENVIRONMENT=local
    export PORTFOLIO_OPS_PLATFORM_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA=instrument_registry
    export PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL=http://127.0.0.1:8000
    cd "$PROJECT_ROOT/apps/platform/backend"
    exec "$PYTHON_BIN" \
      "$PROJECT_ROOT/apps/platform/backend/scripts/run_market_data_outbox_worker.py" \
      --worker-id-prefix local-platform-market-data-outbox
    ;;
  watchlist-api)
    export PYTHONPATH="$PROJECT_ROOT/apps/watchlist/backend:$PYTHONPATH"
    export PORTFOLIO_OPS_WATCHLIST_ENVIRONMENT=local
    export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA=watchlist
    export PORTFOLIO_OPS_WATCHLIST_FRONTEND_URL=http://127.0.0.1:5173
    export PORTFOLIO_OPS_WATCHLIST_CORS_ORIGINS='["http://127.0.0.1:5173","http://localhost:5173"]'
    export PORTFOLIO_OPS_WATCHLIST_DOCUMENT_STORAGE_ROOT="$PROJECT_ROOT/var/watchlist-documents"
    cd "$PROJECT_ROOT/apps/watchlist/backend"
    exec "$PYTHON_BIN" -m uvicorn watchlist_app.main:app --host 127.0.0.1 --port 8000
    ;;
  watchlist-worker)
    export PYTHONPATH="$PROJECT_ROOT/apps/watchlist/backend:$PROJECT_ROOT/packages/instrument-core/python"
    export PORTFOLIO_OPS_WATCHLIST_ENVIRONMENT=local
    export PORTFOLIO_OPS_WATCHLIST_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA=watchlist
    cd "$PROJECT_ROOT/apps/watchlist/backend"
    exec "$PYTHON_BIN" -m watchlist_app.services.recalc_worker \
      --worker-id-prefix local-watchlist-recalc
    ;;
  portfolio-api)
    export PYTHONPATH="$PROJECT_ROOT/apps/portfolio/backend:$PROJECT_ROOT/packages/instrument-core/python:$PROJECT_ROOT/packages/calculation-core/python"
    export PORTFOLIO_OPS_PORTFOLIO_ENVIRONMENT=local
    export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA=portfolio
    export PORTFOLIO_OPS_PORTFOLIO_FRONTEND_URL=http://127.0.0.1:5174
    export PORTFOLIO_OPS_PORTFOLIO_CORS_ORIGINS='["http://127.0.0.1:5174","http://localhost:5174"]'
    export PORTFOLIO_OPS_PORTFOLIO_ALLOCATION_RESEARCH_OUTPUTS_ROOT="$PROJECT_ROOT/var/portfolio-allocation-research-outputs"
    cd "$PROJECT_ROOT/apps/portfolio/backend"
    exec "$PYTHON_BIN" -m uvicorn portfolio_app.main:app --host 127.0.0.1 --port 8001
    ;;
  portfolio-worker)
    export PYTHONPATH="$PROJECT_ROOT/apps/portfolio/backend:$PROJECT_ROOT/packages/instrument-core/python:$PROJECT_ROOT/packages/calculation-core/python"
    export PORTFOLIO_OPS_PORTFOLIO_ENVIRONMENT=local
    export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA=portfolio
    cd "$PROJECT_ROOT/apps/portfolio/backend"
    exec "$PYTHON_BIN" -m portfolio_app.calculations.portfolio_daily.worker \
      --worker-id-prefix local-portfolio-daily
    ;;
  platform-web)
    exec "$NODE_BIN" "$PROJECT_ROOT/deploy/serve_spa_proxy.mjs" \
      --host 127.0.0.1 --port 5172 \
      --dist "$PROJECT_ROOT/apps/platform/frontend/dist" \
      --api-target http://127.0.0.1:8002
    ;;
  watchlist-web)
    exec "$NODE_BIN" "$PROJECT_ROOT/deploy/serve_spa_proxy.mjs" \
      --host 127.0.0.1 --port 5173 \
      --dist "$PROJECT_ROOT/apps/watchlist/frontend/dist" \
      --api-target http://127.0.0.1:8000
    ;;
  portfolio-web)
    exec "$NODE_BIN" "$PROJECT_ROOT/deploy/serve_spa_proxy.mjs" \
      --host 127.0.0.1 --port 5174 \
      --dist "$PROJECT_ROOT/apps/portfolio/frontend/dist" \
      --api-target http://127.0.0.1:8001
    ;;
  *)
    echo "Unknown service: $SERVICE" >&2
    exit 64
    ;;
esac
