"""The deployed PostgreSQL gate and JSON projection boundary, without model calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from watchlist_app.db.models.research_execution import ResearchExecution
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_execution as queue, research_runner as runner
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


def test_postgres_global_claim_and_runner_projection_preserve_retained_nul(postgres_watchlist_env, monkeypatch):
    monkeypatch.setattr(queue.get_settings(), "research_worker_concurrency", 1)
    now = datetime.now(UTC)
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="durable-queue", title="Queue", visibility="team"))
        session.flush()
        for name in ("first", "second"):
            session.add(ResearchEntry(entry_id=name, topic_id="durable-queue", kind="analysis", title="Queue",
                status="queued", context_json={"execution": {"attempt": 0}, "submitted_draft": {"reviews": []},
                    "web_evidence": [{"source_id": "original", "text": "retained\x00source"}]}))
        session.flush()
        for name in ("first", "second"):
            session.add(ResearchExecution(run_id=name, attempt=1, status="queued", queued_at=now,
                delegation_token="offline-test-grant"))
        session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(queue.claim_next, ["worker-a", "worker-b"]))
    claimed = [value for value in claims if value is not None]
    assert len(claimed) == 1
    assert queue.claim_next("worker-c") is None
    claim = claimed[0]
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, claim.run_id)
        run.status = "running"
        run.context_json = {**run.context_json, "execution": {"attempt": 1}}
        session.commit()
    def reject_full_entry(*_):
        pytest.fail("Runner metadata must not load the full evidence row")
    event.listen(ResearchEntry, "load", reject_full_entry)
    try:
        assert runner._current_attempt(claim.run_id) == 1
        assert runner._accepted_submission(claim.run_id, 1, risk_run=False)
        assert not runner._accepted_submission(claim.run_id, 2, risk_run=False)
    finally:
        event.remove(ResearchEntry, "load", reject_full_entry)
    assert queue.recover_expired(now=now + timedelta(minutes=6)) == 1
    assert not queue.heartbeat(claim, now=now + timedelta(minutes=6))
    assert runner._current_attempt(claim.run_id) == 2
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, claim.run_id).context_json["web_evidence"][0]["text"] == "retained\x00source"
