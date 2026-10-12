"""Run the existing pinned Harness with research-only tools and a bound input snapshot."""
import json
import logging
from studio_runtime import operation
from studio_runtime.processes import stop_process_group as _stop_process_group
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from types import SimpleNamespace
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from fastapi import HTTPException
from sqlalchemy import JSON, select, true
from studio_identity import (IdentityError, current_principal, issue_delegation, principal_context,
                             resolve_token, revoke_delegation, service_principal)
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.research_errors import ResearchInputUnavailable
from watchlist_app.services.research_access import run_attempt

ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "apps/watchlist/backend/scripts/run_research_harness.sh"
RESTART_FAILURE = "服务重新启动，本次分析未完成。输入快照已保留，可重新发起。"


class ResearchExecutionInterrupted(Exception):
    """The worker stopped or lost its lease; recovery owns the next attempt."""


def _execution_state(session, run_id, *, draft=False):
    from watchlist_app.services.research_access import research_context_projection, research_projection_rows
    names = ["execution", "submitted_draft"] if draft else ["execution"]
    relation, values = research_context_projection(session, {name: JSON for name in names},
        json_column=ResearchEntry.read_context_json)
    query = select(ResearchEntry.status, *(values[name].label(name) for name in names)).select_from(ResearchEntry)
    if relation is not None:
        query = query.join(relation, true())
    rows = research_projection_rows(session, query.where(ResearchEntry.entry_id == run_id),
        {name: (name,) for name in names}, json_column=ResearchEntry.read_context_json)
    return rows[0] if rows else None


def _accepted_submission(run_id, attempt, *, risk_run):
    with get_session_factory()() as session:
        row = _execution_state(session, run_id, draft=not risk_run)
        if row is None or row.status != "running" or (row.execution or {}).get("attempt") != attempt:
            return False
        if risk_run:
            from watchlist_app.services.research_risk_state import get_risk_submission
            return get_risk_submission(session, run_id, attempt=attempt) is not None
        return bool(row.submitted_draft)


def _wait_harness(process, *, cancellation=None, accepted=None, timeout=1800):
    """Own the process group until completion, including local polling failures."""
    cleanup_started = False
    try:
        if cancellation is None:
            reply, errors = process.communicate(timeout=timeout)
            return reply, errors, process.returncode
        deadline = time.monotonic() + timeout
        while True:
            if cancellation.is_set():
                raise ResearchExecutionInterrupted()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            try:
                reply, errors = process.communicate(timeout=min(2, remaining))
                # A durable, validated submission is the completion boundary even
                # when the CLI exits during its final acknowledgement/cleanup.
                returncode = 0 if accepted is not None and accepted() else process.returncode
            except subprocess.TimeoutExpired:
                if accepted is not None and accepted():
                    # Stop unnecessary acknowledgements/tool loops before publication.
                    cleanup_started = True
                    _stop_process_group(process)
                    reply, errors = process.communicate(timeout=5)
                    returncode = 0
                else:
                    continue
            # A stop can arrive during a result read, acceptance check or cleanup.
            # Both completion paths retain the worker's lease-recovery boundary.
            if cancellation.is_set():
                raise ResearchExecutionInterrupted()
            return reply, errors, returncode
    except BaseException:
        if not cleanup_started:
            # Stop descendants even when the shell has already exited. This must
            # precede failure publication and release of the durable queue slot.
            try:
                _stop_process_group(process)
            except Exception as cleanup_error:
                # Preserve the original failure and never expose process output,
                # command arguments or credentials through a cleanup diagnostic.
                logging.getLogger(__name__).error(
                    "Harness process-group cleanup failed (%s)", type(cleanup_error).__name__)
        raise


