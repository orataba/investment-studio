from datetime import UTC, datetime, timedelta
from pathlib import Path
import json
import zipfile

import pytest
from sqlalchemy import select

from studio_market.text import TextStore
from studio_market.text.bundle import PROTOCOL, canonical_json, digest
from studio_market.text.schema import metadata, versions


def document(version="v1", *, document_id="article-1", status="active", text="阿里巴巴公布了新的资本开支计划。", observed="2026-01-02T10:00:00+00:00"):
    raw = ("<article>" + text + "</article>").encode()
    return {
        "document_id": document_id, "version_id": version,
        "channel_id": "official-issuer", "source_name": "公司公告", "title": "资本开支公告", "url": "https://example.com/announcement",
        "information_type": "disclosure_release", "status": status,
        "published_at": "2020-01-01T08:00:00+00:00", "occurred_at": None, "observed_at": observed,
        "body_sha256": digest(text.encode()), "raw_sha256": digest(raw), "raw_format": "html",
        "content_completeness": "full_text", "content_warnings": [],
        "entities": [{"entity_id": "BABA", "label": "阿里巴巴", "kind": "issuer"}], "event_ids": ["capex-2026"],
        "provenance": {"source_version_identity": version},
    }, text.encode(), raw


def bundle(path, captures, *, extra=None, producer="market-intelligence"):
    records, objects = [], {}
    for record, body, raw in captures:
        records.append(record)
        objects["objects/" + record["body_sha256"] + ".txt"] = body
        objects["objects/" + record["raw_sha256"] + ".bin"] = raw
    manifest = {"protocol": PROTOCOL, "producer": producer, "window_start": None, "window_end": "2026-01-04T00:00:00+00:00", "documents": records, "coverage": {"sources": [{"channel_id": "official-issuer", "status": "succeeded"}]}}
    manifest["bundle_id"] = "mi-text-" + digest(canonical_json(manifest))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", canonical_json(manifest))
        for name, payload in objects.items():
            archive.writestr(name, payload)
        if extra:
            archive.writestr(*extra)
    return path


@pytest.fixture
def store(tmp_path):
    value = TextStore(database_url="sqlite://", data_root=tmp_path)
    metadata.create_all(value.engine)
    yield value
    value.close()


def test_late_old_publication_and_complete_source_are_imported(store, tmp_path):
    path = bundle(tmp_path / "late.zip", [document()])
    receipt = store.import_bundle(path, expected_sha256=digest(path.read_bytes()))
    assert receipt["imported"] == 1
    result = store.search("资本开支", observed_after=datetime(2026, 1, 1, tzinfo=UTC), entities=["BABA"])
    assert result["total"] == 1
    row = result["rows"][0]
    assert row["published_at"].startswith("2020-01-01")
    assert row["observed_at"].startswith("2026-01-02")
    assert row["source_id"] == "text:v1"
    assert row["content_text"] == document()[1].decode()
    assert Path(row["raw_path"]).read_bytes() == document()[2]
    assert store.read("text:v1")["event_ids"] == ["capex-2026"]
    assert result["coverage"]["bundle_count"] == 1


def test_producer_name_is_metadata_under_one_protocol(store, tmp_path):
    path = bundle(tmp_path / "renamed.zip", [document()], producer="market-information-feed")
    assert store.import_bundle(path)["imported"] == 1
    assert store.read("text:v1")["content_text"] == document()[1].decode()


def test_redelivery_correction_withdrawal_and_explicit_history(store, tmp_path):
    first = bundle(tmp_path / "first.zip", [document()])
    store.import_bundle(first)
    assert store.import_bundle(first)["duplicate"] is True
    second = bundle(tmp_path / "second.zip", [document("v2", status="corrected", text="公司更正资本开支金额。", observed="2026-01-03T10:00:00+00:00")])
    store.import_bundle(second)
    assert store.search()["rows"][0]["version_id"] == "v2"
    assert store.read("article-1", version_id="v1")["version_id"] == "v1"
    withdrawal = bundle(tmp_path / "withdrawn.zip", [document("v3", status="withdrawn", text="公告已撤回。", observed="2026-01-04T10:00:00+00:00")])
    store.import_bundle(withdrawal)
    assert store.search()["total"] == 0
    assert store.search(include_withdrawn=True)["rows"][0]["withdrawn"] is True
    # Out-of-order delivery must not restore the earlier active document.
    assert store.import_bundle(first)["duplicate"] is True
    assert store.read("article-1")["version_id"] == "v3"


def test_bad_later_member_never_exposes_partial_bundle(store, tmp_path):
    bad, body, raw = document("bad", document_id="second")
    path = bundle(tmp_path / "bad.zip", [document(), (bad, b"tampered", raw)])
    with pytest.raises(ValueError, match="checksum"):
        store.import_bundle(path)
    assert store.search()["total"] == 0
    assert store.get_coverage()["bundle_count"] == 0


def test_archive_traversal_and_authenticated_digest_rejected(store, tmp_path):
    path = bundle(tmp_path / "unsafe.zip", [document()], extra=("../outside", b"bad"))
    with pytest.raises(ValueError, match="unexpected"):
        store.import_bundle(path)
    assert not (tmp_path.parent / "outside").exists()
    with pytest.raises(ValueError, match="authenticated"):
        store.import_bundle(path, expected_sha256="0" * 64)


