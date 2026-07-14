#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS launchd." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
LABEL_PREFIX="${LABEL_PREFIX:-com.orataba.portfolio-ops}"
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
LOG_DIR="${LOG_DIR:-$HOME/Library/Logs/portfolio-operations-workbench}"
ENV_ROOT="${PORTFOLIO_OPS_LOCAL_ENV_ROOT:-$HOME/.config/orataba/secrets/portfolio-operations-workbench}"
WEB_RELEASES_ROOT="${PORTFOLIO_OPS_LOCAL_WEB_RELEASES_ROOT:-$PROJECT_ROOT/var/local-web-releases}"
BUILD_FRONTENDS="${BUILD_FRONTENDS:-true}"
DATABASE_URL="${PORTFOLIO_OPS_LOCAL_DATABASE_URL:-postgresql+psycopg://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops}"
REFRESH_HOUR="${PORTFOLIO_OPS_LOCAL_REFRESH_HOUR:-21}"
REFRESH_MINUTE="${PORTFOLIO_OPS_LOCAL_REFRESH_MINUTE:-0}"
RELEASE_AS_OF_DATE="${PORTFOLIO_OPS_RELEASE_AS_OF_DATE:-}"

PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
NODE_BIN_DIR="${NODE_BIN%/*}"
if [[ -z "${NPM_BIN:-}" && -x "$NODE_BIN_DIR/npm" ]]; then
  NPM_BIN="$NODE_BIN_DIR/npm"
else
  NPM_BIN="${NPM_BIN:-$(command -v npm || true)}"
fi

for executable in "$PYTHON_BIN" "$NODE_BIN" "$NPM_BIN"; do
  if [[ -z "$executable" || ! -x "$executable" ]]; then
    echo "Required executable is missing: ${executable:-<empty>}" >&2
    exit 1
  fi
done

NODE_VERSION_FILE="$PROJECT_ROOT/.node-version"
if [[ ! -f "$NODE_VERSION_FILE" ]]; then
  echo "Missing Node.js version file: $NODE_VERSION_FILE" >&2
  exit 1
fi
expected_node_version="$(tr -d '[:space:]' < "$NODE_VERSION_FILE")"
actual_node_version="$("$NODE_BIN" --version)"
actual_node_version="${actual_node_version#v}"
if [[ "$actual_node_version" != "$expected_node_version" ]]; then
  echo "Node.js $expected_node_version is required; found $actual_node_version." >&2
  exit 1
fi
# npm uses an env-based shebang.  Prepend the validated Node directory so
# frontend builds cannot silently execute under a different system Node.
export PATH="$NODE_BIN_DIR:$PATH"

if [[ ! -x "$PROJECT_ROOT/infra/scripts/migrate_all.sh" ]]; then
  echo "Missing migration runner: $PROJECT_ROOT/infra/scripts/migrate_all.sh" >&2
  exit 1
fi
if [[ ! -x "$PROJECT_ROOT/infra/scripts/release_database.sh" ]]; then
  echo "Missing safe database release orchestrator: $PROJECT_ROOT/infra/scripts/release_database.sh" >&2
  exit 1
fi
if [[ ! -f "$SCRIPT_DIR/generate_local_service_plists.py" ]]; then
  echo "Missing LaunchAgent plist generator: $SCRIPT_DIR/generate_local_service_plists.py" >&2
  exit 1
fi
if [[ ! -f "$SCRIPT_DIR/stage_local_runtime.py" ]]; then
  echo "Missing runtime release stager: $SCRIPT_DIR/stage_local_runtime.py" >&2
  exit 1
fi
if [[ ! -f "$SCRIPT_DIR/load_runtime_env.sh" ]]; then
  echo "Missing safe runtime environment loader: $SCRIPT_DIR/load_runtime_env.sh" >&2
  exit 1
fi
if [[ ! -x "$SCRIPT_DIR/run_market_data_refresh.sh" ]]; then
  echo "Missing scheduled refresh runner: $SCRIPT_DIR/run_market_data_refresh.sh" >&2
  exit 1
fi
if [[ ! "$REFRESH_HOUR" =~ ^[0-9]+$ || "$REFRESH_HOUR" -gt 23 ]]; then
  echo "PORTFOLIO_OPS_LOCAL_REFRESH_HOUR must be an integer between 0 and 23." >&2
  exit 64
