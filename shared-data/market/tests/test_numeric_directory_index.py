"""Exercise conditional wire reads without a network or a production store."""
import gzip
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from studio_market.numeric import directory_index as index
from studio_market.numeric.collect import failure_summary


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    remote = tmp_path / 'remote'
    incoming = tmp_path / 'incoming'
    temporary = tmp_path / 'temporary'
    for path in (remote, incoming, temporary):
        path.mkdir()
    document = {'format': 'investment-studio-numeric-index', 'version': 1,
                'bundles': [dict(name='capture.zip', sha256='a' * 64, bytes=10,
                                 batch_ids=[f'batch-{number}' for number in range(1000)])]}
    (remote / 'index.json').write_text(json.dumps(document, indent=2))
    original = subprocess.run
    calls = []
    def ssh(command, **kwargs):
        assert command[:2] == ['ssh', '-o']
        assert 'ConnectTimeout=10' in command
        assert kwargs['timeout'] == 300
        # Run the exact remote program, substituting only this fixture path.
        args = shlex.split(command[-1])
        assert args[0] == 'python3'
        args[0], args[3] = sys.executable, str(remote / 'index.json')
        calls.append(args)
        return original(args, **kwargs)
    monkeypatch.setattr(index.subprocess, 'run', ssh)
    def read():
        return index.read_index(host='trusted-host', remote_dir='/outbox', incoming=incoming,
                                temporary=temporary, timeout=3600)
    return SimpleNamespace(remote=remote, incoming=incoming, temporary=temporary,
                           document=document, read=read, calls=calls)


def test_unchanged_catalog_transfers_only_confirmation_and_changed_retirement_is_visible(fixture):
    value, first = fixture.read()
    assert value == fixture.document
    assert first['status'] == 'updated' and first['wire_bytes'] < first['bytes'] / 2
    value, same = fixture.read()
    assert value == fixture.document
    assert same['status'] == 'unchanged' and same['wire_bytes'] < 200
    assert same['sha256'] == first['sha256']
    # No new bundle is needed for retirement metadata to invalidate the cache.
    fixture.document['bundles'][0]['payload_state'] = 'retired'
    (fixture.remote / 'index.json').write_text(json.dumps(fixture.document))
    value, changed = fixture.read()
    assert changed['status'] == 'updated' and changed['sha256'] != first['sha256']
    assert value['bundles'][0]['payload_state'] == 'retired'
    assert value['bundles'][0]['batch_ids'] == fixture.document['bundles'][0]['batch_ids']


@pytest.mark.parametrize('corruption', ['truncated_gzip', 'wrong_checksum', 'oversize_body', 'malformed_index'])
def test_invalid_response_never_replaces_previous_catalog(fixture, monkeypatch, corruption):
    fixture.read()
    cache = next(fixture.incoming.glob('.index-*.json'))
    retained = cache.read_bytes()
    body = json.dumps(fixture.document).encode()
    if corruption == 'malformed_index':
        body = b'{"format":"unknown","version":1,"bundles":[]}'
    header = {'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body), 'changed': True}
    compressed = gzip.compress(body)
    if corruption == 'truncated_gzip': compressed = compressed[:-5]
    if corruption == 'wrong_checksum': header['sha256'] = 'b' * 64
    if corruption == 'oversize_body': header['bytes'] -= 1
    def ssh(command, **kwargs):
        kwargs['stdout'].write(json.dumps(header).encode() + b'\n' + compressed)
    monkeypatch.setattr(index.subprocess, 'run', ssh)
    with pytest.raises((ValueError, EOFError)):
        fixture.read()
    assert cache.read_bytes() == retained


@pytest.mark.parametrize('body_started', [False, True])
def test_timeout_preserves_safe_phase_and_never_uses_stale_cache(fixture, monkeypatch, body_started):
    fixture.read()
    def ssh(command, **kwargs):
        if body_started:
            kwargs['stdout'].write(b'{"bytes":100,"sha256":"not-needed-for-diagnostic"}\npartial')
            kwargs['stdout'].flush()
        raise subprocess.TimeoutExpired(['ssh', 'do-not-log-private-command'], 300,
                                        stderr=b'do-not-log-remote-output')
    monkeypatch.setattr(index.subprocess, 'run', ssh)
    with pytest.raises(index.NumericIndexTransferError) as failed:
        fixture.read()
    result = failure_summary(failed.value)
    assert result['phase'] == ('compressed_body' if body_started else 'connection_or_remote_header')
    assert result['timeout_seconds'] == 300
    assert (result['received_bytes'] > 0) == body_started
    assert 'do-not-log' not in json.dumps(result)


def test_same_hash_reply_rechecks_cached_bytes_before_reuse(fixture, monkeypatch):
    fixture.read()
    cache = next(fixture.incoming.glob('.index-*.json'))
    old = cache.read_bytes()
    def ssh(command, **kwargs):
        # Another reader can replace this cache while our request is in flight.
        cache.write_bytes(old.replace(b'capture.zip', b'changed.zip'))
        kwargs['stdout'].write(json.dumps(dict(sha256=hashlib.sha256(old).hexdigest(),
                                             bytes=len(old), changed=False)).encode() + b'\n')
    monkeypatch.setattr(index.subprocess, 'run', ssh)
    with pytest.raises(ValueError, match='changed during'):
        fixture.read()
