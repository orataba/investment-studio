"""Legacy raw gzip envelopes may differ; response and publication identities may not."""
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import stat
from types import SimpleNamespace
import zipfile

import pytest
from sqlalchemy import select

from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore, raw, replication
from studio_market.numeric.schema import batches


BODY = b'{"symbol":"AAPL","target":123.45,"source":"public"}'


def legacy_gzip(body, os_byte):
    encoded = bytearray(gzip.compress(body, compresslevel=6, mtime=0))
    encoded[9] = os_byte
    return bytes(encoded)


def test_new_raw_archives_normalize_platform_header_without_changing_body_identity(tmp_path, monkeypatch):
    compress = gzip.compress
    archives = []
    for os_byte in (3, 19):  # zlib headers emitted on Linux and macOS.
        def platform_compress(*args, **kwargs):
            encoded = bytearray(compress(*args, **kwargs))
            encoded[9] = os_byte
            return bytes(encoded)
        monkeypatch.setattr(raw.gzip, "compress", platform_compress)
        settings = MarketSettings("sqlite://", tmp_path / str(os_byte))
        body_sha, ref = raw.archive_response(settings, "fixture", BODY)
        assert body_sha == hashlib.sha256(BODY).hexdigest()
        assert ref == f"numeric/raw/fixture/{body_sha[:2]}/{body_sha}.gz"
        archives.append((settings.data_root / ref).read_bytes())
    assert archives[0] == archives[1]
    assert archives[0][4:8] == b"\0\0\0\0"
    assert archives[0][9] == 255
    assert gzip.decompress(archives[0]) == BODY


@pytest.fixture
def publication(tmp_path):
    source = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'source.db'}", tmp_path / "source"))
    target = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'target.db'}", tmp_path / "target"))
    source.create_schema_for_testing()
    target.create_schema_for_testing()
    try:
        _, ref = raw.archive_response(source.settings, "fixture", BODY)
        (source.settings.data_root / ref).write_bytes(legacy_gzip(BODY, 3))
        source.ingest("analyst_price_targets", [[dict(
            symbol="AAPL", target=123.45, raw_ref=ref,
            observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )]], source="fixture")
        bundle = tmp_path / "numeric.zip"
        replication.export_bundle(source.settings, bundle)
        with zipfile.ZipFile(bundle) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        yield SimpleNamespace(source=source, target=target, ref=ref, bundle=bundle, manifest=manifest)
    finally:
        target.close()
        source.close()


