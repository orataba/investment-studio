#!/usr/bin/env bash

# Shared post-start runtime gates for release, restore, and service installers.
# This file is sourced; callers retain their own `set -euo pipefail` policy.

portfolio_ops_runtime_health_host() {
  local bind_host="${1:-127.0.0.1}"

  case "$bind_host" in
    ""|0.0.0.0|::|\[::\]|\*)
      printf '%s\n' '127.0.0.1'
      ;;
    \[*\])
      printf '%s\n' "$bind_host"
      ;;
    *:*)
      printf '[%s]\n' "$bind_host"
      ;;
    *)
      printf '%s\n' "$bind_host"
      ;;
  esac
}

portfolio_ops_runtime_urls_for_service_state() {
  local state_file="$1"
  local systemd_unit_prefix="$2"
  local item
  local platform_api_port="${PLATFORM_API_PORT:-8102}"
  local watchlist_api_port="${WATCHLIST_API_PORT:-8100}"
  local portfolio_api_port="${PORTFOLIO_API_PORT:-8101}"
  local platform_web_port="${PLATFORM_WEB_PORT:-3100}"
  local watchlist_web_port="${WATCHLIST_WEB_PORT:-3101}"
  local portfolio_web_port="${PORTFOLIO_WEB_PORT:-3102}"
  local api_health_host web_health_host
  api_health_host="$(
    portfolio_ops_runtime_health_host \
      "${API_HEALTH_HOST:-${API_HOST:-127.0.0.1}}"
  )"
  web_health_host="$(
    portfolio_ops_runtime_health_host \
      "${WEB_HEALTH_HOST:-${WEB_HOST:-127.0.0.1}}"
  )"

  [[ -f "$state_file" ]] || return 0
  while IFS= read -r item || [[ -n "$item" ]]; do
    case "$item" in
      platform-api) printf '%s\n' 'http://127.0.0.1:8002/api/readiness' ;;
      watchlist-api) printf '%s\n' 'http://127.0.0.1:8000/api/readiness' ;;
      portfolio-api) printf '%s\n' 'http://127.0.0.1:8001/api/readiness' ;;
      platform-web) printf '%s\n' 'http://127.0.0.1:5172/' ;;
      watchlist-web) printf '%s\n' 'http://127.0.0.1:5173/' ;;
      portfolio-web) printf '%s\n' 'http://127.0.0.1:5174/' ;;
      "$systemd_unit_prefix-platform-api.service") printf 'http://%s:%s/api/readiness\n' "$api_health_host" "$platform_api_port" ;;
      "$systemd_unit_prefix-watchlist-api.service") printf 'http://%s:%s/api/readiness\n' "$api_health_host" "$watchlist_api_port" ;;
      "$systemd_unit_prefix-portfolio-api.service") printf 'http://%s:%s/api/readiness\n' "$api_health_host" "$portfolio_api_port" ;;
      "$systemd_unit_prefix-platform-web.service") printf 'http://%s:%s/\n' "$web_health_host" "$platform_web_port" ;;
      "$systemd_unit_prefix-watchlist-web.service") printf 'http://%s:%s/\n' "$web_health_host" "$watchlist_web_port" ;;
      "$systemd_unit_prefix-portfolio-web.service") printf 'http://%s:%s/\n' "$web_health_host" "$portfolio_web_port" ;;
    esac
  done < "$state_file"
}

