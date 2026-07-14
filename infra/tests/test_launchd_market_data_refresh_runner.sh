#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-launchd-refresh-runner-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

STATE_ROOT="$TEST_ROOT/project with spaces"
RUNTIME_ROOT="$TEST_ROOT/release/runtime"
BACKEND_ROOT="$RUNTIME_ROOT/apps/platform/backend"
REFRESH_SCRIPT="$BACKEND_ROOT/scripts/refresh_market_data_scheduled.py"
WAIT_SCRIPT="$RUNTIME_ROOT/infra/scripts/wait_for_refresh_convergence.py"
WATCHLIST_REBUILD_SCRIPT="$RUNTIME_ROOT/apps/watchlist/backend/scripts/rebuild_watchlist_derived_state.py"
PORTFOLIO_PUBLISH_SCRIPT="$RUNTIME_ROOT/apps/portfolio/backend/scripts/publish_portfolio_daily.py"
AUDIT_SCRIPT="$RUNTIME_ROOT/infra/scripts/audit_live_data.py"
CAPTURE_PATH="$TEST_ROOT/captured.json"
WAIT_CAPTURE_PATH="$TEST_ROOT/wait-captured.jsonl"
REBUILD_CAPTURE_PATH="$TEST_ROOT/rebuild-captured.json"
PUBLISH_CAPTURE_PATH="$TEST_ROOT/publish-captured.json"
AUDIT_CAPTURE_PATH="$TEST_ROOT/audit-ran"
COMMAND_LOG_PATH="$TEST_ROOT/command-order.log"
SENTINEL_PATH="$TEST_ROOT/unsafe-env-executed"
LOCK_PATH="$STATE_ROOT/var/market-data-refresh.lock"
ENV_ROOT="$TEST_ROOT/secure env"
CLI_HELP_PATH="$TEST_ROOT/refresh-cli-help.txt"
CONTRACT_PYTHON="${PYTHON_BIN:-$REPOSITORY_ROOT/.venv/bin/python}"
if [[ ! -x "$CONTRACT_PYTHON" ]]; then
  echo "Python environment for the refresh CLI contract is missing: $CONTRACT_PYTHON" >&2
  exit 1
fi
PYTHONPATH="$REPOSITORY_ROOT/apps/platform/backend:$REPOSITORY_ROOT/packages/instrument-core/python" \
  "$CONTRACT_PYTHON" \
  "$REPOSITORY_ROOT/apps/platform/backend/scripts/refresh_market_data_scheduled.py" \
  --help > "$CLI_HELP_PATH"
mkdir -p \
  "$(dirname "$REFRESH_SCRIPT")" \
  "$(dirname "$WAIT_SCRIPT")" \
  "$(dirname "$WATCHLIST_REBUILD_SCRIPT")" \
  "$(dirname "$PORTFOLIO_PUBLISH_SCRIPT")" \
  "$(dirname "$AUDIT_SCRIPT")" \
  "$ENV_ROOT" \
  "$RUNTIME_ROOT/infra/launchd" \
  "$RUNTIME_ROOT/packages/instrument-core/python" \
  "$STATE_ROOT/apps/platform/backend" \
  "$STATE_ROOT/apps/watchlist/backend" \
  "$STATE_ROOT/apps/portfolio/backend"
STATE_ROOT="$(cd "$STATE_ROOT" && pwd -P)"
RUNTIME_ROOT="$(cd "$RUNTIME_ROOT" && pwd -P)"
BACKEND_ROOT="$RUNTIME_ROOT/apps/platform/backend"
REFRESH_SCRIPT="$BACKEND_ROOT/scripts/refresh_market_data_scheduled.py"
PORTFOLIO_PUBLISH_SCRIPT="$RUNTIME_ROOT/apps/portfolio/backend/scripts/publish_portfolio_daily.py"
AUDIT_SCRIPT="$RUNTIME_ROOT/infra/scripts/audit_live_data.py"
LOCK_PATH="$STATE_ROOT/var/market-data-refresh.lock"
chmod 700 "$ENV_ROOT"
cp "$REPOSITORY_ROOT/infra/launchd/load_runtime_env.sh" "$RUNTIME_ROOT/infra/launchd/load_runtime_env.sh"
: > "$RUNTIME_ROOT/infra/launchd/stage_local_runtime.py"

