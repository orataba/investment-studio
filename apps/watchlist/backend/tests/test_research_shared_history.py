"""Preparation may read shared history without widening a delegated run token."""
from dataclasses import replace

import pytest
from fastapi import HTTPException
from studio_identity import Principal, principal_context

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_run_context import load_run_fields, load_shared_history_fields
from watchlist_app.services.research_fmp_scope import latest_research_focus
from .test_research_fmp_scope import add_research, clock


def seed_current(session):
    actor = Principal(user_id="analyst", display_name="Analyst", team_id="default",
                      resource_scope={"kind": "run", "id": "current", "attempt": "1"})
    session.add(ResearchTopic(topic_id="current", title="Current", visibility="team", team_id="default"))
    session.flush()
    session.add(ResearchEntry(entry_id="current", topic_id="current", title="Current", kind="analysis",
        status="running", team_id="default", created_at=clock(7), context_json={"sector_run": True,
            "instrument_ids": ["xlk"], "research_actor": {"kind": "user", "user_id": "analyst", "team_id": "default"},
            "execution": {"attempt": 1}}))
    session.commit()
    return actor


def test_delegated_preparation_reads_previous_cutoff_and_focus_without_other_run_access(client):
    with get_session_factory()() as session:
        add_research(session, "previous", ["fmp:AAA"])
        previous = session.get(ResearchEntry, "previous")
        previous.context_json = {**previous.context_json, "input_snapshot_cutoff": clock(3).isoformat()}
        add_research(session, "ordinary-team-conversation", ["fmp:NOT_A_RESEARCH_FOCUS"], day=6)
        ordinary = session.get(ResearchEntry, "ordinary-team-conversation")
        ordinary.context_json = {**ordinary.context_json, "sector_run": False, "risk_run": True}
        actor = seed_current(session)
        with principal_context(actor):
            fields = load_shared_history_fields(session, "previous", "xlk", {"input_snapshot_cutoff"})
            assert fields.context_json == {"input_snapshot_cutoff": clock(3).isoformat()}
            assert latest_research_focus(session, "xlk", clock(8)) == ["AAA"]
            with pytest.raises(HTTPException) as error:
                load_run_fields(session, "previous", {"input_snapshot_cutoff"}, summary_only=True)
            assert error.value.status_code == 403


@pytest.mark.parametrize("excluded", ["private", "portfolio", "historical_portfolio", "other_team", "other_instrument"])
def test_shared_preparation_rejects_private_or_unrelated_history(client, excluded):
    with get_session_factory()() as session:
        add_research(session, "excluded", ["fmp:SECRET"],
            visibility="private" if excluded == "private" else "team",
            portfolio="private-account" if excluded == "portfolio" else None,
            team="other" if excluded == "other_team" else "default")
        if excluded == "historical_portfolio":
            session.add(ResearchEntry(entry_id="binding", topic_id="excluded", kind="note", title="Private",
                                      context_json={"portfolio_id": "private-account"}))
        if excluded == "other_instrument":
            row = session.get(ResearchEntry, "excluded")
            row.context_json = {**row.context_json, "instrument_ids": ["other"]}
        actor = seed_current(session)
        with principal_context(actor), pytest.raises(HTTPException) as error:
            load_shared_history_fields(session, "excluded", "xlk", {"cutoff"})
        assert error.value.status_code == 404


def test_shared_preparation_revalidates_current_subject_attempt_and_instrument(client):
    with get_session_factory()() as session:
        add_research(session, "previous", ["fmp:AAA"])
        actor = seed_current(session)
        for changed, instrument, expected in [
            (replace(actor, user_id="other"), "xlk", 403),
            (replace(actor, resource_scope={"kind": "run", "id": "current", "attempt": "2"}), "xlk", 409),
            (actor, "other", 403),
        ]:
            with principal_context(changed), pytest.raises(HTTPException) as error:
                load_shared_history_fields(session, "previous", instrument, {"cutoff"})
            assert error.value.status_code == expected
        with principal_context(actor), pytest.raises(ValueError):
            load_shared_history_fields(session, "previous", "xlk", {"web_evidence"})
