#!/usr/bin/env bash

# Canonical process inventory shared by launchd, systemd, release, and restore.
# Keep the cross-language LaunchAgent generator in sync through
# infra/tests/test_launchd_plist_generation.sh.
PORTFOLIO_OPS_RUNTIME_SERVICE_NAMES=(
  platform-api
  watchlist-api
  watchlist-worker
  platform-outbox-worker
  portfolio-api
  portfolio-worker
  platform-web
  watchlist-web
  portfolio-web
)
PORTFOLIO_OPS_SCHEDULED_SERVICE_NAMES=(
  market-data-refresh
)
PORTFOLIO_OPS_ALL_SERVICE_NAMES=(
  "${PORTFOLIO_OPS_RUNTIME_SERVICE_NAMES[@]}"
  "${PORTFOLIO_OPS_SCHEDULED_SERVICE_NAMES[@]}"
)
PORTFOLIO_OPS_SYSTEMD_MANAGED_UNIT_SUFFIXES=(
  platform-api.service
  watchlist-api.service
  watchlist-worker.service
  platform-outbox-worker.service
  portfolio-api.service
  portfolio-worker.service
  platform-web.service
  watchlist-web.service
  portfolio-web.service
  market-data-refresh.timer
  market-data-refresh.service
)
