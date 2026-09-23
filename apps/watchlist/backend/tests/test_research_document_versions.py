"""Mutable document directories must not silently rebind retained citations."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from watchlist_app.services import research_dossier
from watchlist_app.services.research_notebook import (
    ResearchNotebook, dossier_source, research_sources, retain_notebook, validate_notebook,
)


@pytest.fixture
def document(monkeypatch, tmp_path):
    from watchlist_app.api.routes import funds

    monkeypatch.setattr(funds, "_instrument_document_dir", lambda instrument_id: tmp_path)
    path = tmp_path / "manager.txt"
    path.write_text("Cash exposure is 30%.", encoding="utf-8")
    row = {"title": "Manager report", "stored_file_name": path.name, "file_name": path.name,
           "download_url": "/api/instruments/fund/documents/files/manager.txt", "source": "Fund manager",
           "as_of_date": "2026-08-31", "published_at": "2026-09-01", "version_label": "August",
           "uploaded_at": "2026-09-02T08:00:00+00:00", "status": "uploaded"}
    profile = SimpleNamespace(documents_payload_json={"current_documents": [row]})
    session = SimpleNamespace(get=lambda model, instrument_id: profile)
    return row, path, lambda: research_dossier._document_materials(session, "fund")[0]


def test_repeated_reads_and_operational_metadata_keep_the_same_document_version(document, monkeypatch):
    row, _, read = document
    original = read()
    assert read() == original
    # JSON key order and directory workflow status do not identify a new original.
    reordered = dict(reversed(list(row.items())))
    row.clear()
    row.update(reordered)
    row["status"] = "reviewed"
    assert read()["source_id"] == original["source_id"]
    monkeypatch.setattr(research_dossier, "_file_text", lambda path: (
        original["body"], "extracted", "Updated extraction diagnostic"))
    assert read()["source_id"] == original["source_id"]


@pytest.mark.parametrize(("field", "value"), [
    ("as_of_date", "2026-09-15"),
    ("published_at", "2026-09-16"),
    ("source", "Corrected originating manager"),
    ("title", "Revised manager report"),
    ("version_label", "Revised August"),
    ("body", "Cash exposure is 20%."),
])
def test_changed_original_or_attribution_gets_a_new_document_version(document, field, value):
    row, path, read = document
    original = read()
    if field == "body":
        path.write_text(value, encoding="utf-8")
    else:
        row[field] = value
    revised = read()
    assert revised["source_id"] != original["source_id"]
    assert read()["source_id"] == revised["source_id"]


@pytest.mark.parametrize("legacy_source_id", [False, True])
def test_update_to_one_module_keeps_another_modules_original_and_can_cite_new_material(document, legacy_source_id):
    row, path, read = document
    original = read()
    if legacy_source_id:
        original["source_id"] = "material:document:fund:manager.txt"
    old_id = original["source_id"]
    cutoff = "2026-09-20T00:00:00+00:00"
    context = {"cutoff": cutoff, "research_dossiers": [{"instrument_id": "fund", "materials": [original]}]}
    sources = research_sources(context, "first")
    first = retain_notebook(ResearchNotebook(modules=[
        {"key": "fund-strategy", "summary": "Original manager allocation", "coverage": "supported",
         "source_ids": [old_id]},
        {"key": "pricing-compensation", "summary": "Original pricing assessment", "coverage": "supported",
         "source_ids": [old_id]},
    ]), None, sources, "first", "2026-09-03T00:00:00+00:00")
    before = deepcopy(first)

    row["as_of_date"] = "2026-09-15"
    row["source"] = "Revised fund manager disclosure"
    path.write_text("Cash exposure is now 20%.", encoding="utf-8")
    revised = read()
    new_id = revised["source_id"]
    dossier = {"instrument_id": "fund", "materials": [revised], "notebook": first}
    sources = research_sources({"cutoff": cutoff, "research_dossiers": [dossier]}, "second")
    update = ResearchNotebook(modules=[{"key": "pricing-compensation", "summary": "Revised pricing assessment",
                                       "source_ids": [new_id]}])
    validate_notebook(update, "fund", sources, previous=first, cutoff=datetime.fromisoformat(cutoff))
    saved = retain_notebook(update, first, sources, "second", cutoff)

    assert saved["version_id"] == "second"
    assert saved["modules"][0] == first["modules"][0]
    assert saved["modules"][1]["source_ids"] == [new_id]
    retained = {source["source_id"]: source for source in saved["sources"]}
    assert retained[old_id]["metadata"]["effective_date"] == "2026-08-31"
    assert retained[old_id]["metadata"]["source"] == "Fund manager"
    assert retained[old_id]["text"] == "Cash exposure is 30%."
    assert retained[new_id]["metadata"]["effective_date"] == "2026-09-15"
    assert retained[new_id]["text"] == "Cash exposure is now 20%."
    assert saved["modules"][1]["versions"][0]["source_ids"] == [old_id]
    # Reading either source through the shared dossier resolves its own original.
    dossier["notebook"] = saved
    assert dossier_source(dossier, old_id)["text"] == "Cash exposure is 30%."
    assert dossier_source(dossier, new_id)["body"] == "Cash exposure is now 20%."
    assert first == before
