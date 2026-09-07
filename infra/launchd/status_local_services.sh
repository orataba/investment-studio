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

for refresh_service in market-data-refresh cn-market-data-refresh hk-market-data-refresh \
  us-market-data-refresh cn-hk-reference-data-refresh us-reference-data-refresh; do
refresh_label="$LABEL_PREFIX.$refresh_service"
refresh_plist="$LAUNCH_AGENTS_DIR/$refresh_label.plist"
refresh_schedule=unavailable
if [[ -f "$refresh_plist" && -x "$PYTHON_BIN" ]]; then
  refresh_schedule="$("$PYTHON_BIN" - "$refresh_plist" <<'PY_SCHEDULE'
import plistlib
import sys

with open(sys.argv[1], "rb") as source:
    plist = plistlib.load(source)
environment = plist.get("EnvironmentVariables", {})
timezone = environment.get("INVESTMENT_STUDIO_LOCAL_REFRESH_TIMEZONE", "Asia/Shanghai system time")
if environment.get("INVESTMENT_STUDIO_LOCAL_REFRESH_CHANNEL") == "market" and environment.get("INVESTMENT_STUDIO_LOCAL_REFRESH_MARKET_SCOPE") in {"hk", "us"}:
    print(f"session close + 30 minutes {timezone} (hourly calendar checks)")
elif "INVESTMENT_STUDIO_LOCAL_REFRESH_TIMEZONE" in environment:
    hour = int(environment["INVESTMENT_STUDIO_LOCAL_REFRESH_HOUR"])
    minute = int(environment["INVESTMENT_STUDIO_LOCAL_REFRESH_MINUTE"])
    print(f"{hour:02}:{minute:02} {timezone} (half-hour timezone checks)")
else:
    intervals = plist["StartCalendarInterval"]
    if isinstance(intervals, dict):
        intervals = [intervals]
    print(", ".join(f"{entry['Hour']:02}:{entry['Minute']:02}" for entry in intervals), timezone)
PY_SCHEDULE
)"
fi
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

refresh_summary="${INVESTMENT_STUDIO_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/investment-studio}/$refresh_service-summary.json"
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
printf '  log: %s/%s.log (errors: %s/%s.error.log)\n' \
  "$LOG_DIR" "$refresh_service" "$LOG_DIR" "$refresh_service"
printf '  summary: %s\n' "$refresh_summary"
done

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
