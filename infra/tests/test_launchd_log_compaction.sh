#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-log-compaction.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

prepare_logs() {
  local target_dir="$1"
  mkdir -p "$target_dir"
  printf 'abc' > "$target_dir/small.log"
  printf '0123456789ABCDEFGHIJ' > "$target_dir/large.log"
}

assert_compacted_logs() {
  local target_dir="$1"
  [[ "$(cat "$target_dir/small.log")" == "abc" ]]
  [[ "$(cat "$target_dir/large.log")" == "EFGHIJ" ]]
  [[ "$(wc -c < "$target_dir/large.log" | tr -d ' ')" == "6" ]]
}

NATIVE_WORK_DIR="$WORK_DIR/native"
prepare_logs "$NATIVE_WORK_DIR"
PORTFOLIO_OPS_LOCAL_LOG_MAX_BYTES=10 \
PORTFOLIO_OPS_LOCAL_LOG_RETAIN_BYTES=6 \
  "$PROJECT_ROOT/infra/launchd/compact_local_logs.sh" "$NATIVE_WORK_DIR"
assert_compacted_logs "$NATIVE_WORK_DIR"

GNU_WORK_DIR="$WORK_DIR/gnu"
FAKE_BIN_DIR="$WORK_DIR/fake-bin"
prepare_logs "$GNU_WORK_DIR"
mkdir -p "$FAKE_BIN_DIR"
cp "$PROJECT_ROOT/infra/tests/fixtures/stat_with_gnu_failed_bsd_probe.sh" \
  "$FAKE_BIN_DIR/stat"
chmod 700 "$FAKE_BIN_DIR/stat"
PATH="$FAKE_BIN_DIR:$PATH" \
PORTFOLIO_OPS_LOCAL_LOG_MAX_BYTES=10 \
PORTFOLIO_OPS_LOCAL_LOG_RETAIN_BYTES=6 \
  "$PROJECT_ROOT/infra/launchd/compact_local_logs.sh" "$GNU_WORK_DIR"
assert_compacted_logs "$GNU_WORK_DIR"
