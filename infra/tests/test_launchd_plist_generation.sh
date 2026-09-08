#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/investment-studio-launchd-plist-test.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

PROJECT_ROOT="$TEST_ROOT/project with spaces"
PLIST_ROOT="$TEST_ROOT/LaunchAgents"
LOG_ROOT="$TEST_ROOT/logs"
ENV_ROOT="$TEST_ROOT/secure env"
mkdir -p "$PROJECT_ROOT" "$PLIST_ROOT" "$LOG_ROOT" "$ENV_ROOT"
chmod 700 "$ENV_ROOT"

INVESTMENT_STUDIO_LOCAL_DATABASE_URL="postgresql+psycopg://local@127.0.0.1:5432/test" \
  python3 "$REPOSITORY_ROOT/infra/launchd/generate_local_service_plists.py" \
  --project-root "$PROJECT_ROOT" \
  --python-bin "/runtime/python with spaces" \
  --node-bin "/runtime/node with spaces" \
  --label-prefix "test.investment-studio" \
  --launch-agents-dir "$PLIST_ROOT" \
  --log-dir "$LOG_ROOT" \
  --env-root "$ENV_ROOT" \
  --refresh-hour 21 \
  --refresh-minute 0 \
  --refresh-retry-hour 23 \
  --refresh-retry-minute 0

PLIST_ROOT="$PLIST_ROOT" PROJECT_ROOT="$PROJECT_ROOT" LOG_ROOT="$LOG_ROOT" ENV_ROOT="$ENV_ROOT" python3 - <<'PY'
import os
import plistlib
from pathlib import Path


plist_root = Path(os.environ["PLIST_ROOT"])
project_root = str(Path(os.environ["PROJECT_ROOT"]).resolve())
log_root = str(Path(os.environ["LOG_ROOT"]).resolve())
env_root = str(Path(os.environ["ENV_ROOT"]).resolve())
expected_services = {
    "home-api",
    "watchlist-api",
    "portfolio-api",
    "briefing-api",
    "home-web",
    "watchlist-web",
    "portfolio-web",
    "briefing-web",
    "market-data-refresh",
    "cn-market-data-refresh",
    "hk-market-data-refresh",
    "us-market-data-refresh",
    "cn-hk-reference-data-refresh",
    "us-reference-data-refresh",
}
generated_services = {
    path.name.removeprefix("test.investment-studio.").removesuffix(".plist")
    for path in plist_root.glob("test.investment-studio.*.plist")
}
assert generated_services == expected_services

with (plist_root / "test.investment-studio.home-api.plist").open("rb") as source:
    api = plistlib.load(source)
assert api["KeepAlive"] is True
assert api["RunAtLoad"] is True
assert api["Umask"] == 0o077
assert api["ProgramArguments"][2] == project_root
assert api["ProgramArguments"][5] == env_root
assert api["EnvironmentVariables"] == {
    "INVESTMENT_STUDIO_LOCAL_DATABASE_URL": "postgresql+psycopg://local@127.0.0.1:5432/test"
}
with (plist_root / "test.investment-studio.watchlist-api.plist").open("rb") as source:
    watchlist_api = plistlib.load(source)
assert watchlist_api["EnvironmentVariables"] == {
    "INVESTMENT_STUDIO_LOCAL_DATABASE_URL": "postgresql+psycopg://local@127.0.0.1:5432/test"
}

with (plist_root / "test.investment-studio.home-web.plist").open("rb") as source:
    web = plistlib.load(source)
assert "EnvironmentVariables" not in web

refresh_path = plist_root / "test.investment-studio.market-data-refresh.plist"
with refresh_path.open("rb") as source:
    refresh = plistlib.load(source)
assert refresh["KeepAlive"] is False
assert refresh["RunAtLoad"] is True
assert refresh["StartCalendarInterval"] == [
    {"Hour": 21, "Minute": 0},
    {"Hour": 23, "Minute": 0},
]
assert refresh["ProcessType"] == "Background"
assert refresh["LowPriorityIO"] is True
assert refresh["Umask"] == 0o077
assert refresh["ProgramArguments"] == [
    f"{project_root}/infra/launchd/run_market_data_refresh.sh",
    project_root,
    "/runtime/python with spaces",
    str(Path.home() / ".local/state/investment-studio/market-data-refresh.lock"),
    env_root,
]
assert refresh["StandardOutPath"] == f"{log_root}/market-data-refresh.log"
assert refresh["StandardErrorPath"] == f"{log_root}/market-data-refresh.error.log"
assert refresh["EnvironmentVariables"] == {
    "INVESTMENT_STUDIO_LOCAL_DATABASE_URL": "postgresql+psycopg://local@127.0.0.1:5432/test",
    "INVESTMENT_STUDIO_LOCAL_REFRESH_CHANNEL": "settlement",
    "INVESTMENT_STUDIO_LOCAL_REFRESH_HOUR": "21",
    "INVESTMENT_STUDIO_LOCAL_REFRESH_MINUTE": "0",
    "INVESTMENT_STUDIO_LOCAL_REFRESH_RETRY_HOUR": "23",
    "INVESTMENT_STUDIO_LOCAL_REFRESH_RETRY_MINUTE": "0",
}
assert refresh_path.stat().st_mode & 0o777 == 0o600

for scope, channel, hour, minute in (
    ("cn", "market", 15, 30),
    ("hk", "market", 16, 30),
    ("us", "market", 16, 30),
    ("cn-hk", "reference", 8, 0),
    ("us", "reference", 8, 0),
):
    name = f"{scope}-{channel}-data-refresh"
    with (plist_root / f"test.investment-studio.{name}.plist").open("rb") as source:
        scoped = plistlib.load(source)
    assert scoped["RunAtLoad"] is False
    assert scoped["KeepAlive"] is False
    assert scoped["ProgramArguments"] == refresh["ProgramArguments"]
    environment = scoped["EnvironmentVariables"]
    assert environment["INVESTMENT_STUDIO_LOCAL_REFRESH_CHANNEL"] == channel
    assert environment["INVESTMENT_STUDIO_LOCAL_REFRESH_MARKET_SCOPE"] == scope
    assert environment["INVESTMENT_STUDIO_LOCAL_REFRESH_RUN_KIND"] == "primary"
    assert environment["INVESTMENT_STUDIO_LOCAL_REFRESH_HOUR"] == str(hour)
    assert environment["INVESTMENT_STUDIO_LOCAL_REFRESH_MINUTE"] == str(minute)
    if channel == "market" and scope in {"hk", "us"}:
        assert scoped["StartCalendarInterval"] == {"Minute": 30}
    elif scope == "us":
        assert scoped["StartCalendarInterval"] == [{"Minute": 0}, {"Minute": 30}]
        assert environment["INVESTMENT_STUDIO_LOCAL_REFRESH_TIMEZONE"] == "America/New_York"
    else:
        assert scoped["StartCalendarInterval"] == {"Hour": hour, "Minute": minute}
    assert scoped["StandardOutPath"] == f"{log_root}/{name}.log"
    assert scoped["StandardErrorPath"] == f"{log_root}/{name}.error.log"

PY

echo "launchd plist generation test passed."
