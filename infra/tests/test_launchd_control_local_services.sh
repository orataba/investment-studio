#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-launchd-control-test.XXXXXX")"
MANAGED_TEST_PID=""
trap '[[ -z "$MANAGED_TEST_PID" ]] || kill "$MANAGED_TEST_PID" >/dev/null 2>&1 || true; rm -rf "$TEST_ROOT"' EXIT

MOCK_BIN="$TEST_ROOT/bin"
PLIST_ROOT="$TEST_ROOT/LaunchAgents"
STATE_FILE="$TEST_ROOT/service-state"
CALL_LOG="$TEST_ROOT/launchctl-calls"
BOOTSTRAP_ATTEMPT_FILE="$TEST_ROOT/bootstrap-attempts"
mkdir -p "$MOCK_BIN" "$PLIST_ROOT"

printf '%s\n' '#!/usr/bin/env bash' 'printf "%s\n" Darwin' > "$MOCK_BIN/uname"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$CALL_LOG"' \
  'if [[ "$1" == "print" ]]; then' \
  '  case "$2" in' \
  '    *.platform-api) printf "pid = %s\n" "$MANAGED_TEST_PID"; exit 0 ;;' \
  '    *.watchlist-api|*.watchlist-worker|*.platform-outbox-worker|*.portfolio-worker|*.portfolio-web|*.market-data-refresh) exit 0 ;;' \
  '    *) exit 1 ;;' \
  '  esac' \
  'fi' \
  'if [[ "$1" == "bootout" && "$2" == *.platform-api ]]; then' \
  '  (sleep 0.2; kill "$MANAGED_TEST_PID") >/dev/null 2>&1 &' \
  'fi' \
  'if [[ "$1" == "bootstrap" && "$*" == *"test.portfolio-ops.platform-api.plist" ]]; then' \
  '  attempt="$(($(cat "$BOOTSTRAP_ATTEMPT_FILE" 2>/dev/null || printf 0) + 1))"' \
  '  printf "%s\n" "$attempt" > "$BOOTSTRAP_ATTEMPT_FILE"' \
  '  if [[ "$attempt" -eq 1 ]]; then exit 5; fi' \
  'fi' \
  'exit 0' \
  > "$MOCK_BIN/launchctl"
chmod +x "$MOCK_BIN/uname" "$MOCK_BIN/launchctl"

for service in platform-api watchlist-api watchlist-worker platform-outbox-worker portfolio-api portfolio-worker platform-web watchlist-web portfolio-web market-data-refresh; do
  touch "$PLIST_ROOT/test.portfolio-ops.$service.plist"
done

sleep 30 &
MANAGED_TEST_PID="$!"
export CALL_LOG BOOTSTRAP_ATTEMPT_FILE MANAGED_TEST_PID
PATH="$MOCK_BIN:$PATH" \
LABEL_PREFIX=test.portfolio-ops \
LAUNCH_AGENTS_DIR="$PLIST_ROOT" \
  "$REPOSITORY_ROOT/infra/launchd/control_local_services.sh" stop "$STATE_FILE"

expected_state="$(printf '%s\n' platform-api watchlist-api watchlist-worker platform-outbox-worker portfolio-worker portfolio-web market-data-refresh)"
[[ "$(cat "$STATE_FILE")" == "$expected_state" ]]
grep -q 'bootout .*test.portfolio-ops.platform-api' "$CALL_LOG"
grep -q 'bootout .*test.portfolio-ops.watchlist-api' "$CALL_LOG"
grep -q 'bootout .*test.portfolio-ops.watchlist-worker' "$CALL_LOG"
grep -q 'bootout .*test.portfolio-ops.platform-outbox-worker' "$CALL_LOG"
grep -q 'bootout .*test.portfolio-ops.portfolio-worker' "$CALL_LOG"
grep -q 'bootout .*test.portfolio-ops.portfolio-web' "$CALL_LOG"
grep -q 'bootout .*test.portfolio-ops.market-data-refresh' "$CALL_LOG"
if kill -0 "$MANAGED_TEST_PID" 2>/dev/null; then
  echo "launchd stop returned before the managed process exited." >&2
  exit 1
fi
MANAGED_TEST_PID=""

PATH="$MOCK_BIN:$PATH" \
LABEL_PREFIX=test.portfolio-ops \
LAUNCH_AGENTS_DIR="$PLIST_ROOT" \
LAUNCHD_BOOTSTRAP_RETRY_DELAY_SECONDS=0 \
  "$REPOSITORY_ROOT/infra/launchd/control_local_services.sh" start "$STATE_FILE"

grep -q 'bootstrap .*test.portfolio-ops.platform-api.plist' "$CALL_LOG"
[[ "$(cat "$BOOTSTRAP_ATTEMPT_FILE")" == "2" ]]
grep -q 'kickstart -k .*test.portfolio-ops.platform-api' "$CALL_LOG"
grep -q 'bootstrap .*test.portfolio-ops.watchlist-api.plist' "$CALL_LOG"
grep -q 'kickstart -k .*test.portfolio-ops.watchlist-api' "$CALL_LOG"
grep -q 'bootstrap .*test.portfolio-ops.watchlist-worker.plist' "$CALL_LOG"
grep -q 'kickstart -k .*test.portfolio-ops.watchlist-worker' "$CALL_LOG"
grep -q 'bootstrap .*test.portfolio-ops.platform-outbox-worker.plist' "$CALL_LOG"
grep -q 'kickstart -k .*test.portfolio-ops.platform-outbox-worker' "$CALL_LOG"
grep -q 'bootstrap .*test.portfolio-ops.portfolio-worker.plist' "$CALL_LOG"
grep -q 'kickstart -k .*test.portfolio-ops.portfolio-worker' "$CALL_LOG"
grep -q 'bootstrap .*test.portfolio-ops.portfolio-web.plist' "$CALL_LOG"
grep -q 'bootstrap .*test.portfolio-ops.market-data-refresh.plist' "$CALL_LOG"
if grep -q 'kickstart .*test.portfolio-ops.market-data-refresh' "$CALL_LOG"; then
  echo "Scheduled refresh was incorrectly kickstarted while restoring services." >&2
  exit 1
fi

echo "launchd stop/start state preservation test passed."
