import importlib.util
import gzip
import hashlib
import json
from pathlib import Path
import plistlib
import sys
from types import SimpleNamespace

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


@pytest.fixture
def snapshot_transfer(tmp_path, monkeypatch):
    calls = []
    archive = gzip.compress(b'complete-cloud-database', mtime=0)
    digest = hashlib.sha256(archive).hexdigest()
    control = {'archive': archive, 'checksum': digest, 'failure': None}
    def ssh(config, command, **kwargs):
        calls.append(('ssh', command))
        if 'mktemp -d' in command:
            return SimpleNamespace(stdout='/srv/runtime/.local/state/investment-studio/cloud-sync/snapshot.aB12345678\n')
        if 'sha256sum' in command:
            if control['failure'] == 'dump':
                raise RuntimeError('remote dump failed')
            return SimpleNamespace(stdout=control['checksum'] + '  /remote/cloud.pgdump.gz\n')
        return SimpleNamespace(stdout='')
    def run(args, **kwargs):
        calls.append(('run', [str(value) for value in args]))
        if args[0] == '/bin/cp':
            sync.shutil.copyfile(args[-2], args[-1])
        if args[0] == '/test/rsync':
            destination = Path(args[-1])
            control['basis'] = destination.read_bytes() if destination.exists() else None
            if control['failure'] == 'transfer':
                raise RuntimeError('transfer failed')
            destination.write_bytes(control['archive'])
    monkeypatch.setattr(sync, "ssh", ssh)
    monkeypatch.setattr(sync, "run", run)
    monkeypatch.setattr(sync, "postgres_tool", lambda name: name)
    config = {"remote_user": "studio", "remote_port": 55433, 'ssh_host': 'cloud',
              "remote_database_user": "studio", "remote_database": "studio"}
    return config, calls, control


def test_download_preserves_complete_snapshot_and_excludes_runtime_credentials(tmp_path, snapshot_transfer):
    config, calls, control = snapshot_transfer
    dump = sync.remote_snapshot(config, tmp_path, '/test/rsync')
    setup = next(command for kind, command in calls if kind == 'ssh' and 'mktemp -d' in command)
    assert 'runuser -u studio -- bash -c' in setup
    assert '$HOME/.local/state/investment-studio/cloud-sync' in setup
    assert 'umask 077' in setup
    assert '/tmp/' not in setup and '/home/studio' not in setup
    command = next(command for kind, command in calls if kind == 'ssh' and 'sha256sum' in command)
    assert all("--exclude-table-data=identity." + table in command for table in sync.PRIVATE_CREDENTIAL_TABLES)
    assert all('--schema=' + name in command for name in sync.SCHEMAS)
    assert '--exclude-table-data=portfolio.' not in command
    assert '--compress=0' in command and 'gzip -n --rsyncable -1' in command
    assert command.index('gzip -n --rsyncable -1 </dev/null >/dev/null') < command.index('pg_dump')
    assert 'set -euo pipefail' in command and '.partial' in command and 'mv --' in command
    assert control['basis'] is None
    assert dump.read_bytes() == b'complete-cloud-database'
    assert (tmp_path / 'cloud.pgdump.gz.sha256').read_text().strip() == control['checksum']
    transfer = next(command for kind, command in calls if kind == 'run' and command[0] == '/test/rsync')
    assert '--no-whole-file' in transfer and '--stats' in transfer
    assert transfer[-2] == 'cloud:/srv/runtime/.local/state/investment-studio/cloud-sync/snapshot.aB12345678/cloud.pgdump.gz'
    assert any(kind == 'ssh' and 'rmdir --' in command for kind, command in calls)


@pytest.mark.parametrize('existing_stage', [False, True])
def test_snapshot_basis_is_replaced_by_verified_cloud_bytes(tmp_path, snapshot_transfer, existing_stage):
    config, calls, control = snapshot_transfer
    previous = tmp_path / 'previous'
    previous.mkdir()
    previous_archive = previous / 'cloud.pgdump.gz'
    previous_archive.write_bytes(b'previous-cloud-basis')
    (tmp_path / 'last-success.json').write_text(json.dumps({'snapshot': str(previous)}))
    current = tmp_path / 'current'
    current.mkdir()
    if existing_stage:
        (current / 'cloud.pgdump.gz').write_bytes(b'operator-local-basis')
    sync.remote_snapshot(config, current, '/test/rsync')
    assert control['basis'] == (b'operator-local-basis' if existing_stage else b'previous-cloud-basis')
    assert previous_archive.read_bytes() == b'previous-cloud-basis'
    assert (current / 'cloud.pgdump.gz').read_bytes() == control['archive']
    assert (current / 'cloud.pgdump').read_bytes() == b'complete-cloud-database'


@pytest.mark.parametrize('existing_stage', [False, True])
def test_snapshot_rejects_symlinked_archive_or_basis_before_remote_changes(tmp_path, snapshot_transfer, existing_stage):
    config, calls, control = snapshot_transfer
    original = tmp_path / 'original'
    original.write_bytes(b'keep')
    current = tmp_path / 'current'
    current.mkdir()
    if existing_stage:
        (current / 'cloud.pgdump.gz').symlink_to(original)
    else:
        previous = tmp_path / 'previous'
        previous.mkdir()
        (previous / 'cloud.pgdump.gz').symlink_to(original)
        (tmp_path / 'last-success.json').write_text(json.dumps({'snapshot': str(previous)}))
    with pytest.raises(ValueError, match='symlink'):
        sync.remote_snapshot(config, current, '/test/rsync')
    assert not calls
    assert original.read_bytes() == b'keep'


