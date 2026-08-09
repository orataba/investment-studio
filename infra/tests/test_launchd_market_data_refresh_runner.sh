#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-launchd-refresh-runner-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

PROJECT_ROOT="$TEST_ROOT/project with spaces"
BACKEND_ROOT="$PROJECT_ROOT/apps/platform/backend"
REFRESH_SCRIPT="$BACKEND_ROOT/scripts/refresh_market_data_scheduled.py"
AUDIT_SCRIPT="$PROJECT_ROOT/infra/scripts/audit_live_data.py"
CAPTURE_PATH="$TEST_ROOT/captured.json"
AUDIT_CAPTURE_PATH="$TEST_ROOT/audit-ran"
SENTINEL_PATH="$TEST_ROOT/unsafe-env-executed"
LOCK_PATH="$PROJECT_ROOT/var/market-data-refresh.lock"
RUN_STATE_PATH="$PROJECT_ROOT/var/market-data-refresh-run-state.json"
ENV_ROOT="$TEST_ROOT/secure env"
mkdir -p \
  "$(dirname "$REFRESH_SCRIPT")" \
  "$(dirname "$AUDIT_SCRIPT")" \
  "$ENV_ROOT" \
  "$PROJECT_ROOT/infra/launchd"
chmod 700 "$ENV_ROOT"
cp "$REPOSITORY_ROOT/infra/launchd/load_runtime_env.sh" "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"

printf '%s\n' \
  'PORTFOLIO_OPS_PLATFORM_TUSHARE_TOKEN=test-token-loaded' \
  'PORTFOLIO_OPS_PLATFORM_EMAIL_SYNC_ENABLED=true' \
  'PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_PASSWORD=$(touch "'$SENTINEL_PATH'")' \
  > "$ENV_ROOT/platform.env"
chmod 600 "$ENV_ROOT/platform.env"

printf '%s\n' \
  'from __future__ import annotations' \
  'import json' \
  'import os' \
  'import sys' \
  'from pathlib import Path' \
  'Path(os.environ["CAPTURE_PATH"]).write_text(json.dumps({' \
  '    "argv": sys.argv[1:],' \
  '    "database_url": os.environ.get("PORTFOLIO_OPS_PLATFORM_DATABASE_URL"),' \
  '    "database_schema": os.environ.get("PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA"),' \
  '    "operations_database_schema": os.environ.get("PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA"),' \
  '    "environment": os.environ.get("PORTFOLIO_OPS_PLATFORM_ENVIRONMENT"),' \
  '    "watchlist_api_url": os.environ.get("PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL"),' \
  '    "portfolio_api_url": os.environ.get("PORTFOLIO_OPS_PLATFORM_PORTFOLIO_API_URL"),' \
  '    "tushare_token": os.environ.get("PORTFOLIO_OPS_PLATFORM_TUSHARE_TOKEN"),' \
  '    "email_sync_enabled": os.environ.get("PORTFOLIO_OPS_PLATFORM_EMAIL_SYNC_ENABLED"),' \
  '    "email_password": os.environ.get("PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_PASSWORD"),' \
  '    "pythonpath": os.environ.get("PYTHONPATH"),' \
  '}), encoding="utf-8")' \
  > "$REFRESH_SCRIPT"

printf '%s\n' \
  'import json' \
  'from pathlib import Path' \
  'import os' \
  'import sys' \
  'Path(os.environ["AUDIT_CAPTURE_PATH"]).write_text(json.dumps({' \
  '    "argv": sys.argv[1:],' \
  '    "database_url": os.environ.get("PORTFOLIO_OPS_PLATFORM_DATABASE_URL"),' \
  '}), encoding="utf-8")' \
  > "$AUDIT_SCRIPT"

export CAPTURE_PATH
export AUDIT_CAPTURE_PATH
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql://explicit/local" \
  PORTFOLIO_OPS_LOCAL_REFRESH_RUN_KIND=primary \
  PORTFOLIO_OPS_LOCAL_REFRESH_NOW=2026-07-22T21:00:00+08:00 \
  "$REPOSITORY_ROOT/infra/launchd/run_market_data_refresh.sh" \
  "$PROJECT_ROOT" "$(command -v python3)" "$LOCK_PATH" "$ENV_ROOT"

if [[ -e "$SENTINEL_PATH" ]]; then
  echo "The launchd runner executed shell syntax from the Platform .env file." >&2
  exit 1
fi
if [[ ! -e "$AUDIT_CAPTURE_PATH" ]]; then
  echo "The launchd runner skipped the post-refresh data audit." >&2
  exit 1
fi

CAPTURE_PATH="$CAPTURE_PATH" PROJECT_ROOT="$PROJECT_ROOT" LOCK_PATH="$LOCK_PATH" \
  RUN_STATE_PATH="$RUN_STATE_PATH" python3 - <<'PY'
import json
import os
from pathlib import Path