fi
if [[ ! "$REFRESH_MINUTE" =~ ^[0-9]+$ || "$REFRESH_MINUTE" -gt 59 ]]; then
  echo "PORTFOLIO_OPS_LOCAL_REFRESH_MINUTE must be an integer between 0 and 59." >&2
  exit 64
fi
if [[ -z "$RELEASE_AS_OF_DATE" ]]; then
  echo "Set PORTFOLIO_OPS_RELEASE_AS_OF_DATE to an explicit YYYY-MM-DD." >&2
  exit 64
fi
if [[ -z "${CONFIRM_RELEASE:-}" ]]; then
  echo "Set CONFIRM_RELEASE to database@host:port after confirming the release target." >&2
  exit 64
fi

source "$SCRIPT_DIR/load_runtime_env.sh"
source "$PROJECT_ROOT/infra/service_inventory.sh"
source "$PROJECT_ROOT/infra/scripts/runtime_readiness.sh"
portfolio_ops_reject_repository_env_files "$PROJECT_ROOT"
for env_spec in \
  platform:PORTFOLIO_OPS_PLATFORM_ \
  watchlist:PORTFOLIO_OPS_WATCHLIST_ \
  portfolio:PORTFOLIO_OPS_PORTFOLIO_; do
  app="${env_spec%%:*}"
  prefix="${env_spec#*:}"
  runtime_env_file="$(portfolio_ops_runtime_env_file "$app" "$ENV_ROOT")"
  if [[ "$app" == "platform" || -f "$runtime_env_file" ]]; then
    portfolio_ops_validate_env_file "$runtime_env_file" "$prefix"
  fi
done

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR" "$PROJECT_ROOT/var/watchlist-documents" \
  "$PROJECT_ROOT/var/portfolio-allocation-research-outputs"

"$SCRIPT_DIR/bootstrap_local_database.sh"

# Build and stage every frontend before stopping a healthy installation or
# migrating its database.  Network/package/build failures therefore leave the
# currently running system untouched.
if [[ "$BUILD_FRONTENDS" == "true" ]]; then
  for app in platform watchlist portfolio; do
    "$NPM_BIN" --prefix "$PROJECT_ROOT/apps/$app/frontend" ci
    "$NPM_BIN" --prefix "$PROJECT_ROOT/apps/$app/frontend" run build
  done
fi

for app in platform watchlist portfolio; do
  if [[ ! -f "$PROJECT_ROOT/apps/$app/frontend/dist/index.html" ]]; then
    echo "Missing frontend build: apps/$app/frontend/dist/index.html" >&2
    exit 1
  fi
done

# Stage the web build and every backend/runtime source used by launchd into one
# versioned release before stopping a healthy installation.  Service restarts
# therefore cannot silently pick up a newer repository checkout.
mkdir -p "$WEB_RELEASES_ROOT"
web_release_id="release-$(date -u +%Y%m%dT%H%M%SZ)-$$"
web_release_stage="$(mktemp -d "$WEB_RELEASES_ROOT/.staging.XXXXXX")"
cleanup_release_stage() {
  if [[ -n "${web_release_stage:-}" && -d "$web_release_stage" ]]; then
    rm -rf "$web_release_stage"
  fi
}
trap cleanup_release_stage EXIT
for app in platform watchlist portfolio; do
  mkdir -p "$web_release_stage/$app"
  cp -R "$PROJECT_ROOT/apps/$app/frontend/dist/." "$web_release_stage/$app/"
  if [[ -d "$WEB_RELEASES_ROOT/current/$app/assets" ]]; then
    mkdir -p "$web_release_stage/$app/assets"
    cp -R "$WEB_RELEASES_ROOT/current/$app/assets/." \
      "$web_release_stage/$app/assets/"
  fi
done
"$PYTHON_BIN" "$SCRIPT_DIR/stage_local_runtime.py" stage \
  --project-root "$PROJECT_ROOT" \
  --destination "$web_release_stage/runtime" \
  --release-id "$web_release_id"
"$PYTHON_BIN" - "$web_release_stage" "$web_release_id" <<'PY'
from pathlib import Path
import sys


