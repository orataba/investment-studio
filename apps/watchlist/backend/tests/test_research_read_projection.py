"""Archive reads stay scoped and bounded without weakening historical access."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from studio_identity import Principal, principal_context

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_dossier import _research_records
from watchlist_app.services import research_notebook


def _source(sid):
    return {"source_id": sid, "source_type": "public_source", "url": "https://issuer.example/" + sid,
            "text": "Original source retained in full.", "published_at": "2026-09-01",
            "retrieved_at": "2026-09-02T00:00:00+00:00"}


def _add_run(session, key, *, scope=None, visibility="team", topic_team="default", entry_team="default",
             sources=None, research=None, status="completed", portfolio=None):
    session.add(ResearchTopic(topic_id=key, team_id=topic_team, title=key, visibility=visibility,
                              instrument_ids=["xlk"], portfolio_id=portfolio))
    session.flush()
    sources = sources if sources is not None else [_source(key)]
    research = research if research is not None else {"fundamental_view": key, "sources": sources}
    session.add(ResearchEntry(entry_id=key, topic_id=key, team_id=entry_team, kind="analysis", title=key,
        status=status, created_at=datetime(2026, 9, 3, tzinfo=UTC),
        context_json={"research_run": True, "instrument_ids": scope if scope is not None else ["xlk"],
            "cutoff": "2026-09-03T00:00:00+00:00", "reviews": {"xlk": {"status": "completed", "research": research}},
            "web_evidence": [{"operation": "fetch", "sources": sources}],
            "instrument_inputs": {"xlk": {"large_unrelated_price_array": ["unused"] * 500}},
            "transcript": "Not needed in aggregate reads."}))


@pytest.mark.parametrize("run_count", [1, 30])
def test_scoped_archive_reads_use_two_queries_and_never_materialize_complete_runs(client, run_count):
    with get_session_factory()() as session:
        for index in range(run_count):
            _add_run(session, f"target-{index}")
            _add_run(session, f"unrelated-{index}", scope=["xlk-related"])
        session.commit()
    with get_session_factory()() as session:
        queries = []
        def capture(state):
            queries.append(state.statement)
        event.listen(session, "do_orm_execute", capture)
        rows = list(_research_records(session, "xlk"))
        assert len(rows) == run_count and len(queries) == 2
        assert not session.identity_map  # No ORM run/context or per-topic lazy loads.
        assert all(not column.compare(ResearchEntry.__table__.c.context_json)
                   for statement in queries for column in statement.selected_columns)
        assert all(set(record.context_json) == {"cutoff", "recordkeeping_only", "citation_correction"}
                   for record, _ in rows)
        queries.clear()
        sources = research_notebook.retained_public_sources(session, "xlk")
        assert len(sources) == run_count and len(queries) == 2
        assert not session.identity_map
        assert all(not column.compare(ResearchEntry.__table__.c.context_json)
                   for statement in queries for column in statement.selected_columns)


def test_projection_keeps_team_and_all_historical_portfolio_boundaries(client):
    with get_session_factory()() as session:
        _add_run(session, "team-public")
        _add_run(session, "explicit-private-publication", visibility="private")
        _add_run(session, "other-team-topic", topic_team="other")
        _add_run(session, "other-team-entry", entry_team="other")
        _add_run(session, "past-portfolio")
        _add_run(session, "past-risk-portfolio")
        _add_run(session, "current-portfolio", portfolio="private")
        for key, context in (("past-portfolio", {"portfolio_id": "private"}),
                             ("past-risk-portfolio", {"risk_scope": {"portfolio_id": "private"}})):
            session.add(ResearchEntry(entry_id=key + "-old-note", topic_id=key, kind="note", title="old scope",
                                      context_json=context))
        session.commit()
    with principal_context(Principal("member", "Member", "default")), get_session_factory()() as session:
        rows = list(_research_records(session, "xlk"))
        assert {record.entry_id for record, _ in rows} == {"team-public", "explicit-private-publication"}
        # Approved research may be published from a private discussion; unshared
        # fetched material from that discussion does not become a team original.
        assert [source["source_id"] for source in research_notebook.retained_public_sources(session, "xlk")] == ["team-public"]


def test_exact_stored_scope_and_projection_preserve_versions_and_orm_original(client):
    research = {"version_id": "saved", "investment_view": {"direction": "unchanged",
        "version_id": "current-view", "versions": [{"version_id": "old-view", "source_ids": ["old"]}]},
        "sources": [_source("old")], "questions": [{"key": "q", "assessment": "original"}]}
    with get_session_factory()() as session:
        _add_run(session, "target", research=research)
        for key, scope in (("suffix", ["xlk-other"]), ("empty", []), ("null", None), ("scalar", "xlk")):
            _add_run(session, key, scope=scope)
            if key == "null":
                session.flush()
                entry = session.get(ResearchEntry, key)
                entry.context_json = {**entry.context_json, "instrument_ids": None}
        session.commit()
    with get_session_factory()() as session:
        original = session.get(ResearchEntry, "target")
        complete_context = deepcopy(original.context_json)
        rows = list(_research_records(session, "xlk"))
        assert [row.entry_id for row, _ in rows] == ["target"]
        assert rows[0][1] == research
        assert session.get(ResearchEntry, "target") is original
        assert original.context_json == complete_context


def test_hydration_occurs_once_per_retained_version_after_scope_filter(client, monkeypatch):
    shared = {"source_id": "read-original", "document_id": "document", "version_id": "retained-v1"}
    unused = {"source_id": "not-assigned", "document_id": "unrelated", "version_id": "unused-v1"}
    with get_session_factory()() as session:
        _add_run(session, "first", scope=["xlk", "xlf"], sources=[shared, unused], research={
            "investment_view": {"versions": [{"version_id": "old-view", "source_ids": ["read-original"]}]}})
        _add_run(session, "rejected", sources=[shared], status="failed")
        session.flush()
        latest = session.get(ResearchEntry, "rejected")
        latest.created_at += timedelta(hours=1)
        session.commit()
    reads = []
    def hydrate(source):
        reads.append(source["version_id"])
        return {**_source(source["source_id"]), **source}
    monkeypatch.setattr(research_notebook, "hydrate_source", hydrate)
    with get_session_factory()() as session:
        sources = research_notebook.retained_public_sources(session, "xlk")
    assert reads == ["retained-v1"]
    assert len(sources) == 1 and sources[0]["version_id"] == "retained-v1"
    assert sources[0]["source_run_id"] == "rejected"
    assert sources[0]["published_at"] == "2026-09-01"
