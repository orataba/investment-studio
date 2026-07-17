#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "Usage: $0 <service> <project-root> <python-bin> <node-bin> <external-env-root>" >&2
  exit 64
fi

SERVICE="$1"
PROJECT_ROOT="$(cd "$2" && pwd)"
PYTHON_BIN="$3"
NODE_BIN="$4"
EXTERNAL_ENV_ROOT="$5"
DATABASE_URL=""

case "$SERVICE" in
  platform-api|watchlist-api|portfolio-api)
    DATABASE_URL="${PORTFOLIO_OPS_LOCAL_DATABASE_URL:-}"
    if [[ -z "$DATABASE_URL" ]]; then
      echo "PORTFOLIO_OPS_LOCAL_DATABASE_URL is required for API services." >&2
      exit 64
    fi
    case "$DATABASE_URL" in
      postgresql://*|postgresql+psycopg://*) ;;
      *)
        echo "PORTFOLIO_OPS_LOCAL_DATABASE_URL must use postgresql:// or postgresql+psycopg://." >&2
        exit 64
        ;;
    esac
    ;;
esac

# Load only namespaced dotenv assignments as data.  This preserves the current
# external secret file without copying secrets into a plist or evaluating shell
# syntax contained in a value.
source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
case "$SERVICE" in
  platform-api|watchlist-api|portfolio-api)
    portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
    ;;
esac
case "$SERVICE" in
  platform-api)
    ENV_FILE="$(portfolio_ops_runtime_env_file platform "$EXTERNAL_ENV_ROOT")"
    portfolio_ops_load_env_file "$ENV_FILE" PORTFOLIO_OPS_PLATFORM_
    ;;
  watchlist-api)
    ENV_FILE="$(portfolio_ops_runtime_env_file watchlist "$EXTERNAL_ENV_ROOT")"
    if [[ -f "$ENV_FILE" ]]; then
      portfolio_ops_load_env_file "$ENV_FILE" PORTFOLIO_OPS_WATCHLIST_
    fi
    ;;
  portfolio-api)
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
    export PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA=platform
    export PORTFOLIO_OPS_PLATFORM_FRONTEND_URL=http://127.0.0.1:5172
    export PORTFOLIO_OPS_PLATFORM_WATCHLIST_URL=http://127.0.0.1:5173
    export PORTFOLIO_OPS_PLATFORM_PORTFOLIO_URL=http://127.0.0.1:5174
    export PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL=http://127.0.0.1:8000
    export PORTFOLIO_OPS_PLATFORM_PORTFOLIO_API_URL=http://127.0.0.1:8001
    export PORTFOLIO_OPS_PLATFORM_CORS_ORIGINS='["http://127.0.0.1:5172","http://localhost:5172"]'
    cd "$PROJECT_ROOT/apps/platform/backend"
    exec "$PYTHON_BIN" -m uvicorn platform_app.main:app --host 127.0.0.1 --port 8002
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
  portfolio-api)
    export PYTHONPATH="$PROJECT_ROOT/apps/portfolio/backend:$PYTHONPATH"
    export PORTFOLIO_OPS_PORTFOLIO_ENVIRONMENT=local
    export PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL="$DATABASE_URL"
    export PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA=portfolio
    export PORTFOLIO_OPS_PORTFOLIO_FRONTEND_URL=http://127.0.0.1:5174
    export PORTFOLIO_OPS_PORTFOLIO_CORS_ORIGINS='["http://127.0.0.1:5174","http://localhost:5174"]'
    export PORTFOLIO_OPS_PORTFOLIO_RESEARCH_OUTPUTS_ROOT="$PROJECT_ROOT/var/portfolio-research-outputs"
    cd "$PROJECT_ROOT/apps/portfolio/backend"
    exec "$PYTHON_BIN" -m uvicorn portfolio_app.main:app --host 127.0.0.1 --port 8001
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