def _provider_failure(errors):
    """Retain known provider failure classes, never its response or credentials."""
    text = (errors or "").replace('\\"', '"')
    if 'RESEARCH_HARNESS_END {"reason":"max-tokens"}' in text.splitlines():
        return {"type": "OutputLimitExceeded", "summary": "本次研究达到模型单次输出上限，尚未完成提交；已取得的证据和计算保留，可从原进度恢复。", "retryable": False}
    if 'RESEARCH_HARNESS_END {"reason":"content-filter"}' in text.splitlines():
        return {"type": "ProviderContentFilter", "summary": "模型服务中止了本次输出，研究尚未完成；已取得的证据和草稿已保留。", "retryable": False}
    codes = set(re.findall(r'"code"\s*:\s*"([a-z_]+)"', text))
    if codes.intersection({"insufficient_user_quota", "insufficient_quota"}) or any(
        line.strip() == "dsh: QUOTA: Insufficient Balance" for line in text.splitlines()
    ):
        return {"type": "InsufficientBalance", "summary": "DeepSeek账户余额不足，本次分析未完成。充值后可重新更新。"}
    if "model_not_found" in codes or "no available channel for model" in text.lower():
        return {"type": "ModelUnavailable", "summary": "DeepSeek服务商当前没有可用的模型通道，本次分析未完成。请检查模型和通道配置。"}
    if codes.intersection({"context_length_exceeded", "context_window_exceeded"}) or (
        "dsh:" in text and "Input exceeds the context limit" in text):
        return {"type": "ContextLimitExceeded", "summary": "本轮必要资料超过模型上下文容量，未发布研究；请检查证据装配与研究范围。", "retryable": False}
    if codes.intersection({"rate_limit_exceeded", "too_many_requests"}) or re.search(r"^dsh: (?:RATE_LIMIT|RATE):", text, re.M):
        return {"type": "ProviderRateLimit", "summary": "模型服务暂时限流，草稿与已有证据已保留。", "retryable": True}
    if codes.intersection({"service_unavailable", "server_error", "overloaded_error"}) or re.search(
        r"^dsh: (?:NETWORK|SERVER|TRANSPORT|TIMEOUT|EMPTY_RESPONSE):", text, re.M):
        return {"type": "ProviderUnavailable", "summary": "模型服务或网络暂时不可用，草稿与已有证据已保留。", "retryable": True}
    if re.search(r"^dsh: (?:INVALID_REQUEST|BAD_REQUEST|UNSUPPORTED_OPTION|UNSUPPORTED_REASONING_EFFORT):", text, re.M):
        return {"type": "ProviderRequestRejected", "summary": "模型接口拒绝了请求参数，本轮未完成；请检查模型与工具接口配置。", "retryable": False}
    return None


def review_checkpoint(context):
    """A checkpoint belongs to the exact submitted draft and information horizon."""
    recheck = (context.get("execution") or {}).get("review_recheck") or {}
    captures = context.get("web_evidence", [])[recheck.get("after_evidence_count", 0):]
    for capture in reversed(captures):
        checkpoint = (capture.get("review") or {}).get("checkpoint")
        if (checkpoint and checkpoint.get("draft") == context.get("submitted_draft")
                and checkpoint.get("cutoff") == context.get("cutoff")):
            return checkpoint.get("result")
    return None


def conversation_answer(context):
    """Preserve private prose independently of the shared research checkpoint."""
    answer = context.get("conversation_answer")
    if isinstance(answer, str):
        return answer
    # The earlier conversation wrapper retained the completed private answer
    # before starting review. Recover that already-running path without a model.
    for capture in reversed(context.get("web_evidence", [])):
        answer = (capture.get("review") or {}).get("raw_draft")
        if isinstance(answer, str):
            return answer
    return "共同研究草稿已保留；上次私人答复在完成前中断，核证与发布结果见本条记录。"


def same_research_initiator(context, principal):
    actor = context.get("research_actor") or {}
    return principal.local_unrestricted or (actor.get("kind") == principal.kind and (
        principal.kind == "service" and actor.get("service_id") == principal.service_id or
        principal.kind == "user" and actor.get("user_id") == principal.user_id))


def _failed_attempt(context, body):
    execution = context.get("execution") or {}
    attempt = execution.get("attempt", 1)
    # Startup deliberately updates only scalar status fields, without loading
    # retained evidence. A queued retry had already reserved its next number;
    # consume that reservation when recovering the interrupted task.
    return attempt + 1 if body == RESTART_FAILURE and execution.get("stage") == "queued" else attempt


def automatic_retry_due(context, completed_at, body, *, now=None):
    """Shared eligibility only; queue_retry still locks and rechecks authority."""
    attempt = _failed_attempt(context, body)
    error = context.get("runtime_error") or {}
    interrupted = body == RESTART_FAILURE
    ended = completed_at.replace(tzinfo=completed_at.tzinfo or UTC) if completed_at else None
    return (attempt < 3 and (error.get("retryable") is True or interrupted) and ended is not None
            and (now or datetime.now(UTC)) >= ended + timedelta(seconds=60 if attempt == 1 else 300))


