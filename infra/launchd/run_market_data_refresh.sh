#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <project-root> <python-bin> <lock-file> <external-env-root>" >&2
  exit 64
fi

PROJECT_ROOT="$(cd "$1" && pwd)"
PYTHON_BIN="$2"
LOCK_FILE="$3"
EXTERNAL_ENV_ROOT="$4"
STATE_DIR="${INVESTMENT_STUDIO_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/investment-studio}"
BACKEND_ROOT="$PROJECT_ROOT/shared-data"
REFRESH_SCRIPT="$BACKEND_ROOT/scripts/refresh_market_data_scheduled.py"
AUDIT_SCRIPT="$PROJECT_ROOT/infra/scripts/audit_live_data.py"
CHANNEL="${INVESTMENT_STUDIO_LOCAL_REFRESH_CHANNEL:-settlement}"
MARKET_SCOPE="${INVESTMENT_STUDIO_LOCAL_REFRESH_MARKET_SCOPE:-}"
REFRESH_NAME=market-data-refresh
if [[ "$CHANNEL" == "reference" ]]; then
  REFRESH_NAME=reference-data-refresh
fi
if [[ -n "$MARKET_SCOPE" ]]; then
  REFRESH_NAME="$MARKET_SCOPE-$REFRESH_NAME"
fi
SUMMARY_FILE="$STATE_DIR/$REFRESH_NAME-summary.json"
RUN_STATE_FILE="$STATE_DIR/$REFRESH_NAME-run-state.json"
RETRY_FAILED_ATTEMPTS="${INVESTMENT_STUDIO_LOCAL_REFRESH_RETRY_FAILED_ATTEMPTS:-2}"
FAIL_ON_ITEM_FAILURE="${INVESTMENT_STUDIO_LOCAL_REFRESH_FAIL_ON_ITEM_FAILURE:-true}"
PRIMARY_HOUR="${INVESTMENT_STUDIO_LOCAL_REFRESH_HOUR:-21}"
PRIMARY_MINUTE="${INVESTMENT_STUDIO_LOCAL_REFRESH_MINUTE:-0}"
RETRY_HOUR="${INVESTMENT_STUDIO_LOCAL_REFRESH_RETRY_HOUR:-23}"
RETRY_MINUTE="${INVESTMENT_STUDIO_LOCAL_REFRESH_RETRY_MINUTE:-0}"
RUN_KIND="${INVESTMENT_STUDIO_LOCAL_REFRESH_RUN_KIND:-auto}"
NOW_OVERRIDE="${INVESTMENT_STUDIO_LOCAL_REFRESH_NOW:-}"
SCHEDULE_TIMEZONE="${INVESTMENT_STUDIO_LOCAL_REFRESH_TIMEZONE:-}"

source "$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
investment_studio_reject_repository_env_files "$PROJECT_ROOT"
ENV_FILE="$(investment_studio_runtime_env_file data "$EXTERNAL_ENV_ROOT")"
investment_studio_load_env_file "$ENV_FILE" INVESTMENT_STUDIO_DATA_ INVESTMENT_STUDIO_INSTRUMENT_DATA_ INVESTMENT_STUDIO_AUTH_
investment_studio_load_env_file "$EXTERNAL_ENV_ROOT/market.env" INVESTMENT_STUDIO_MARKET_

DATABASE_URL="${INVESTMENT_STUDIO_LOCAL_DATABASE_URL:-}"
if [[ -z "$DATABASE_URL" ]]; then
  echo "INVESTMENT_STUDIO_LOCAL_DATABASE_URL is required for scheduled refresh." >&2
  exit 64
fi
case "$DATABASE_URL" in
  postgresql://*|postgresql+psycopg://*) ;;
  *)
    echo "INVESTMENT_STUDIO_LOCAL_DATABASE_URL must use postgresql:// or postgresql+psycopg://." >&2
    exit 64
    ;;
esac
investment_studio_require_password_free_database_url "$DATABASE_URL"
DATABASE_URL="$(investment_studio_sqlalchemy_database_url "$DATABASE_URL")"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable is missing: $PYTHON_BIN" >&2
  exit 1
fi
if [[ ! -f "$REFRESH_SCRIPT" ]]; then
  echo "Scheduled refresh script is missing: $REFRESH_SCRIPT" >&2
  exit 1
fi
if [[ ! -f "$AUDIT_SCRIPT" ]]; then
  echo "Post-refresh data audit script is missing: $AUDIT_SCRIPT" >&2
  exit 1
