"""Conditional, compressed reads of the collector's one authoritative index.

The remote helper uses only Python's standard library and reads one open file.
It neither imports the remote application nor writes a second publication index.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time


# Metadata only, not numerical payloads. Bound both decoding and remote work;
# the observed 6 MiB / 125k-batch catalog has ample room within this limit.
MAX_INDEX_BYTES = 64 * 1024 * 1024
INDEX_TIMEOUT_SECONDS = 300

# Keep the protocol implementation independent of either installation's release.
# Atomic index replacement cannot change the file held by this descriptor.
REMOTE_INDEX = r'''
import gzip, hashlib, json, os, shutil, sys
with open(sys.argv[1], 'rb') as source:
    size = os.fstat(source.fileno()).st_size
    if not 0 < size <= int(sys.argv[3]):
        raise ValueError('numeric index exceeds metadata size limit')
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(65536), b''):
        digest.update(chunk)
    checksum = digest.hexdigest()
    changed = checksum != sys.argv[2]
    header = {'sha256': checksum, 'bytes': size, 'changed': changed}
    sys.stdout.buffer.write(json.dumps(header).encode() + b'\n')
    sys.stdout.buffer.flush()
    if changed:
        source.seek(0)
        with gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', mtime=0) as output:
            shutil.copyfileobj(source, output, 65536)
'''


class NumericIndexTransferError(RuntimeError):
    """Only these credential-free transport facts may enter a pipeline receipt."""
    def __init__(self, *, phase, timeout_seconds, elapsed_seconds, received_bytes, exit_code=None):
        self.details = dict(phase=phase, timeout_seconds=timeout_seconds,
                            elapsed_seconds=round(elapsed_seconds, 3), received_bytes=received_bytes)
        if exit_code is not None:
            self.details['exit_code'] = exit_code
        super().__init__('Numeric index transfer did not complete')


def validate_index(index):
    if (not isinstance(index, dict) or index.get('format') != 'investment-studio-numeric-index'
            or index.get('version') != 1 or not isinstance(index.get('bundles'), list)):
        raise ValueError('Unsupported numeric directory index')
    names = set()
    for item in index['bundles']:
        if (not isinstance(item, dict) or not isinstance(item.get('name'), str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]+\.zip', item['name']) or item['name'] in names):
            raise ValueError('Invalid or duplicate numeric bundle filename')
        names.add(item['name'])
        if not isinstance(item.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
            raise ValueError('Invalid numeric bundle checksum')
        if (type(item.get('bytes')) is not int or item['bytes'] < 0
                or not isinstance(item.get('batch_ids'), list)
                or not all(isinstance(value, str) and value for value in item['batch_ids'])):
            raise ValueError('Invalid numeric bundle metadata')
    return index


def read_index(*, host: str, remote_dir: str, incoming: Path, temporary: Path, timeout: int):
    """Reuse bytes only after this request confirms their remote content hash.

    A cached index is never an import receipt: callers must still verify actual
    ready batches, including after restoring a database or retiring an archive.
    """
    origin = hashlib.sha256((host + ':' + remote_dir).encode()).hexdigest()
    cache = incoming / ('.index-' + origin + '.json')
    cached_sha = ''
    if cache.is_file() and 0 < cache.stat().st_size <= MAX_INDEX_BYTES:
        with cache.open('rb') as source:
            cached_sha = hashlib.file_digest(source, 'sha256').hexdigest()
    command = 'python3 -c ' + shlex.quote(REMOTE_INDEX) + ' ' + ' '.join(map(shlex.quote, (
        remote_dir + '/index.json', cached_sha, str(MAX_INDEX_BYTES))))
    wire = temporary / 'index.response'
    budget = min(timeout, INDEX_TIMEOUT_SECONDS)
    started = time.monotonic()
    try:
        with wire.open('wb') as output:
            subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                            '-o', 'ConnectTimeout=10', '--', host, command],
                           stdout=output, stderr=subprocess.PIPE, check=True, timeout=budget)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        # A complete header proves the connection authenticated and opened the
        # index. No header is ambiguous: do not label every timeout network loss.
        with wire.open('rb') as source:
            header_line = source.readline(2048)
        try:
            has_header = isinstance(json.loads(header_line), dict) and header_line.endswith(b'\n')
        except (ValueError, UnicodeDecodeError):
            has_header = False
        raise NumericIndexTransferError(
            phase='compressed_body' if has_header else 'connection_or_remote_header',
            timeout_seconds=budget, elapsed_seconds=time.monotonic() - started,
            received_bytes=wire.stat().st_size, exit_code=getattr(exc, 'returncode', None)) from None
    with wire.open('rb') as source:
        header = json.loads(source.readline(2048))
        if (not isinstance(header, dict) or type(header.get('bytes')) is not int
                or not 0 < header['bytes'] <= MAX_INDEX_BYTES or type(header.get('changed')) is not bool
                or not isinstance(header.get('sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', header['sha256'])):
            raise ValueError('Invalid numeric index response header')
        if not header['changed']:
            if not cached_sha or header['sha256'] != cached_sha or source.read(1):
                raise ValueError('Invalid unchanged numeric index response')
            with cache.open('rb') as cached:
                if (os.fstat(cached.fileno()).st_size != header['bytes']
                        or hashlib.file_digest(cached, 'sha256').hexdigest() != header['sha256']):
                    raise ValueError('Cached numeric index changed during the request')
                cached.seek(0)
                index = validate_index(json.load(cached))
        else:
            decoded = temporary / 'index.json'
            size = 0
            checksum = hashlib.sha256()
            with gzip.GzipFile(fileobj=source, mode='rb') as compressed, decoded.open('wb') as output:
                while chunk := compressed.read(65536):
                    size += len(chunk)
                    if size > header['bytes']:
                        raise ValueError('Numeric index exceeds declared size')
                    checksum.update(chunk)
                    output.write(chunk)
            if size != header['bytes'] or checksum.hexdigest() != header['sha256']:
                raise ValueError('Numeric index checksum or size mismatch')
            index = validate_index(json.loads(decoded.read_text()))
            os.replace(decoded, cache)
    return index, dict(status='updated' if header['changed'] else 'unchanged',
                       sha256=header['sha256'], bytes=header['bytes'], wire_bytes=wire.stat().st_size,
                       elapsed_seconds=round(time.monotonic() - started, 3))