def test_version_identity_cannot_silently_change(store, tmp_path):
    store.import_bundle(bundle(tmp_path / "original.zip", [document()]))
    conflict = document(text="同一版本的来源内容被修改。")
    with pytest.raises(ValueError, match="immutable"):
        store.import_bundle(bundle(tmp_path / "conflict.zip", [conflict]))
    assert store.read("text:v1")["content_text"] == document()[1].decode()
    assert store.get_coverage()["bundle_count"] == 1


def test_pagination_is_explicit_and_has_stable_ties(store, tmp_path):
    store.import_bundle(bundle(tmp_path / "many.zip", [document("v" + str(i), document_id="article-" + str(i)) for i in range(4)]))
    first = store.search(limit=2)
    second = store.search(limit=2, offset=2, as_of=datetime.fromisoformat(first["as_of"]))
    assert first["total"] == second["total"] == 4
    assert [row["document_id"] for row in first["rows"] + second["rows"]] == ["article-0", "article-1", "article-2", "article-3"]


def test_source_observation_and_local_receipt_are_both_required_for_asof(store):
    observed = datetime(2024, 1, 1, tzinfo=UTC)
    received = datetime(2024, 2, 1, tzinfo=UTC)
    capture = {"source_id": "old-web-evidence", "url": "https://example.com/policy", "title": "政策发布", "text": "政策正文", "retrieved_at": observed.isoformat(), "published_at": "2023-12-31T00:00:00+00:00", "text_truncated": False}
    first = store.capture_public_source(capture, received_at=received)
    assert store.search(as_of=received - timedelta(seconds=1))["total"] == 0
    assert store.search(as_of=received)["total"] == 1
    capture["source_id"] = "another-run-source"
    capture["retrieved_at"] = "2024-03-01T00:00:00+00:00"
    second = store.capture_public_source(capture, received_at=datetime(2024, 3, 1, tzinfo=UTC))
    assert first["source_id"] == second["source_id"]
    assert first["received_at"] == second["received_at"]
    assert second["provenance"]["original_source_id"] == "old-web-evidence"


def test_unknown_time_does_not_become_today(store):
    capture = {"url": "https://example.com/unknown", "text": "没有发布时间的原文", "retrieved_at": "2024-01-01T00:00:00+00:00"}
    row = store.capture_public_source(capture)
    assert row["published_at"] is None
    assert store.search(published_after=datetime(2020, 1, 1, tzinfo=UTC))["total"] == 0


def test_date_only_stays_a_source_date_without_midnight_or_timezone(store):
    capture = {"url": "https://example.com/day", "text": "按日发布的原文", "retrieved_at": "2024-01-02T10:00:00+00:00", "published_at": "2024-01-01", "occurred_at": "2023-12-31"}
    row = store.capture_public_source(capture)
    assert row["published_at"] == "2024-01-01"
    assert row["published_at_precision"] == "date"
    assert row["occurred_at"] == "2023-12-31"
    with store.engine.connect() as connection:
        stored = connection.execute(select(versions)).mappings().one()
    assert stored["published_at"] is None
    assert stored["published_date"].isoformat() == "2024-01-01"
    assert store.search(published_after=datetime(2024, 1, 1, tzinfo=UTC), published_before=datetime(2024, 1, 2, tzinfo=UTC))["total"] == 1
    assert store.search(published_after=datetime(2024, 1, 1, 12, tzinfo=UTC))["total"] == 0


def test_naive_publication_timestamp_is_not_assigned_a_timezone(store):
    capture = {"url": "https://example.com/naive", "text": "原文", "retrieved_at": "2024-01-02T10:00:00+00:00", "published_at": "2024-01-01T08:30:00"}
    with pytest.raises(ValueError, match="timezone"):
        store.capture_public_source(capture)


def test_later_reversion_is_a_new_version_while_unchanged_captures_are_reused(store):
    source = {"url": "https://example.com/revision", "text": "first wording", "retrieved_at": "2024-01-01T10:00:00+00:00"}
    first = store.capture_public_source(source)
    source.update(text="corrected wording", retrieved_at="2024-01-02T10:00:00+00:00")
    second = store.capture_public_source(source)
    source.update(text="first wording", retrieved_at="2024-01-03T10:00:00+00:00")
    third = store.capture_public_source(source)
    assert len({first["version_id"], second["version_id"], third["version_id"]}) == 3
    assert store.read(first["document_id"])["version_id"] == third["version_id"]
    source["retrieved_at"] = "2024-01-04T10:00:00+00:00"
    assert store.capture_public_source(source)["version_id"] == third["version_id"]


def test_received_after_finds_late_delivery_with_old_observation(store, tmp_path):
    start = datetime.now(UTC)
    store.import_bundle(bundle(tmp_path / "delayed.zip", [document()]))
    assert store.search(observed_after=start)["total"] == 0
    result = store.search(received_after=start)
    assert result["total"] == 1
    received = datetime.fromisoformat(result["rows"][0]["received_at"])
    assert store.search(received_after=start, as_of=received - timedelta(microseconds=1))["total"] == 0
    assert store.search(received_after=start, as_of=received)["total"] == 1
