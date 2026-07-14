#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-launchd-plist-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

PROJECT_ROOT="$TEST_ROOT/project with spaces"
PLIST_ROOT="$TEST_ROOT/LaunchAgents"
LOG_ROOT="$TEST_ROOT/logs"
ENV_ROOT="$TEST_ROOT/secure env"
mkdir -p "$PROJECT_ROOT" "$PLIST_ROOT" "$LOG_ROOT" "$ENV_ROOT"
chmod 700 "$ENV_ROOT"
source "$REPOSITORY_ROOT/infra/service_inventory.sh"
EXPECTED_SERVICES="$(printf '%s\n' "${PORTFOLIO_OPS_ALL_SERVICE_NAMES[@]}")"

python3 "$REPOSITORY_ROOT/infra/launchd/generate_local_service_plists.py" \
  --project-root "$PROJECT_ROOT" \
  --python-bin "/runtime/python with spaces" \
  --node-bin "/runtime/node with spaces" \
  --database-url "postgresql+psycopg://local/test" \
  --label-prefix "test.portfolio-ops" \
  --launch-agents-dir "$PLIST_ROOT" \
  --log-dir "$LOG_ROOT" \
  --env-root "$ENV_ROOT" \
  --refresh-hour 21 \
  --refresh-minute 0

PLIST_ROOT="$PLIST_ROOT" PROJECT_ROOT="$PROJECT_ROOT" LOG_ROOT="$LOG_ROOT" ENV_ROOT="$ENV_ROOT" EXPECTED_SERVICES="$EXPECTED_SERVICES" python3 - <<'PY'
import os
import plistlib
from pathlib import Path


plist_root = Path(os.environ["PLIST_ROOT"])
project_root = str(Path(os.environ["PROJECT_ROOT"]).resolve())
log_root = str(Path(os.environ["LOG_ROOT"]).resolve())
env_root = str(Path(os.environ["ENV_ROOT"]).resolve())
expected_services = set(os.environ["EXPECTED_SERVICES"].splitlines())
generated_services = {
    path.name.removeprefix("test.portfolio-ops.").removesuffix(".plist")
    for path in plist_root.glob("test.portfolio-ops.*.plist")
}
assert generated_services == expected_services

with (plist_root / "test.portfolio-ops.platform-api.plist").open("rb") as source:
    api = plistlib.load(source)
assert api["KeepAlive"] is True
assert api["RunAtLoad"] is True
assert api["Umask"] == 0o077
assert api["ProgramArguments"][2] == project_root
assert api["ProgramArguments"][5] == env_root

with (plist_root / "test.portfolio-ops.portfolio-worker.plist").open("rb") as source:
    worker = plistlib.load(source)
assert worker["KeepAlive"] is True
assert worker["RunAtLoad"] is True
assert worker["ProcessType"] == "Background"
assert worker["ProgramArguments"][1] == "portfolio-worker"
assert worker["StandardOutPath"] == f"{log_root}/portfolio-worker.log"
assert worker["StandardErrorPath"] == f"{log_root}/portfolio-worker.error.log"

with (plist_root / "test.portfolio-ops.watchlist-worker.plist").open("rb") as source:
    watchlist_worker = plistlib.load(source)
assert watchlist_worker["KeepAlive"] is True
assert watchlist_worker["RunAtLoad"] is True
assert watchlist_worker["ProcessType"] == "Background"
assert watchlist_worker["ProgramArguments"][1] == "watchlist-worker"
assert watchlist_worker["StandardOutPath"] == f"{log_root}/watchlist-worker.log"
assert watchlist_worker["StandardErrorPath"] == f"{log_root}/watchlist-worker.error.log"

with (plist_root / "test.portfolio-ops.platform-outbox-worker.plist").open("rb") as source:
    platform_outbox_worker = plistlib.load(source)
assert platform_outbox_worker["KeepAlive"] is True
assert platform_outbox_worker["RunAtLoad"] is True
assert platform_outbox_worker["ProcessType"] == "Background"
assert platform_outbox_worker["ProgramArguments"][1] == "platform-outbox-worker"
assert platform_outbox_worker["StandardOutPath"] == f"{log_root}/platform-outbox-worker.log"
assert platform_outbox_worker["StandardErrorPath"] == f"{log_root}/platform-outbox-worker.error.log"

refresh_path = plist_root / "test.portfolio-ops.market-data-refresh.plist"
with refresh_path.open("rb") as source:
    refresh = plistlib.load(source)
assert refresh["KeepAlive"] is False
assert refresh["RunAtLoad"] is False
assert refresh["StartCalendarInterval"] == {"Hour": 21, "Minute": 0}
assert refresh["ProcessType"] == "Background"
assert refresh["LowPriorityIO"] is True
assert refresh["Umask"] == 0o077
assert refresh["ProgramArguments"] == [
    f"{project_root}/infra/launchd/run_market_data_refresh.sh",
    project_root,
    "/runtime/python with spaces",
    f"{project_root}/var/market-data-refresh.lock",
    env_root,
]
assert refresh["StandardOutPath"] == f"{log_root}/market-data-refresh.log"
assert refresh["StandardErrorPath"] == f"{log_root}/market-data-refresh.error.log"
assert refresh_path.stat().st_mode & 0o777 == 0o600
PY

grep -Fq 'http://127.0.0.1:8002/api/readiness' \
  "$REPOSITORY_ROOT/infra/launchd/install_local_services.sh"
grep -Fq 'http://127.0.0.1:8002/api/readiness' \
  "$REPOSITORY_ROOT/infra/launchd/status_local_services.sh"
grep -Fq 'source "$PROJECT_ROOT/infra/service_inventory.sh"' \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh"
grep -Fq 'PORTFOLIO_OPS_SYSTEMD_MANAGED_UNIT_SUFFIXES' \
  "$REPOSITORY_ROOT/infra/postgres/restore_project_dump.sh"

echo "launchd plist generation test passed."
