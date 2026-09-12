from datetime import UTC, datetime, timedelta

import pytest

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_access, sector_research


@pytest.mark.parametrize("pending_status", ["queued", "running", "failed"])
def test_scoped_states_keep_unpublished_attempts_and_only_requested_instruments(client, monkeypatch, pending_status):
    calls = []
    original_scopes = research_access.topic_portfolio_ids_by_topic

    def scopes(session, topics):
        calls.append(sorted(topic.topic_id for topic in topics))
        return original_scopes(session, topics)

    monkeypatch.setattr(research_access, "topic_portfolio_ids_by_topic", scopes)
    with get_session_factory()() as session:
        for topic_id in ("shared-batch", "unrelated-run"):
            session.add(ResearchTopic(topic_id=topic_id, title="研究", visibility="team"))
        session.flush()
        created = datetime(2026, 9, 1, tzinfo=UTC)
        session.add(ResearchEntry(entry_id="batch-published", topic_id="shared-batch", kind="analysis",
            status="completed", title="已发布", completed_at=created,
            context_json={"sector_run": True, "instrument_ids": ["target", "batch-peer"], "reviews": {
                iid: {"status": "completed", "research": {"investment_view": {"direction": iid,
                    "source_run_id": "batch-published", "updated_at": created.isoformat()}}}
                for iid in ("target", "batch-peer")}}))
        session.add(ResearchEntry(entry_id="pending-attempt", topic_id="shared-batch", kind="analysis",
            status=pending_status, title="本轮检查", body="来源获取失败" if pending_status == "failed" else "",
            created_at=created + timedelta(days=1),
            context_json={"sector_run": True, "instrument_ids": ["target"], "cutoff": "2026-09-02T00:00:00Z"}))
        session.add(ResearchEntry(entry_id="unrelated", topic_id="unrelated-run", kind="analysis",
            status="queued", title="其他标的", context_json={"sector_run": True, "instrument_ids": ["unrelated"]}))
        session.commit()
        all_states = sector_research.review_states(session)
        calls.clear()
        scoped = sector_research.review_states(session, instrument_ids=["target", "target", "unknown"])
        assert scoped == {key: {"target": value["target"]} for key, value in all_states.items()}
        assert scoped["latest"]["target"]["status"] == pending_status
        assert scoped["last_completed"]["target"]["run_id"] == "batch-published"
        assert calls == [["shared-batch"]]
        assert sector_research.review_states(session, instrument_ids=["batch-peer", "target"]) == {
            key: {iid: value[iid] for iid in ("target", "batch-peer")} for key, value in all_states.items()}


def test_scoped_states_use_exact_saved_array_not_current_topic_membership(client):
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="scope-topic", title="范围", visibility="team", instrument_ids=["target"]))
        session.flush()
        for index, stored_scope in enumerate((["target-extra"], "target", None, [], ["other"])):
            session.add(ResearchEntry(entry_id=f"different-scope-{index}", topic_id="scope-topic", kind="analysis",
                status="queued", title="检查", context_json={"sector_run": True, "instrument_ids": stored_scope}))
        session.commit()
        assert sector_research.review_states(session, instrument_ids=["target"]) == {"latest": {}, "last_completed": {}}


def test_explicit_empty_scope_does_not_read_research_history(client, monkeypatch):
    with get_session_factory()() as session:
        def unexpected_query(*args, **kwargs):
            pytest.fail("An empty research scope must not read unrelated history")
        monkeypatch.setattr(session, "execute", unexpected_query)
        assert sector_research.review_states(session, instrument_ids=[]) == {"latest": {}, "last_completed": {}}
        assert sector_research.latest_reviews(session, instrument_ids=[]) == {}
