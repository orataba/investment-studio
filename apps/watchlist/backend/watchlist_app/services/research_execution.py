"""Durable Watchlist queue with one database-wide capacity and fenced leases.

Only this private table retains a narrowly scoped, expiring queue grant. Human
login credentials are never stored here. A worker revalidates that original
subject through Home before preparing inputs or starting a model.
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text, update
from studio_identity import IdentityError, principal_context, resolve_token, revoke_delegation
from studio_runtime.model_work import ModelWorkPaused, model_work_status

from watchlist_app.core.settings import get_settings
from watchlist_app.db.models.research_execution import ResearchExecution
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_access import require_entry_access, run_attempt

QUEUE_GRANT_SECONDS = 24 * 3600  # Home's existing maximum, still bounded by the parent login.
MAX_INTERRUPTED_ATTEMPTS = 3
logger = logging.getLogger(__name__)


def _policy_reason(session, run):
    from watchlist_app.services.research_policy import require_run_policy
    try:
        require_run_policy(session, run)
    except ModelWorkPaused as error:
        return str(error)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return str(error.detail)
    return None


def _pause_business(run, attempt, reason):
    """Preserve unfinished inputs without changing an already terminal result."""
    if run and run.status in {"queued", "running"}:
        context = dict(run.context_json or {})
        execution = dict(context.get("execution") or {})
        execution.update(stage="paused", paused_at=utc_now().isoformat(), pause_reason=reason,
                         attempt=max(execution.get("attempt", 0), attempt))
        run.context_json = {**context, "execution": execution}
        run.status, run.body, run.completed_at = "paused", reason, None


def _pause_locked(run, job, reason):
    """End execution, preserving unfinished evidence and fencing old credentials."""
    _pause_business(run, job.attempt, reason)
    token = job.delegation_token
    job.status, job.owner_id, job.delegation_token = "finished", None, None
    job.lease_expires_at = None
    return token


def utc_now():
    return datetime.now(UTC)


def _utc(value):
    return value.replace(tzinfo=value.tzinfo or UTC) if value is not None else None


def _revoke(token):
    if token:
        try:
            revoke_delegation(token)
        except IdentityError:
            logger.warning("Research queue grant could not be revoked; its expiry and attempt fence remain enforced")


def enqueue_run(principal, run_id, *, attempt=None):
    """Persist dispatch before an HTTP request acknowledges the queued run."""
    from watchlist_app.services.research_runner import authorize_run, same_research_initiator
    with get_session_factory()() as session, principal_context(principal):
        run = session.get(ResearchEntry, run_id, with_for_update=True)
        require_entry_access(session, run)
        expected = run_attempt(run)
        if run.status != "queued" or (attempt is not None and expected != attempt):
            raise HTTPException(409, "研究任务已开始或已被新的执行替代")
        if not same_research_initiator(run.context_json, principal):
            raise HTTPException(403, "只能排队执行原发起人的研究任务")
        existing = session.get(ResearchExecution, run_id)
        if existing and existing.attempt == expected and existing.status in {"queued", "running"}:
            return False
        _require_dispatch_policy(session, run, expected)
    token = authorize_run(principal, run_id, attempt=expected, ttl_seconds=QUEUE_GRANT_SECONDS)
    previous_token = None
    keep_token = False
    try:
        with get_session_factory()() as session, principal_context(principal):
            run = session.get(ResearchEntry, run_id, with_for_update=True)
            require_entry_access(session, run)
            if run.status != "queued" or run_attempt(run) != expected:
                raise HTTPException(409, "研究任务已开始或已被新的执行替代")
            job = session.get(ResearchExecution, run_id, with_for_update=True)
            if job and job.attempt == expected and job.status in {"queued", "running"}:
                return False
            _require_dispatch_policy(session, run, expected)
            if job is None:
                job = ResearchExecution(run_id=run_id, attempt=expected, status="queued", queued_at=utc_now())
                session.add(job)
            else:
                previous_token = job.delegation_token
            job.attempt, job.status, job.delegation_token = expected, "queued", token
            job.owner_id, job.heartbeat_at, job.lease_expires_at = None, None, None
            job.queued_at = utc_now()
            session.commit()
            keep_token = True
    finally:
        if not keep_token:
            _revoke(token)
    _revoke(previous_token)
    return True


def _require_dispatch_policy(session, run, attempt):
    from watchlist_app.services.research_policy import require_run_policy
    try:
        require_run_policy(session, run)
    except ModelWorkPaused as error:
        # The request may have committed immediately before the host switch.
        # Preserve it as paused instead of leaving an undispatched queued row.
        _pause_business(run, attempt, str(error))
        session.commit()
        raise


@dataclass(frozen=True)
class Claim:
    run_id: str
    attempt: int
    owner_id: str
    # Keep out of repr/logging and API serializers.
    delegation_token: str = field(repr=False)


def claim_next(owner_id, *, now=None):
    """Admission across every worker process; manual and scheduled work share it."""
    now = now or utc_now()
    if model_work_status()["paused"]:
        return None
    with get_session_factory()() as session:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext('watchlist:research-capacity'))"))
        elif session.get_bind().dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        # Expired owners still occupy capacity until recovery fences their attempt.
        running = session.scalar(select(func.count()).select_from(ResearchExecution).where(ResearchExecution.status == "running"))
        if running >= get_settings().research_worker_concurrency:
            return None
        row = session.execute(select(ResearchExecution.run_id, ResearchExecution.attempt)
            .join(ResearchEntry, ResearchEntry.entry_id == ResearchExecution.run_id)
            .where(ResearchExecution.status == "queued", ResearchEntry.status == "queued")
            .order_by(ResearchExecution.queued_at, ResearchExecution.run_id).limit(1)).first()
        if row is None:
            return None
        # Lock order is always business run then private execution state.
        from watchlist_app.services.research_policy import run_policy_projection
        run = run_policy_projection(session, row.run_id, locking=True)
        job = session.get(ResearchExecution, row.run_id, with_for_update=True)
        if run is None or run.status != "queued" or job.status != "queued" or job.attempt != row.attempt:
            return None
        if not job.delegation_token:
            return None
        reason = _policy_reason(session, run)
        if reason:
            run = session.get(ResearchEntry, row.run_id, with_for_update=True)
            token = _pause_locked(run, job, reason)
            session.commit()
            _revoke(token)
            return None
        job.status, job.owner_id, job.heartbeat_at = "running", owner_id, now
        job.lease_expires_at = now + timedelta(seconds=get_settings().research_worker_lease_seconds)
        claim = Claim(job.run_id, job.attempt, owner_id, job.delegation_token)
        session.commit()
        return claim


def heartbeat(claim, *, now=None):
    now = now or utc_now()
    with get_session_factory()() as session:
        result = session.execute(update(ResearchExecution).where(
            ResearchExecution.run_id == claim.run_id, ResearchExecution.attempt == claim.attempt,
            ResearchExecution.status == "running", ResearchExecution.owner_id == claim.owner_id,
            ResearchExecution.lease_expires_at > now).values(
                heartbeat_at=now, lease_expires_at=now + timedelta(seconds=get_settings().research_worker_lease_seconds)))
        session.commit()
        return result.rowcount == 1


def finish_claim(claim):
    token = None
    with get_session_factory()() as session:
        status = session.scalar(select(ResearchEntry.status).where(
            ResearchEntry.entry_id == claim.run_id).with_for_update())
        job = session.get(ResearchExecution, claim.run_id, with_for_update=True)
        if job and job.status == "running" and job.owner_id == claim.owner_id and job.attempt == claim.attempt:
            if status in {"queued", "running"}:
                # A failed terminal write must not orphan the business run or
                # destroy its only remaining authority to finish/recover.
                job.lease_expires_at = utc_now()
                session.commit()
                return False
            token = job.delegation_token
            job.status, job.delegation_token, job.owner_id = "finished", None, None
            job.lease_expires_at = None
            session.commit()
    _revoke(token)
    return token is not None


def recover_expired(*, now=None):
    """An expired lease invalidates the old attempt before any replacement starts."""
    now = now or utc_now()
    with get_session_factory()() as session:
        candidates = list(session.execute(select(ResearchExecution.run_id, ResearchExecution.attempt,
            ResearchExecution.owner_id, ResearchExecution.delegation_token).where(
                ResearchExecution.status == "running", ResearchExecution.lease_expires_at <= now)))
    recovered = 0
    for row in candidates:
        old = Claim(row.run_id, row.attempt, row.owner_id, row.delegation_token or "")
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, row.run_id, with_for_update=True)
            job = session.get(ResearchExecution, row.run_id, with_for_update=True)
            if (not job or job.status != "running" or job.owner_id != row.owner_id
                    or job.attempt != row.attempt or _utc(job.lease_expires_at) > now):
                continue
            if run is None or run.status not in {"queued", "running"}:
                pass  # A committed publication survives loss of the worker acknowledgement.
            else:
                reason = _policy_reason(session, run)
                if reason:
                    token = _pause_locked(run, job, reason)
                    session.commit()
                    _revoke(token)
                    continue
                context = dict(run.context_json or {})
                execution = dict(context.get("execution") or {})
                failures = [*execution.get("failures", []), {"attempt": row.attempt,
                    "failed_at": now.isoformat(), "error": {"type": "WorkerLeaseExpired"}}]
                execution.update(attempt=row.attempt, failures=failures)
                if row.attempt < MAX_INTERRUPTED_ATTEMPTS and job.delegation_token:
                    execution.update(stage="queued", resume=True)
                    context.pop("runtime_error", None)
                    context.pop("validation_error", None)
                    # Every replacement is a fresh attempt. Risk receipts are now
                    # independently attempt-bound; never reuse an old submission.
                    context.pop("submitted_risk_review", None)
                    if context.get("risk_run"):
                        context["risk_delivered_pages"] = []
                        from watchlist_app.services.research_risk_state import get_risk_submission
                        submission_attempt = execution.get("risk_submission_attempt", row.attempt)
                        if context.get("input_snapshot_cutoff") and get_risk_submission(
                                session, row.run_id, attempt=submission_attempt) is not None:
                            # Resume publication of a validated artifact. Its old
                            # read receipts are never copied into a new model run.
                            execution["risk_submission_attempt"] = submission_attempt
                    run.context_json = {**context, "execution": execution}
                    run.status, run.body, run.completed_at = "queued", "", None
                    job.attempt, job.status, job.owner_id = row.attempt + 1, "queued", None
                    job.queued_at, job.heartbeat_at, job.lease_expires_at = now, None, None
                    session.commit()
                    recovered += 1
                    continue
                run.context_json = {**context, "execution": execution,
                    "runtime_error": {"type": "WorkerLeaseExpired", "retryable": False,
                        "summary": "研究执行多次中断，输入与草稿已保留；请检查运行服务后重试。"}}
                run.status, run.body, run.completed_at = "failed", "研究执行多次中断，输入与草稿已保留；请检查运行服务后重试。", now
            # Keep finish_claim's owner/attempt comparison; do not revoke a new grant.
            session.commit()
        finish_claim(old)
    return recovered


def recover_undispatched(*, now=None):
    """Close the API-commit/queue-commit crash gap without inventing authority."""
    now = now or utc_now()
    if model_work_status()["paused"]:
        return
    cutoff = now - timedelta(seconds=get_settings().research_worker_lease_seconds)
    with get_session_factory()() as session:
        ids = list(session.scalars(select(ResearchEntry.entry_id).outerjoin(
            ResearchExecution, ResearchExecution.run_id == ResearchEntry.entry_id).where(
                ResearchEntry.kind == "analysis", ResearchEntry.status == "queued",
                ResearchEntry.updated_at <= cutoff,
                or_(ResearchExecution.run_id.is_(None), ResearchExecution.status == "finished"))))
    for run_id in ids:
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id, with_for_update=True)
            job = session.get(ResearchExecution, run_id, with_for_update=True)
            if (run is None or run.status != "queued" or _utc(run.updated_at) > cutoff
                    or (job is not None and job.status != "finished")):
                continue
            summary = "研究任务派发中断，输入已保留；请重新发起。"
            context = dict(run.context_json or {})
            context["execution"] = {**context.get("execution", {}), "attempt": run_attempt(run)}
            context["runtime_error"] = {"type": "DispatchInterrupted", "summary": summary, "retryable": False}
            run.context_json = context
            run.status, run.body, run.completed_at = "failed", summary, now
            session.commit()


def pause_pending(*, now=None, limit=100):
    """Operator/worker reconciliation while paused; never steal a live lease.

    Paused work requires an explicit new request. Merely resuming the host must
    not replay the backlog accumulated before an operator's pause.
    """
    if not model_work_status()["paused"]:
        return 0
    now = now or utc_now()
    with get_session_factory()() as session:
        ids = list(session.scalars(select(ResearchExecution.run_id).where(or_(
            ResearchExecution.status == "queued",
            (ResearchExecution.status == "running") & (ResearchExecution.lease_expires_at <= now)))
            .order_by(ResearchExecution.queued_at, ResearchExecution.run_id).limit(limit)))
    paused = 0
    for run_id in ids:
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id, with_for_update=True)
            job = session.get(ResearchExecution, run_id, with_for_update=True)
            state = model_work_status()
            if (not state["paused"] or not job or job.status == "finished"
                    or (job.status == "running" and _utc(job.lease_expires_at) > now)):
                continue
            token = _pause_locked(run, job, state["message"])
            session.commit()
        _revoke(token)
        paused += 1
    # A request can commit its run and crash before durable dispatch. Give that
    # dispatch the same grace window as normal recovery, then close its status
    # without inventing a grant or replaying it when the host resumes.
    remaining = max(0, limit - paused)
    if not remaining:
        return paused
    cutoff = now - timedelta(seconds=get_settings().research_worker_lease_seconds)
    with get_session_factory()() as session:
        ids = list(session.scalars(select(ResearchEntry.entry_id).outerjoin(
            ResearchExecution, ResearchExecution.run_id == ResearchEntry.entry_id).where(
                ResearchEntry.kind == "analysis", ResearchEntry.status == "queued",
                ResearchEntry.updated_at <= cutoff,
                or_(ResearchExecution.run_id.is_(None), ResearchExecution.status == "finished"))
            .order_by(ResearchEntry.updated_at, ResearchEntry.entry_id).limit(remaining)))
    for run_id in ids:
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, run_id, with_for_update=True)
            job = session.get(ResearchExecution, run_id, with_for_update=True)
            state = model_work_status()
            if (not state["paused"] or run is None or run.kind != "analysis" or run.status != "queued"
                    or _utc(run.updated_at) > cutoff or (job is not None and job.status != "finished")):
                continue
            _pause_business(run, 0, state["message"])
            session.commit()
            paused += 1
    return paused


def execute_claim(claim, cancellation):
    from watchlist_app.services.research_runner import (authorize_run, run_analysis, _fail_authorization, _fail_run,
        ResearchExecutionInterrupted)
    interrupted = False
    try:
        with get_session_factory()() as session:
            from watchlist_app.services.research_policy import require_run_policy, run_policy_projection
            require_run_policy(session, run_policy_projection(session, claim.run_id))
        issuer = resolve_token(claim.delegation_token, "watchlist")
        # A fresh per-attempt grant remains a child of the original queue grant;
        # never substitute the worker's service identity for a user's task.
        token = authorize_run(issuer, claim.run_id, attempt=claim.attempt)
        run_analysis(claim.run_id, token, issuer, attempt=claim.attempt, cancellation=cancellation)
    except (ResearchExecutionInterrupted, ModelWorkPaused):
        interrupted = True
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, claim.run_id, with_for_update=True)
            job = session.get(ResearchExecution, claim.run_id, with_for_update=True)
            token = None
            if job and job.attempt == claim.attempt and job.owner_id == claim.owner_id and job.status == "running":
                reason = _policy_reason(session, run)
                if reason:
                    token = _pause_locked(run, job, reason)
                else:
                    job.lease_expires_at = utc_now()
            session.commit()
        _revoke(token)
    except (IdentityError, HTTPException) as error:
        _fail_authorization(claim.run_id, error, attempt=claim.attempt)
    except Exception as error:
        logger.exception("Research execution failed run_id=%s attempt=%s", claim.run_id, claim.attempt)
        summary = "研究执行未完成，输入与草稿已保留；请检查运行服务后重试。"
        _fail_run(claim.run_id, summary, attempt=claim.attempt,
            runtime_error={"type": type(error).__name__, "summary": summary, "retryable": False})
    finally:
        if not interrupted:
            finish_claim(claim)
