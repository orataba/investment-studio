#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-f" ]]; then
  printf '  File: "%s"\n' "${3:-unknown}"
  printf '    ID: deadbeef Namelen: 255 Type: ext2/ext3\n'
  exit 1
fi

if [[ "${1:-}" == "-c" && "${2:-}" == "%s" ]]; then
  wc -c < "${3:?missing file path}" | tr -d '[:space:]'
  printf '\n'
  exit 0
fi

printf 'Unexpected stat arguments: %s\n' "$*" >&2
exit 64
