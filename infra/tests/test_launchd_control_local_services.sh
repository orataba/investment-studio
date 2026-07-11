#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-launchd-control-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

MOCK_BIN="$TEST_ROOT/bin"
PLIST_ROOT="$TEST_ROOT/LaunchAgents"
STATE_FILE="$TEST_ROOT/service-state"
CALL_LOG="$TEST_ROOT/launchctl-calls"
mkdir -p "$MOCK_BIN" "$PLIST_ROOT"

printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" Darwin' > "$MOCK_BIN/uname"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$CALL_LOG"' \
  'if [[ "$1" == "print" ]]; then' \
  '  case "$2" in' \
  '    *.platform-api|*.portfolio-web|*.market-data-refresh) exit 0 ;;' \
  '    *) exit 1 ;;' \
  '  esac' \
  'fi' \
  'exit 0' \
  > "$MOCK_BIN/launchctl"
chmod +x "$MOCK_BIN/uname" "$MOCK_BIN/launchctl"

for service in platform-api watchlist-api portfolio-api platform-web watchlist-web portfolio-web market-data-refresh; do
  touch "$PLIST_ROOT/test.portfolio-ops.$service.plist"
done

export CALL_LOG
PATH="$MOCK_BIN:$PATH" \
LABEL_PREFIX=test.portfolio-ops \
LAUNCH_AGENTS_DIR="$PLIST_ROOT" \
  "$REPOSITORY_ROOT/infra/launchd/control_local_services.sh" stop "$STATE_FILE"

expected_state="$(printf '%s\n' platform-api portfolio-web market-data-refresh)"
[[ "$(cat "$STATE_FILE")" == "$expected_state" ]]
grep -q 'bootout .*test.portfolio-ops.platform-api' "$CALL_LOG"
grep -q 'bootout .*test.portfolio-ops.portfolio-web' "$CALL_LOG"
grep -q 'bootout .*test.portfolio-ops.market-data-refresh' "$CALL_LOG"

PATH="$MOCK_BIN:$PATH" \
LABEL_PREFIX=test.portfolio-ops \
LAUNCH_AGENTS_DIR="$PLIST_ROOT" \
  "$REPOSITORY_ROOT/infra/launchd/control_local_services.sh" start "$STATE_FILE"

grep -q 'bootstrap .*test.portfolio-ops.platform-api.plist' "$CALL_LOG"
grep -q 'kickstart -k .*test.portfolio-ops.platform-api' "$CALL_LOG"
grep -q 'bootstrap .*test.portfolio-ops.portfolio-web.plist' "$CALL_LOG"
grep -q 'bootstrap .*test.portfolio-ops.market-data-refresh.plist' "$CALL_LOG"
if grep -q 'kickstart .*test.portfolio-ops.market-data-refresh' "$CALL_LOG"; then
  echo "Scheduled refresh was incorrectly kickstarted while restoring services." >&2
  exit 1
fi

echo "launchd stop/start state preservation test passed."
