#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BACKEND_ROOT="${BACKEND_ROOT:-$PROJECT_ROOT/apps/platform/backend}"
DEFAULT_PYTHON_BIN="$(command -v python3)"
if [[ -x "$BACKEND_ROOT/.venv/bin/python" ]]; then
  DEFAULT_PYTHON_BIN="$BACKEND_ROOT/.venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"
UNIT_NAME="${UNIT_NAME:-portfolio-ops-market-data-refresh}"
ON_CALENDAR="${ON_CALENDAR:-*-*-* 09:00 Asia/Shanghai}"
ENV_ROOT="${ENV_ROOT:-}"
ENV_FILE="${ENV_FILE:-}"
LOG_DIR="${LOG_DIR:-$HOME/.local/state/portfolio-ops/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/market-data-refresh.log}"
CHANNEL="${CHANNEL:-all}"
UPDATED_BY="${UPDATED_BY:-scheduler}"
RETRY_FAILED_ATTEMPTS="${RETRY_FAILED_ATTEMPTS:-2}"
TIMEOUT_START_SEC="${TIMEOUT_START_SEC:-2h}"
FAIL_ON_ITEM_FAILURE="${FAIL_ON_ITEM_FAILURE:-true}"
RESTART_ON_FAILURE="${RESTART_ON_FAILURE:-true}"
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

if [[ -z "$ENV_FILE" && -n "$ENV_ROOT" ]]; then
  ENV_FILE="$ENV_ROOT/platform.env"
fi

if [[ -n "$ENV_FILE" && ! -f "$ENV_FILE" ]]; then
  echo "ENV_FILE does not exist: $ENV_FILE" >&2
  exit 1
fi

PROJECT_INSTRUMENT_CORE="$PROJECT_ROOT/packages/instrument-core/python"
PYTHONPATH_VALUE="$BACKEND_ROOT"
if [[ -d "$PROJECT_INSTRUMENT_CORE" ]]; then
  PYTHONPATH_VALUE="$BACKEND_ROOT:$PROJECT_INSTRUMENT_CORE"
fi

USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$USER_SYSTEMD_DIR" "$LOG_DIR"

SERVICE_FILE="$USER_SYSTEMD_DIR/$UNIT_NAME.service"
TIMER_FILE="$USER_SYSTEMD_DIR/$UNIT_NAME.timer"

escaped_backend_root="$(printf '%q' "$BACKEND_ROOT")"
escaped_python_bin="$(printf '%q' "$PYTHON_BIN")"
escaped_pythonpath_value="$(printf '%q' "$PYTHONPATH_VALUE")"
escaped_log_file="$(printf '%q' "$LOG_FILE")"
escaped_channel="$(printf '%q' "$CHANNEL")"
escaped_updated_by="$(printf '%q' "$UPDATED_BY")"
escaped_retry_failed_attempts="$(printf '%q' "$RETRY_FAILED_ATTEMPTS")"
fail_on_item_failure_arg=""
if [[ "$FAIL_ON_ITEM_FAILURE" == "true" ]]; then
  fail_on_item_failure_arg=" --fail-on-item-failure"
fi
restart_policy="no"
if [[ "$RESTART_ON_FAILURE" == "true" ]]; then
  restart_policy="on-failure"
fi
environment_file_line=""
if [[ -n "$ENV_FILE" ]]; then
  environment_file_line="EnvironmentFile=$ENV_FILE"
fi

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Portfolio Operations scheduled market data refresh
StartLimitIntervalSec=$START_LIMIT_INTERVAL_SEC
StartLimitBurst=$START_LIMIT_BURST

[Service]
Type=oneshot
WorkingDirectory=$BACKEND_ROOT
Environment=PYTHONPATH=$PYTHONPATH_VALUE
Environment=PYTHONNOUSERSITE=1
$environment_file_line
TimeoutStartSec=$TIMEOUT_START_SEC
Restart=$restart_policy
RestartSec=$RESTART_SEC
ExecStart=/bin/bash -lc 'cd $escaped_backend_root && PYTHONPATH=$escaped_pythonpath_value $escaped_python_bin $escaped_backend_root/scripts/refresh_market_data_scheduled.py --channel $escaped_channel --updated-by $escaped_updated_by --retry-failed-attempts $escaped_retry_failed_attempts$fail_on_item_failure_arg >> $escaped_log_file 2>&1'
EOF

cat > "$TIMER_FILE" <<EOF
[Unit]
Description=Run Portfolio Operations market data refresh

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