VERIFYING_PYTHON="$TEST_ROOT/verifying-python"
REAL_PYTHON="$(command -v python3)"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'if [[ "${1##*/}" == "stage_local_runtime.py" && "${2:-}" == "verify" ]]; then exit 0; fi' \
  'exec "$REAL_PYTHON" "$@"' \
  > "$VERIFYING_PYTHON"
chmod +x "$VERIFYING_PYTHON"
export REAL_PYTHON

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
  'with Path(os.environ["COMMAND_LOG_PATH"]).open("a", encoding="utf-8") as output:' \
  '    output.write("refresh\n")' \
  'Path(os.environ["CAPTURE_PATH"]).write_text(json.dumps({' \
  '    "argv": sys.argv[1:],' \
  '    "database_url": os.environ.get("PORTFOLIO_OPS_PLATFORM_DATABASE_URL"),' \
  '    "database_schema": os.environ.get("PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA"),' \
  '    "environment": os.environ.get("PORTFOLIO_OPS_PLATFORM_ENVIRONMENT"),' \
  '    "watchlist_api_url": os.environ.get("PORTFOLIO_OPS_PLATFORM_WATCHLIST_API_URL"),' \
  '    "tushare_token": os.environ.get("PORTFOLIO_OPS_PLATFORM_TUSHARE_TOKEN"),' \
  '    "email_sync_enabled": os.environ.get("PORTFOLIO_OPS_PLATFORM_EMAIL_SYNC_ENABLED"),' \
  '    "email_password": os.environ.get("PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_PASSWORD"),' \
  '    "pythonpath": os.environ.get("PYTHONPATH"),' \
  '}), encoding="utf-8")' \
  > "$REFRESH_SCRIPT"

printf '%s\n' \
  'from __future__ import annotations' \
  'import json' \
  'import os' \
  'import sys' \
  'from pathlib import Path' \
  'phase = sys.argv[sys.argv.index("--phase") + 1]' \
  'with Path(os.environ["COMMAND_LOG_PATH"]).open("a", encoding="utf-8") as output:' \
  '    output.write(f"wait:{phase}\n")' \
  'with Path(os.environ["WAIT_CAPTURE_PATH"]).open("a", encoding="utf-8") as output:' \
  '    output.write(json.dumps({"argv": sys.argv[1:]}, sort_keys=True) + "\n")' \
  'if os.environ.get("WAIT_FAIL_PHASE") == phase:' \
  '    raise SystemExit(1)' \
  > "$WAIT_SCRIPT"

printf '%s\n' \
  'from __future__ import annotations' \
  'import json' \
  'import os' \
  'import sys' \
  'from pathlib import Path' \
  'with Path(os.environ["COMMAND_LOG_PATH"]).open("a", encoding="utf-8") as output:' \
  '    output.write("rebuild\n")' \
  'Path(os.environ["REBUILD_CAPTURE_PATH"]).write_text(json.dumps({' \
  '    "argv": sys.argv[1:],' \
  '    "database_url": os.environ.get("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL"),' \
  '    "database_schema": os.environ.get("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA"),' \
  '    "environment": os.environ.get("PORTFOLIO_OPS_WATCHLIST_ENVIRONMENT"),' \
  '    "pythonpath": os.environ.get("PYTHONPATH"),' \
  '}), encoding="utf-8")' \
  > "$WATCHLIST_REBUILD_SCRIPT"