payload = json.loads(Path(os.environ["CAPTURE_PATH"]).read_text(encoding="utf-8"))
arguments = payload["argv"]
audit_payload = json.loads(
    Path(os.environ["AUDIT_CAPTURE_PATH"]).read_text(encoding="utf-8")
)
audit_arguments = audit_payload["argv"]
assert arguments[arguments.index("--channel") + 1] == "all"
assert arguments[arguments.index("--updated-by") + 1] == "launchd-scheduler"
assert arguments[arguments.index("--retry-failed-attempts") + 1] == "2"
assert arguments[arguments.index("--lock-file") + 1] == os.environ["LOCK_PATH"]
assert arguments[arguments.index("--summary-file") + 1] == str(
    Path(os.environ["PROJECT_ROOT"]) / "var" / "market-data-refresh-summary.json"
)
assert "--require-downstream-success" in arguments
assert "--fail-on-item-failure" in arguments
assert "--json" in arguments
assert payload["database_url"] == "postgresql+psycopg://explicit/local"
assert payload["database_schema"] == "instrument_registry"
assert payload["operations_database_schema"] == "platform"
assert payload["environment"] == "local"
assert payload["watchlist_api_url"] == "http://127.0.0.1:8000"
assert payload["portfolio_api_url"] == "http://127.0.0.1:8001"
assert payload["tushare_token"] == "test-token-loaded"
assert payload["email_sync_enabled"] == "true"
assert payload["email_password"].startswith("$(touch ")
assert audit_payload["database_url"] == "postgresql+psycopg://explicit/local"
assert "--database-url" not in audit_arguments
assert "postgresql+psycopg://explicit/local" not in audit_arguments
assert "--json" in audit_arguments
normalized_project_root = os.path.normpath(os.environ["PROJECT_ROOT"])
assert payload["pythonpath"] == (
    f"{normalized_project_root}/apps/platform/backend:"
    f"{normalized_project_root}/packages/instrument-core/python"
)
run_state = json.loads(Path(os.environ["RUN_STATE_PATH"]).read_text(encoding="utf-8"))
assert run_state["status"] == "succeeded"
assert run_state["run_kind"] == "primary"
assert run_state["scheduled_date"] == "2026-07-22"
assert run_state["refresh_exit_code"] == 0
assert run_state["audit_exit_code"] == 0
PY

rm -f "$CAPTURE_PATH" "$AUDIT_CAPTURE_PATH"
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
  PORTFOLIO_OPS_LOCAL_REFRESH_RUN_KIND=retry \
  PORTFOLIO_OPS_LOCAL_REFRESH_NOW=2026-07-22T23:00:00+08:00 \
  "$REPOSITORY_ROOT/infra/launchd/run_market_data_refresh.sh" \
  "$PROJECT_ROOT" "$(command -v python3)" "$LOCK_PATH" "$ENV_ROOT" \
  > "$TEST_ROOT/retry-skipped.out"
grep -q 'the primary run already succeeded' "$TEST_ROOT/retry-skipped.out"
if [[ -e "$CAPTURE_PATH" || -e "$AUDIT_CAPTURE_PATH" ]]; then
  echo "The conditional retry reran work after a successful primary run." >&2
  exit 1
fi

RUN_STATE_PATH="$RUN_STATE_PATH" python3 - <<'PY'
import json
import os
from pathlib import Path


path = Path(os.environ["RUN_STATE_PATH"])
payload = json.loads(path.read_text(encoding="utf-8"))
payload["status"] = "failed"
path.write_text(json.dumps(payload), encoding="utf-8")
PY
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
  PORTFOLIO_OPS_LOCAL_REFRESH_RUN_KIND=retry \
  PORTFOLIO_OPS_LOCAL_REFRESH_NOW=2026-07-22T23:00:00+08:00 \
  "$REPOSITORY_ROOT/infra/launchd/run_market_data_refresh.sh" \
  "$PROJECT_ROOT" "$(command -v python3)" "$LOCK_PATH" "$ENV_ROOT"
if [[ ! -e "$CAPTURE_PATH" || ! -e "$AUDIT_CAPTURE_PATH" ]]; then
  echo "The conditional retry did not run after a failed primary run." >&2
  exit 1
fi

set +e
env \
  -u PORTFOLIO_OPS_LOCAL_DATABASE_URL \
  PORTFOLIO_OPS_PLATFORM_DATABASE_URL=postgresql+psycopg://must-not-be-used/other \
  "$REPOSITORY_ROOT/infra/launchd/run_market_data_refresh.sh" \
  "$PROJECT_ROOT" "$(command -v python3)" "$LOCK_PATH" "$ENV_ROOT" \
  > "$TEST_ROOT/missing-database-url.out" 2>&1
missing_database_url_status=$?
set -e
if [[ $missing_database_url_status -eq 0 ]]; then
  echo "The scheduled runner accepted a missing database URL." >&2
  exit 1
fi
grep -q 'PORTFOLIO_OPS_LOCAL_DATABASE_URL is required' "$TEST_ROOT/missing-database-url.out"

mkdir -p "$PROJECT_ROOT/apps/watchlist/backend"
ln -s "$TEST_ROOT/missing-watchlist-env" "$PROJECT_ROOT/apps/watchlist/backend/.env"
set +e
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
  "$REPOSITORY_ROOT/infra/launchd/run_market_data_refresh.sh" \
  "$PROJECT_ROOT" "$(command -v python3)" "$LOCK_PATH" "$ENV_ROOT" \
  > "$TEST_ROOT/repository-env.out" 2>&1
repository_env_status=$?
set -e
if [[ $repository_env_status -eq 0 ]]; then
  echo "The scheduled runner accepted a repository-local .env symlink." >&2
  exit 1
fi
grep -q 'Repository runtime environment files are not allowed for managed services' \
  "$TEST_ROOT/repository-env.out"

echo "launchd scheduled refresh runner test passed."
