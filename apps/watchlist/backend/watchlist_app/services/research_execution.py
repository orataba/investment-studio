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

from watchlist_app.core.settings import get_settings
from watchlist_app.db.models.research_execution import ResearchExecution
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_access import require_entry_access, run_attempt

QUEUE_GRANT_SECONDS = 24 * 3600  # Home's existing maximum, still bounded by the parent login.
MAX_INTERRUPTED_ATTEMPTS = 3
logger = logging.getLogger(__name__)


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
        run = session.get(ResearchEntry, run_id)
        require_entry_access(session, run)
        expected = run_attempt(run)
        if run.status != "queued" or (attempt is not None and expected != attempt):
            raise HTTPException(409, "研究任务已开始或已被新的执行替代")
        if not same_research_initiator(run.context_json, principal):
            raise HTTPException(403, "只能排队执行原发起人的研究任务")
        existing = session.get(ResearchExecution, run_id)
        if existing and existing.attempt == expected and existing.status in {"queued", "running"}:
            return False
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
        run = session.execute(select(ResearchEntry.status).where(ResearchEntry.entry_id == row.run_id).with_for_update()).first()
        job = session.get(ResearchExecution, row.run_id, with_for_update=True)
        if run is None or run.status != "queued" or job.status != "queued" or job.attempt != row.attempt:
            return None
        if not job.delegation_token:
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


def execute_claim(claim, cancellation):
    from watchlist_app.services.research_runner import (authorize_run, run_analysis, _fail_authorization, _fail_run,
        ResearchExecutionInterrupted)
    interrupted = False
    try:
        issuer = resolve_token(claim.delegation_token, "watchlist")
        # A fresh per-attempt grant remains a child of the original queue grant;
        # never substitute the worker's service identity for a user's task.
        token = authorize_run(issuer, claim.run_id, attempt=claim.attempt)
        run_analysis(claim.run_id, token, issuer, attempt=claim.attempt, cancellation=cancellation)
    except ResearchExecutionInterrupted:
        interrupted = True
        with get_session_factory()() as session:
            session.execute(update(ResearchExecution).where(ResearchExecution.run_id == claim.run_id,
                ResearchExecution.attempt == claim.attempt, ResearchExecution.owner_id == claim.owner_id,
                ResearchExecution.status == "running").values(lease_expires_at=utc_now()))
            session.commit()
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
