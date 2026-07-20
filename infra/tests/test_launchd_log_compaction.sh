#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-log-compaction.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

printf 'abc' > "$WORK_DIR/small.log"
printf '0123456789ABCDEFGHIJ' > "$WORK_DIR/large.log"

PORTFOLIO_OPS_LOCAL_LOG_MAX_BYTES=10 \
PORTFOLIO_OPS_LOCAL_LOG_RETAIN_BYTES=6 \
  "$PROJECT_ROOT/infra/launchd/compact_local_logs.sh" "$WORK_DIR"

[[ "$(cat "$WORK_DIR/small.log")" == "abc" ]]
[[ "$(cat "$WORK_DIR/large.log")" == "EFGHIJ" ]]
[[ "$(wc -c < "$WORK_DIR/large.log" | tr -d ' ')" == "6" ]]