release_root = Path(sys.argv[1])
release_id = sys.argv[2]
for app_name in ("platform", "watchlist", "portfolio"):
    app_root = release_root / app_name
    index_path = app_root / "index.html"
    if index_path.is_symlink() or not index_path.is_file():
        raise SystemExit(f"staged web release is missing a plain index: {index_path}")
    linked_path = next((path for path in app_root.rglob("*") if path.is_symlink()), None)
    if linked_path is not None:
        raise SystemExit(f"staged web release must not contain symlinks: {linked_path}")
    (app_root / "release-id.txt").write_text(
        f"{release_id}\n",
        encoding="utf-8",
    )
PY
web_release_dir="$WEB_RELEASES_ROOT/$web_release_id"
if [[ -e "$web_release_dir" || -L "$web_release_dir" ]]; then
  echo "Release destination already exists: $web_release_dir" >&2
  exit 1
fi
mv "$web_release_stage" "$web_release_dir"
web_release_stage=""
trap - EXIT
runtime_release_dir="$web_release_dir/runtime"
"$PYTHON_BIN" "$runtime_release_dir/infra/launchd/stage_local_runtime.py" verify \
  --runtime-root "$runtime_release_dir" \
  --release-id "$web_release_id"
source "$runtime_release_dir/infra/service_inventory.sh"
source "$runtime_release_dir/infra/scripts/runtime_readiness.sh"

SERVICE_STATE_FILE="$(mktemp "${TMPDIR:-/tmp}/portfolio-ops-launchd-install-state.XXXXXX")"
services_stopped=false
leave_services_stopped_on_failure() {
  local exit_code=$?
  trap - EXIT
  if [[ $exit_code -ne 0 && "$services_stopped" == "true" ]]; then
    local service
    for service in "${PORTFOLIO_OPS_ALL_SERVICE_NAMES[@]}"; do
      launchctl bootout "gui/$UID/$LABEL_PREFIX.$service" >/dev/null 2>&1 || true
    done
    echo "Install failed; managed services remain stopped for operator review." >&2
  fi
  rm -f "$SERVICE_STATE_FILE"
  exit "$exit_code"
}
trap leave_services_stopped_on_failure EXIT

# A running pre-upgrade worker does not understand a newly introduced database
# fencing protocol.  Stop every managed process before applying migrations.
services_stopped=true
LABEL_PREFIX="$LABEL_PREFIX" LAUNCH_AGENTS_DIR="$LAUNCH_AGENTS_DIR" \
  "$SCRIPT_DIR/control_local_services.sh" stop "$SERVICE_STATE_FILE"

# A listener left behind by an unmanaged or partially stopped process could
# answer the later health probes and make an old release look like this one.
for port in 8002 8000 8001 5172 5173 5174; do
  listener_pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -n "$listener_pids" ]]; then
    echo "Refusing release: TCP port $port is still owned by PID(s) $listener_pids." >&2
    exit 1
  fi
done

CONFIRM_RELEASE="$CONFIRM_RELEASE" \
PORTFOLIO_OPS_RELEASE_DATABASE_URL="$DATABASE_URL" \
PORTFOLIO_OPS_RELEASE_AS_OF_DATE="$RELEASE_AS_OF_DATE" \
PORTFOLIO_OPS_RELEASE_SERVICE_MANAGER=none \
PYTHONDONTWRITEBYTECODE=1 \
PROJECT_ROOT="$runtime_release_dir" PYTHON_BIN="$PYTHON_BIN" \
  "$runtime_release_dir/infra/scripts/release_database.sh"

"$PYTHON_BIN" - "$web_release_dir" "$WEB_RELEASES_ROOT/current" <<'PY'
from __future__ import annotations

import os
from pathlib import Path
import sys


release_dir = Path(sys.argv[1]).resolve()
current_link = Path(sys.argv[2])
next_link = current_link.with_name(f".{current_link.name}.next-{os.getpid()}")
os.symlink(release_dir, next_link)
os.replace(next_link, current_link)
PY

