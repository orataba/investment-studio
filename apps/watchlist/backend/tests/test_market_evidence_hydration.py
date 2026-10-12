from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from watchlist_app.services import market_evidence


def test_document_hydration_preserves_reference_metadata_versions_and_cutoff(monkeypatch):
    calls = []
    cutoff = datetime(2026, 10, 1, tzinfo=UTC)
    def read(document_id, *, version_id, as_of):
        calls.append((document_id, version_id, as_of))
        if version_id == "future":
            return None
        return {"source_id": f"text:{document_id}:{version_id}", "document_id": document_id,
            "version_id": version_id, "content_text": f"Original {version_id}", "published_at": "2026-09-30"}
    monkeypatch.setattr(market_evidence, "text_store", lambda: SimpleNamespace(read=read))
    hydrate = market_evidence.SourceHydrator(cutoff=cutoff)
    ref = {"document_id": "doc", "version_id": "one", "instrument_id": "one", "pm_binding_note": "first"}
    assert hydrate(ref)["text"] == "Original one"
    other = hydrate({**ref, "instrument_id": "two", "pm_binding_note": "second"})
    assert other["instrument_id"] == "two" and other["pm_binding_note"] == "second"
    assert hydrate({**ref, "version_id": "two"})["text"] == "Original two"
    assert calls == [("doc", "one", cutoff), ("doc", "two", cutoff)]
    with pytest.raises(ValueError, match="截止时间不可用"):
        hydrate({**ref, "version_id": "future"})
    # A new request repeats the actual read; no process-wide authorization/data cache.
    market_evidence.hydrate_source(ref, cutoff=cutoff)
    assert calls[-1] == ("doc", "one", cutoff) and len(calls) == 4
