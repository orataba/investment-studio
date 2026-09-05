#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERIFY="$REPOSITORY_ROOT/infra/scripts/verify_repository.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/investment-studio-verify-selection.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

assert_invalid_selection() {
  local command_name="$1"
  local output="$TEST_ROOT/$command_name.err"
  local status=0

  PYTHON_BIN=/usr/bin/true \
  INVESTMENT_STUDIO_TEST_POSTGRES_URL=postgresql+psycopg://invalid/invalid \
    "$VERIFY" "$command_name" invalid-app >"$TEST_ROOT/$command_name.out" 2>"$output" || status=$?

  if [[ "$status" -ne 64 ]]; then
    echo "$command_name invalid selection returned $status instead of 64." >&2
    exit 1
  fi
  grep -q 'Unknown app selection: invalid-app' "$output"
}

assert_invalid_selection backend
assert_invalid_selection frontend
assert_invalid_selection postgres-integration

status=0
PYTHON_BIN=/usr/bin/true \
  "$VERIFY" infra invalid-selection >"$TEST_ROOT/infra.out" 2>"$TEST_ROOT/infra.err" || status=$?
if [[ "$status" -ne 64 ]]; then
  echo "infra invalid selection returned $status instead of 64." >&2
  exit 1
fi
grep -q 'Unknown infra selection: invalid-selection' "$TEST_ROOT/infra.err"

status=0
"$VERIFY" invalid-command >"$TEST_ROOT/command.out" 2>"$TEST_ROOT/command.err" || status=$?
if [[ "$status" -ne 64 ]]; then
  echo "Invalid command returned $status instead of 64." >&2
  exit 1
fi
grep -q 'Usage: verify_repository.sh' "$TEST_ROOT/command.err"

echo "Repository verification selection tests passed."
