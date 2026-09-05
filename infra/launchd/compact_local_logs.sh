#!/usr/bin/env bash
set -euo pipefail
umask 077

LOG_DIR="${1:-${LOG_DIR:-$HOME/Library/Logs/investment-studio}}"
MAX_BYTES="${INVESTMENT_STUDIO_LOCAL_LOG_MAX_BYTES:-104857600}"
RETAIN_BYTES="${INVESTMENT_STUDIO_LOCAL_LOG_RETAIN_BYTES:-10485760}"

for value_name in MAX_BYTES RETAIN_BYTES; do
  value="${!value_name}"
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "$value_name must be a positive integer." >&2
    exit 64
  fi
done
if (( RETAIN_BYTES > MAX_BYTES )); then
  echo "RETAIN_BYTES must not exceed MAX_BYTES." >&2
  exit 64
fi

mkdir -p "$LOG_DIR"

file_size_bytes() {
  local path="$1"
  local size_bytes

  if size_bytes="$(stat -f '%z' "$path" 2>/dev/null)" \
    && [[ "$size_bytes" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$size_bytes"
    return 0
  fi
  if size_bytes="$(stat -c '%s' "$path" 2>/dev/null)" \
    && [[ "$size_bytes" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$size_bytes"
    return 0
  fi

  echo "Unable to determine the size of $path." >&2
  return 1
}

while IFS= read -r -d '' log_file; do
  size_bytes="$(file_size_bytes "$log_file")"
  if (( size_bytes <= MAX_BYTES )); then
    continue
  fi
  file_name="$(basename "$log_file")"
  temporary_file="$(mktemp "$LOG_DIR/.${file_name}.compact.XXXXXX")"
  if ! tail -c "$RETAIN_BYTES" "$log_file" > "$temporary_file"; then
    rm -f "$temporary_file"
    exit 1
  fi
  chmod 600 "$temporary_file"
  mv -f "$temporary_file" "$log_file"
  echo "Compacted $file_name from $size_bytes to $(file_size_bytes "$log_file") bytes."
done < <(find "$LOG_DIR" -maxdepth 1 -type f -name '*.log' -print0)
