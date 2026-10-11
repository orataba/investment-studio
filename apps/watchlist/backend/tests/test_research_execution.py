"""HTTP restarts, competing workers, and lease loss cannot duplicate publication."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from studio_identity import IdentityError, current_principal

from watchlist_app.db.models.research_execution import ResearchExecution
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_execution as queue
from watchlist_app.services import research_runner as runner


def save_run(run_id, *, context=None):
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id=run_id, title="Queue", created_by_user_id=current_principal().user_id))
        session.flush()
        session.add(ResearchEntry(entry_id=run_id, topic_id=run_id, kind="analysis", title="Queue", status="queued",
            context_json={"research_run": True, "research_actor": current_principal().to_dict(), **(context or {})}))
        session.commit()
    queue.enqueue_run(current_principal(), run_id)


def running(claim):
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, claim.run_id)
        run.status = "running"
        run.context_json = {**run.context_json, "execution": {"attempt": claim.attempt, "stage": "generation"}}
        session.commit()


def test_competing_workers_share_one_capacity_and_enqueue_is_idempotent(client):
    save_run("a")
    save_run("b")
    assert queue.enqueue_run(current_principal(), "a") is False
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(queue.claim_next, ["owner-one", "owner-two"]))
    accepted = [claim for claim in claims if claim]
    assert len(accepted) == 1
    assert "fixture-run" not in repr(accepted[0])
    assert queue.claim_next("owner-three") is None
    assert queue.heartbeat(accepted[0])


def test_expired_owner_is_fenced_and_checkpoint_retained_with_bounded_recovery(client):
    save_run("resume", context={"sector_run": True, "input_snapshot_cutoff": "2026-10-09T00:00:00Z",
        "submitted_draft": {"reviews": []}, "web_evidence": [{"review": {"checkpoint": "retained"}}]})
    start = datetime.now(UTC)
    old_claims = []
    for attempt in range(1, 4):
        claim = queue.claim_next(f"owner-{attempt}", now=start)
        assert claim.attempt == attempt
        old_claims.append(claim)
        running(claim)
        start += timedelta(seconds=queue.get_settings().research_worker_lease_seconds + 1)
        assert queue.recover_expired(now=start) == (1 if attempt < 3 else 0)
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, "resume")
            assert run.context_json["submitted_draft"] == {"reviews": []}
            assert run.context_json["web_evidence"][0]["review"]["checkpoint"] == "retained"
            assert run.status == ("queued" if attempt < 3 else "failed")
        assert not queue.heartbeat(claim, now=start)
        queue.finish_claim(claim)  # A late worker cannot finish/revoke its replacement.
    assert queue.claim_next("four", now=start) is None
    with get_session_factory()() as session:
        job = session.get(ResearchExecution, "resume")
        assert job.status == "finished" and job.delegation_token is None


def test_committed_result_is_not_replayed_after_worker_dies_before_ack(client):
    save_run("published")
    start = datetime.now(UTC)
    claim = queue.claim_next("owner", now=start)
    running(claim)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "published")
        run.status, run.body = "completed", "Published once"
        session.commit()
    assert queue.recover_expired(now=start + timedelta(minutes=6)) == 0
    assert queue.claim_next("next") is None
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "published").body == "Published once"
        assert session.get(ResearchExecution, "published").status == "finished"


def test_expired_user_grant_fails_without_adopting_service_identity(client, monkeypatch):
    save_run("expired-auth")
    claim = queue.claim_next("owner")
    monkeypatch.setattr(queue, "resolve_token", lambda *a: (_ for _ in ()).throw(IdentityError(401, "expired")))
    monkeypatch.setattr(runner, "run_analysis", lambda *a, **k: pytest.fail("Expired authority must not run a model"))
    queue.execute_claim(claim, Event())
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, claim.run_id).context_json["runtime_error"]["type"] == "AuthorizationUnavailable"
        assert session.get(ResearchExecution, claim.run_id).delegation_token is None


def test_worker_shutdown_preserves_queue_grant_for_new_attempt(client, monkeypatch):
    save_run("shutdown")
    claim = queue.claim_next("owner")
    def interrupted(*args, **kwargs):
        running(claim)
        raise runner.ResearchExecutionInterrupted()
    monkeypatch.setattr(runner, "run_analysis", interrupted)
    queue.execute_claim(claim, Event())
    assert queue.recover_expired() == 1
    next_claim = queue.claim_next("new-owner")
    assert next_claim.attempt == claim.attempt + 1


def test_accepted_submission_stops_acknowledgement_loop_without_changing_evidence(monkeypatch):
    class Process:
        args = ["offline-harness"]
        returncode = None
        def communicate(self, timeout=None):
            if self.returncode is None:
                raise runner.subprocess.TimeoutExpired(self.args, timeout)
            return "", ""
    process = Process()
    monkeypatch.setattr(runner, "_stop_process_group", lambda item: setattr(item, "returncode", -15))
    assert runner._wait_harness(process, cancellation=Event(), accepted=lambda: True) == ("", "", 0)


def test_api_lifespan_does_not_abort_or_consume_durable_work(client, monkeypatch):
    save_run("api-restart")
    claim = queue.claim_next("worker")
    running(claim)
    from fastapi.testclient import TestClient
    from watchlist_app.main import app
    with TestClient(app):
        pass
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, claim.run_id).status == "running"
        assert session.get(ResearchExecution, claim.run_id).owner_id == "worker"


def test_recovered_attempt_reauthorizes_original_subject_and_executes(client, monkeypatch):
    from studio_identity import current_principal
    from watchlist_app.services.research_access import require_entry_access
    save_run("reauthorize")
    old = queue.claim_next("old")
    running(old)
    assert queue.recover_expired(now=datetime.now(UTC) + timedelta(minutes=6)) == 1
    replacement = queue.claim_next("replacement")
    executed = []
    def complete(run_id, **kwargs):
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id)
            require_entry_access(session, run, tool_write=True)
            assert current_principal().resource_scope["attempt"] == "2"
            run.status = "completed"
            session.commit()
        executed.append(run_id)
    monkeypatch.setattr(runner, "_run_analysis", complete)
    queue.execute_claim(replacement, Event())
    assert executed == ["reauthorize"]
    with get_session_factory()() as session:
        assert session.get(ResearchExecution, "reauthorize").status == "finished"


def test_unexpected_preparation_dispatch_failure_has_terminal_cause(client, monkeypatch):
    save_run("dispatch-error")
    claim = queue.claim_next("worker")
    monkeypatch.setattr(queue, "resolve_token", lambda *args: (_ for _ in ()).throw(RuntimeError("private detail")))
    queue.execute_claim(claim, Event())
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, claim.run_id)
        assert run.status == "failed"
        assert run.context_json["runtime_error"]["type"] == "RuntimeError"
        assert "private detail" not in run.body
        assert session.get(ResearchExecution, claim.run_id).delegation_token is None


def test_accepted_submission_survives_nonzero_acknowledgement_exit():
    class Process:
        returncode = 1
        def communicate(self, timeout=None):
            return "", "dsh: NETWORK: final acknowledgement failed"
    assert runner._wait_harness(Process(), cancellation=Event(), accepted=lambda: True)[2] == 0
    assert runner._wait_harness(Process(), cancellation=Event(), accepted=lambda: False)[2] == 1


def test_dispatch_crash_gap_terminates_only_abandoned_unqueued_requests(client):
    save_run("healthy-queued")
    with get_session_factory()() as session:
        session.add(ResearchEntry(entry_id="orphan", topic_id="healthy-queued", kind="analysis", title="Interrupted dispatch",
            status="queued", context_json={"research_actor": current_principal().to_dict()}))
        session.commit()
    queue.recover_undispatched()
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "orphan").status == "queued"
    queue.recover_undispatched(now=datetime.now(UTC) + timedelta(minutes=6))
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "healthy-queued").status == "queued"
        orphan = session.get(ResearchEntry, "orphan")
        assert orphan.status == "failed"
        assert orphan.context_json["runtime_error"]["type"] == "DispatchInterrupted"


def test_failed_terminal_write_keeps_lease_recoverable_and_grant_retained(client, monkeypatch):
    save_run("terminal-write-error")
    claim = queue.claim_next("worker")
    monkeypatch.setattr(queue, "resolve_token", lambda *args: (_ for _ in ()).throw(IdentityError(401, "expired")))
    monkeypatch.setattr(runner, "_fail_authorization", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("write failed")))
    with pytest.raises(RuntimeError, match="write failed"):
        queue.execute_claim(claim, Event())
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, claim.run_id).status == "queued"
        job = session.get(ResearchExecution, claim.run_id)
        assert job.status == "running" and job.delegation_token
    assert queue.recover_expired() == 1
    assert queue.claim_next("replacement").attempt == 2


def test_accepted_risk_checkpoint_recovers_publication_without_another_model(client, monkeypatch):
    from watchlist_app.db.models.workbench import ResearchRiskReadState
    from watchlist_app.services import risk_officer
    frozen = {"scope": {"instrument_id": "same-input"}}
    save_run("risk-checkpoint", context={"risk_run": True, "risk_inputs": frozen,
        "input_snapshot_cutoff": "2026-10-09T00:00:00Z"})
    first = queue.claim_next("old-owner")
    running(first)
    accepted = {"summary": "Validated proposal", "priorities": [], "limitations": []}
    with get_session_factory()() as session:
        session.add(ResearchRiskReadState(run_id=first.run_id, attempt=1,
            submitted_review=accepted, delivered_pages=[["same-input", "overview", 0]]))
        session.commit()
    assert queue.recover_expired(now=datetime.now(UTC) + timedelta(minutes=6)) == 1
    replacement = queue.claim_next("new-owner")
    monkeypatch.setattr(risk_officer, "prepare_run", lambda *args: pytest.fail("Frozen accepted input cannot be rebound"))
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Accepted proposal needs no new model"))
    published = []
    def publish(session, run, proposal):
        assert current_principal().resource_scope["attempt"] == "2"
        assert run.context_json["risk_inputs"] == frozen
        assert proposal == accepted
        assert run.context_json["execution"]["stage"] == "publication"
        # The receipts still belong to the original generation; no new session
        # is credited with reading them.
        assert session.get(ResearchRiskReadState, run.entry_id).attempt == 1
        run.status = "completed"
        published.append(run.entry_id)
    monkeypatch.setattr(risk_officer, "apply_result", publish)
    queue.execute_claim(replacement, Event())
    assert published == [first.run_id]
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, first.run_id).status == "completed"
        assert session.get(ResearchExecution, first.run_id).status == "finished"
    queue.recover_expired(now=datetime.now(UTC) + timedelta(days=1))
    assert published == [first.run_id]


def test_worker_does_not_claim_during_dependency_restart(client, monkeypatch):
    from watchlist_app import research_worker as worker
    stop = Event()
    monkeypatch.setattr(worker, "recover_undispatched", lambda: None)
    monkeypatch.setattr(worker, "recover_expired", lambda: None)
    monkeypatch.setattr(worker, "claim_next", lambda *args: pytest.fail("API restart must not consume an attempt"))
    def unavailable():
        stop.set()
        return False
    monkeypatch.setattr(worker, "dependencies_ready", unavailable)
    worker.run_worker(stop)
