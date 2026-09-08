"""Only new public objects activate inherited named-reader ACLs."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

import pytest

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore
from studio_market.numeric.delivery import _atomic_json
from studio_market.numeric.raw import archive_response
from studio_market.numeric.replication import export_bundle, import_bundle
from studio_market.text import TextStore


def _create_public_objects(base, identity):
    base = Path(base)
    settings = MarketSettings(f"sqlite:///{base / ('source-' + identity + '.sqlite')}", base / 'shared')
    source = NumericStore(settings)
    source.create_schema_for_testing()
    replica_settings = MarketSettings(f"sqlite:///{base / ('replica-' + identity + '.sqlite')}", base / 'replica')
    replica = NumericStore(replica_settings)
    replica.create_schema_for_testing()
    text_store = TextStore(database_url="sqlite://", data_root=settings.data_root)
    try:
        body = ('public numeric fixture ' + identity).encode()
        _, ref = archive_response(settings, 'fixture', body)
        assert gzip.decompress((settings.data_root / ref).read_bytes()) == body
        source.ingest('analyst_price_targets', [[{'symbol': identity, 'target': 100, 'raw_ref': ref}]], source='fixture')
        archive = base / ('bundle-' + identity + '.zip')
        export_bundle(settings, archive)
        import_bundle(replica_settings, archive)
        text_body = ('public text fixture ' + identity).encode()
        text_path = text_store._save_raw(hashlib.sha256(text_body).hexdigest(), text_body)
        # Include the normal 0666 Parquet/ZIP/status writers in the real ACL
        # read check, but change permissions only at the three mkstemp writers.
        status_path = base / ('status-' + identity + '.json')
        _atomic_json(status_path, {'status': 'ready'})
        objects = [settings.data_root / ref, text_path]
        with zipfile.ZipFile(archive) as exported:
            manifest = json.loads(exported.read('manifest.json'))
        objects += [replica_settings.data_root / item['path'] for item in manifest['objects']]
        normal_files = [archive, status_path, *settings.data_root.glob('numeric/analyst_price_targets/*/*.parquet')]
        def describe(path):
            return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        return {'objects': [describe(p) for p in objects], 'normal_files': [describe(p) for p in normal_files]}
    finally:
        text_store.close()
        replica.close()
        source.close()


def test_new_public_objects_are_0640_and_existing_objects_are_not_chmodded(tmp_path):
    result = _create_public_objects(tmp_path, 'owner')
    assert all(stat.S_IMODE(Path(item['path']).stat().st_mode) == 0o640 for item in result['objects'])
    settings = MarketSettings('sqlite://', tmp_path / 'shared')
    _, ref = archive_response(settings, 'fixture', b'public numeric fixture owner')
    raw = settings.data_root / ref
    raw.chmod(0o600)
    original_inode = raw.stat().st_ino
    assert archive_response(settings, 'fixture', b'public numeric fixture owner')[1] == ref
    assert raw.stat().st_ino == original_inode
    assert stat.S_IMODE(raw.stat().st_mode) == 0o600
    text_store = TextStore(database_url='sqlite://', data_root=settings.data_root)
    try:
        body = b'public text fixture owner'
        path = text_store._save_raw(hashlib.sha256(body).hexdigest(), body)
        path.chmod(0o600)
        original_inode = path.stat().st_ino
        assert text_store._save_raw(hashlib.sha256(body).hexdigest(), body) == path
        assert path.stat().st_ino == original_inode
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        text_store.close()


def test_linux_default_acl_allows_bidirectional_public_object_reads():
    if sys.platform != 'linux' or os.geteuid() != 0:
        pytest.skip('Real cross-UID ACL verification requires Linux root in an isolated temporary directory')
    if not shutil.which('setfacl') or not shutil.which('getfacl'):
        pytest.skip('POSIX ACL tools are required')
    # Import this test helper in subprocesses without changing any production
    # configuration or inheriting a database connection across a UID change.
    worker = r'''
import hashlib, importlib.util, json, os, pathlib, stat, sys, tempfile
spec = importlib.util.spec_from_file_location('public_permission_fixture', sys.argv[1])
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
import encodings.cp437, sqlite3, sqlalchemy.dialects.sqlite.pysqlite, pyarrow.dataset
# The pinned interpreter may live below the service owner's private home.
# Load executable code first, then permanently drop all root IDs before any
# fixture object or private control is created, read, or opened for writing.
uid = int(sys.argv[4])
os.setgroups([])
os.setgid(uid)
os.setuid(uid)
assert os.getuid() == os.geteuid() == uid
os.umask(0o077)
if sys.argv[2] == 'write':
    result = fixture._create_public_objects(sys.argv[3], str(os.getuid()))
    fd, private = tempfile.mkstemp(prefix='private-', dir=sys.argv[3])
    with os.fdopen(fd, 'wb') as output: output.write(b'private fixture remains private')
    result['private'] = private
    print(json.dumps(result))
else:
    result = json.loads(sys.argv[3])
    for item in result['objects'] + result['normal_files']:
        assert hashlib.sha256(pathlib.Path(item['path']).read_bytes()).hexdigest() == item['sha256']
    for item in result['objects']:
        assert stat.S_IMODE(pathlib.Path(item['path']).stat().st_mode) == 0o640
        try: fd = os.open(item['path'], os.O_WRONLY)
        except PermissionError: pass
        else:
            os.close(fd)
            raise AssertionError('A different writer must not modify a new immutable object')
    try: pathlib.Path(result['private']).read_bytes()
    except PermissionError: pass
    else: raise AssertionError('Private mkstemp permissions were broadened')
    print(json.dumps({'reader_uid': os.getuid(), 'readable_objects': len(result['objects']), 'normal_files': len(result['normal_files']), 'private_denied': True}))
'''
    def run_as(uid, action, value):
        completed = subprocess.run([sys.executable, '-c', worker, str(Path(__file__).resolve()), action, value, str(uid)],
            capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)

    with tempfile.TemporaryDirectory(prefix='studio-public-acl-', dir='/tmp') as temporary:
        # The default other entry matches the existing public root. New
        # mkstemp publications must nevertheless keep other permissions empty.
        subprocess.run(['setfacl', '-m',
            'u:1000:rwx,u:10001:rwx,m::rwx,o::---,d:u::rwx,d:u:1000:rwx,d:u:10001:rwx,d:g::---,d:m::rwx,d:o::r-x', temporary], check=True)
        for writer, reader in [(1000, 10001), (10001, 1000)]:
            created = run_as(writer, 'write', temporary)
            for item in created['objects']:
                acl = subprocess.run(['getfacl', '-cpn', item['path']], check=True, capture_output=True, text=True).stdout
                assert f'user:{reader}:rwx' in acl
                assert 'mask::r--' in acl and 'other::---' in acl
            result = run_as(reader, 'read', json.dumps(created))
            assert result['readable_objects'] >= 4 and result['private_denied']
            print(json.dumps({'writer_uid': writer, **result}, sort_keys=True))
