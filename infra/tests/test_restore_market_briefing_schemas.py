"""Portable restore invocation: no PostgreSQL connection or service mutation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ["identity", "instrument_data", "data_ingestion", "portfolio", "watchlist", "market_data", "market_text", "briefing"]


def test_restore_includes_public_data_and_briefing_then_restarts_prior_writers(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    events = tmp_path / "events"
    driver = binary / "mock-command"
    driver.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
schemas = json.loads(os.environ["TEST_SCHEMAS"])
with Path(os.environ["TEST_EVENTS"]).open("a") as log:
    log.write(json.dumps({"command": name, "args": args}) + "\\n")
if name == "psql":
    command = args[args.index("--command") + 1] if "--command" in args else sys.stdin.read()
    with Path(os.environ["TEST_EVENTS"]).open("a") as log:
        log.write(json.dumps({"sql": command}) + "\\n")
    if "current_user" in command:
        print("investment_studio|investment_studio")
    elif "pg_stat_activity" in command and "count(*)" in command:
        print(0)
    elif "pg_namespace" in command:
        print(len(schemas) if "count(*)" in command else "\\n".join(sorted(schemas)))
elif name == "pg_dump":
    Path(args[args.index("--file") + 1]).write_bytes(b"test database backup")
elif name == "pg_restore":
    if "--list" in args:
        for index, schema in enumerate(schemas):
            print(f"{index + 1}; 0 0 SCHEMA - {schema} owner")
    else:
        print("SELECT 1;")
elif name == "systemctl" and "is-active" in args:
    raise SystemExit(0 if args[-1] in {"investment-studio-briefing-api.service", "investment-studio-briefing-daily.timer", "investment-studio-market-daily.timer", "investment-studio-market-sync.service"} else 3)
elif name == "migration":
    target = os.environ["INVESTMENT_STUDIO_DATA_DATABASE_URL"]
    for name in ("INVESTMENT_STUDIO_HOME_DATABASE_URL", "INVESTMENT_STUDIO_MARKET_DATABASE_URL", "INVESTMENT_STUDIO_BRIEFING_DATABASE_URL", "INVESTMENT_STUDIO_BRIEFING_ALEMBIC_DATABASE_URL"):
        assert os.environ[name] == target
''')
    driver.chmod(0o700)
    for name in ("psql", "pg_dump", "pg_restore", "systemctl", "migration"):
        (binary / name).symlink_to(driver)
    dump = tmp_path / "incoming.pgdump"
    dump.write_bytes(b"test incoming archive")
    dump.with_suffix(".sha256").write_text(f"{hashlib.sha256(dump.read_bytes()).hexdigest()}  {dump.name}\n")
    env = dict(os.environ, PATH=f"{binary}:{os.environ['PATH']}", PYTHON_BIN=sys.executable,
               TEST_EVENTS=str(events), TEST_SCHEMAS=json.dumps(SCHEMAS),
               INVESTMENT_STUDIO_LOCAL_DATABASE_URL="postgresql://investment_studio@127.0.0.1:5432/investment_studio",
               INVESTMENT_STUDIO_RESTORE_SERVICE_MANAGER="systemd", CONFIRM_RESTORE="investment_studio",
               INVESTMENT_STUDIO_RESTORE_MIGRATION_RUNNER=str(binary / "migration"),
               INVESTMENT_STUDIO_RESTORE_BACKUP_DIR=str(tmp_path / "backups"))
    result = subprocess.run([str(ROOT / "infra/postgres/restore_project_dump.sh"), str(dump)], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(line) for line in events.read_text().splitlines()]
    restore = next(row for row in records if row.get("command") == "pg_restore" and str(dump) in row["args"] and "--list" not in row["args"])
    sql = "\n".join(row.get("sql", "") for row in records)
    for schema in SCHEMAS:
        assert f"--schema={schema}" in restore["args"]
        assert f"DROP SCHEMA IF EXISTS {schema} CASCADE" in sql
    restarts = [row for row in records if row.get("command") == "systemctl" and "start" in row["args"]]
    assert len(restarts) == 1
    assert restarts[0]["args"] == ["--user", "start", "investment-studio-briefing-api.service", "investment-studio-briefing-daily.timer", "investment-studio-market-daily.timer", "investment-studio-market-sync.service"]
    assert any(row.get("command") == "migration" for row in records)
