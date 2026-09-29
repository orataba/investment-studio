import importlib.util
import hashlib
import json
from pathlib import Path
import plistlib
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import sync_cloud_to_local as sync


@pytest.mark.parametrize("url", [
    "postgresql://user:password@127.0.0.1/db",
    "postgresql://user@production/db",
    "postgresql://user@127.0.0.1/db?host=production",
    "postgresql://user@127.0.0.1/",
])
def test_sync_cannot_replace_remote_or_implicitly_addressed_databases(url):
    with pytest.raises(ValueError):
        sync.database_url(url)


def test_file_preflight_rejects_missing_escaped_and_symlinked_evidence(tmp_path):
    root = tmp_path / "objects"
    root.mkdir()
    (root / "present").write_text("evidence")
    (tmp_path / "outside").write_text("private")
    (root / "link").symlink_to(tmp_path / "outside")
    assert sync.checked_file(root, "present").read_text() == "evidence"
    for value in ("missing", "../outside", str(tmp_path / "outside"), "link"):
        with pytest.raises(ValueError):
            sync.checked_file(root, value)


def test_weekly_schedule_only_checks_due_snapshots_without_activating_during_install(tmp_path, monkeypatch):
    execute = []
    monkeypatch.setattr(sync, "run", lambda *args, **kwargs: execute.append(args))
    sync.install_schedule(tmp_path / "config.json", tmp_path / "state", activate=False, output_dir=tmp_path)
    value = plistlib.loads((tmp_path / (sync.LABEL + ".plist")).read_bytes())
    assert value["StartCalendarInterval"] == {"Weekday": 0, "Hour": 9, "Minute": 0}
    assert "--if-due" in value["ProgramArguments"]
    assert value["RunAtLoad"] is True
    assert not execute


@pytest.mark.parametrize('previous,now,due', [
    ('2026-09-29T18:00:00+08:00', '2026-10-04T08:59:59+08:00', False),
    ('2026-09-29T18:00:00+08:00', '2026-10-04T09:00:00+08:00', True),
    ('2026-10-04T09:10:00+08:00', '2026-10-10T15:00:00+08:00', False),
    ('2026-10-04T09:10:00+08:00', '2026-10-12T08:00:00+08:00', True),
])
def test_weekly_catch_up_uses_sunday_boundary_instead_of_shifting_after_manual_sync(previous, now, due):
    assert sync.weekly_sync_due(sync.datetime.fromisoformat(previous), sync.datetime.fromisoformat(now)) is due


def test_download_excludes_cloud_runtime_credentials(tmp_path, monkeypatch):
    calls = []
    def ssh(config, command, **kwargs):
        calls.append(command)
        kwargs["stdout"].write(b"PGDMP-test")
    monkeypatch.setattr(sync, "ssh", ssh)
    monkeypatch.setattr(sync, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(sync, "postgres_tool", lambda name: name)
    sync.remote_snapshot({"remote_user": "studio", "remote_port": 55433,
                          "remote_database_user": "studio", "remote_database": "studio"}, tmp_path)
    assert all("--exclude-table-data=identity." + table in calls[0] for table in sync.PRIVATE_CREDENTIAL_TABLES)
    assert "--schema=portfolio" in calls[0]
    assert "--schema=watchlist" in calls[0]
    assert "--schema=identity" in calls[0]


def test_file_sync_clones_market_before_exact_cloud_reconciliation(tmp_path, monkeypatch):
    original = tmp_path / 'active'
    original.mkdir()
    calls = []
    monkeypatch.setattr(sync, 'run', lambda args, **kwargs: calls.append([str(value) for value in args]))
    sync.copy_files({'ssh_host': 'cloud', 'files': {'market': {
        'local': str(original), 'remote': '/market'}}}, tmp_path / 'snapshot')
    assert calls[0] == ['/bin/cp', '-cpR', str(original), str(tmp_path / 'snapshot/market')]
    assert '--delete' in calls[1] and '--delete-excluded' in calls[1]
    assert '--inplace' not in calls[1]
    assert not any(value.startswith('--link-dest') for value in calls[1])


class DocumentConnection:
    def execute(self, query):
        if 'research_entry' in query:
            return [('upload', 'private-topic', {'file_name': 'report.pdf'}),
                    ('text-only', 'private-topic', {'source': 'https://example.test'})]
        return [('fund/id', {'current_documents': [
            {'stored_file_name': 'file-report.pdf', 'file_size': 4},
            {'title': 'External report', 'source': 'https://example.test'}]})]


def test_document_preflight_checks_research_uploads_and_fund_files(tmp_path):
    research = tmp_path / 'research/private-topic/upload-report.pdf'
    research.parent.mkdir(parents=True)
    research.write_bytes(b'original')
    fund = tmp_path / 'id/file-report.pdf'
    fund.parent.mkdir()
    fund.write_bytes(b'fund')
    sync.verify_document_files(DocumentConnection(), tmp_path)
    research.unlink()
    with pytest.raises(ValueError, match='missing'):
        sync.verify_document_files(DocumentConnection(), tmp_path)
    research.write_bytes(b'original')
    fund.write_bytes(b'truncated')
    with pytest.raises(ValueError, match='size differs'):
        sync.verify_document_files(DocumentConnection(), tmp_path)
    fund.unlink()
    with pytest.raises(ValueError, match='missing'):
        sync.verify_document_files(DocumentConnection(), tmp_path)


def test_market_preflight_resolves_text_objects_under_text_root_and_checks_content(tmp_path):
    content = b'original retained source'
    digest = hashlib.sha256(content).hexdigest()
    target = tmp_path / 'market/text/objects' / digest[:2] / digest
    target.parent.mkdir(parents=True)
    target.write_bytes(content)

    class Connection:
        def execute(self, query):
            return [(digest,)] if 'market_text.document_version' in query else []

    sync.verify_files(Connection(), tmp_path)
    target.write_bytes(b'truncated')
    with pytest.raises(ValueError, match='content hash'):
        sync.verify_files(Connection(), tmp_path)


def test_retention_only_removes_older_successful_snapshots(tmp_path, monkeypatch):
    dropped = []
    class Admin:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query): dropped.append(query.as_string())
    monkeypatch.setattr(sync.psycopg, 'connect', lambda *args, **kwargs: Admin())
    for name, phase in [('001', 'published'), ('002', 'recovery_failed'),
                        ('003', 'rolled_back'), ('004', 'published'), ('005', 'published')]:
        directory = tmp_path / name
        directory.mkdir()
        (directory / 'cutover.json').write_text(json.dumps({
            'phase': phase, 'backup_database': 'studio_before_' + name}))
    sync.prune_published_snapshots({'database_url': 'postgresql://studio@127.0.0.1/studio',
                                   'admin_url': 'postgresql://admin@127.0.0.1/postgres'}, tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == ['002', '003', '004', '005']
    assert dropped == ['DROP DATABASE IF EXISTS "studio_before_001"']