def queue_retry(session, run, *, now=None, manual=False, recheck_review=False):
    """Requeue an authorized failed research/risk run without rebinding its evidence.

    Automatic recovery is bounded to two retries of an identified transient fault.
    Manual recovery is useful after an operator repairs a deterministic defect;
    publication still verifies the studied dossier versions and current access.
    Explicit manual rechecking retains the accepted draft and all old receipts,
    but only checkpoints written after that request may be reused.
    The caller holds the same instrument/topic lock used by begin_run.
    """
    session.refresh(run, with_for_update=True)
    context = dict(run.context_json or {})
    if run.status != "failed" or not (context.get("sector_run") or context.get("risk_run")):
        return False
    from watchlist_app.services.research_access import require_entry_access, require_portfolio, require_team_write
    require_entry_access(session, run)
    principal = current_principal()
    if not same_research_initiator(context, principal):
        return False  # Recovery never adopts another person's original task.
    portfolio_id = (context.get("risk_scope") or {}).get("portfolio_id")
    if context.get("risk_run") and portfolio_id:
        require_portfolio(portfolio_id)
    else:
        require_team_write()
    if recheck_review and (not manual or not context.get("sector_run")
            or not context.get("submitted_draft") or not context.get("input_snapshot_cutoff")
            or not context.get("cutoff")):
        return False
    execution = dict(context.get("execution") or {})
    attempt = _failed_attempt(context, run.body)
    error = context.get("runtime_error") or {}
    ended = run.completed_at
    if ended is not None:
        ended = ended.replace(tzinfo=ended.tzinfo or UTC)
    if not manual and not automatic_retry_due(context, ended, run.body, now=now):
        return False
    validation_error = context.get("validation_error")
    failure = {
        "attempt": attempt, "failed_at": ended.isoformat() if ended else None,
        "error": error or {"type": "PublicationValidation" if validation_error else "Interrupted"},
        "manual_recovery": manual}
    if validation_error:
        failure["validation_error"] = validation_error
    if recheck_review:
        # web_evidence is append-only. This boundary keeps old receipts auditable
        # while letting a newly checked result recover without another review.
        recheck = {"after_evidence_count": len(context.get("web_evidence", [])),
            "requested_at": (now or datetime.now(UTC)).isoformat(), "failed_attempt": attempt,
            "publication_error": str(validation_error or "").split("\n", 1)[0][:1000]}
        execution["review_recheck"] = recheck
        failure["review_recheck"] = recheck
    execution["failures"] = [*execution.get("failures", []), failure]
    execution.update(attempt=attempt, stage="queued", resume=True)
    context.pop("runtime_error", None)
    context.pop("validation_error", None)
    if context.get("risk_run"):
        # Risk retries launch a new Harness session over the frozen evidence.
        # Old delivery receipts (including prior projection layouts) cannot prove
        # this session received the required pages or submitted a fresh proposal.
        context["risk_delivered_pages"] = []
        context.pop("submitted_risk_review", None)
        # Explicit retry of a failed publication requests a new proposal; a
        # worker interruption alone uses the accepted publication checkpoint.
        execution.pop("risk_submission_attempt", None)
    run.context_json = {**context, "execution": execution}
    run.status, run.body, run.completed_at = "queued", "", None
    session.flush()
    return True


def harness_available():
    secret_root = Path(os.environ.get("INVESTMENT_STUDIO_SECRET_ROOT", str(Path.home() / ".config/orataba/secrets/investment-studio")))
    env_file = Path(os.environ.get("INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ENV_FILE", str(secret_root / "portfolio-copilot.env")))
    node = shutil.which("node")
    entry = ROOT / "infra/harness/node_modules/@deepseek-ai/dsh/lib/bin.js"
    launcher = ROOT / "infra/harness/run.sh"
    return bool(env_file.is_file() and SCRIPT.is_file() and node
                and entry.is_file() and launcher.is_file() and os.access(launcher, os.X_OK))


def _current_attempt(run_id):
    with get_session_factory()() as session:
        row = _execution_state(session, run_id)
    if row is None or row.status not in {"queued", "running"}:
        return None
    return run_attempt(SimpleNamespace(status=row.status, context_json={"execution": row.execution}))