def existing_raw(publication, payload):
    path = publication.target.settings.data_root / publication.ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def rewrite_raw(publication, payload, *, update_digest=True):
    """Keep a valid ZIP while changing only the declared raw object's bytes."""
    with zipfile.ZipFile(publication.bundle) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members[publication.ref] = payload
    if update_digest:
        manifest = json.loads(members["manifest.json"])
        item = next(item for item in manifest["objects"] if item["path"] == publication.ref)
        item.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        members["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(publication.bundle, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)


def assert_unpublished(publication):
    with publication.target.engine.connect() as conn:
        assert conn.execute(select(batches)).first() is None


@pytest.mark.parametrize("envelope", ["os_byte", "filename_and_mtime"])
def test_import_accepts_equal_verified_raw_bodies_preserving_existing_bytes_and_source_ids(publication, envelope):
    payload = legacy_gzip(BODY, 19)
    if envelope == "filename_and_mtime":
        output = io.BytesIO()
        with gzip.GzipFile(filename="historical-response.json", mode="wb", fileobj=output, mtime=123, compresslevel=1) as capture:
            capture.write(BODY)
        payload = output.getvalue()
    path = existing_raw(publication, payload)
    path.chmod(0o600)
    original = path.stat()

    result = replication.import_bundle(publication.target.settings, publication.bundle)
    assert result["batches"][0]["status"] == "ready"
    assert path.read_bytes() == payload
    assert path.stat().st_ino == original.st_ino
    assert path.stat().st_mtime_ns == original.st_mtime_ns
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    source_row = publication.source.latest("analyst_price_targets")["rows"][0]
    assert publication.target.latest("analyst_price_targets")["rows"][0] == source_row
    assert publication.target.read_source(source_row["source_id"])["raw_ref"] == publication.ref
    assert replication.import_bundle(publication.target.settings, publication.bundle)["batches"][0]["status"] == "already_imported"


@pytest.mark.parametrize("payload", [b"not gzip", legacy_gzip(BODY, 19)[:-1], legacy_gzip(b"different body", 19)])
def test_import_rejects_corrupt_or_wrong_existing_raw_body(publication, payload):
    path = existing_raw(publication, payload)
    with pytest.raises(ValueError, match="existing immutable object"):
        replication.import_bundle(publication.target.settings, publication.bundle)
    assert path.read_bytes() == payload
    assert_unpublished(publication)


@pytest.mark.parametrize("payload", [b"not gzip", legacy_gzip(BODY, 3)[:-1], legacy_gzip(b"different body", 3)])
def test_import_checks_incoming_body_even_when_compressed_manifest_hash_is_valid(publication, payload):
    path = existing_raw(publication, legacy_gzip(BODY, 19))
    rewrite_raw(publication, payload)
    with pytest.raises(ValueError, match="existing immutable object"):
        replication.import_bundle(publication.target.settings, publication.bundle)
    assert gzip.decompress(path.read_bytes()) == BODY
    assert_unpublished(publication)


def test_equal_decoded_bodies_must_both_match_the_raw_path_sha(publication):
    existing_raw(publication, legacy_gzip(b"same wrong body", 19))
    rewrite_raw(publication, legacy_gzip(b"same wrong body", 3))
    with pytest.raises(ValueError, match="existing immutable object"):
        replication.import_bundle(publication.target.settings, publication.bundle)
    assert_unpublished(publication)


def test_equivalent_body_does_not_bypass_incoming_compressed_manifest_integrity(publication):
    existing_raw(publication, legacy_gzip(BODY, 19))
    rewrite_raw(publication, legacy_gzip(BODY, 255), update_digest=False)
    with pytest.raises(ValueError, match="object integrity mismatch"):
        replication.import_bundle(publication.target.settings, publication.bundle)
    assert_unpublished(publication)


def test_non_raw_immutable_objects_still_require_exact_bytes(publication):
    part = publication.manifest["files"][0]["path"]
    original = (publication.source.settings.data_root / part).read_bytes()
    altered = original[:-1] + bytes([original[-1] ^ 1])
    target = publication.target.settings.data_root / part
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(altered)
    with pytest.raises(ValueError, match="existing immutable object"):
        replication.import_bundle(publication.target.settings, publication.bundle)
    assert target.read_bytes() == altered
    assert_unpublished(publication)


@pytest.mark.parametrize("body", [BODY, b"conflicting concurrent response"])
def test_concurrent_raw_publication_uses_the_same_body_integrity_boundary(publication, monkeypatch, body):
    link = replication.os.link
    path = publication.target.settings.data_root / publication.ref
    conflicts = []
    def concurrent_link(source, target):
        if Path(target) == path:
            conflicts.append(target)
            Path(target).write_bytes(legacy_gzip(body, 19))
            raise FileExistsError(target)
        return link(source, target)
    monkeypatch.setattr(replication.os, "link", concurrent_link)
    if body == BODY:
        assert replication.import_bundle(publication.target.settings, publication.bundle)["batches"][0]["status"] == "ready"
    else:
        with pytest.raises(ValueError, match="existing immutable object"):
            replication.import_bundle(publication.target.settings, publication.bundle)
        assert_unpublished(publication)
    assert len(conflicts) == 1
    assert path.read_bytes() == legacy_gzip(body, 19)
    assert list(publication.target.settings.data_root.rglob(".receiving-*")) == []
