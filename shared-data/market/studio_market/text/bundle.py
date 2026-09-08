"""The single MI text exchange format. ZIP members are read, never extracted."""

from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path
import re
import zipfile

PROTOCOL = "mi-text/1"
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def timestamp(value: str | datetime | None, *, required: bool = False) -> datetime | None:
    if value is None:
        if required:
            raise ValueError("A timezone-aware observation timestamp is required")
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Text timestamps must include their timezone")
    return parsed.astimezone(UTC)


def source_time(value: str | datetime | date | None) -> tuple[datetime | None, date | None, str]:
    """A source's day label is not an instant at an invented midnight/timezone."""
    if value is None or value == "":
        return None, None, "unknown"
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None, date.fromisoformat(value), "date"
    if isinstance(value, date) and not isinstance(value, datetime):
        return None, value, "date"
    return timestamp(value, required=True), None, "datetime"


def validate_document(document: dict) -> None:
    for key in ("document_id", "version_id", "channel_id", "source_name", "title", "url", "information_type", "raw_format"):
        if not isinstance(document.get(key), str) or not document[key].strip():
            raise ValueError(f"Text document requires {key}")
    if document.get("status") not in {"active", "corrected", "withdrawn", "superseded"}:
        raise ValueError("Unsupported text document status")
    if document.get("content_completeness") not in {"full_text", "source_excerpt", "title_only", "unavailable"}:
        raise ValueError("Missing or unsupported content completeness")
    timestamp(document.get("observed_at"), required=True)
    source_time(document.get("published_at"))
    source_time(document.get("occurred_at"))
    for key in ("body_sha256", "raw_sha256"):
        if not isinstance(document.get(key), str) or not _DIGEST.fullmatch(document[key]):
            raise ValueError(f"Invalid {key}")
    if not isinstance(document.get("provenance"), dict):
        raise ValueError("Text provenance must be an object")
    if not isinstance(document.get("content_warnings"), list):
        raise ValueError("Text content_warnings must be an array")
    for entity in document.get("entities", []):
        if not all(isinstance(entity.get(k), str) and entity[k] for k in ("entity_id", "label", "kind")):
            raise ValueError("Invalid text entity")
    if not all(isinstance(event, str) and event for event in document.get("event_ids", [])):
        raise ValueError("Invalid text event identity")


def load_bundle(path: str | Path, expected_sha256: str | None = None) -> tuple[dict, dict[str, bytes], str]:
    path = Path(path)
    with path.open("rb") as handle:
        package_digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if expected_sha256 is not None and package_digest != expected_sha256.lower():
        raise ValueError("Bundle SHA-256 does not match the authenticated delivery receipt")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate ZIP members are not permitted")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("protocol") != PROTOCOL:
            raise ValueError("Unsupported text bundle protocol")
        if not isinstance(manifest.get("producer"), str) or not manifest["producer"].strip():
            raise ValueError("Bundle producer is required as provenance metadata")
        if not isinstance(manifest.get("documents"), list) or not isinstance(manifest.get("coverage"), dict):
            raise ValueError("Bundle documents and coverage are required")
        timestamp(manifest.get("window_start"))
        timestamp(manifest.get("window_end"), required=True)
        bundle_id = manifest.pop("bundle_id", None)
        if bundle_id != "mi-text-" + digest(canonical_json(manifest)):
            raise ValueError("Bundle manifest identity mismatch")
        manifest["bundle_id"] = bundle_id
        expected_members = {"manifest.json"}
        seen_versions = set()
        for document in manifest["documents"]:
            validate_document(document)
            if document["version_id"] in seen_versions:
                raise ValueError("A bundle cannot repeat a document version")
            seen_versions.add(document["version_id"])
            for field, suffix in (("body_sha256", ".txt"), ("raw_sha256", ".bin")):
                expected_members.add("objects/" + document[field] + suffix)
        # Exact member names also reject traversal, absolute paths, symlinks and extra payloads.
        if set(names) != expected_members:
            raise ValueError("Bundle contains missing or unexpected members")
        objects = {}
        for name in sorted(expected_members - {"manifest.json"}):
            payload = archive.read(name)
            if digest(payload) != Path(name).stem:
                raise ValueError(f"Bundle object checksum mismatch: {name}")
            objects[name] = payload
        for document in manifest["documents"]:
            body = objects[f"objects/{document['body_sha256']}.txt"].decode("utf-8")
            if not body.strip() and document["content_completeness"] != "unavailable":
                raise ValueError("Missing document body must be explicitly marked unavailable")
    return manifest, objects, package_digest
