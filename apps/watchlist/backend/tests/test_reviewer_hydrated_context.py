"""Reviewer subprocesses have API access, deliberately no market DB credentials."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import market_evidence, research_notebook, sector_fact_review


def test_context_expands_theme_and_pm_original_versions_before_sandboxed_review(client, monkeypatch):
    cutoff = "2026-09-24T00:00:00+00:00"
    def source(name):
        return {"source_id": name, "document_id": "document-" + name, "version_id": "version-" + name,
                "source_type": "public_source", "body_available": True}
    refs = [source(name) for name in ("theme-source", "pm-source")]
    dossier = {"instrument_id": "asset", "themes": [{"theme_key": "demand", "sources": [refs[0]]}],
               "pm_views": [{"note_id": "pm", "revision_number": 1, "sources": [refs[1]]}]}
    context = {"sector_run": True, "run_id": "review-hydration", "cutoff": cutoff,
               "instrument_ids": ["asset"], "research_dossiers": [dossier]}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="review-hydration", title="Research"))
        session.flush()
        session.add(ResearchEntry(entry_id="review-hydration", topic_id="review-hydration", kind="analysis",
            title="Research", status="failed", context_json=deepcopy(context)))
        session.commit()
        persisted = deepcopy(session.get(ResearchEntry, "review-hydration").context_json)
    reads = []
    def read(document_id, *, version_id, as_of):
        reads.append((document_id, version_id, as_of))
        return {"document_id": document_id, "version_id": version_id,
                "content_text": "该版本保留的原始披露。", "published_at": "2026-09-23T00:00:00+00:00",
                "observed_at": "2026-09-23T08:00:00+00:00", "received_at": "2026-09-23T09:00:00+00:00"}
    monkeypatch.setattr(market_evidence, "text_store", lambda: SimpleNamespace(read=read))
    response = client.get("/api/research/runs/review-hydration/context?originals=true")
    assert response.status_code == 200, response.text
    hydrated = response.json()
    assert reads == [(ref["document_id"], ref["version_id"], datetime.fromisoformat(cutoff)) for ref in refs]
    monkeypatch.delenv("INVESTMENT_STUDIO_MARKET_DATABASE_URL", raising=False)
    def unavailable():
        raise AssertionError("Reviewer must not create TextStore or require a DB credential")
    monkeypatch.setattr(market_evidence, "text_store", unavailable)
    originals = research_notebook.research_sources(hydrated, "review-hydration")
    assert set(originals) == {ref["source_id"] for ref in refs}
    draft = [{"instrument_id": "asset", "events": [], "research": {"source_ids": list(originals)}}]
    packet = sector_fact_review._evidence_packet(hydrated, draft, "review-hydration")
    assert {s["source_id"] for s in packet["sources"]} == set(originals)
    assert all(s["text"] == "该版本保留的原始披露。" for s in packet["sources"])
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "review-hydration").context_json == persisted
