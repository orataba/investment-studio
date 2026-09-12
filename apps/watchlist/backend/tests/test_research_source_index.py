from copy import deepcopy
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from studio_identity import Principal, principal_context

from watchlist_app.api.routes.workbench import _run_source_context
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_read_projection import run_source_index


def sources_context():
    first = {"source_id": "original-v1", "document_id": "original", "version_id": "v1",
             "url": "https://example.com/release", "title": "原文", "published_at": "2026-09-01",
             "retrieved_at": "2026-09-02T00:00:00Z", "body_available": True}
    second = {**first, "source_id": "original-v2", "version_id": "v2", "retrieved_at": "2026-09-03T00:00:00Z"}
    return {"research_run": True, "research_actor": {"kind": "user", "user_id": "pm-one"},
        "web_evidence": [{"operation": "search", "sources": [{"source_id": "lead", "text": "检索片段"}]},
            {"operation": "fetch", "sources": [first, {"source_id": "legacy", "url": "https://example.com/legacy",
                "text": "完整历史原文" * 50000}, {"source_id": "failed-fetch", "text": ""}]}],
        "market_text_sources": [first, second, {**first, "source_id": "empty-body", "body_available": False}],
        "computed_metrics": [{"unrelated": "庞大数值输入" * 50000}]}


def test_sources_projection_keeps_original_versions_without_bodies_or_search_leads():
    context = sources_context()
    original = deepcopy(context)
    sources = run_source_index(context)
    assert [row["source_id"] for row in sources] == ["original-v1", "legacy", "original-v2"]
    assert sources[0]["url"] == sources[2]["url"] and sources[0]["version_id"] != sources[2]["version_id"]
    assert all(row["body_available"] and "text" not in row and "content_text" not in row for row in sources)
    assert context == original


def test_context_sources_endpoint_uses_same_private_run_access_and_small_projection(client, monkeypatch):
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="source-topic", title="研究", visibility="private", created_by_user_id="pm-one"))
        session.flush()
        session.add(ResearchEntry(entry_id="source-run", topic_id="source-topic", kind="analysis", title="研究",
            status="completed", context_json=sources_context()))
        session.commit()
    import watchlist_app.main as main
    # Identity resolution itself does not read research rows in production; the
    # shared fixture normally infers an actor from the whole test record.
    monkeypatch.setattr(main, "resolve_request", lambda *args, **kwargs:
        Principal("pm-one", "PM", "default", resource_scope={"kind": "run", "id": "source-run"}))
    def reject_full_entry_load(*_):
        pytest.fail("Source directory must not hydrate the complete research context, including in access middleware")
    event.listen(ResearchEntry, "load", reject_full_entry_load)
    try:
        response = client.get("/api/research/runs/source-run/context?section=sources")
    finally:
        event.remove(ResearchEntry, "load", reject_full_entry_load)
    assert response.status_code == 200, response.text
    assert response.json() == {"sources": run_source_index(sources_context())}
    assert len(response.content) < 3000
    assert client.get("/api/research/runs/source-run/context?section=sources&originals=true").status_code == 422
    with get_session_factory()() as session:
        outsider = Principal("other-pm", "Other", "default")
        with principal_context(outsider), pytest.raises(HTTPException) as denied:
            _run_source_context(session, "source-run")
        assert denied.value.status_code == 404
        scoped = Principal("pm-one", "PM", "default", resource_scope={"kind": "run", "id": "another-run"})
        with principal_context(scoped), pytest.raises(HTTPException) as denied:
            _run_source_context(session, "source-run")
        assert denied.value.status_code == 403
        assert "computed_metrics" in session.get(ResearchEntry, "source-run").context_json


def test_source_projection_rechecks_original_portfolio_after_topic_scope_changed(client, monkeypatch):
    from watchlist_app.services import research_access
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="historical-topic", title="研究", visibility="private",
                                  created_by_user_id="pm-one", portfolio_id="current-portfolio"))
        session.flush()
        session.add(ResearchEntry(entry_id="historical-run", topic_id="historical-topic", kind="analysis", title="研究",
            context_json={**sources_context(), "portfolio_id": "original-portfolio"}))
        session.commit()
    checked = []
    def portfolio_access(portfolio_id):
        checked.append(portfolio_id)
        if portfolio_id == "original-portfolio":
            raise HTTPException(403, "Original portfolio access revoked")
    monkeypatch.setattr(research_access, "require_portfolio", portfolio_access)
    with get_session_factory()() as session, principal_context(Principal("pm-one", "PM", "default")):
        with pytest.raises(HTTPException) as denied:
            _run_source_context(session, "historical-run")
        assert denied.value.status_code == 403
    assert checked == ["current-portfolio", "original-portfolio"]