def run_analysis(run_id: str, token: str | None = None, issuer=None, *, attempt: int | None = None,
                 cancellation=None):
    tokens = []
    try:
        if token is not None and attempt is None:
            raise ValueError("A dispatched research credential must carry its analysis attempt")
        if attempt is None:
            attempt = _current_attempt(run_id)
        if attempt is None:
            return
        if token is None:
            issuer = issuer or service_principal("watchlist")
            token = authorize_run(issuer, run_id, attempt=attempt)
        tokens.append(token)
        with ExitStack() as identities:
            identities.enter_context(principal_context(resolve_token(token, "watchlist")))

            def execution_authorization():
                # Input materialization can outlast the initial one-hour grant.
                # Revalidate the original subject after preparation; never renew
                # from an expired run token or substitute a different service.
                if issuer is not None:
                    execution_token = authorize_run(issuer, run_id, attempt=attempt)
                    tokens.append(execution_token)
                    identities.enter_context(principal_context(resolve_token(execution_token, "watchlist")))

            with operation("watchlist_research", run_id=run_id):
                _run_analysis(run_id, execution_authorization=execution_authorization, attempt=attempt,
                              cancellation=cancellation)
    except ResearchExecutionInterrupted:
        raise
    except (IdentityError, HTTPException) as error:
        _fail_authorization(run_id, error, attempt=attempt)
    except Exception as error:
        logging.getLogger(__name__).exception("Research run did not finish: %s", run_id)
        summary = "研究运行未完成，本轮没有发布成果；输入与草稿已保留，可重新发起。"
        _fail_run(run_id, summary, attempt=attempt, runtime_error={"type": type(error).__name__, "summary": summary})
    finally:
        for issued_token in dict.fromkeys(reversed(tokens)):
            try:
                # A delegated queue issuer may grant stage credentials through
                # the backend, but Home only allows a delegation to revoke itself.
                revoke_delegation(issued_token)
            except IdentityError:
                pass  # Credentials also expire server-side; never publish on an auth failure.


def _fail_authorization(run_id, error, *, attempt):
    message = "账号或研究范围授权不可用，本轮没有发布成果；恢复授权后可重新发起。"
    # Preserve the failure class without storing credentials, response bodies or
    # private provider details. A generic message alone cannot distinguish a
    # rejected credential/scope from an unavailable identity service afterwards.
    _fail_run(run_id, message, attempt=attempt, runtime_error={"type": "AuthorizationUnavailable",
        "status_code": error.status_code, "summary": message})


def _fail_run(run_id, message, *, attempt, runtime_error=None):
    """Finish an abandoned dispatch/publication in a fresh transaction after rollback."""
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id, with_for_update=True)
        if run and run.status in {"queued", "running"} and attempt is not None and run_attempt(run) == attempt:
            context = dict(run.context_json or {})
            if run.status == "queued":
                # A grant may have been issued even if dispatch failed before
                # the worker started. Consume that reserved number so a later
                # retry cannot reuse its still-live credential.
                context["execution"] = {**context.get("execution", {}), "attempt": attempt}
            run.status, run.body = "failed", message
            if runtime_error:
                context["runtime_error"] = runtime_error
            run.context_json = context
            run.completed_at = datetime.now(UTC)
            session.commit()


def authorize_run(principal, run_id, *, attempt=None, ttl_seconds=3600):
    """A persisted queue item must reach a terminal state if dispatch is rejected."""
    try:
        current = _current_attempt(run_id)
        if attempt is None:
            attempt = current
        if current is None or attempt != current:
            raise HTTPException(409, "研究执行已结束或已被重试替代，不能签发旧任务凭证")
        return issue_delegation(principal, audience="watchlist", resource_scope={"kind": "run", "id": run_id, "attempt": str(attempt)},
                                **({"ttl_seconds": ttl_seconds} if ttl_seconds != 3600 else {}))
    except (IdentityError, HTTPException) as error:
        _fail_authorization(run_id, error, attempt=attempt)
        raise


