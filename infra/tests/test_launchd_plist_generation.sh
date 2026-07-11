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

PLIST_ROOT="$PLIST_ROOT" PROJECT_ROOT="$PROJECT_ROOT" LOG_ROOT="$LOG_ROOT" ENV_ROOT="$ENV_ROOT" python3 - <<'PY'
import os
import plistlib
from pathlib import Path


plist_root = Path(os.environ["PLIST_ROOT"])
project_root = str(Path(os.environ["PROJECT_ROOT"]).resolve())
log_root = str(Path(os.environ["LOG_ROOT"]).resolve())
env_root = str(Path(os.environ["ENV_ROOT"]).resolve())
expected_services = {
    "platform-api",
    "watchlist-api",
    "portfolio-api",
    "platform-web",
    "watchlist-web",
    "portfolio-web",
    "market-data-refresh",
}
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

echo "launchd plist generation test passed."