printf '%s\n' \
  'from __future__ import annotations' \
  'import json' \
  'import os' \
  'import sys' \
  'from pathlib import Path' \
  'with Path(os.environ["COMMAND_LOG_PATH"]).open("a", encoding="utf-8") as output:' \
  '    output.write("publish\n")' \
  'Path(os.environ["PUBLISH_CAPTURE_PATH"]).write_text(json.dumps({' \
  '    "argv": sys.argv[1:],' \
  '    "database_url": os.environ.get("PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL"),' \
  '    "database_schema": os.environ.get("PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA"),' \
  '    "environment": os.environ.get("PORTFOLIO_OPS_PORTFOLIO_ENVIRONMENT"),' \
  '    "outputs_root": os.environ.get("PORTFOLIO_OPS_PORTFOLIO_ALLOCATION_RESEARCH_OUTPUTS_ROOT"),' \
  '    "pythonpath": os.environ.get("PYTHONPATH"),' \
  '}), encoding="utf-8")' \
  'if os.environ.get("PUBLISH_FAIL") == "true":' \
  '    raise SystemExit(1)' \
  > "$PORTFOLIO_PUBLISH_SCRIPT"

printf '%s\n' \
  'from pathlib import Path' \
  'import os' \
  'import json' \
  'import sys' \
  'with Path(os.environ["COMMAND_LOG_PATH"]).open("a", encoding="utf-8") as output:' \
  '    output.write("audit\n")' \
  'Path(os.environ["AUDIT_CAPTURE_PATH"]).write_text(json.dumps({"argv": sys.argv[1:]}), encoding="utf-8")' \
  > "$AUDIT_SCRIPT"

export CAPTURE_PATH
export WAIT_CAPTURE_PATH
export REBUILD_CAPTURE_PATH
export PUBLISH_CAPTURE_PATH
export AUDIT_CAPTURE_PATH
export COMMAND_LOG_PATH
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
PORTFOLIO_OPS_LOCAL_RELEASE_ID=release-test \
PORTFOLIO_OPS_LOCAL_STATE_ROOT="$STATE_ROOT" \
PORTFOLIO_OPS_LOCAL_REFRESH_VALUATION_DATE=2026-07-14 \
  "$REPOSITORY_ROOT/infra/launchd/run_market_data_refresh.sh" \
  "$RUNTIME_ROOT" "$VERIFYING_PYTHON" "$LOCK_PATH" "$ENV_ROOT"

if [[ -e "$SENTINEL_PATH" ]]; then
  echo "The launchd runner executed shell syntax from the Platform .env file." >&2
  exit 1
fi
if [[ ! -e "$AUDIT_CAPTURE_PATH" ]]; then
  echo "The launchd runner skipped the post-refresh data audit." >&2
  exit 1
fi

CAPTURE_PATH="$CAPTURE_PATH" WAIT_CAPTURE_PATH="$WAIT_CAPTURE_PATH" REBUILD_CAPTURE_PATH="$REBUILD_CAPTURE_PATH" PUBLISH_CAPTURE_PATH="$PUBLISH_CAPTURE_PATH" AUDIT_CAPTURE_PATH="$AUDIT_CAPTURE_PATH" COMMAND_LOG_PATH="$COMMAND_LOG_PATH" STATE_ROOT="$STATE_ROOT" RUNTIME_ROOT="$RUNTIME_ROOT" LOCK_PATH="$LOCK_PATH" CLI_HELP_PATH="$CLI_HELP_PATH" python3 - <<'PY'
import json
import os
from pathlib import Path


payload = json.loads(Path(os.environ["CAPTURE_PATH"]).read_text(encoding="utf-8"))
arguments = payload["argv"]
help_text = Path(os.environ["CLI_HELP_PATH"]).read_text(encoding="utf-8")
assert arguments[arguments.index("--channel") + 1] == "all"
assert arguments[arguments.index("--updated-by") + 1] == "launchd-scheduler"
assert arguments[arguments.index("--retry-failed-attempts") + 1] == "2"
assert arguments[arguments.index("--lock-file") + 1] == os.environ["LOCK_PATH"]
actual_summary = os.path.normpath(arguments[arguments.index("--summary-file") + 1])
expected_summary = os.path.normpath(
    str(Path(os.environ["STATE_ROOT"]) / "var" / "market-data-refresh-summary.json")
)
assert actual_summary == expected_summary, (actual_summary, expected_summary)
assert "--fail-on-item-failure" in arguments
assert "--json" in arguments
for argument in arguments:
    if argument.startswith("--"):
        assert argument in help_text, f"runner passed an option absent from CLI --help: {argument}"
