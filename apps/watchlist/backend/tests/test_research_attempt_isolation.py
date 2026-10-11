"""A retry reuses a research ID, but never an old task's write authority."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from studio_identity import current_principal, principal_context

from watchlist_app.api.routes import risk_officer, sector_research, workbench
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner as runner
from watchlist_app.services import sector_research as sector_service
from .test_account_research_access import accounts, as_user


def save_run(*, attempt=1, status="running", actor=None):
    actor = actor or current_principal()
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="attempt-topic", title="Attempt isolation",
            visibility="private", created_by_user_id=actor.user_id))
        session.flush()
        session.add(ResearchEntry(entry_id="attempt-run", topic_id="attempt-topic", kind="analysis",
            status=status, title="Attempt isolation", context_json={"research_run": True,
                "research_actor": actor.to_dict(), "execution": {"attempt": attempt},
                "cutoff": "2026-10-01T00:00:00Z", "catalogue": [], "retained": "original facts"}))
        session.commit()


def state():
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "attempt-run")
        return run.status, run.body, deepcopy(run.context_json)


@pytest.mark.parametrize("status,stored_attempt", [("queued", 1), ("running", 2), ("draft", 2)])
def test_old_or_unversioned_credentials_cannot_read_or_write_a_retried_run(accounts, status, stored_attempt):
    client, principals, _ = accounts
    save_run(attempt=stored_attempt, status=status, actor=principals["alice"])
    before = state()
    for scope in ({"kind": "run", "id": "attempt-run"},
                  {"kind": "run", "id": "attempt-run", "attempt": "1"}):
        principals["old-run"] = replace(principals["alice"], credential="old-run", resource_scope=scope)
        as_user(client, "old-run")
        assert client.get("/api/research/runs/attempt-run/context").status_code == 409
        assert client.post("/api/research/runs/attempt-run/sector-evidence",
            json={"operation": "search", "query": "late old query"}).status_code == 409
    assert state() == before
    principals["current-run"] = replace(principals["alice"], credential="current-run",
        resource_scope={"kind": "run", "id": "attempt-run", "attempt": "2"})
    response = as_user(client, "current-run").get("/api/research/runs/attempt-run/context")
    assert response.status_code == (409 if status == "draft" else 200)
    # Human access to retained history is independent of ended task credentials.
    assert as_user(client, "alice").get("/api/research/runs/attempt-run/context").status_code == 200


@pytest.mark.parametrize("handler", ["sector_evidence", "market_inputs", "conversation_tools", "risk_draft"])
def test_write_rechecks_attempt_after_lock_even_when_session_loaded_old_row(client, handler):
    actor = current_principal()
    save_run(actor=actor)
    old = replace(actor, resource_scope={"kind": "run", "id": "attempt-run", "attempt": "1"})
    with get_session_factory()() as stale_session:
        cached = stale_session.get(ResearchEntry, "attempt-run")
        assert cached.context_json["execution"]["attempt"] == 1
        with get_session_factory()() as other:
            current = other.get(ResearchEntry, "attempt-run")
            current.context_json = {**current.context_json, "execution": {"attempt": 2},
                "risk_run": handler == "risk_draft", "risk_inputs": {"scope": {"kind": "watchlist"}}}
            other.commit()
        with principal_context(old), pytest.raises(HTTPException) as rejected:
            if handler == "sector_evidence":
                sector_research.current_run(stale_session, "attempt-run", writing=True)
            elif handler == "market_inputs":
                sector_research.market_run(stale_session, "attempt-run")
            elif handler == "conversation_tools":
                workbench.research_tool("attempt-run", workbench.ResearchToolInput(tool="instruments"), stale_session)
            else:
                risk_officer._running_risk_run(stale_session, "attempt-run")
        assert rejected.value.status_code == 409
        stale_session.expire(cached, ["context_json"])  # The risk route reads its small projection, not this stale original.
        assert cached.context_json["execution"]["attempt"] == 2
    assert state()[2]["execution"]["attempt"] == 2


@pytest.mark.parametrize("status,stored_attempt", [("queued", 1), ("running", 2)])
def test_late_failure_or_credential_renewal_cannot_fail_new_attempt(client, monkeypatch, status, stored_attempt):
    save_run(attempt=stored_attempt, status=status)
    before = state()
    runner._fail_run("attempt-run", "old process failure", attempt=1,
        runtime_error={"type": "OldProcessFailure"})
    monkeypatch.setattr(runner, "issue_delegation", lambda *a, **k: pytest.fail("Cannot renew an old task grant"))
    with pytest.raises(HTTPException) as rejected:
        runner.authorize_run(current_principal(), "attempt-run", attempt=1)
    assert rejected.value.status_code == 409
    assert state() == before
    runner._fail_run("attempt-run", "current process failure", attempt=2,
        runtime_error={"type": "CurrentProcessFailure"})
    assert state()[0] == "failed"
    assert state()[2]["runtime_error"]["type"] == "CurrentProcessFailure"


@pytest.mark.parametrize("returncode", [0, 1])
def test_late_process_success_or_failure_leaves_current_attempt_untouched(client, monkeypatch, returncode):
    save_run(attempt=0, status="queued")
    monkeypatch.setattr(sector_service, "prepare_run", lambda *_: None)
    monkeypatch.setattr(sector_service, "prepare_initialization_searches", lambda *_: None)
    newer = []
    def communicate(**_kwargs):
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, "attempt-run")
            assert run.context_json["execution"]["attempt"] == 1
            run.context_json = {**run.context_json, "execution": {"attempt": 2}, "new_attempt_input": "retained"}
            run.body = "new attempt remains in progress"
            session.commit()
        newer.append(state())
        return '{"answer":"late answer from old attempt"}', ""
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: SimpleNamespace(
        returncode=returncode, communicate=communicate))
    runner.run_analysis("attempt-run")
    assert newer and state() == newer[0]


@pytest.mark.parametrize("prepare", ["research", "risk"])
def test_late_preparation_cannot_replace_new_attempt_inputs(client, prepare):
    from watchlist_app.services import risk_officer as risk_service
    actor = current_principal()
    save_run(attempt=2, actor=actor)
    before = state()
    with principal_context(replace(actor, resource_scope={"kind": "run", "id": "attempt-run", "attempt": "1"})):
        with pytest.raises(HTTPException) as rejected:
            (sector_service if prepare == "research" else risk_service).prepare_run("attempt-run")
    assert rejected.value.status_code == 409
    assert state() == before


def test_dispatch_failure_consumes_reserved_number_before_another_retry(client):
    from watchlist_app.services.research_access import run_attempt
    save_run(attempt=1, status="queued")  # Attempt 2 is reserved by this queue item.
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "attempt-run")
        run.context_json = {**run.context_json, "sector_run": True}
        session.commit()
    runner._fail_run("attempt-run", "dispatch failed before worker start", attempt=2,
        runtime_error={"type": "AuthorizationUnavailable"})
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "attempt-run")
        assert run.context_json["execution"]["attempt"] == 2
        assert runner.queue_retry(session, run, manual=True)
        assert run_attempt(run) == 3
        assert run.context_json["execution"]["failures"][-1]["attempt"] == 2
        session.commit()
    before = state()
    runner._fail_run("attempt-run", "late failure from dispatch 2", attempt=2)
    assert state() == before


def test_lease_loss_before_worker_start_consumes_reserved_attempt_with_bounded_recovery(client):
    from watchlist_app.services import research_execution as queue
    from watchlist_app.services.research_access import require_entry_access, run_attempt
    save_run(attempt=1, status="queued")
    queue.enqueue_run(current_principal(), "attempt-run")
    now = datetime.now(UTC)
    first = queue.claim_next("before-start", now=now)
    assert first.attempt == 2
    assert queue.recover_expired(now=now + timedelta(minutes=6)) == 1
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "attempt-run")
        assert run_attempt(run) == 3
        assert run.context_json["execution"]["failures"][-1]["attempt"] == 2
        old_principal = replace(current_principal(), resource_scope={"kind": "run", "id": run.entry_id, "attempt": "2"})
        with principal_context(old_principal), pytest.raises(HTTPException) as error:
            require_entry_access(session, run, tool_write=True)
        assert error.value.status_code == 409
    replacement = queue.claim_next("next", now=now + timedelta(minutes=6))
    assert replacement.attempt == 3
    assert queue.recover_expired(now=now + timedelta(minutes=12)) == 0
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "attempt-run")
        assert run.status == "failed"
        assert run.context_json["runtime_error"]["type"] == "WorkerLeaseExpired"
