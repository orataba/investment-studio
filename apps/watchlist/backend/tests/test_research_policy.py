"""Real admission checks: no credentials or model calls are needed."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
import os
from threading import Event

import pytest
from fastapi import HTTPException
from studio_identity import current_principal
from studio_runtime.model_work import ModelWorkPaused

from watchlist_app.core.settings import get_settings
from watchlist_app.db.models import InstrumentAttributeValue
from watchlist_app.db.models.research_execution import ResearchExecution
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_execution as queue, research_runner as runner, research_policy as policy
from .test_sector_research import seed_sector


@pytest.fixture
def invested(client, monkeypatch):
    seed_sector(client, monkeypatch)
    monkeypatch.setattr(get_settings(), "research_automatic_enabled", True)
    with get_session_factory()() as session:
        for iid in ("xlk", "xlf"):
            session.add(InstrumentAttributeValue(instrument_id=iid, attribute_key="coverage_status",
                value_json="Invested", adopted_at=datetime.now(UTC)))
        session.commit()
    return client


def save_run(run_id, iid="xlk", **context):
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id=run_id, title="Policy", created_by_user_id=current_principal().user_id))
        session.flush()
        session.add(ResearchEntry(entry_id=run_id, topic_id=run_id, kind="analysis", title="Policy", status="queued",
            context_json={"sector_run": True, "instrument_ids": [iid], "scheduled": True,
                          "research_actor": current_principal().to_dict(), **context}))
        session.commit()


def pause():
    Path(os.environ["INVESTMENT_STUDIO_MODEL_WORK_CONTROL_FILE"]).write_text('{"paused":true}')


def set_status(session, instrument_id, value):
    session.add(InstrumentAttributeValue(instrument_id=instrument_id, attribute_key="coverage_status",
        value_json=value, adopted_at=datetime.now(UTC) + timedelta(seconds=1)))
    session.commit()


def test_all_same_class_invested_assets_are_eligible_without_an_allowlist(invested):
    with get_session_factory()() as session:
        assert policy.automatic_research_ids(session) == ["xlf", "xlk"]
        policy.require_instrument_research(session, ["xlf", "xlk"], scheduled=True)
        assert all(row["eligible"] for row in policy.research_capability(session)["instruments"]
                   if row["instrument_id"] in {"xlf", "xlk"})


@pytest.mark.parametrize("status", ["Watch", "Proposed", "Paused", "Exited"])
def test_latest_non_invested_status_excludes_a_formerly_invested_asset(invested, status):
    with get_session_factory()() as session:
        set_status(session, "xlf", status)
        assert policy.automatic_research_ids(session) == ["xlk"]
        with pytest.raises(HTTPException) as error:
            policy.require_instrument_research(session, ["xlf"])
        assert error.value.status_code == 409


@pytest.mark.parametrize("boundary", ["canonical_archived", "detail_inactive", "mismatched_type"])
def test_invested_status_does_not_override_inactive_registration(invested, boundary):
    from investment_studio_instrument_core.db_models import Instrument
    from watchlist_app.db.models import InstrumentDetail
    with get_session_factory()() as session:
        if boundary == "canonical_archived":
            session.get(Instrument, "xlf").lifecycle_state_json = {"status": "archived"}
        elif boundary == "detail_inactive":
            session.get(InstrumentDetail, "xlf").is_active = False
        else:
            session.get(InstrumentDetail, "xlf").instrument_type = "equity"
        session.commit()
        assert policy.automatic_research_ids(session) == ["xlk"]
        with pytest.raises(HTTPException):
            policy.require_instrument_research(session, ["xlf"])


def test_latest_status_revision_wins_when_adoption_timestamps_match(invested):
    instant = datetime.now(UTC) + timedelta(seconds=1)
    with get_session_factory()() as session:
        for value in ("Invested", "Exited"):
            session.add(InstrumentAttributeValue(instrument_id="xlf", attribute_key="coverage_status",
                value_json=value, adopted_at=instant))
            session.flush()
        session.commit()
        assert policy.automatic_research_ids(session) == ["xlk"]
        assert policy.research_capability(session, "xlf")["instruments"][0]["eligible"] is False


def test_local_scheduler_disabled_does_not_disable_explicit_invested_request(invested, monkeypatch):
    with get_session_factory()() as session:
        monkeypatch.setattr(get_settings(), "research_automatic_enabled", False)
        assert policy.automatic_research_ids(session) == []
        with pytest.raises(HTTPException):
            policy.require_instrument_research(session, ["xlk"], scheduled=True)
        policy.require_instrument_research(session, ["xlk"])


def test_paused_requests_save_no_run_and_theme_can_still_be_saved(invested, monkeypatch):
    from watchlist_app.api.routes import sector_research as routes
    monkeypatch.setattr(routes, "harness_available", lambda: True)
    pause()
    response = invested.post("/api/sector-research/runs", json={"instrument_ids": ["xlk"]})
    assert response.status_code == 409 and "暂停" in response.json()["detail"]
    theme = invested.post("/api/research/instruments/xlk/themes", json={"title": "Liquidity", "question": "Does liquidity change?"})
    assert theme.status_code == 201, theme.text
    assert theme.json()["research_status"] == "paused"
    with get_session_factory()() as session:
        from sqlalchemy import select
        assert session.scalar(select(ResearchEntry).where(ResearchEntry.kind == "analysis")) is None


def test_excluded_scope_cannot_enqueue_or_retry(invested, monkeypatch):
    with get_session_factory()() as session:
        set_status(session, "xlf", "Proposed")
    save_run("excluded", "xlf")
    monkeypatch.setattr(runner, "authorize_run", lambda *a, **kw: pytest.fail("No grant for excluded assets"))
    with pytest.raises(ModelWorkPaused):
        queue.enqueue_run(current_principal(), "excluded")
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "excluded")
        run.status = "failed"
        session.commit()
        with pytest.raises(ModelWorkPaused):
            runner.queue_retry(session, run, manual=True)


def test_expired_excluded_run_is_paused_without_replaying_or_losing_draft(invested, monkeypatch):
    save_run("old", submitted_draft={"reviews": []})
    queue.enqueue_run(current_principal(), "old")
    claim = queue.claim_next("owner")
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "old")
        run.status = "running"
        run.context_json = {**run.context_json, "execution": {"attempt": claim.attempt}}
        session.commit()
    with get_session_factory()() as session:
        set_status(session, "xlk", "Exited")
    assert queue.recover_expired(now=datetime.now(UTC) + timedelta(minutes=6)) == 0
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "old")
        assert run.status == "paused" and run.completed_at is None
        assert run.context_json["submitted_draft"] == {"reviews": []}
        assert session.get(ResearchExecution, "old").delegation_token is None
    assert not queue.heartbeat(claim)
    assert queue.claim_next("new-owner") is None


def test_switch_between_claim_and_execute_never_calls_model(invested, monkeypatch):
    save_run("raced")
    queue.enqueue_run(current_principal(), "raced")
    claim = queue.claim_next("owner")
    pause()
    monkeypatch.setattr(runner, "run_analysis", lambda *a, **kw: pytest.fail("Paused model start"))
    queue.execute_claim(claim, Event())
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "raced").status == "paused"


def test_actual_launch_rechecks_policy_after_preparation(invested, monkeypatch):
    save_run("before-spawn")
    from watchlist_app.services import sector_research
    monkeypatch.setattr(sector_research, "prepare_run", lambda *a: pause())
    monkeypatch.setattr(sector_research, "prepare_initialization_searches", lambda *a: None)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: pytest.fail("Paused Popen"))
    with pytest.raises(runner.ResearchExecutionInterrupted):
        runner._run_analysis("before-spawn", attempt=1)


@pytest.mark.parametrize("finished_job", [False, True])
def test_pause_closes_aged_undispatched_run_without_replaying(invested, monkeypatch, finished_job):
    now = datetime.now(UTC)
    old = now - timedelta(seconds=get_settings().research_worker_lease_seconds + 1)
    evidence = {"submitted_draft": {"reviews": []}, "execution": {"attempt": 2, "failures": [{"attempt": 1}]}}
    save_run("orphan", **evidence)
    with get_session_factory()() as session:
        session.get(ResearchEntry, "orphan").updated_at = old
        if finished_job:
            session.add(ResearchExecution(run_id="orphan", attempt=1, status="finished", queued_at=old))
        session.commit()
    pause()
    monkeypatch.setattr(runner, "authorize_run", lambda *a, **kw: pytest.fail("Do not invent a grant"))
    monkeypatch.setattr(runner, "run_analysis", lambda *a, **kw: pytest.fail("Do not replay the orphan"))
    assert queue.pause_pending(now=now) == 1
    assert queue.pause_pending(now=now) == 0
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "orphan")
        assert run.status == "paused" and run.completed_at is None
        assert run.context_json["submitted_draft"] == evidence["submitted_draft"]
        execution = run.context_json["execution"]
        assert execution["stage"] == "paused" and execution["attempt"] == 2
        assert execution["failures"] == evidence["execution"]["failures"]
        assert execution["pause_reason"] and execution["paused_at"]
        job = session.get(ResearchExecution, "orphan")
        assert (job is not None) == finished_job
        if job:
            assert job.status == "finished" and job.attempt == 1 and job.delegation_token is None
    Path(os.environ["INVESTMENT_STUDIO_MODEL_WORK_CONTROL_FILE"]).write_text('{"paused":false}')
    queue.recover_undispatched(now=now + timedelta(minutes=10))
    assert queue.claim_next("after-resume") is None
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "orphan").status == "paused"


def test_pause_leaves_fresh_dispatch_and_completed_result_unchanged(invested):
    now = datetime.now(UTC)
    old = now - timedelta(seconds=get_settings().research_worker_lease_seconds + 1)
    save_run("fresh", submitted_draft={"still_dispatching": True})
    save_run("completed", submitted_draft={"accepted": True})
    with get_session_factory()() as session:
        fresh = session.get(ResearchEntry, "fresh")
        fresh.updated_at = now
        completed = session.get(ResearchEntry, "completed")
        completed.status, completed.body, completed.completed_at, completed.updated_at = "completed", "Published", old, old
        original_context = completed.context_json.copy()
        # A lost final acknowledgement may leave an old execution to clean up.
        session.add(ResearchExecution(run_id="completed", attempt=1, status="running", queued_at=old,
            lease_expires_at=old, owner_id="expired"))
        session.commit()
    pause()
    assert queue.pause_pending(now=now) == 1  # only the terminal run's expired execution
    with get_session_factory()() as session:
        fresh = session.get(ResearchEntry, "fresh")
        assert fresh.status == "queued" and "execution" not in fresh.context_json
        completed = session.get(ResearchEntry, "completed")
        assert completed.status == "completed" and completed.body == "Published"
        assert queue._utc(completed.completed_at) == old
        assert completed.context_json == original_context
        assert session.get(ResearchExecution, "completed").status == "finished"
