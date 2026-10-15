"""Browser review status does not transfer the model's retained source catalogue."""
from copy import deepcopy
from datetime import UTC, datetime
import json

import pytest

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_access, research_dossier
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env


def check_working_set_summary(monkeypatch):
    iid = "working-set-reader"
    actor = {"team_id": "default"}
    wanted = ("mode", "cutoff", "selected_theme_ids", "reviewed_theme_ids")
    values = [
        {"mode": "incremental", "cutoff": "2026-10-14T00:00:00Z",
         "selected_theme_ids": ["second", "first"], "reviewed_theme_ids": ["first"],
         "reasons": [{"original": "retained\x00original" * 10000}],
         "source_dependencies": {"source": [{"kind": "theme", "id": "first"}]}},
        {"mode": "explicit", "cutoff": None, "selected_theme_ids": ["原值\x00", r"literal\u0000", "�"],
         "reviewed_theme_ids": [], "reasons": [{"original": "keep all original evidence" * 10000}]},
        {"selected_theme_ids": [], "reviewed_theme_ids": None, "unrelated": {"original": True}},
        {}, None,
    ]
    observed = []
    original_read = research_access.research_projection_rows

    def read_projection(*args, **kwargs):
        rows = original_read(*args, **kwargs)
        observed.extend(deepcopy(row.working_set) for row in rows)
        return rows

    monkeypatch.setattr(research_access, "research_projection_rows", read_projection)
    with get_session_factory()() as session:
        topic = ResearchTopic(topic_id=f"instrument-events:{iid}", title="Reader fixture",
                              team_id="default", visibility="team", instrument_ids=[iid])
        session.add(topic)
        session.flush()
        run = ResearchEntry(entry_id="working-set-reader-run", topic_id=topic.topic_id,
                            team_id="default", kind="analysis", title="Retained research", status="completed",
                            completed_at=datetime.now(UTC), context_json={})
        session.add(run)
        session.commit()
        for index, value in enumerate(values):
            run.context_json = {"research_working_sets": {iid: value}}
            session.commit()
            before = deepcopy(run.context_json)
            expected = {key: item for key, item in value.items() if key in wanted} if isinstance(value, dict) else value
            observed.clear()
            assert research_dossier.latest_working_set(session, iid, actor=actor, summary_only=True) == expected
            if index == 0 and session.get_bind().dialect.name == "postgresql":
                # Even an excluded source containing NUL must not make the
                # selected database result hydrate the complete original.
                assert observed == [expected]
                assert len(json.dumps(observed, ensure_ascii=False).encode()) < 500
            assert research_dossier.latest_working_set(session, iid, actor=actor) == value
            session.refresh(run)
            assert run.context_json == before
        topic.visibility = "private"
        session.flush()
        assert research_dossier.latest_working_set(session, iid, actor=actor, summary_only=True) is None
        topic.visibility = "team"
        session.flush()
        assert research_dossier.latest_working_set(session, iid, actor={"team_id": "another"}, summary_only=True) is None


def test_working_set_reader_preserves_status_and_originals(client, monkeypatch):
    check_working_set_summary(monkeypatch)


@pytest.mark.postgresql_integration
def test_postgres_working_set_reader_projects_before_transfer(postgres_watchlist_env, monkeypatch):
    check_working_set_summary(monkeypatch)


def test_browser_dossier_returns_review_status_while_model_read_keeps_originals(client, monkeypatch):
    from .test_sector_research import seed_sector
    from watchlist_app.services import sector_research
    seed_sector(client, monkeypatch)
    value = {"mode": "incremental", "cutoff": "2026-10-14T00:00:00Z",
             "selected_theme_ids": ["one", "two"], "reviewed_theme_ids": ["one"],
             "reasons": [{"original": "retained evidence" * 1000}]}
    with get_session_factory()() as session:
        run, _ = sector_research.begin_run(session, ["xlk"])
        run.status = "completed"
        run.context_json = {**run.context_json, "research_working_sets": {"xlk": value}}
        session.commit()
    expected = {key: item for key, item in value.items() if key != "reasons"}
    for query in ("current_only=true", "include_history=true"):
        response = client.get(f"/api/research/instruments/xlk/dossier?{query}")
        assert response.status_code == 200, response.text
        assert response.json()["working_set"] == expected
    with get_session_factory()() as session:
        assert research_dossier.read_dossier(session, "xlk")["working_set"] == value
