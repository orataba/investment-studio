#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BACKEND_ROOT="${BACKEND_ROOT:-$PROJECT_ROOT/apps/platform/backend}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
UNIT_NAME="${UNIT_NAME:-yungu-market-data-refresh}"
ON_CALENDAR="${ON_CALENDAR:-*-*-* 09:00 Asia/Shanghai}"
LOG_DIR="${LOG_DIR:-$HOME/.local/state/yungu/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/market-data-refresh.log}"
CHANNEL="${CHANNEL:-all}"
UPDATED_BY="${UPDATED_BY:-scheduler}"
RETRY_FAILED_ATTEMPTS="${RETRY_FAILED_ATTEMPTS:-2}"
TIMEOUT_START_SEC="${TIMEOUT_START_SEC:-45min}"
PERSISTENT="${PERSISTENT:-false}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "PYTHON_BIN is not executable: $PYTHON_BIN" >&2
  exit 1
fi

if [[ ! -f "$BACKEND_ROOT/scripts/refresh_market_data_scheduled.py" ]]; then
  echo "Cannot find refresh script under BACKEND_ROOT: $BACKEND_ROOT" >&2
  exit 1
fi

USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$USER_SYSTEMD_DIR" "$LOG_DIR"

SERVICE_FILE="$USER_SYSTEMD_DIR/$UNIT_NAME.service"
TIMER_FILE="$USER_SYSTEMD_DIR/$UNIT_NAME.timer"

escaped_backend_root="$(printf '%q' "$BACKEND_ROOT")"
escaped_python_bin="$(printf '%q' "$PYTHON_BIN")"
escaped_log_file="$(printf '%q' "$LOG_FILE")"
escaped_channel="$(printf '%q' "$CHANNEL")"
escaped_updated_by="$(printf '%q' "$UPDATED_BY")"
escaped_retry_failed_attempts="$(printf '%q' "$RETRY_FAILED_ATTEMPTS")"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Yungu scheduled market data refresh

[Service]
Type=oneshot
WorkingDirectory=$BACKEND_ROOT
Environment=PYTHONPATH=$BACKEND_ROOT
TimeoutStartSec=$TIMEOUT_START_SEC
ExecStart=/bin/bash -lc 'cd $escaped_backend_root && PYTHONPATH=$escaped_backend_root $escaped_python_bin $escaped_backend_root/scripts/refresh_market_data_scheduled.py --channel $escaped_channel --updated-by $escaped_updated_by --retry-failed-attempts $escaped_retry_failed_attempts >> $escaped_log_file 2>&1'
EOF

cat > "$TIMER_FILE" <<EOF
[Unit]
Description=Run Yungu market data refresh daily at 09:00

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