@pytest.mark.parametrize('failure', ['dump', 'transfer', 'checksum'])
def test_snapshot_failure_cleans_remote_stage_and_never_expands_unverified_data(tmp_path, snapshot_transfer, failure):
    config, calls, control = snapshot_transfer
    if failure == 'checksum':
        control['checksum'] = '0' * 64
    else:
        control['failure'] = failure
    with pytest.raises((RuntimeError, ValueError)):
        sync.remote_snapshot(config, tmp_path, '/test/rsync')
    assert not (tmp_path / 'cloud.pgdump').exists()
    assert not (tmp_path / 'cloud.pgdump.gz.sha256').exists()
    assert calls[-1][0] == 'ssh' and 'rmdir --' in calls[-1][1]


def test_file_sync_clones_market_before_exact_cloud_reconciliation(tmp_path, monkeypatch):
    original = tmp_path / 'active'
    original.mkdir()
    calls = []
    def run(args, **kwargs):
        calls.append([str(value) for value in args])
        if str(args[0]) == '/bin/cp':
            Path(args[-1]).mkdir(parents=True)
    monkeypatch.setattr(sync, 'run', run)
    sync.copy_files({'ssh_host': 'cloud', 'files': {'market': {
        'local': str(original), 'remote': '/market'}}}, tmp_path / 'snapshot', '/opt/homebrew/bin/rsync')
    assert calls[0] == ['/bin/cp', '-cpR', str(original), str(tmp_path / 'snapshot/market')]
    assert calls[1][0] == '/opt/homebrew/bin/rsync'
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


@pytest.mark.parametrize('path', ['numeric/us_eod_daily/batch/part.parquet',
                                 'numeric/outbox/unexpected-reference.zip'])
def test_market_preflight_never_ignores_missing_catalogue_files(tmp_path, path):
    class Connection:
        def execute(self, query):
            return [(path, 4)] if 'market_data.files' in query else []

    target = tmp_path / 'market' / path
    target.parent.mkdir(parents=True)
    target.write_bytes(b'data')
    sync.verify_files(Connection(), tmp_path)
    target.unlink()
    with pytest.raises(ValueError, match='Snapshot file is missing'):
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


@pytest.mark.parametrize('version', [
    'openrsync: protocol version 29\nrsync version 2.6.9 compatible\n',
    'rsync  version 2.6.9  protocol version 29\n',
])
def test_cloud_sync_rejects_apple_openrsync_and_old_versions(monkeypatch, version):
    from types import SimpleNamespace
    monkeypatch.setattr(sync.shutil, 'which', lambda *args, **kwargs: '/usr/bin/rsync')
    monkeypatch.setattr(sync, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=version))
    with pytest.raises(ValueError, match="requires rsync 3.*brew install rsync"):
        sync.rsync_tool()


def test_cloud_sync_finds_homebrew_rsync_under_launchd_path(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('PATH', '/usr/bin:/bin:/usr/sbin:/sbin')
    search = []
    def which(name, *, path):
        search.append((name, path))
        return '/opt/homebrew/bin/rsync'
    monkeypatch.setattr(sync.shutil, 'which', which)
    monkeypatch.setattr(sync, 'run', lambda *args, **kwargs: SimpleNamespace(stdout='rsync  version 3.4.3  protocol version 32\n'))
    assert sync.rsync_tool() == '/opt/homebrew/bin/rsync'
    assert search == [('rsync', '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin')]


def test_incremental_file_sync_reuses_staged_roots_and_rejects_symlink_before_transfer(tmp_path, monkeypatch):
    stage = tmp_path / 'snapshot'
    stage.mkdir()
    config = {'ssh_host': 'cloud', 'files': {name: {'local': str(tmp_path / (name + '-live')),
        'remote': '/cloud/' + name} for name in ('market', 'documents', 'research', 'evidence')}}
    for name in config['files']:
        (stage / name).mkdir()
        (stage / name / 'prepared').write_text('retained')
    calls = []
    monkeypatch.setattr(sync, 'run', lambda args, **kwargs: calls.append(args))
    sync.sync_files(config, stage, '/opt/homebrew/bin/rsync')
    assert len(calls) == 4 and all(call[0] == '/opt/homebrew/bin/rsync' for call in calls)
    assert '--exclude=/numeric/outbox/' in calls[0]
    assert all('--exclude=/numeric/outbox/' not in call for call in calls[1:])
    assert all('--delete-excluded' in call for call in calls)
    assert all((stage / name / 'prepared').read_text() == 'retained' for name in config['files'])
    (stage / 'evidence/prepared').unlink()
    (stage / 'evidence').rmdir()
    (stage / 'evidence').symlink_to(tmp_path)
    calls.clear()
    with pytest.raises(ValueError, match='unsafe: evidence'):
        sync.sync_files(config, stage, '/opt/homebrew/bin/rsync')
    assert not calls


def test_unsupported_rsync_fails_before_database_download_or_file_clone(tmp_path, monkeypatch):
    config = {'state_root': str(tmp_path)}
    monkeypatch.setattr(sync, 'load_config', lambda path: config)
    monkeypatch.setattr(sync, 'rsync_tool', lambda: (_ for _ in ()).throw(ValueError('requires rsync 3')))
    monkeypatch.setattr(sync, 'remote_snapshot', lambda *args: pytest.fail('must not download'))
    monkeypatch.setattr(sync, 'copy_files', lambda *args: pytest.fail('must not clone'))
    with pytest.raises(ValueError, match='requires rsync 3'):
        sync.main(['--config', '/unused'])
    assert not list(tmp_path.glob('20*'))