assert "--require-downstream-success" not in arguments
assert "--require-downstream-success" not in help_text
assert payload["database_url"] == "postgresql+psycopg://explicit/local"
assert payload["database_schema"] == "instrument_registry"
assert payload["environment"] == "local"
assert payload["watchlist_api_url"] == "http://127.0.0.1:8000"
assert payload["tushare_token"] == "test-token-loaded"
assert payload["email_sync_enabled"] == "true"
assert payload["email_password"].startswith("$(touch ")
normalized_runtime_root = os.path.normpath(os.environ["RUNTIME_ROOT"])
assert payload["pythonpath"] == (
    f"{normalized_runtime_root}/apps/platform/backend:"
    f"{normalized_runtime_root}/packages/instrument-core/python"
)

wait_calls = [
    json.loads(line)
    for line in Path(os.environ["WAIT_CAPTURE_PATH"]).read_text(encoding="utf-8").splitlines()
]
assert len(wait_calls) == 2
for expected_phase, call in zip(("targeted", "final"), wait_calls):
    arguments = call["argv"]
    assert arguments[arguments.index("--phase") + 1] == expected_phase
    assert arguments[arguments.index("--timeout-seconds") + 1] == "900"
    assert arguments[arguments.index("--poll-interval-seconds") + 1] == "1"
    assert arguments[arguments.index("--stable-samples") + 1] == "2"
    actual_summary = os.path.normpath(
        arguments[arguments.index("--summary-file") + 1]
    )
    assert actual_summary == expected_summary
    assert "--json" in arguments
    if expected_phase == "targeted":
        assert "--required-portfolio-as-of-date" not in arguments
    else:
        assert arguments[arguments.index("--required-portfolio-as-of-date") + 1] == "2026-07-14"

publish = json.loads(
    Path(os.environ["PUBLISH_CAPTURE_PATH"]).read_text(encoding="utf-8")
)
assert publish["argv"] == [
    "--as-of-date",
    "2026-07-14",
    "--timeout-seconds",
    "900",
]
assert publish["database_url"] == "postgresql+psycopg://explicit/local"
assert publish["database_schema"] == "portfolio"
assert publish["environment"] == "local"
assert publish["outputs_root"] == str(
    Path(os.environ["STATE_ROOT"]) / "var" / "portfolio-allocation-research-outputs"
)
assert publish["pythonpath"] == (
    f"{normalized_runtime_root}/apps/portfolio/backend:"
    f"{normalized_runtime_root}/packages/instrument-core/python:"
    f"{normalized_runtime_root}/packages/calculation-core/python"
)

rebuild = json.loads(
    Path(os.environ["REBUILD_CAPTURE_PATH"]).read_text(encoding="utf-8")
)
assert rebuild["argv"] == [
    "--valuation-date",
    "2026-07-14",
    "--all-active",
    "--rounds",
    "2",
    "--continue-on-error",
]
assert rebuild["database_url"] == "postgresql+psycopg://explicit/local"
assert rebuild["database_schema"] == "watchlist"
assert rebuild["environment"] == "local"
assert rebuild["pythonpath"] == (
    f"{normalized_runtime_root}/apps/watchlist/backend:"
    f"{normalized_runtime_root}/packages/instrument-core/python"
)

audit = json.loads(Path(os.environ["AUDIT_CAPTURE_PATH"]).read_text(encoding="utf-8"))
assert audit["argv"] == ["--json", "--fail-on-warning"]
assert Path(os.environ["COMMAND_LOG_PATH"]).read_text(encoding="utf-8").splitlines() == [
    "refresh",
    "wait:targeted",
    "publish",
    "rebuild",
    "wait:final",
    "audit",
]
PY

rm -f \
  "$WAIT_CAPTURE_PATH" \
  "$REBUILD_CAPTURE_PATH" \
  "$PUBLISH_CAPTURE_PATH" \
  "$AUDIT_CAPTURE_PATH" \
  "$COMMAND_LOG_PATH"