def _run_analysis(run_id: str, *, execution_authorization=None, attempt=None, cancellation=None):
    if cancellation is not None and cancellation.is_set():
        raise ResearchExecutionInterrupted()
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id, with_for_update=True)
        if not run or run.status != "queued" or (attempt is not None and run_attempt(run) != attempt):
            return
        from watchlist_app.services.research_access import require_entry_access
        require_entry_access(session, run)
        # Resolve the same reserved number for internal callers as for an
        # explicitly dispatched claim before changing queued -> running.
        attempt = run_attempt(run)
        run.status = "running"
        sector_run = bool(run.context_json.get("sector_run"))
        risk_run = bool(run.context_json.get("risk_run"))
        context = dict(run.context_json)
        execution = dict(context.get("execution") or {})
        resuming = bool(execution.get("resume"))
        prepared = resuming and bool(context.get("input_snapshot_cutoff"))
        resume_review = not risk_run and prepared and bool(context.get("submitted_draft"))
        checkpoint = review_checkpoint(context) if resume_review else None
        saved_answer = (not sector_run and not risk_run and prepared
                        and isinstance(context.get("conversation_answer"), str))
        risk_checkpoint = None
        if risk_run and resuming and execution.get("risk_submission_attempt") is not None:
            from watchlist_app.services.research_risk_state import get_risk_submission
            risk_checkpoint = get_risk_submission(session, run_id, attempt=execution["risk_submission_attempt"])
            if not prepared or risk_checkpoint is None:
                raise ValueError("已受理的风控发布检查点或冻结输入不可用")
        execution.update(attempt=execution.get("attempt", 0) + 1,
                         stage="publication" if checkpoint or risk_checkpoint is not None else "review" if resume_review else "generation" if prepared else "preparation",
                         started_at=datetime.now(UTC).isoformat(), resume=False)
        run.context_json = {**context, "execution": execution}
        session.commit()
    outcome = "研究助手未返回回答，请重试。"
    completed = False
    process = None
    runtime_error = None
    rejected_reply = None
    try:
        if not risk_run and not prepared:
            from watchlist_app.services.sector_research import prepare_run
            prepare_run(run_id)
        elif risk_run and not prepared:
            from watchlist_app.services.risk_officer import prepare_run
            prepare_run(run_id)
        if execution_authorization is not None:
            execution_authorization()
        if cancellation is not None and cancellation.is_set():
            raise ResearchExecutionInterrupted()
        if not risk_run and not resume_review and not saved_answer:
            from watchlist_app.services.sector_research import prepare_initialization_searches
            prepare_initialization_searches(run_id)
        if checkpoint is None and risk_checkpoint is None and not resume_review:
            with get_session_factory()() as session:
                current = session.get(ResearchEntry, run_id, with_for_update=True)
                if current is None or current.status != "running":
                    return
                require_entry_access(session, current)
                current.context_json = {**current.context_json, "execution": {
                    **current.context_json.get("execution", {}), "stage": "generation"}}
                session.commit()
        # The pinned headless CLI returns its last assistant text.
        # Research/risk drafts use structured tool submissions; conversations retain prose.
        mode = ["sector"] if sector_run else ["risk"] if risk_run else []
        if risk_checkpoint is not None:
            reply, errors, returncode = "", "", 0
        elif checkpoint is not None:
            document = checkpoint if sector_run else {"answer": conversation_answer(context), "research_result": checkpoint}
            reply, errors, returncode = json.dumps(document, ensure_ascii=False), "", 0
        elif saved_answer and not resume_review:
            reply, errors, returncode = json.dumps({"answer": context["conversation_answer"],
                "research_result": None, "research_publication": {"status": "not_requested"}}, ensure_ascii=False), "", 0
        else:
            def launch():
                return subprocess.Popen(["/bin/bash", str(SCRIPT), run_id, *mode], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
                    env={**os.environ, "INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN": current_principal().credential,
                     "INVESTMENT_STUDIO_RESEARCH_GENERATION_ONLY": "1" if not risk_run and not resume_review else "0",
                     "INVESTMENT_STUDIO_RESEARCH_RESUME_GENERATION": "1" if not risk_run and prepared and not resume_review else "0",
                     "INVESTMENT_STUDIO_RESEARCH_RESUME_REVIEW": "1" if resume_review else "0"})
            process = launch()
            # Generation and independent review are two actual work stages.
            # A long generation must not consume the reviewer's entire allowance.
            accepted = (lambda: _accepted_submission(run_id, attempt, risk_run=risk_run)) if (risk_run or sector_run) and not resume_review else None
            reply, errors, returncode = _wait_harness(process, cancellation=cancellation, accepted=accepted)
            if not risk_run and not resume_review and not returncode:
                with get_session_factory()() as session:
                    current = session.get(ResearchEntry, run_id, with_for_update=True)
                    if current.status != "running":
                        return
                    require_entry_access(session, current)
                    context = dict(current.context_json)
                    if not sector_run:
                        context["conversation_answer"] = reply
                        current.context_json = context
                    if context.get("submitted_draft"):
                        current.context_json = {**context, "execution": {
                            **context.get("execution", {}), "stage": "review",
                            "review_started_at": datetime.now(UTC).isoformat()}}
                        resume_review = True
                    session.commit()
                if resume_review:
                    if execution_authorization is not None:
                        execution_authorization()
                    process = launch()
                    reply, errors, returncode = _wait_harness(process, cancellation=cancellation)
                elif sector_run:
                    reply, errors, returncode = "", 'SECTOR_REVIEW_ERROR {"type":"MissingResearchDraft","summary":"研究生成已结束但未提交完整草稿，本轮未发布研究。"}', 1
                else:
                    reply = json.dumps({"answer": reply, "research_result": None,
                        "research_publication": {"status": "not_requested"}}, ensure_ascii=False)
        if returncode:
            runtime_error = {"type": "ProcessExit", "summary": "研究运行进程退出，未生成有效结果。", "exit_code": returncode}
            harness_codes = sorted(set(re.findall(r"^dsh: ([A-Z][A-Z_]{1,63}):", errors or "", re.M)))
            if harness_codes:
                runtime_error["harness_codes"] = harness_codes
            for line in (errors or "").splitlines():
                if not line.startswith("SECTOR_REVIEW_ERROR "):
                    continue
                try:
                    marker = json.loads(line.removeprefix("SECTOR_REVIEW_ERROR "))
                except ValueError:
                    continue
                if (isinstance(marker, dict) and isinstance(marker.get("type"), str)
                        and isinstance(marker.get("summary"), str)):
                    runtime_error.update(type=marker["type"][:80], summary=marker["summary"][:500])
                    if isinstance(marker.get("diagnostic"), str):
                        runtime_error["diagnostic"] = marker["diagnostic"][:500]
                    if type(marker.get("retryable")) is bool:
                        runtime_error["retryable"] = marker["retryable"]
                    if isinstance(marker.get("stage"), str):
                        runtime_error["stage"] = marker["stage"][:40]
                    break
            provider_error = _provider_failure(errors)
            if provider_error is not None:
                runtime_error = {**provider_error, "exit_code": returncode}
                if provider_error["type"] in {"OutputLimitExceeded", "ProviderContentFilter"}:
                    runtime_error["stage"] = "review" if resume_review else "generation"
            outcome = ("事实核证失败：" if runtime_error["type"] not in {"ProcessExit", "InsufficientBalance", "ModelUnavailable", "MissingResearchDraft", "OutputLimitExceeded", "ProviderContentFilter"} else "") + runtime_error["summary"]
            if sector_run and reply.strip():
                rejected_reply = reply.strip()
        elif risk_run and risk_checkpoint is None and not _accepted_submission(run_id, attempt, risk_run=True):
            outcome = "风控进程已结束，但尚未提交通过范围与证据校验的完整研判。"
            runtime_error = {"type": "NoAcceptedSubmission", "summary": outcome, "retryable": False,
                             "stage": "generation"}
        elif reply.strip() or risk_run:
            outcome = reply.strip()
            completed = True
    except ResearchExecutionInterrupted:
        raise
    except subprocess.TimeoutExpired:
        outcome = "研究助手本次运行超时；可以缩小问题范围后重试。"
        runtime_error = {"type": "TimeoutExpired", "summary": outcome, "exit_code": process.returncode,
                         "retryable": resume_review, "stage": "review" if resume_review else "generation"}
    except (IdentityError, HTTPException):
        raise  # Preserve authorization status and do not start/publish a model result.
    except ResearchInputUnavailable as error:
        outcome = str(error)
        runtime_error = {"type": type(error).__name__, "summary": outcome,
                         "retryable": True, "stage": "preparation"}
    except OSError as error:
        outcome = "无法启动研究运行进程。" if process is None else "读取研究运行进程结果失败。"
        runtime_error = {"type": type(error).__name__, "summary": outcome, "exit_code": process.returncode if process else None}
    except ValueError as error:
        outcome = str(error) if risk_run else "研究资料不符合本轮分析范围，本次检查未完成。"
        runtime_error = {"type": type(error).__name__, "summary": outcome}
    except Exception as error:
        logging.getLogger(__name__).exception("Research input preparation failed")
        outcome = "研究资料读取失败，本次没有完成检查。"
        runtime_error = {"type": type(error).__name__, "summary": outcome, "retryable": False,
                         "stage": "preparation" if process is None else "review" if resume_review else "generation"}
    with principal_context(resolve_token(current_principal().credential, "watchlist")), get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id, with_for_update=True, populate_existing=True)
        if run and run.status == "running":
            require_entry_access(session, run)
            if runtime_error:
                # Generation may save its bound draft just before its deadline.
                # Recover that review without another investigation even when
                # the generation process did not exit in time to start review.
                if runtime_error["type"] == "TimeoutExpired" and sector_run:
                    review_ready = bool(run.context_json.get("submitted_draft") and
                                        run.context_json.get("input_snapshot_cutoff"))
                    runtime_error.update(retryable=review_ready, stage="review" if review_ready else "generation")
                run.context_json = {**run.context_json, "runtime_error": runtime_error}
                if not sector_run and not risk_run and resume_review:
                    # A completed private answer and a failed shared publication
                    # are separate outcomes. Keep both visible; an elapsed review
                    # deadline is not a worker-loss retry or a publication success.
                    outcome = json.dumps({"answer": conversation_answer(run.context_json), "research_result": None,
                        "research_publication": {"status": "failed", "stage": "review",
                            "message": runtime_error["summary"], "error": runtime_error,
                            "automatic_retry": False}}, ensure_ascii=False)
                    completed = True
            if rejected_reply:
                run.context_json = {**run.context_json, "rejected_reply": rejected_reply}
            if not sector_run and not risk_run and completed:
                from watchlist_app.services.sector_research import apply_result, ResearchVersionConflict
                try:
                    document = json.loads(outcome)
                    answer = document["answer"]
                    if not isinstance(answer, str):
                        raise ValueError("研究助手回复格式不正确")
                    publication = document.get("research_publication") or {"status": "not_requested"}
                    result = document.get("research_result")
                    if result is not None:
                        try:
                            run.context_json = {**run.context_json, "execution": {
                                **run.context_json.get("execution", {}), "stage": "publication"}}
                            apply_result(session, run, json.dumps(result, ensure_ascii=False))
                            publication = {"status": "published", "instrument_ids": list(run.context_json.get("reviews", {})),
                                           "message": "已更新共同研究记录，研究追踪与助手将使用同一版本。"}
                        except Exception as error:
                            session.rollback()
                            run = session.get(ResearchEntry, run_id, with_for_update=True, populate_existing=True)
                            require_entry_access(session, run)
                            if not isinstance(error, ValueError):
                                logging.getLogger(__name__).exception("Conversation research publication failed: %s", run_id)
                            publication = {"status": "conflict" if isinstance(error, ResearchVersionConflict) else "failed",
                                           "stage": "publication", "automatic_retry": False,
                                           "message": str(error) if isinstance(error, ValueError) else
                                               "共同研究保存未完成，对话回答已保留，本轮未发布研究更新。"}
                    run.context_json = {**run.context_json, "research_publication": publication}
                    outcome = answer
                except (ValueError, KeyError, TypeError):
                    completed = False
                    outcome = "研究助手未返回完整回复，本轮草稿已保留，未发布研究更新。"
            if (sector_run or risk_run) and completed:
                if sector_run:
                    from watchlist_app.services.sector_research import apply_result
                else:
                    from watchlist_app.services.risk_officer import apply_result
                try:
                    if risk_run:
                        from watchlist_app.services.research_risk_state import get_risk_submission
                        submission = risk_checkpoint if risk_checkpoint is not None else get_risk_submission(session, run_id, attempt=attempt)
                        if submission is None:
                            raise ValueError("本轮风控尚未提交完整研判")
                    else:
                        submission = outcome
                    apply_result(session, run, submission)
                    run.completed_at = datetime.now(UTC)
                    session.commit()
                    return
                except (ValueError, KeyError) as error:
                    session.rollback()
                    run = session.get(ResearchEntry, run_id, with_for_update=True, populate_existing=True)
                    require_entry_access(session, run)
                    run.context_json = {**run.context_json, "rejected_reply": outcome, "validation_error": str(error)}
                    completed = False
                    outcome = "风控未提交符合当前范围与证据的完整研判。" if risk_run else "研究追踪未提交有效的完整结果或时间证据，未发布新的风险与机会。"
            run.status = "draft" if completed else "failed"
            run.body = outcome
            run.completed_at = datetime.now(UTC)
            session.commit()
