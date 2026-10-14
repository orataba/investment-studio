"""Publish retained FMP text through the same authenticated text-bundle transport."""
from datetime import UTC, datetime
from pathlib import Path
import os
import tempfile
import zipfile

from sqlalchemy import func, select

from .bundle import PROTOCOL, canonical_json, digest
from .schema import bundles, collection_receipts, versions
from .store import _iso

PRODUCER = "investment-studio-fmp"
DOCUMENT_FIELDS = ("document_id", "version_id", "channel_id", "source_name", "title", "url",
    "information_type", "status", "published_at", "occurred_at", "observed_at", "body_sha256",
    "raw_sha256", "raw_format", "content_completeness", "content_warnings", "provenance", "entities", "event_ids")


def publish_fmp_text(store):
    cutoff = datetime.now(UTC)
    with store.engine.connect() as connection:
        cursor = connection.execute(select(func.max(bundles.c.window_end)).where(bundles.c.producer == PRODUCER)).scalar_one()
        conditions = [versions.c.channel_id.like("fmp:%"), versions.c.received_at <= cutoff,
                      versions.c.observed_at <= cutoff]
        receipt_conditions = [collection_receipts.c.received_at <= cutoff]
        if cursor is not None:
            conditions.append(versions.c.received_at > cursor)
            receipt_conditions.append(collection_receipts.c.received_at > cursor)
        records = [store._row(connection, row) for row in connection.execute(select(versions).where(*conditions)
                   .order_by(versions.c.observed_at, versions.c.version_id)).mappings()]
        receipts = [dict(row) for row in connection.execute(select(collection_receipts).where(*receipt_conditions)
                    .order_by(collection_receipts.c.checked_at, collection_receipts.c.receipt_id)).mappings()]
    if not records and not receipts:
        return {"status": "current", "documents": 0}
    for receipt in receipts:
        for key in ("checked_at", "received_at"):
            receipt[key] = _iso(receipt[key])
        for key in ("window_start", "window_end"):
            receipt[key] = receipt[key].isoformat()
    documents = [{key: row[key] for key in DOCUMENT_FIELDS} for row in records]
    manifest = {"protocol": PROTOCOL, "producer": PRODUCER, "window_start": _iso(cursor),
                "window_end": cutoff.isoformat(), "documents": documents, "coverage": {"fmp_sources": receipts}}
    manifest["bundle_id"] = "mi-text-" + digest(canonical_json(manifest))
    outbox = store.data_root / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    target = outbox / (manifest["bundle_id"] + ".zip")
    descriptor, temporary = tempfile.mkstemp(prefix=".incoming-", dir=outbox)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", canonical_json(manifest))
            included = set()
            for row in records:
                for name, payload in (("objects/" + row["body_sha256"] + ".txt", row["content_text"].encode("utf-8")),
                                      ("objects/" + row["raw_sha256"] + ".bin", Path(row["raw_path"]).read_bytes())):
                    if name not in included:
                        archive.writestr(name, payload)
                        included.add(name)
        os.chmod(temporary, 0o640)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    checksum = digest(target.read_bytes())
    checksum_path = target.with_suffix(".zip.sha256")
    checksum_path.write_text(checksum + "  " + target.name + "\n")
    os.chmod(checksum_path, 0o640)
    # The local import receipt is the durable publication cursor. If interrupted
    # before it commits, the next publication includes the still-unacknowledged
    # versions again; immutable identities make that replay harmless.
    store.import_bundle(target, expected_sha256=checksum)
    return {"status": "ready", "bundle_id": manifest["bundle_id"], "documents": len(documents),
            "coverage_receipts": len(receipts), "path": str(target), "sha256": checksum}