fi
case "$CHANNEL" in
  all|email|tushare|fmp|projection|reference|market|settlement) ;;
  *)
    echo "Invalid refresh channel: $CHANNEL" >&2
    exit 64
    ;;
esac
if [[ ! "$RETRY_FAILED_ATTEMPTS" =~ ^[0-9]+$ ]]; then
  echo "Refresh retry count must be a non-negative integer: $RETRY_FAILED_ATTEMPTS" >&2
  exit 64
fi
if [[ ! "$PRIMARY_HOUR" =~ ^[0-9]+$ || "$PRIMARY_HOUR" -gt 23 ]]; then
  echo "Refresh hour must be an integer between 0 and 23: $PRIMARY_HOUR" >&2
  exit 64
fi
if [[ ! "$PRIMARY_MINUTE" =~ ^[0-9]+$ || "$PRIMARY_MINUTE" -gt 59 ]]; then
  echo "Refresh minute must be an integer between 0 and 59: $PRIMARY_MINUTE" >&2
  exit 64
fi
if [[ ! "$RETRY_HOUR" =~ ^[0-9]+$ || "$RETRY_HOUR" -gt 23 ]]; then
  echo "Refresh retry hour must be an integer between 0 and 23: $RETRY_HOUR" >&2
  exit 64
fi
if [[ ! "$RETRY_MINUTE" =~ ^[0-9]+$ || "$RETRY_MINUTE" -gt 59 ]]; then
  echo "Refresh retry minute must be an integer between 0 and 59: $RETRY_MINUTE" >&2
  exit 64
fi
primary_minutes=$((10#$PRIMARY_HOUR * 60 + 10#$PRIMARY_MINUTE))
retry_minutes=$((10#$RETRY_HOUR * 60 + 10#$RETRY_MINUTE))
if (( retry_minutes <= primary_minutes )); then
  echo "Refresh retry time must be later than the primary refresh time." >&2
  exit 64
fi
case "$RUN_KIND" in
  auto|primary|retry) ;;
  *)
    echo "Refresh run kind must be auto, primary, or retry: $RUN_KIND" >&2
    exit 64
    ;;
esac
case "$FAIL_ON_ITEM_FAILURE" in
  true|false) ;;
  *)
    echo "INVESTMENT_STUDIO_LOCAL_REFRESH_FAIL_ON_ITEM_FAILURE must be true or false." >&2
    exit 64
    ;;
esac

mkdir -p "$(dirname "$LOCK_FILE")" "$(dirname "$RUN_STATE_FILE")"

export PYTHONUNBUFFERED=1
export PYTHONNOUSERSITE=1
export PYTHONPATH="$PROJECT_ROOT/packages/identity:$BACKEND_ROOT:$PROJECT_ROOT/shared-data/instruments/python:$PROJECT_ROOT/shared-data/market"
export INVESTMENT_STUDIO_DATA_ENVIRONMENT=local
export INVESTMENT_STUDIO_DATA_DATABASE_URL="$DATABASE_URL"
export INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA=instrument_data
export INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA=data_ingestion
export INVESTMENT_STUDIO_DATA_WATCHLIST_API_URL=http://127.0.0.1:8000
export INVESTMENT_STUDIO_DATA_PORTFOLIO_API_URL=http://127.0.0.1:8001

clock_fields="$(
  "$PYTHON_BIN" - "$NOW_OVERRIDE" "$SCHEDULE_TIMEZONE" <<'PY'
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


override = sys.argv[1].strip()
try:
    current = datetime.fromisoformat(override) if override else datetime.now().astimezone()
except ValueError as error:
    raise SystemExit(f"Invalid INVESTMENT_STUDIO_LOCAL_REFRESH_NOW: {error}") from error
if current.tzinfo is None:
    raise SystemExit("INVESTMENT_STUDIO_LOCAL_REFRESH_NOW must include a UTC offset")
if sys.argv[2]:
    current = current.astimezone(ZoneInfo(sys.argv[2]))
print(
    current.date().isoformat(),
    (current.date() - timedelta(days=1)).isoformat(),
    current.hour * 60 + current.minute,
    current.isoformat(),
)
PY
)" || exit 64
read -r current_date previous_date current_minutes started_at <<<"$clock_fields"