set +e
WAIT_FAIL_PHASE=targeted \
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
PORTFOLIO_OPS_LOCAL_RELEASE_ID=release-test \
PORTFOLIO_OPS_LOCAL_STATE_ROOT="$STATE_ROOT" \
PORTFOLIO_OPS_LOCAL_REFRESH_VALUATION_DATE=2026-07-14 \
  "$REPOSITORY_ROOT/infra/launchd/run_market_data_refresh.sh" \
  "$RUNTIME_ROOT" "$VERIFYING_PYTHON" "$LOCK_PATH" "$ENV_ROOT" \
  > "$TEST_ROOT/convergence-failure.out" 2>&1
convergence_failure_status=$?
set -e
if [[ $convergence_failure_status -eq 0 ]]; then
  echo "The scheduled runner accepted failed targeted convergence." >&2
  exit 1
fi
if [[ -e "$PUBLISH_CAPTURE_PATH" || -e "$REBUILD_CAPTURE_PATH" || -e "$AUDIT_CAPTURE_PATH" ]]; then
  echo "The scheduled runner continued after failed targeted convergence." >&2
  exit 1
fi
if [[ "$(<"$COMMAND_LOG_PATH")" != $'refresh\nwait:targeted' ]]; then
  echo "Unexpected command order after failed targeted convergence." >&2
  exit 1
fi

rm -f \
  "$WAIT_CAPTURE_PATH" \
  "$PUBLISH_CAPTURE_PATH" \
  "$REBUILD_CAPTURE_PATH" \
  "$AUDIT_CAPTURE_PATH" \
  "$COMMAND_LOG_PATH"
set +e
PUBLISH_FAIL=true \
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
PORTFOLIO_OPS_LOCAL_RELEASE_ID=release-test \
PORTFOLIO_OPS_LOCAL_STATE_ROOT="$STATE_ROOT" \
PORTFOLIO_OPS_LOCAL_REFRESH_VALUATION_DATE=2026-07-14 \
  "$REPOSITORY_ROOT/infra/launchd/run_market_data_refresh.sh" \
  "$RUNTIME_ROOT" "$VERIFYING_PYTHON" "$LOCK_PATH" "$ENV_ROOT" \
  > "$TEST_ROOT/publish-failure.out" 2>&1
publish_failure_status=$?
set -e
if [[ $publish_failure_status -eq 0 ]]; then
  echo "The scheduled runner accepted failed Portfolio publication." >&2
  exit 1
fi
if [[ ! -e "$PUBLISH_CAPTURE_PATH" ]]; then
  echo "The scheduled runner skipped required Portfolio publication." >&2
  exit 1
fi
if [[ -e "$REBUILD_CAPTURE_PATH" || -e "$AUDIT_CAPTURE_PATH" ]]; then
  echo "The scheduled runner continued after failed Portfolio publication." >&2
  exit 1
fi
if [[ "$(<"$COMMAND_LOG_PATH")" != $'refresh\nwait:targeted\npublish' ]]; then
  echo "Unexpected command order after failed Portfolio publication." >&2
  exit 1
fi

ln -s "$TEST_ROOT/missing-watchlist-env" "$STATE_ROOT/apps/watchlist/backend/.env"
set +e
PORTFOLIO_OPS_LOCAL_DATABASE_URL="postgresql+psycopg://explicit/local" \
PORTFOLIO_OPS_LOCAL_RELEASE_ID=release-test \
PORTFOLIO_OPS_LOCAL_STATE_ROOT="$STATE_ROOT" \
PORTFOLIO_OPS_LOCAL_REFRESH_VALUATION_DATE=2026-07-14 \
  "$REPOSITORY_ROOT/infra/launchd/run_market_data_refresh.sh" \
  "$RUNTIME_ROOT" "$VERIFYING_PYTHON" "$LOCK_PATH" "$ENV_ROOT" \
  > "$TEST_ROOT/repository-env.out" 2>&1
repository_env_status=$?
set -e
if [[ $repository_env_status -eq 0 ]]; then
  echo "The scheduled runner accepted a repository-local .env symlink." >&2
  exit 1
fi
grep -q 'Repository runtime environment files are not allowed for launchd' "$TEST_ROOT/repository-env.out"

echo "launchd scheduled refresh runner test passed."
