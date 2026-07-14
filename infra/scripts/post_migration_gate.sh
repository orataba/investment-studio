#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
AS_OF_DATE="${1:-${PORTFOLIO_OPS_RELEASE_AS_OF_DATE:-}}"
AUDIT_OUTPUT_PATH="${PORTFOLIO_OPS_RELEASE_AUDIT_OUTPUT_PATH:-}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "PYTHON_BIN is not executable: $PYTHON_BIN" >&2
  exit 1
fi
if [[ -z "$AS_OF_DATE" ]]; then
  echo "Pass an explicit YYYY-MM-DD as-of date to the post-migration gate." >&2
  exit 64
fi

"$PYTHON_BIN" - "$AS_OF_DATE" <<'PY'
from datetime import date
import sys

try:
    parsed = date.fromisoformat(sys.argv[1])
except ValueError as exc:
    raise SystemExit(f"Invalid release as-of date: {sys.argv[1]}") from exc
if parsed.isoformat() != sys.argv[1]:
    raise SystemExit(f"Release as-of date must be canonical YYYY-MM-DD: {sys.argv[1]}")
PY

PORTFOLIO_PUBLISH="$PROJECT_ROOT/apps/portfolio/backend/scripts/publish_portfolio_daily.py"
WATCHLIST_REBUILD="$PROJECT_ROOT/apps/watchlist/backend/scripts/rebuild_watchlist_derived_state.py"
AUDIT_SCRIPT="$PROJECT_ROOT/infra/scripts/audit_live_data.py"
for required_file in "$PORTFOLIO_PUBLISH" "$WATCHLIST_REBUILD" "$AUDIT_SCRIPT"; do
  if [[ ! -f "$required_file" ]]; then
    echo "Missing post-migration gate component: $required_file" >&2
    exit 1
  fi
done

temporary_audit_output="false"
if [[ -z "$AUDIT_OUTPUT_PATH" ]]; then
  AUDIT_OUTPUT_PATH="$(mktemp "${TMPDIR:-/tmp}/portfolio-ops-release-audit.XXXXXX.json")"
  temporary_audit_output="true"
else
  mkdir -p "$(dirname "$AUDIT_OUTPUT_PATH")"
fi
cleanup() {
  if [[ "$temporary_audit_output" == "true" ]]; then
    rm -f "$AUDIT_OUTPUT_PATH"
  fi
}
trap cleanup EXIT

echo "Publishing every current Portfolio Daily generation for $AS_OF_DATE."
PORTFOLIO_PYTHONPATH="$PROJECT_ROOT/apps/portfolio/backend:$PROJECT_ROOT/packages/instrument-core/python:$PROJECT_ROOT/packages/calculation-core/python"
if [[ -n "${PYTHONPATH:-}" ]]; then
  PORTFOLIO_PYTHONPATH="$PORTFOLIO_PYTHONPATH:$PYTHONPATH"
fi
PYTHONPATH="$PORTFOLIO_PYTHONPATH" "$PYTHON_BIN" "$PORTFOLIO_PUBLISH" \
  --as-of-date "$AS_OF_DATE" \
  --timeout-seconds "${PORTFOLIO_OPS_RELEASE_PORTFOLIO_DRAIN_TIMEOUT_SECONDS:-3600}"

echo "Rebuilding all active Watchlist derived state through $AS_OF_DATE."
"$PYTHON_BIN" "$WATCHLIST_REBUILD" \
  --valuation-date "$AS_OF_DATE" \
  --all-active \
  --rounds 2 \
  --continue-on-error

echo "Running the zero-warning live-data audit."
if ! "$PYTHON_BIN" "$AUDIT_SCRIPT" --json --fail-on-warning > "$AUDIT_OUTPUT_PATH"; then
  cat "$AUDIT_OUTPUT_PATH" >&2
  echo "Post-migration audit failed." >&2
  exit 1
fi

"$PYTHON_BIN" - "$AUDIT_OUTPUT_PATH" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"Audit did not produce valid JSON at {path}") from exc
if payload.get("status") != "passed":
    raise SystemExit(f"Audit status is not passed: {payload.get('status')!r}")
if payload.get("failed_count") != 0 or payload.get("warning_count") != 0:
    raise SystemExit(
        "Audit must finish with failed_count=0 and warning_count=0; "
        f"received {payload.get('failed_count')!r}/{payload.get('warning_count')!r}"
    )
print(
    "Post-migration gate passed: "
    f"{len(payload.get('checks', []))} checks, zero failures, zero warnings."
)
PY

echo "Audit evidence retained at: $AUDIT_OUTPUT_PATH"
