#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BACKEND_ROOT="${BACKEND_ROOT:-$PROJECT_ROOT/shared-data}"
DEFAULT_PYTHON_BIN="$(command -v python3)"
if [[ -x "$BACKEND_ROOT/.venv/bin/python" ]]; then
  DEFAULT_PYTHON_BIN="$BACKEND_ROOT/.venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"
CHANNEL="${CHANNEL:-settlement}"
MARKET_SCOPE="${MARKET_SCOPE:-}"
REFRESH_NAME=market-data-refresh
DEFAULT_ON_CALENDAR="*-*-* 21:00 Asia/Shanghai"
DEFAULT_RESTART_ON_FAILURE=true
if [[ -n "$MARKET_SCOPE" ]]; then
  DEFAULT_RESTART_ON_FAILURE=false
  case "$CHANNEL:$MARKET_SCOPE" in
    market:cn) DEFAULT_ON_CALENDAR="*-*-* 15:30 Asia/Shanghai" ;;
    market:hk) DEFAULT_ON_CALENDAR="*-*-* *:30 Asia/Shanghai" ;;
    market:us) DEFAULT_ON_CALENDAR="*-*-* *:30 America/New_York" ;;
    reference:cn-hk) DEFAULT_ON_CALENDAR="*-*-* 08:00 Asia/Shanghai" ;;
    reference:us) DEFAULT_ON_CALENDAR="*-*-* 08:00 America/New_York" ;;
    *) echo "Unsupported scheduled channel/market scope: $CHANNEL/$MARKET_SCOPE" >&2; exit 64 ;;
  esac
  if [[ "$CHANNEL" == "reference" ]]; then
    REFRESH_NAME=reference-data-refresh
  fi
  REFRESH_NAME="$MARKET_SCOPE-$REFRESH_NAME"
fi
UNIT_NAME="${UNIT_NAME:-investment-studio-$REFRESH_NAME}"
ON_CALENDAR="${ON_CALENDAR:-$DEFAULT_ON_CALENDAR}"
ENV_ROOT="${ENV_ROOT:-}"
STATE_DIR="${STATE_DIR:-$HOME/.local/state/investment-studio}"
LOG_DIR="${LOG_DIR:-$STATE_DIR/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/$REFRESH_NAME.log}"
LOCK_FILE="${LOCK_FILE:-$STATE_DIR/market-data-refresh.lock}"
SUMMARY_FILE="${SUMMARY_FILE:-$STATE_DIR/$REFRESH_NAME-summary.json}"
UPDATED_BY="${UPDATED_BY:-scheduler}"
RETRY_FAILED_ATTEMPTS="${RETRY_FAILED_ATTEMPTS:-2}"
TIMEOUT_START_SEC="${TIMEOUT_START_SEC:-2h}"
FAIL_ON_ITEM_FAILURE="${FAIL_ON_ITEM_FAILURE:-true}"
REQUIRE_DOWNSTREAM_SUCCESS="${REQUIRE_DOWNSTREAM_SUCCESS:-true}"
RESTART_ON_FAILURE="${RESTART_ON_FAILURE:-$DEFAULT_RESTART_ON_FAILURE}"
RESTART_SEC="${RESTART_SEC:-20min}"
START_LIMIT_INTERVAL_SEC="${START_LIMIT_INTERVAL_SEC:-3h}"
START_LIMIT_BURST="${START_LIMIT_BURST:-3}"
PERSISTENT="${PERSISTENT:-false}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "PYTHON_BIN is not executable: $PYTHON_BIN" >&2
  exit 1
fi

if [[ ! -f "$BACKEND_ROOT/scripts/refresh_market_data_scheduled.py" ]]; then
  echo "Cannot find refresh script under BACKEND_ROOT: $BACKEND_ROOT" >&2
  exit 1
fi

RUNTIME_ENV_HELPER="$PROJECT_ROOT/infra/launchd/load_runtime_env.sh"
if [[ ! -f "$RUNTIME_ENV_HELPER" ]]; then
  echo "Cannot find runtime environment helper: $RUNTIME_ENV_HELPER" >&2
  exit 1
fi
source "$RUNTIME_ENV_HELPER"
if [[ -z "$ENV_ROOT" ]]; then
  echo "ENV_ROOT must explicitly name the external runtime environment directory." >&2
  exit 64
fi
investment_studio_reject_repository_env_files "$PROJECT_ROOT"
ENV_ROOT="$(investment_studio_resolve_external_env_root "$PROJECT_ROOT" "$ENV_ROOT")"
ENV_FILE="$ENV_ROOT/data.env"
investment_studio_validate_env_file \
  "$ENV_FILE" \
  INVESTMENT_STUDIO_DATA_ \
  INVESTMENT_STUDIO_INSTRUMENT_DATA_ \
  INVESTMENT_STUDIO_AUTH_
