#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
LABEL_PREFIX="${LABEL_PREFIX:-com.orataba.investment-studio}"
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
LOG_DIR="${LOG_DIR:-$HOME/Library/Logs/investment-studio}"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
domain="gui/$UID"

for service in home-api watchlist-api portfolio-api home-web watchlist-web portfolio-web; do
  label="$LABEL_PREFIX.$service"
  if details="$(launchctl print "$domain/$label" 2>/dev/null)"; then
    state="$(sed -n 's/^[[:space:]]*state = //p' <<<"$details" | head -n 1)"
    pid="$(awk '/^[[:space:]]*pid = / { print $3; exit }' <<<"$details")"
    printf '%-15s state=%-8s pid=%s\n' "$service" "${state:-unknown}" "${pid:--}"
  else
    printf '%-15s not installed\n' "$service"
  fi
done

refresh_service=market-data-refresh
refresh_label="$LABEL_PREFIX.$refresh_service"
refresh_plist="$LAUNCH_AGENTS_DIR/$refresh_label.plist"
refresh_hour=21
refresh_minute=0
refresh_retry_hour=23
refresh_retry_minute=0
if [[ -f "$refresh_plist" && -x /usr/libexec/PlistBuddy ]]; then
  if /usr/libexec/PlistBuddy -c 'Print :StartCalendarInterval:0:Hour' "$refresh_plist" >/dev/null 2>&1; then
    refresh_hour="$(/usr/libexec/PlistBuddy -c 'Print :StartCalendarInterval:0:Hour' "$refresh_plist")"
    refresh_minute="$(/usr/libexec/PlistBuddy -c 'Print :StartCalendarInterval:0:Minute' "$refresh_plist")"
    refresh_retry_hour="$(/usr/libexec/PlistBuddy -c 'Print :StartCalendarInterval:1:Hour' "$refresh_plist")"
    refresh_retry_minute="$(/usr/libexec/PlistBuddy -c 'Print :StartCalendarInterval:1:Minute' "$refresh_plist")"
  else
    refresh_hour="$(/usr/libexec/PlistBuddy -c 'Print :StartCalendarInterval:Hour' "$refresh_plist" 2>/dev/null || printf '21')"
    refresh_minute="$(/usr/libexec/PlistBuddy -c 'Print :StartCalendarInterval:Minute' "$refresh_plist" 2>/dev/null || printf '0')"
  fi
fi
printf -v refresh_schedule '%02d:%02d primary, %02d:%02d conditional retry (local)' \
  "$refresh_hour" "$refresh_minute" "$refresh_retry_hour" "$refresh_retry_minute"
if details="$(launchctl print "$domain/$refresh_label" 2>/dev/null)"; then
  state="$(sed -n 's/^[[:space:]]*state = //p' <<<"$details" | head -n 1)"
  pid="$(awk '/^[[:space:]]*pid = / { print $3; exit }' <<<"$details")"
  runs="$(awk '/^[[:space:]]*runs = / { print $3; exit }' <<<"$details")"
  last_exit="$(sed -n 's/^[[:space:]]*last exit code = //p' <<<"$details" | head -n 1)"
  if [[ "$last_exit" == "(never exited)" ]]; then
    last_exit=never
  fi
  printf '%-20s state=%-11s pid=%-8s runs=%-5s last_exit=%-4s schedule=%s\n' \
    "$refresh_service" "${state:-idle}" "${pid:--}" "${runs:-0}" "${last_exit:--}" "$refresh_schedule"
else
  printf '%-20s not installed (schedule=%s)\n' "$refresh_service" "$refresh_schedule"
fi

refresh_summary="${INVESTMENT_STUDIO_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/investment-studio}/market-data-refresh-summary.json"
if [[ -f "$refresh_summary" && -x "$PYTHON_BIN" ]]; then
  "$PYTHON_BIN" - "$refresh_summary" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path


try:
    summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, ValueError) as error:
    print(f"  latest summary is unreadable: {error}")
else:
    print(
        "  latest result: "
        f"status={summary.get('status', 'unknown')} "
        f"finished_at={summary.get('finished_at', '-')} "
        f"updated={summary.get('updated_instrument_count', '-')} "
        f"item_failures={summary.get('failed_item_count', '-')} "
        f"downstream_failures={summary.get('downstream_failure_count', '-')}"
    )
PY
elif [[ -f "$refresh_summary" ]]; then
  printf '  latest summary: %s\n' "$refresh_summary"
else
  printf '  latest result: no completed run recorded\n'
fi

printf '\nHealth checks:\n'
for url in \
  http://127.0.0.1:8002/api/health \
  http://127.0.0.1:8000/api/health \
  http://127.0.0.1:8001/api/health \
  http://127.0.0.1:5172/ \
  http://127.0.0.1:5173/ \
  http://127.0.0.1:5174/; do
  if curl --noproxy '*' --max-time 2 --fail --silent --output /dev/null "$url"; then
    printf 'ok      %s\n' "$url"
  else
    printf 'failed  %s\n' "$url"
  fi
done

printf '\nLogs: %s\n' "$LOG_DIR"
printf 'Scheduled refresh: %s/%s.log (errors: %s/%s.error.log)\n' \
  "$LOG_DIR" "$refresh_service" "$LOG_DIR" "$refresh_service"
printf 'Scheduled summary: %s\n' "$refresh_summary"