if [[ "$CHANNEL" == "market" && ( "$MARKET_SCOPE" == "hk" || "$MARKET_SCOPE" == "us" ) ]]; then
  if "$PYTHON_BIN" "$PROJECT_ROOT/infra/scripts/market_close_schedule.py" \
    --market-scope "$MARKET_SCOPE" --now "$started_at"; then
    :
  else
    schedule_exit_code=$?
    [[ $schedule_exit_code -eq 1 ]] && exit 0
    exit "$schedule_exit_code"
  fi
elif [[ -n "$SCHEDULE_TIMEZONE" ]] \
  && (( current_minutes < primary_minutes || current_minutes >= primary_minutes + 30 )); then
  exit 0
fi

scheduled_date="$current_date"
if [[ "$RUN_KIND" == "auto" ]]; then
  if (( current_minutes >= retry_minutes )); then
    RUN_KIND="retry"
  elif (( current_minutes < primary_minutes )); then
    RUN_KIND="retry"
    scheduled_date="$previous_date"
  else
    RUN_KIND="primary"
  fi
fi

if [[ "$RUN_KIND" == "retry" ]]; then
  retry_decision="$(
    "$PYTHON_BIN" - "$RUN_STATE_FILE" "$scheduled_date" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path


state_path = Path(sys.argv[1])
scheduled_date = sys.argv[2]
try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except (FileNotFoundError, OSError, ValueError):
    print("run")
else:
    if state.get("scheduled_date") == scheduled_date and state.get("status") == "succeeded":
        print("skip")
    else:
        print("run")
PY
  )"
  if [[ "$retry_decision" == "skip" ]]; then
    echo "Skipping conditional market-data retry for $scheduled_date: the primary run already succeeded."
    exit 0
  fi
fi

write_run_state() {
  local status="$1"
  local completed_at="$2"
  local refresh_exit_code="$3"
  local audit_exit_code="$4"
  "$PYTHON_BIN" - \
    "$RUN_STATE_FILE" \
    "$status" \
    "$RUN_KIND" \
    "$scheduled_date" \
    "$started_at" \
    "$completed_at" \
    "$refresh_exit_code" \
    "$audit_exit_code" <<'PY'
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


target = Path(sys.argv[1])
payload = {
    "status": sys.argv[2],
    "run_kind": sys.argv[3],
    "scheduled_date": sys.argv[4],
    "started_at": sys.argv[5],
    "completed_at": sys.argv[6] or None,
    "refresh_exit_code": int(sys.argv[7]) if sys.argv[7] else None,
    "audit_exit_code": int(sys.argv[8]) if sys.argv[8] else None,
}
target.parent.mkdir(parents=True, exist_ok=True)
descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, sort_keys=True)
        output.write("\n")
    os.chmod(temporary_name, 0o600)
    os.replace(temporary_name, target)
except BaseException:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
    raise
PY
}

write_run_state "running" "" "" ""

refresh_arguments=(
  --channel "$CHANNEL"
  --updated-by launchd-scheduler
  --retry-failed-attempts "$RETRY_FAILED_ATTEMPTS"
  --lock-file "$LOCK_FILE"
  --summary-file "$SUMMARY_FILE"
  --require-downstream-success
  --json
)
if [[ "$FAIL_ON_ITEM_FAILURE" == "true" ]]; then
  refresh_arguments+=(--fail-on-item-failure)
fi
if [[ -n "$MARKET_SCOPE" ]]; then
  refresh_arguments+=(--market-scope "$MARKET_SCOPE")
fi

cd "$BACKEND_ROOT"
set +e
"$PYTHON_BIN" "$REFRESH_SCRIPT" "${refresh_arguments[@]}"
refresh_exit_code=$?
audit_exit_code=""
if [[ "$CHANNEL" != "reference" ]]; then
  "$PYTHON_BIN" "$AUDIT_SCRIPT" --json
  audit_exit_code=$?
fi
set -e

completed_at="$("$PYTHON_BIN" -c 'from datetime import datetime; print(datetime.now().astimezone().isoformat())')"
if [[ $refresh_exit_code -eq 0 && ${audit_exit_code:-0} -eq 0 ]]; then
  write_run_state "succeeded" "$completed_at" "$refresh_exit_code" "$audit_exit_code"
  exit 0
fi

write_run_state "failed" "$completed_at" "$refresh_exit_code" "$audit_exit_code"
if [[ $refresh_exit_code -ne 0 ]]; then
  exit "$refresh_exit_code"
fi
exit "$audit_exit_code"