portfolio_ops_wait_for_runtime_urls() {
  local max_attempts="$1"
  shift
  local poll_interval_seconds="${PORTFOLIO_OPS_RUNTIME_HEALTH_POLL_INTERVAL_SECONDS:-1}"
  local attempt url failed_url="" healthy

  if [[ ! "$max_attempts" =~ ^[1-9][0-9]*$ ]]; then
    echo "Runtime health attempts must be a positive integer: $max_attempts" >&2
    return 64
  fi
  if [[ ! "$poll_interval_seconds" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "Runtime health poll interval must be non-negative: $poll_interval_seconds" >&2
    return 64
  fi
  if [[ $# -eq 0 ]]; then
    echo "No active HTTP services require a runtime readiness check."
    return 0
  fi
  local urls=("$@")
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required for runtime readiness checks." >&2
    return 1
  fi

  for ((attempt = 1; attempt <= max_attempts; attempt += 1)); do
    healthy="true"
    failed_url=""
    for url in "${urls[@]}"; do
      if ! curl --noproxy '*' --max-time 2 --fail --silent \
        --output /dev/null "$url"; then
        healthy="false"
        failed_url="$url"
        break
      fi
    done
    if [[ "$healthy" == "true" ]]; then
      echo "All requested runtime readiness checks passed."
      return 0
    fi
    if [[ "$attempt" -lt "$max_attempts" ]]; then
      sleep "$poll_interval_seconds"
    fi
  done

  echo "Runtime readiness failed after $max_attempts attempts; last failing URL: $failed_url" >&2
  return 1
}

portfolio_ops_wait_for_service_state_readiness() {
  local state_file="$1"
  local systemd_unit_prefix="$2"
  local max_attempts="$3"
  local urls=() url url_count=0

  while IFS= read -r url || [[ -n "$url" ]]; do
    if [[ -n "$url" ]]; then
      urls+=("$url")
      url_count=$((url_count + 1))
    fi
  done < <(
    portfolio_ops_runtime_urls_for_service_state \
      "$state_file" "$systemd_unit_prefix"
  )
  if [[ $url_count -eq 0 ]]; then
    portfolio_ops_wait_for_runtime_urls "$max_attempts"
  else
    portfolio_ops_wait_for_runtime_urls "$max_attempts" "${urls[@]}"
  fi
}

portfolio_ops_portfolio_api_base_for_service_state() {
  local state_file="$1"
  local systemd_unit_prefix="$2"
  local item
  local portfolio_api_port="${PORTFOLIO_API_PORT:-8101}"
  local api_health_host
  api_health_host="$(
    portfolio_ops_runtime_health_host \
      "${API_HEALTH_HOST:-${API_HOST:-127.0.0.1}}"
  )"

  [[ -f "$state_file" ]] || return 0
  while IFS= read -r item || [[ -n "$item" ]]; do
    case "$item" in
      portfolio-api)
        printf '%s\n' 'http://127.0.0.1:8001'
        return 0
        ;;
      "$systemd_unit_prefix-portfolio-api.service")
        printf 'http://%s:%s\n' "$api_health_host" "$portfolio_api_port"
        return 0
        ;;
    esac
  done < "$state_file"
}

portfolio_ops_verify_portfolio_read_contract() {
  local python_bin="$1"
  local base_url="$2"

  if [[ -z "$base_url" ]]; then
    echo "No active Portfolio API requires a read-contract smoke test."
    return 0
  fi
  if [[ ! -x "$python_bin" ]]; then
    echo "Portfolio read-contract Python is not executable: $python_bin" >&2
    return 1
  fi

  "$python_bin" - "$base_url" <<'PY'
from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, build_opener


base_url = sys.argv[1].rstrip("/")
opener = build_opener(ProxyHandler({}))


def get_json(path: str) -> object:
    url = f"{base_url}{path}"
    try:
        with opener.open(url, timeout=10) as response:
            if response.status != 200:
                raise SystemExit(
                    f"Portfolio read smoke returned HTTP {response.status}: {url}"
                )
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Portfolio read smoke failed for {url}: {exc}") from exc


portfolios = get_json("/api/portfolios")
if not isinstance(portfolios, list):
    raise SystemExit("Portfolio list response is not a JSON array")

transaction_count = 0
history_count = 0
for portfolio in portfolios:
    if not isinstance(portfolio, dict) or not isinstance(
        portfolio.get("portfolio_id"), str
    ):
        raise SystemExit("Portfolio list contains an invalid portfolio identity")
    portfolio_id = quote(portfolio["portfolio_id"], safe="")
    transaction_response = get_json(
        f"/api/portfolios/{portfolio_id}/transactions"
    )
    if not isinstance(transaction_response, dict) or not isinstance(
        transaction_response.get("transactions"), list
    ):
        raise SystemExit(
            "Portfolio transaction response has an invalid envelope"
        )
    transactions = transaction_response["transactions"]
    transaction_count += len(transactions)
    for transaction in transactions:
        if not isinstance(transaction, dict) or not isinstance(
            transaction.get("transaction_id"), str
        ):
            raise SystemExit(
                "Portfolio transaction response contains an invalid identity"
            )
        transaction_id = quote(transaction["transaction_id"], safe="")
        history = get_json(
            f"/api/portfolios/{portfolio_id}/transactions/"
            f"{transaction_id}/revisions"
        )
        if not isinstance(history, dict) or not isinstance(
            history.get("revisions"), list
        ):
            raise SystemExit(
                "Transaction revision history response has an invalid envelope"
            )
        revisions = history["revisions"]
        if not revisions:
            raise SystemExit(
                "Transaction revision history is unexpectedly empty"
            )
        history_count += len(revisions)

print(
    "Portfolio read-contract smoke passed: "
    f"{len(portfolios)} portfolios, {transaction_count} current transactions, "
    f"{history_count} revisions."
)
PY
}

portfolio_ops_verify_portfolio_read_contract_for_service_state() {
  local state_file="$1"
  local systemd_unit_prefix="$2"
  local python_bin="$3"
  local base_url

  base_url="$(
    portfolio_ops_portfolio_api_base_for_service_state \
      "$state_file" "$systemd_unit_prefix"
  )"
  portfolio_ops_verify_portfolio_read_contract "$python_bin" "$base_url"
}
