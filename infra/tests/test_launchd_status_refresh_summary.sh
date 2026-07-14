#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-launchd-status-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

BIN_ROOT="$TEST_ROOT/bin"
PLIST_ROOT="$TEST_ROOT/LaunchAgents"
INSTALLED_STATE_ROOT="$TEST_ROOT/installed state"
LABEL_PREFIX=test.portfolio-ops
REFRESH_PLIST="$PLIST_ROOT/$LABEL_PREFIX.market-data-refresh.plist"
PYTHON_BIN="${PYTHON_BIN:-$REPOSITORY_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
mkdir -p "$BIN_ROOT" "$PLIST_ROOT" "$INSTALLED_STATE_ROOT/var"
: > "$REFRESH_PLIST"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'case "$2" in' \
  '  *StartCalendarInterval:Hour*) printf "21\n" ;;' \
  '  *StartCalendarInterval:Minute*) printf "0\n" ;;' \
  '  *PORTFOLIO_OPS_LOCAL_STATE_ROOT*) printf "%s\n" "$INSTALLED_STATE_ROOT" ;;' \
  '  *) exit 1 ;;' \
  'esac' \
  > "$BIN_ROOT/PlistBuddy"
chmod +x "$BIN_ROOT/PlistBuddy"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'if [[ "$*" != *".market-data-refresh"* ]]; then' \
  '  exit 1' \
  'fi' \
  'printf "state = not running\n"' \
  'printf "runs = 1\n"' \
  'printf "last exit code = %s\n" "${REFRESH_LAST_EXIT:?}"' \
  > "$BIN_ROOT/launchctl"
chmod +x "$BIN_ROOT/launchctl"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'exit 0' \
  > "$BIN_ROOT/curl"
chmod +x "$BIN_ROOT/curl"

"$PYTHON_BIN" - "$INSTALLED_STATE_ROOT/var/market-data-refresh-summary.json" <<'PY'
import json
from pathlib import Path
import sys


Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "status": "succeeded",
            "ingestion_status": "succeeded",
            "finished_at": "2026-07-14T21:02:03+08:00",
            "updated_instrument_count": 3,
            "failed_item_count": 0,
            "downstream_convergence": {
                "status": "converged",
                "phase": "final",
                "reason_code": "all_current_state_converged",
            },
        },
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY

export INSTALLED_STATE_ROOT
export REFRESH_LAST_EXIT=1
PATH="$BIN_ROOT:$PATH" \
PROJECT_ROOT="$REPOSITORY_ROOT" \
LABEL_PREFIX="$LABEL_PREFIX" \
LAUNCH_AGENTS_DIR="$PLIST_ROOT" \
PLISTBUDDY_BIN="$BIN_ROOT/PlistBuddy" \
PYTHON_BIN="$PYTHON_BIN" \
  "$REPOSITORY_ROOT/infra/launchd/status_local_services.sh" \
  > "$TEST_ROOT/failed-status.out"

grep -Fq \
  'overall=failed ingestion=succeeded downstream=converged phase=final reason=all_current_state_converged launchd_exit=1' \
  "$TEST_ROOT/failed-status.out"
grep -Fq \
  "Scheduled summary: $INSTALLED_STATE_ROOT/var/market-data-refresh-summary.json" \
  "$TEST_ROOT/failed-status.out"

export REFRESH_LAST_EXIT=0
PATH="$BIN_ROOT:$PATH" \
PROJECT_ROOT="$REPOSITORY_ROOT" \
LABEL_PREFIX="$LABEL_PREFIX" \
LAUNCH_AGENTS_DIR="$PLIST_ROOT" \
PLISTBUDDY_BIN="$BIN_ROOT/PlistBuddy" \
PYTHON_BIN="$PYTHON_BIN" \
  "$REPOSITORY_ROOT/infra/launchd/status_local_services.sh" \
  > "$TEST_ROOT/succeeded-status.out"

grep -Fq \
  'overall=succeeded ingestion=succeeded downstream=converged phase=final reason=all_current_state_converged launchd_exit=0' \
  "$TEST_ROOT/succeeded-status.out"

echo "launchd refresh status summary test passed."
