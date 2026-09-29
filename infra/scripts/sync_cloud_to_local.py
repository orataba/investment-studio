#!/usr/bin/env python3
"""Publish a checked cloud snapshot locally, retaining the previous database/files.

The cloud is read only throughout. Downloads and restore run beside the local
services; only the final database/file switch needs a local maintenance window.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

import psycopg
from psycopg import sql
from psycopg.types.json import Json

from audit_live_data import FINAL_FLAT_TABLE_HEADS, VERSION_TABLES

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = tuple(FINAL_FLAT_TABLE_HEADS) + ("market_text",)
PRIVATE_CREDENTIAL_TABLES = ("sessions", "service_credentials", "delegations", "one_time_tokens")
LABEL = "com.orataba.investment-studio.cloud-sync"


def run(args, **kwargs):
    return subprocess.run([str(value) for value in args], check=True, **kwargs)


def postgres_tool(name):
    found = shutil.which(name)
    if found:
        return found
    for version in ("18", "17", "16"):
        path = Path(f"/opt/homebrew/opt/postgresql@{version}/bin/{name}")
        if path.is_file():
            return str(path)
    raise ValueError(f"PostgreSQL tool unavailable: {name}")


def database_url(value, database=None):
    parsed = urlsplit(value.replace("postgresql+psycopg://", "postgresql://", 1))
    if parsed.scheme != "postgresql" or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use an explicit password-free PostgreSQL URL and .pgpass")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.username:
        raise ValueError("Cloud sync only replaces a loopback local database")
    if not parsed.path.strip("/") or "/" in parsed.path.strip("/"):
        raise ValueError("A database name is required")
    return urlunsplit(parsed._replace(path="/" + database)) if database else urlunsplit(parsed)


def load_config(path):
    path = path.expanduser().resolve()
    if path.is_relative_to(ROOT) or path.stat().st_mode & 0o077:
        raise ValueError("Sync configuration must be a private 0600 file outside the repository")
    config = json.loads(path.read_text())
    config["database_url"] = database_url(config["database_url"])
    config["admin_url"] = database_url(config["admin_url"])
    if urlsplit(config["database_url"]).netloc.split("@")[-1] != urlsplit(config["admin_url"]).netloc.split("@")[-1]:
        raise ValueError("Database and administrator must address the same local server")
    if urlsplit(config["database_url"]).path == urlsplit(config["admin_url"]).path:
        raise ValueError("Administrator must connect to a separate maintenance database")
    if len(urlsplit(config["database_url"]).path.encode()) > 30:
        raise ValueError("Local database name is too long for retained snapshot names")
    host = config["ssh_host"]
    if not host or host.startswith("-") or any(char.isspace() for char in host):
        raise ValueError("Invalid SSH host")
    if set(config["files"]) != {"market", "documents", "research", "evidence"}:
        raise ValueError("Configure market, documents, research and evidence file roots")
    for item in config["files"].values():
        for key in ("remote", "local"):
            value = Path(item[key]).expanduser()
            if not value.is_absolute() or value == Path("/") or value.is_relative_to(ROOT):
                raise ValueError("Data roots must be absolute and outside the repository")
            item[key] = str(value)
    local_roots = [Path(item["local"]).resolve() for item in config["files"].values()]
    state_root = Path(config["state_root"]).expanduser().resolve()
    for index, root in enumerate(local_roots):
        if state_root.is_relative_to(root) or root.is_relative_to(state_root):
            raise ValueError("Staging and published data roots must be separate")
        if any(root.is_relative_to(other) or other.is_relative_to(root) for other in local_roots[index + 1:]):
            raise ValueError("Published data roots must not overlap")
    return config


def ssh(config, command, **kwargs):
    return run(["ssh", "-oBatchMode=yes", "-oConnectTimeout=15", config["ssh_host"], command], **kwargs)


def remote_snapshot(config, directory):
    """pg_dump holds a single MVCC snapshot without stopping cloud writers."""
    dump = directory / "cloud.pgdump"
    command = ["runuser", "-u", config["remote_user"], "--", "pg_dump",
               "--format=custom", "--no-owner", "--no-acl", "--host=127.0.0.1",
               f"--port={int(config['remote_port'])}", f"--username={config['remote_database_user']}",
               f"--dbname={config['remote_database']}"]
    command += [f"--schema={name}" for name in SCHEMAS]
    command += [f"--exclude-table-data=identity.{name}" for name in PRIVATE_CREDENTIAL_TABLES]
    print("Downloading a consistent cloud database snapshot", flush=True)
    with dump.open("wb") as output:
        ssh(config, shlex.join(command), stdout=output)
    run([postgres_tool("pg_restore"), "--list", dump], stdout=subprocess.DEVNULL)
    with dump.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (directory / "cloud.sha256").write_text(digest + "\n")
    return dump


def copy_files(config, directory):
    for name, item in config["files"].items():
        target = directory / name
        # macOS copy-on-write clones keep independent inodes even for mutable
        # indexes; rsync can reuse the existing bytes without shared hard links.
        if name == "market" and Path(item["local"]).is_dir():
            run(["/bin/cp", "-cpR", item["local"], target])
        else:
            target.mkdir()
        args = ["rsync", "-a", "--delete", "--delete-excluded", "--partial",
                "--exclude=*.lock", "--exclude=.DS_Store"]
        args += ["-e", "ssh -oBatchMode=yes -oConnectTimeout=15",
                 config["ssh_host"] + ":" + shlex.quote(item["remote"].rstrip("/") + "/"), str(target) + "/"]
        print(f"Downloading {name} files", flush=True)
        run(args)


def verify_heads(connection):
    for schema, expected in FINAL_FLAT_TABLE_HEADS.items():
        rows = connection.execute(sql.SQL("SELECT version_num FROM {}.{}").format(
            sql.Identifier(schema), sql.Identifier(VERSION_TABLES[schema]))).fetchall()
        if rows != [(expected,)]:
            raise ValueError(f"Cloud {schema} migration differs from this checkout; update code before syncing")


def checked_file(root, relative):
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError("Snapshot contains an invalid relative file reference")
    result = root / value
    if not result.is_file() or not result.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Snapshot file is missing: {relative}")
    return result


def verify_files(connection, directory):
    # Published file indexes are authoritative, not the size of the transfer.
    for path, size in connection.execute("SELECT path, bytes FROM market_data.files"):
        target = checked_file(directory / "market", path)
        if target.stat().st_size != size:
            raise ValueError(f"Snapshot file size differs: {path}")
    for (artifacts,) in connection.execute("SELECT artifacts_json FROM portfolio.research_run_record WHERE artifacts_json IS NOT NULL"):
        for artifact in artifacts:
            checked_file(directory / "research", artifact["path"])
    for (digest,) in connection.execute("SELECT DISTINCT raw_sha256 FROM market_text.document_version"):
        target = checked_file(directory / "market", f"text/objects/{digest[:2]}/{digest}")
        with target.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError("A retained source document failed its content hash check")
    verify_document_files(connection, directory / "documents")


def verify_document_files(connection, root):
    # These mirror the two Watchlist download routes. Text-only notes and
    # external catalogue links do not claim to have an uploaded local file.
    for entry_id, topic_id, context in connection.execute("""
        SELECT entry_id, topic_id, context_json FROM watchlist.research_entry
        WHERE kind='evidence'"""):
        if (context or {}).get("file_name"):
            name = Path(context["file_name"]).name
            checked_file(root, str(Path("research") / topic_id / f"{entry_id}-{name}"))
    for instrument_id, payload in connection.execute("""
        SELECT instrument_id, documents_payload_json FROM watchlist.instrument_manual_profile"""):
        folder = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(instrument_id).name.strip()).strip("._") or "instrument"
        for document in (payload or {}).get("current_documents", []):
            stored = document.get("stored_file_name")
            if not stored:
                continue
            normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(stored).name.strip()).strip("._")
            if stored != normalized:
                raise ValueError("Snapshot contains an invalid uploaded document name")
            target = checked_file(root, str(Path(folder) / stored))
            if document.get("file_size") is not None and target.stat().st_size != document["file_size"]:
                raise ValueError(f"Uploaded document size differs: {stored}")


def local_credentials(connection):
    cursor = connection.execute("SELECT * FROM identity.service_credentials")
    return [column.name for column in cursor.description], cursor.fetchall()


def copy_credentials(connection, credentials):
    columns, rows = credentials
    if rows:
        with connection.cursor() as cursor:
            cursor.executemany(sql.SQL("INSERT INTO identity.service_credentials ({}) VALUES ({})").format(
                sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.SQL(",").join(sql.Placeholder() for _ in columns)),
                [tuple(Json(value) if isinstance(value, (dict, list)) else value for value in row) for row in rows])


def refresh_local_credentials(config, stage_name):
    """Preserve rotations and revocations made while the snapshot was staged."""
    with psycopg.connect(config["database_url"]) as original:
        credentials = local_credentials(original)
    with psycopg.connect(database_url(config["admin_url"], stage_name)) as staged:
        staged.execute("DELETE FROM identity.service_credentials")
        copy_credentials(staged, credentials)


def reset_copied_jobs(connection):
    """A process running in the cloud cannot continue in the local snapshot."""
    message = "云端快照中的运行未在本机执行，请在本机重新发起。"
    stamp = datetime.now(timezone.utc).isoformat()
    connection.execute("""UPDATE portfolio.portfolio_calculation_state
        SET daily_snapshot_status='stale',refresh_request_id=NULL,refresh_started_at=NULL,
            refresh_completed_at=NULL,error_message=NULL WHERE daily_snapshot_status='running'""")
    connection.execute("""UPDATE portfolio.research_run_record SET status='failed',finished_at=%s,error_message=%s
        WHERE status='running'""", (stamp, message))
    connection.execute("""UPDATE portfolio.transaction_capture_batch SET analysis_run_status='failed',
        analysis_run_completed_at=%s,analysis_run_error=%s WHERE analysis_run_status IN ('queued','running')""", (stamp, message))
    # Watchlist and Briefing recover copied in-flight analysis during startup.
    connection.execute("""UPDATE watchlist.recalc_job SET job_status='queued',started_at=NULL,
        heartbeat_at=NULL,lease_token=NULL WHERE job_status='running'""")


def local_grants(connection):
    rows = connection.execute("""
        SELECT 'SCHEMA', n.nspname, NULL, r.rolname, a.privilege_type, a.is_grantable
        FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a JOIN pg_roles r ON r.oid=a.grantee
        WHERE n.nspname=ANY(%s) AND a.grantee<>n.nspowner
        UNION ALL
        SELECT CASE WHEN c.relkind='S' THEN 'SEQUENCE' ELSE 'TABLE' END, n.nspname, c.relname,
               r.rolname,a.privilege_type,a.is_grantable
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
        WHERE n.nspname=ANY(%s) AND a.grantee<>c.relowner AND c.relkind IN ('r','p','v','m','S')
        """, (list(SCHEMAS), list(SCHEMAS))).fetchall()
    return rows


def local_default_grants(connection):
    return connection.execute("""SELECT owner.rolname,n.nspname,d.defaclobjtype,
        grantee.rolname,a.privilege_type,a.is_grantable
        FROM pg_default_acl d JOIN pg_roles owner ON owner.oid=d.defaclrole
        JOIN pg_namespace n ON n.oid=d.defaclnamespace
        CROSS JOIN LATERAL aclexplode(d.defaclacl) a JOIN pg_roles grantee ON grantee.oid=a.grantee
        WHERE n.nspname=ANY(%s) AND a.grantee<>d.defaclrole""", (list(SCHEMAS),)).fetchall()


def apply_default_grants(connection, grants):
    kinds = {"r": "TABLES", "S": "SEQUENCES", "f": "FUNCTIONS", "T": "TYPES"}
    for owner, schema, kind, role, privilege, grantable in grants:
        connection.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT {} ON {} TO {}{}").format(
            sql.Identifier(owner), sql.Identifier(schema), sql.SQL(privilege), sql.SQL(kinds[kind]),
            sql.Identifier(role), sql.SQL(" WITH GRANT OPTION" if grantable else "")))


def apply_grants(connection, grants):
    for kind, schema, table, role, privilege, grantable in grants:
        if table and not connection.execute("SELECT to_regclass(%s)", (f'"{schema}"."{table}"',)).fetchone()[0]:
            continue
        target = sql.Identifier(schema, table) if table else sql.Identifier(schema)
        connection.execute(sql.SQL("GRANT {} ON {} {} TO {}{}").format(
            sql.SQL(privilege), sql.SQL(kind), target, sql.Identifier(role),
            sql.SQL(" WITH GRANT OPTION" if grantable else "")))


def restore_stage(config, stage_name, dump, directory):
    target = database_url(config["admin_url"], stage_name)
    owner = urlsplit(config["database_url"]).username
    with psycopg.connect(config["admin_url"], autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0").format(sql.Identifier(stage_name), sql.Identifier(owner)))
    print("Restoring the isolated local snapshot", flush=True)
    run([postgres_tool("pg_restore"), "--exit-on-error", "--no-owner", "--no-acl",
         "--jobs=2", "--role=" + owner, "--dbname=" + target, dump])
    with psycopg.connect(config["database_url"]) as original:
        credentials, grants = local_credentials(original), local_grants(original)
        defaults = local_default_grants(original)
    with psycopg.connect(target) as staged:
        verify_heads(staged)
        verify_files(staged, directory)
        copy_credentials(staged, credentials)
        apply_grants(staged, grants)
        apply_default_grants(staged, defaults)
        reset_copied_jobs(staged)
    return target


def control_services(action, state):
    run([ROOT / "infra/launchd/control_local_services.sh", action, state],
        env={**os.environ, "INVESTMENT_STUDIO_CONTROL_SKIP_CLOUD_SYNC": "true"})


def allow_connections(admin, name, allow):
    admin.execute(sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS {}").format(sql.Identifier(name), sql.SQL("true" if allow else "false")))


def rename_database(admin, old, new):
    admin.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(old), sql.Identifier(new)))


def healthcheck(config):
    deadline = time.monotonic() + 90
    last_error = None
    while time.monotonic() < deadline:
        try:
            with urlopen(config["local_identity_url"], timeout=5) as response:
                value = json.load(response)
            if not value.get("local_unrestricted"):
                raise ValueError("Local full-access identity is not enabled")
            for url in config["health_urls"]:
                with urlopen(url, timeout=5) as response:
                    if response.status != 200:
                        raise ValueError("Local application is not ready")
            return
        except Exception as error:
            last_error = error
            time.sleep(2)
    raise RuntimeError("Local applications did not recover after snapshot switch") from last_error


def write_cutover_journal(path, record, phase, **changes):
    record.update(changes, phase=phase, updated_at=datetime.now(timezone.utc).isoformat())
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(record, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def check_unfinished_cutovers(state):
    for path in sorted(state.glob("*/cutover.json")):
        try:
            phase = json.loads(path.read_text())["phase"]
        except (ValueError, KeyError):
            phase = "unreadable"
        if phase not in {"published", "rolled_back"}:
            raise RuntimeError(f"An unfinished local snapshot switch requires recovery before another sync: {path} (phase: {phase})")


def publish(config, directory, stage_name):
    check_unfinished_cutovers(directory.parent)
    original = urlsplit(config["database_url"]).path.strip("/")
    backup = original + "_before_" + directory.name
    service_state = directory / "services.txt"
    journal = directory / "cutover.json"
    record = {"database": original, "staged_database": stage_name, "backup_database": backup,
              "service_state": str(service_state), "data_roots": {
                  name: {"active": item["local"], "staged": str(directory / name),
                         "backup": str(directory / (name + "-before"))}
                  for name, item in config["files"].items()}}
    # Persist intent before the first service or database change. An interrupted
    # process leaves this journal pending; the next run must not overwrite it.
    write_cutover_journal(journal, record, "stopping_services")
    moved = []
    database_moved = False
    print("Switching local services to the verified snapshot", flush=True)
    try:
        control_services("stop", service_state)
        refresh_local_credentials(config, stage_name)
        write_cutover_journal(journal, record, "switching_database")
        with psycopg.connect(config["admin_url"], autocommit=True) as admin:
            busy = admin.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=%s AND backend_type='client backend' AND state<>'idle'", (original,)).fetchone()[0]
            if busy:
                raise RuntimeError("An independent local database writer is active; retry after it finishes")
            allow_connections(admin, original, False)
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", (original,))
            rename_database(admin, original, backup)
            database_moved = True
            rename_database(admin, stage_name, original)
            allow_connections(admin, original, True)
        write_cutover_journal(journal, record, "switching_files")
        for name, item in config["files"].items():
            destination = Path(item["local"])
            previous = directory / (name + "-before")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination.rename(previous)
            moved.append((destination, previous, directory / name))
            (directory / name).rename(destination)
        write_cutover_journal(journal, record, "starting_services")
        control_services("start", service_state)
        healthcheck(config)
        write_cutover_journal(journal, record, "published")
    except BaseException as failure:
        recovery_errors = []
        data_restored = True
        try:
            write_cutover_journal(journal, record, "rolling_back", failure=str(failure))
        except Exception as error:
            recovery_errors.append(f"Could not update recovery journal: {error}")
        if database_moved:
            try:
                control_services("stop", directory / "failed-services.txt")
            except Exception as error:
                # A partial stop must not prevent restoring the original data.
                # Database connections are explicitly disabled and terminated.
                recovery_errors.append(f"Could not stop every replacement service: {error}")
        try:
            with psycopg.connect(config["admin_url"], autocommit=True) as admin:
                if database_moved:
                    names = {row[0] for row in admin.execute("SELECT datname FROM pg_database")}
                    if original in names:
                        allow_connections(admin, original, False)
                        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", (original,))
                        rename_database(admin, original, stage_name)
                    rename_database(admin, backup, original)
                allow_connections(admin, original, True)
        except Exception as error:
            data_restored = False
            recovery_errors.append(f"Could not restore the original database: {error}")
        for destination, previous, staged in reversed(moved):
            try:
                if destination.exists():
                    destination.rename(staged)
                if previous.exists():
                    previous.rename(destination)
            except Exception as error:
                data_restored = False
                recovery_errors.append(f"Could not restore {destination}: {error}")
        restored = data_restored
        # Serving mixed databases and file roots would corrupt subsequent work.
        # Attempt every original service only after its original data is back.
        if data_restored:
            try:
                control_services("start", service_state)
            except Exception as error:
                restored = False
                recovery_errors.append(f"Could not restore every original service: {error}")
        write_cutover_journal(journal, record, "rolled_back" if restored else "recovery_failed",
                              recovery_errors=recovery_errors)
        if recovery_errors:
            raise RuntimeError(f"Snapshot switch failed: {failure}. Recovery details: {'; '.join(recovery_errors)}. Journal: {journal}") from failure
        raise


def install_schedule(config_path, state_root, *, activate=True, output_dir=None, label=LABEL):
    logs = state_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = (output_dir or Path.home() / "Library/LaunchAgents") / (label + ".plist")
    path.parent.mkdir(parents=True, exist_ok=True)
    definition = {"Label": label,
                  "ProgramArguments": [sys.executable, str(Path(__file__).resolve()), "--config", str(config_path), "--if-due"],
                  "StartCalendarInterval": {"Weekday": 0, "Hour": 9, "Minute": 0},
                  "StartInterval": 86400, "RunAtLoad": True, "ProcessType": "Background",
                  "StandardOutPath": str(logs / "cloud-sync.log"),
                  "StandardErrorPath": str(logs / "cloud-sync.error.log")}
    path.write_bytes(plistlib.dumps(definition))
    path.chmod(0o600)
    if not activate:
        return
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run(["launchctl", "bootstrap", f"gui/{os.getuid()}", path])
    print("Weekly cloud sync installed; missed runs are checked after login/wake")


def prune_published_snapshots(config, state, keep=2):
    """Retain two accepted recovery points; never remove an unfinished switch."""
    published = []
    for journal in sorted(state.glob("*/cutover.json"), reverse=True):
        record = json.loads(journal.read_text())
        if record["phase"] == "published":
            published.append((journal.parent, record))
    original = urlsplit(config["database_url"]).path.strip("/")
    for directory, record in published[keep:]:
        expected = original + "_before_" + directory.name
        if record["backup_database"] != expected:
            raise ValueError("Recovery database does not match its snapshot directory")
        with psycopg.connect(config["admin_url"], autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(expected)))
        shutil.rmtree(directory)


def weekly_sync_due(previous, now):
    """Sunday 09:00 in the Mac's local timezone, including missed-run catch-up."""
    cutoff = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(
        hour=9, minute=0, second=0, microsecond=0)
    if cutoff > now:
        cutoff -= timedelta(days=7)
    return previous < cutoff


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--if-due", action="store_true")
    parser.add_argument("--install-schedule", action="store_true")
    parser.add_argument("--write-schedule", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--label-prefix", default="com.orataba.investment-studio")
    args = parser.parse_args(argv)
    os.umask(0o077)
    config = load_config(args.config)
    state = Path(config["state_root"]).expanduser()
    state.mkdir(parents=True, exist_ok=True)
    if args.install_schedule or args.write_schedule:
        install_schedule(args.config.resolve(), state, activate=args.install_schedule, output_dir=args.output_dir,
                         label=args.label_prefix + ".cloud-sync")
        return 0
    with (state / "sync.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Cloud sync is already running")
            return 0
        check_unfinished_cutovers(state)
        receipt = state / "last-success.json"
        if args.if_due and receipt.exists():
            previous = datetime.fromisoformat(json.loads(receipt.read_text())["completed_at"])
            if not weekly_sync_due(previous, datetime.now().astimezone()):
                return 0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        directory = state / stamp
        directory.mkdir()
        stage_name = urlsplit(config["database_url"]).path.strip("/") + "_incoming_" + stamp
        dump = remote_snapshot(config, directory)
        copy_files(config, directory)
        restore_stage(config, stage_name, dump, directory)
        publish(config, directory, stage_name)
        receipt.write_text(json.dumps({"completed_at": datetime.now(timezone.utc).isoformat(), "snapshot": str(directory)}, indent=2))
        prune_published_snapshots(config, state)
        print("Cloud snapshot is active locally; previous database and files retained", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
