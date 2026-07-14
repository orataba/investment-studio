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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../service_inventory.sh"
services=("${PORTFOLIO_OPS_ALL_SERVICE_NAMES[@]}")

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
  local candidate="$1"
  local service
  for service in "${PORTFOLIO_OPS_SCHEDULED_SERVICE_NAMES[@]}"; do
    if [[ "$candidate" == "$service" ]]; then
      return 0
    fi
  done
  return 1
}

bootstrap_service() {
  local label="$1"
  local plist="$2"
  local attempt
  local max_attempts="${LAUNCHD_BOOTSTRAP_MAX_ATTEMPTS:-5}"
  local retry_delay_seconds="${LAUNCHD_BOOTSTRAP_RETRY_DELAY_SECONDS:-1}"

  if [[ ! "$max_attempts" =~ ^[1-9][0-9]*$ ]]; then
    echo "LAUNCHD_BOOTSTRAP_MAX_ATTEMPTS must be a positive integer." >&2
    return 64
  fi
  if [[ ! "$retry_delay_seconds" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "LAUNCHD_BOOTSTRAP_RETRY_DELAY_SECONDS must be non-negative." >&2
    return 64
  fi

  for attempt in $(seq 1 "$max_attempts"); do
    # bootout can return before launchd has fully released the old job.  Clear
    # any partial retry state and tolerate that short teardown window.
    launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
    if launchctl bootstrap "$domain" "$plist"; then
      return 0
    fi
    if [[ "$attempt" -lt "$max_attempts" ]]; then
      sleep "$retry_delay_seconds"
    fi
  done

  echo "Cannot restart $label after $max_attempts bootstrap attempts." >&2
  return 1
}

stop_services() {
  local service label plist details pid
  local -a stopped_pids=()
  local stop_timeout_seconds="${LAUNCHD_STOP_TIMEOUT_SECONDS:-30}"
  local stop_poll_interval_seconds="${LAUNCHD_STOP_POLL_INTERVAL_SECONDS:-0.2}"
  if [[ ! "$stop_timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
    echo "LAUNCHD_STOP_TIMEOUT_SECONDS must be a positive integer." >&2
    exit 64
  fi
  if [[ ! "$stop_poll_interval_seconds" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "LAUNCHD_STOP_POLL_INTERVAL_SECONDS must be non-negative." >&2
    exit 64
  fi
  mkdir -p "$(dirname "$STATE_FILE")"
  : > "$STATE_FILE"
  chmod 600 "$STATE_FILE"

  # Record and validate the complete restart set before unloading anything.
  # That lets the caller recover cleanly even if a later bootout fails.
  for service in "${services[@]}"; do
    label="$LABEL_PREFIX.$service"
    plist="$LAUNCH_AGENTS_DIR/$label.plist"
    if ! details="$(launchctl print "$domain/$label" 2>/dev/null)"; then
      continue
    fi
    if [[ ! -f "$plist" ]]; then
      echo "Cannot safely stop $label: missing plist $plist" >&2
      exit 1
    fi
    printf '%s\n' "$service" >> "$STATE_FILE"
    pid="$(awk '/^[[:space:]]*pid = / { print $3; exit }' <<<"$details")"
    if [[ "$pid" =~ ^[1-9][0-9]*$ ]]; then
      stopped_pids+=("$pid")
    fi
  done

  while IFS= read -r service || [[ -n "$service" ]]; do
    [[ -n "$service" ]] || continue
    label="$LABEL_PREFIX.$service"
    launchctl bootout "$domain/$label"
  done < "$STATE_FILE"

  # launchctl may acknowledge bootout before the process has actually exited.
  # Every captured API and worker PID must be gone before a database release
  # can safely assume that all writers are fenced.
  if [[ ${#stopped_pids[@]} -gt 0 ]]; then
    local deadline=$((SECONDS + stop_timeout_seconds))
    local -a remaining_pids=()
    while true; do
      remaining_pids=()
      for pid in "${stopped_pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
          remaining_pids+=("$pid")
        fi
      done
      if [[ ${#remaining_pids[@]} -eq 0 ]]; then
        break
      fi
      if (( SECONDS >= deadline )); then
        echo "Timed out waiting for managed PID(s) to exit: ${remaining_pids[*]}" >&2
        exit 1
      fi
      sleep "$stop_poll_interval_seconds"
    done
  fi
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
    bootstrap_service "$label" "$plist"
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