"$PYTHON_BIN" "$runtime_release_dir/infra/launchd/generate_local_service_plists.py" \
  --project-root "$PROJECT_ROOT" \
  --runtime-root "$runtime_release_dir" \
  --python-bin "$PYTHON_BIN" \
  --node-bin "$NODE_BIN" \
  --database-url "$DATABASE_URL" \
  --label-prefix "$LABEL_PREFIX" \
  --launch-agents-dir "$LAUNCH_AGENTS_DIR" \
  --log-dir "$LOG_DIR" \
  --env-root "$ENV_ROOT" \
  --web-release-root "$web_release_dir" \
  --release-id "$web_release_id" \
  --refresh-hour "$REFRESH_HOUR" \
  --refresh-minute "$REFRESH_MINUTE"

domain="gui/$UID"
backend_services=(
  platform-api
  watchlist-api
  watchlist-worker
  platform-outbox-worker
  portfolio-api
  portfolio-worker
)
web_services=(platform-web watchlist-web portfolio-web)

bootstrap_service() {
  local service="$1"
  label="$LABEL_PREFIX.$service"
  plist="$LAUNCH_AGENTS_DIR/$label.plist"
  launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
  launchctl bootstrap "$domain" "$plist"
  launchctl enable "$domain/$label"
}

# Keep every public web port closed until the API/worker cohort is ready and
# the exact-decimal read contract has been exercised directly against port
# 8001.  A failed or stale API can therefore never be paired with the newly
# published frontend, even briefly.
for service in \
  "${backend_services[@]}" \
  "${PORTFOLIO_OPS_SCHEDULED_SERVICE_NAMES[@]}"; do
  bootstrap_service "$service"
done

for service in "${backend_services[@]}"; do
  launchctl kickstart -k "$domain/$LABEL_PREFIX.$service"
done

backend_health_urls=(
  http://127.0.0.1:8002/api/readiness
  http://127.0.0.1:8000/api/readiness
  http://127.0.0.1:8001/api/readiness
)
backend_ready=false
for attempt in {1..30}; do
  healthy=true
  for url in "${backend_health_urls[@]}"; do
    if ! curl --noproxy '*' --max-time 2 --fail --silent --output /dev/null "$url"; then
      healthy=false
      break
    fi
  done
  if [[ "$healthy" == "true" ]]; then
    contract_ready=false
    for contract_attempt in 1 2 3; do
      if portfolio_ops_verify_portfolio_read_contract \
        "$PYTHON_BIN" "http://127.0.0.1:8001" "$web_release_id"; then
        contract_ready=true
        break
      fi
      sleep 1
    done
    if [[ "$contract_ready" != "true" ]]; then
      echo "Portfolio API read-contract smoke failed." >&2
      "$SCRIPT_DIR/status_local_services.sh" || true
      exit 1
    fi
    backend_ready=true
    break
  fi
  sleep 1
done

if [[ "$backend_ready" != "true" ]]; then
  echo "Backend services did not become ready; web ports were not opened." >&2
  "$SCRIPT_DIR/status_local_services.sh" || true
  exit 1
fi

for service in "${web_services[@]}"; do
  bootstrap_service "$service"
  launchctl kickstart -k "$domain/$LABEL_PREFIX.$service"
done

web_health_urls=(
  http://127.0.0.1:5172/
  http://127.0.0.1:5173/
  http://127.0.0.1:5174/
)
for attempt in {1..30}; do
  healthy=true
  for url in "${web_health_urls[@]}"; do
    if ! curl --noproxy '*' --max-time 2 --fail --silent --output /dev/null "$url"; then
      healthy=false
      break
    fi
  done
  if [[ "$healthy" == "true" ]]; then
    for url in \
      http://127.0.0.1:5172/release-id.txt \
      http://127.0.0.1:5173/release-id.txt \
      http://127.0.0.1:5174/release-id.txt; do
      actual_release_id="$(
        curl --noproxy '*' --max-time 2 --fail --silent "$url"
      )"
      if [[ "$actual_release_id" != "$web_release_id" ]]; then
        echo "Web release identity mismatch at $url." >&2
        exit 1
      fi
    done
    services_stopped=false
    rm -f "$SERVICE_STATE_FILE"
    trap - EXIT
    echo "Portfolio Operations Workbench is running at http://127.0.0.1:5172"
    exit 0
  fi
  sleep 1
done

echo "Services were installed, but one or more health checks did not become ready." >&2
"$SCRIPT_DIR/status_local_services.sh" || true
exit 1
