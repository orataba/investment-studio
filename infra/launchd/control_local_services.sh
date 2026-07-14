#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <stop|start> <state-file>" >&2
  exit 64
fi

ACTION="$1"
STATE_FILE="$2"
LABEL_PREFIX="${LABEL_PREFIX:-com.orataba.portfolio-ops}"
LAUNCH_AGENTS_DIR="${LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
domain="gui/$UID"
services=(
  platform-api
  watchlist-api
  portfolio-api
  platform-web
  watchlist-web
  portfolio-web
  market-data-refresh
)

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "launchd service control is only available on macOS." >&2
  exit 1
fi
if ! command -v launchctl >/dev/null 2>&1; then
  echo "launchctl is unavailable." >&2
  exit 1
fi

is_known_service() {
  local candidate="$1"
  local service
  for service in "${services[@]}"; do
    if [[ "$candidate" == "$service" ]]; then
      return 0
    fi
  done
  return 1
}

is_scheduled_service() {
  [[ "$1" == "market-data-refresh" ]]
}

stop_services() {
  local service label plist
  mkdir -p "$(dirname "$STATE_FILE")"
  : > "$STATE_FILE"
  chmod 600 "$STATE_FILE"

  # Record and validate the complete restart set before unloading anything.
  # That lets the caller recover cleanly even if a later bootout fails.
  for service in "${services[@]}"; do
    label="$LABEL_PREFIX.$service"
    plist="$LAUNCH_AGENTS_DIR/$label.plist"
    if ! launchctl print "$domain/$label" >/dev/null 2>&1; then
      continue
    fi
    if [[ ! -f "$plist" ]]; then
      echo "Cannot safely stop $label: missing plist $plist" >&2
      exit 1
    fi
    printf '%s\n' "$service" >> "$STATE_FILE"
  done

  while IFS= read -r service || [[ -n "$service" ]]; do
    [[ -n "$service" ]] || continue
    label="$LABEL_PREFIX.$service"
    launchctl bootout "$domain/$label"
  done < "$STATE_FILE"
}

start_services() {
  local service label plist
  [[ -f "$STATE_FILE" ]] || return 0

  while IFS= read -r service || [[ -n "$service" ]]; do
    [[ -n "$service" ]] || continue
    if ! is_known_service "$service"; then
      echo "Invalid launchd service in state file: $service" >&2
      exit 1
    fi
    label="$LABEL_PREFIX.$service"
    plist="$LAUNCH_AGENTS_DIR/$label.plist"
    if [[ ! -f "$plist" ]]; then
      echo "Cannot restart $label: missing plist $plist" >&2
      exit 1
    fi
    launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
    launchctl bootstrap "$domain" "$plist"
    launchctl enable "$domain/$label"
    if ! is_scheduled_service "$service"; then
      launchctl kickstart -k "$domain/$label"
    fi
  done < "$STATE_FILE"
}

case "$ACTION" in
  stop)
    stop_services
    ;;
  start)
    start_services
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    exit 64
    ;;
esac