investment_studio_validate_env_file "$ENV_ROOT/market.env" INVESTMENT_STUDIO_MARKET_
(
  unset \
    INVESTMENT_STUDIO_DATA_DATABASE_URL \
    INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA \
    INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA \
    INVESTMENT_STUDIO_MARKET_DATABASE_URL
  investment_studio_load_env_file \
    "$ENV_FILE" \
    INVESTMENT_STUDIO_DATA_ \
    INVESTMENT_STUDIO_INSTRUMENT_DATA_ \
  INVESTMENT_STUDIO_AUTH_
  investment_studio_load_env_file "$ENV_ROOT/market.env" INVESTMENT_STUDIO_MARKET_
  if [[ -z "${INVESTMENT_STUDIO_DATA_DATABASE_URL:-}" ]]; then
    echo "External data environment is missing INVESTMENT_STUDIO_DATA_DATABASE_URL." >&2
    exit 1
  fi
  if [[ -z "${INVESTMENT_STUDIO_MARKET_DATABASE_URL:-}" ]]; then
    echo "External market environment is missing INVESTMENT_STUDIO_MARKET_DATABASE_URL." >&2
    exit 1
  fi
  if [[ -n "${INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA:-}" \
    && "$INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA" != "instrument_data" ]]; then
    echo "INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA must be instrument_data." >&2
    exit 1
  fi
  if [[ -n "${INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA:-}" \
    && "$INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA" != "data_ingestion" ]]; then
    echo "INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA must be data_ingestion." >&2
    exit 1
  fi
  INVESTMENT_STUDIO_RUNTIME_DATABASE_URL="$INVESTMENT_STUDIO_DATA_DATABASE_URL" \
    "$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import os
from urllib.parse import parse_qs, urlsplit

raw_value = os.environ.pop("INVESTMENT_STUDIO_RUNTIME_DATABASE_URL").strip()
normalized = raw_value.replace("postgresql+psycopg://", "postgresql://", 1)
parsed = urlsplit(normalized)
query = parse_qs(parsed.query, keep_blank_values=True)
if parsed.scheme != "postgresql" or not parsed.username or not parsed.path.removeprefix("/"):
    raise SystemExit("INVESTMENT_STUDIO_DATA_DATABASE_URL must be an explicit PostgreSQL URL.")
if parsed.password is not None or query.get("password"):
    raise SystemExit(
        "INVESTMENT_STUDIO_DATA_DATABASE_URL must not contain a password; use a 0600 passfile."
    )
market = urlsplit(os.environ["INVESTMENT_STUDIO_MARKET_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1))
if market.scheme != "postgresql" or not market.username or not market.path.removeprefix("/"):
    raise SystemExit("INVESTMENT_STUDIO_MARKET_DATABASE_URL must be an explicit PostgreSQL URL.")
if market.password is not None or parse_qs(market.query).get("password"):
    raise SystemExit("INVESTMENT_STUDIO_MARKET_DATABASE_URL must not contain a password; use a 0600 passfile.")
if (market.hostname, market.port or 5432, market.path) != (parsed.hostname, parsed.port or 5432, parsed.path):
    raise SystemExit("Market and application data must use the same PostgreSQL target.")
PY
)
if [[ ! "$RETRY_FAILED_ATTEMPTS" =~ ^[0-9]+$ ]]; then
  echo "RETRY_FAILED_ATTEMPTS must be a non-negative integer: $RETRY_FAILED_ATTEMPTS" >&2
  exit 64
fi
for boolean_name in FAIL_ON_ITEM_FAILURE REQUIRE_DOWNSTREAM_SUCCESS RESTART_ON_FAILURE PERSISTENT; do
  boolean_value="${!boolean_name}"
  if [[ "$boolean_value" != "true" && "$boolean_value" != "false" ]]; then
    echo "$boolean_name must be true or false." >&2
    exit 64
  fi
done

PROJECT_INSTRUMENT_CORE="$PROJECT_ROOT/shared-data/instruments/python"
PYTHONPATH_VALUE="$BACKEND_ROOT:$PROJECT_ROOT/shared-data/market"
if [[ -d "$PROJECT_INSTRUMENT_CORE" ]]; then
  PYTHONPATH_VALUE="$BACKEND_ROOT:$PROJECT_INSTRUMENT_CORE:$PROJECT_ROOT/shared-data/market"
fi

USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$USER_SYSTEMD_DIR" "$LOG_DIR" "$(dirname "$LOCK_FILE")" "$(dirname "$SUMMARY_FILE")"

SERVICE_FILE="$USER_SYSTEMD_DIR/$UNIT_NAME.service"
TIMER_FILE="$USER_SYSTEMD_DIR/$UNIT_NAME.timer"

escaped_backend_root="$(printf '%q' "$BACKEND_ROOT")"
escaped_python_bin="$(printf '%q' "$PYTHON_BIN")"
escaped_pythonpath_value="$(printf '%q' "$PYTHONPATH_VALUE")"
escaped_log_file="$(printf '%q' "$LOG_FILE")"
escaped_channel="$(printf '%q' "$CHANNEL")"
escaped_updated_by="$(printf '%q' "$UPDATED_BY")"
escaped_retry_failed_attempts="$(printf '%q' "$RETRY_FAILED_ATTEMPTS")"
escaped_lock_file="$(printf '%q' "$LOCK_FILE")"
escaped_summary_file="$(printf '%q' "$SUMMARY_FILE")"
market_scope_arg=""
if [[ -n "$MARKET_SCOPE" ]]; then
  market_scope_arg=" --market-scope $(printf '%q' "$MARKET_SCOPE")"
fi
fail_on_item_failure_arg=""
if [[ "$FAIL_ON_ITEM_FAILURE" == "true" ]]; then
  fail_on_item_failure_arg=" --fail-on-item-failure"
fi
require_downstream_success_arg=""
if [[ "$REQUIRE_DOWNSTREAM_SUCCESS" == "true" ]]; then
  require_downstream_success_arg=" --require-downstream-success"
fi
restart_policy="no"
if [[ "$RESTART_ON_FAILURE" == "true" ]]; then
  restart_policy="on-failure"
fi
environment_file_line="EnvironmentFile=$(printf '%q' "$ENV_FILE")
EnvironmentFile=$(printf '%q' "$ENV_ROOT/market.env")"
schedule_condition=""
if [[ "$CHANNEL" == "market" && ( "$MARKET_SCOPE" == "hk" || "$MARKET_SCOPE" == "us" ) ]]; then
  if [[ ! -f "$PROJECT_ROOT/infra/scripts/market_close_schedule.py" ]]; then
    echo "Cannot find the market close schedule helper under PROJECT_ROOT." >&2
    exit 1
  fi
  escaped_schedule_helper="$(printf '%q' "$PROJECT_ROOT/infra/scripts/market_close_schedule.py")"
  schedule_condition="ExecCondition=/bin/bash -lc '$escaped_python_bin $escaped_schedule_helper --market-scope $MARKET_SCOPE'"
fi

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Investment Studio scheduled market data refresh
StartLimitIntervalSec=$START_LIMIT_INTERVAL_SEC
StartLimitBurst=$START_LIMIT_BURST

[Service]
Type=oneshot
WorkingDirectory=$BACKEND_ROOT
Environment=PYTHONPATH=$PYTHONPATH_VALUE
Environment=PYTHONNOUSERSITE=1
$environment_file_line
Environment=INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA=instrument_data
Environment=INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA=data_ingestion
TimeoutStartSec=$TIMEOUT_START_SEC
Restart=$restart_policy
RestartSec=$RESTART_SEC
$schedule_condition
ExecStart=/bin/bash -lc 'cd $escaped_backend_root && INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA=instrument_data INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA=data_ingestion PYTHONPATH=$escaped_pythonpath_value $escaped_python_bin $escaped_backend_root/scripts/refresh_market_data_scheduled.py --channel $escaped_channel$market_scope_arg --updated-by $escaped_updated_by --retry-failed-attempts $escaped_retry_failed_attempts --lock-file $escaped_lock_file --summary-file $escaped_summary_file --json$fail_on_item_failure_arg$require_downstream_success_arg >> $escaped_log_file 2>&1'
EOF

cat > "$TIMER_FILE" <<EOF
[Unit]
Description=Run Investment Studio market data refresh

[Timer]
OnCalendar=$ON_CALENDAR
Persistent=$PERSISTENT
AccuracySec=1min
Unit=$UNIT_NAME.service

[Install]
WantedBy=timers.target
EOF

systemctl --user stop "$UNIT_NAME.timer" >/dev/null 2>&1 || true
systemctl --user daemon-reload
systemctl --user enable "$UNIT_NAME.timer"
systemctl --user start "$UNIT_NAME.timer"

echo "Installed $SERVICE_FILE"
echo "Installed $TIMER_FILE"
systemctl --user list-timers "$UNIT_NAME.timer" --no-pager
