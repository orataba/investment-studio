"""Run the existing pinned Harness with research-only tools and a bound input snapshot."""
import json
import logging
from studio_runtime import operation
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from fastapi import HTTPException
from sqlalchemy import select, update
from studio_identity import (IdentityError, current_principal, issue_delegation, principal_context,
                             resolve_token, revoke_delegation, service_principal)
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory

ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "apps/watchlist/backend/scripts/run_research_harness.sh"


def _provider_failure(errors):
    """Retain known provider failure classes, never its response or credentials."""
    text = (errors or "").replace('\\"', '"')
    if 'RESEARCH_HARNESS_END {"reason":"max-tokens"}' in text.splitlines():
        return {"type": "OutputLimitExceeded", "summary": "本次研究达到模型单次输出上限，尚未完成提交；已取得的证据和计算保留，可从原进度恢复。", "retryable": False}
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
        r"^dsh: (?:NETWORK|SERVER):.*(?:\b50[234]\b|ECONNRESET|ETIMEDOUT|fetch failed)", text, re.M):
        return {"type": "ProviderUnavailable", "summary": "模型服务或网络暂时不可用，草稿与已有证据已保留。", "retryable": True}
    return None


def review_checkpoint(context):
    """A checkpoint belongs to the exact submitted draft and information horizon."""
    for capture in reversed(context.get("web_evidence", [])):
        checkpoint = (capture.get("review") or {}).get("checkpoint")
        if (checkpoint and checkpoint.get("draft") == context.get("submitted_draft")
                and checkpoint.get("cutoff") == context.get("cutoff")):
            return checkpoint.get("result")
    return None


def queue_retry(session, run, *, now=None, manual=False):
    """Requeue an authorized failed instrument run without rebinding its evidence.

    Automatic recovery is bounded to two retries of an identified transient fault.
    Manual recovery is useful after an operator repairs a deterministic defect;
    publication still verifies the studied dossier versions and current access.
    The caller holds the same instrument lock used by begin_run.
    """
    session.refresh(run, with_for_update=True)
    context = dict(run.context_json or {})
    if run.status != "failed" or not context.get("sector_run"):
        return False
    from watchlist_app.services.research_access import require_entry_access
    require_entry_access(session, run)
    actor = context.get("research_actor") or {}
    principal = current_principal()
    if not principal.local_unrestricted and (
        actor.get("kind") != principal.kind or
        (principal.kind == "service" and actor.get("service_id") != principal.service_id) or
        (principal.kind == "user" and actor.get("user_id") != principal.user_id)
    ):
        return False  # Recovery never adopts another person's original task.
    execution = dict(context.get("execution") or {})
    attempt = execution.get("attempt", 1)
    error = context.get("runtime_error") or {}
    interrupted = run.body == "服务重新启动，本次分析未完成。输入快照已保留，可重新发起。"
    clock = now or datetime.now(UTC)
    ended = run.completed_at
    if ended is not None:
        ended = ended.replace(tzinfo=ended.tzinfo or UTC)
    if not manual and (attempt >= 3 or not (error.get("retryable") is True or interrupted)
        or ended is None or clock < ended + timedelta(seconds=60 if attempt == 1 else 300)):
        return False
    execution["failures"] = [*execution.get("failures", []), {
        "attempt": attempt, "failed_at": ended.isoformat() if ended else None,
        "error": error or {"type": "Interrupted"}, "manual_recovery": manual}]
    execution.update(attempt=attempt, stage="queued", resume=True)
    context.pop("runtime_error", None)
    context.pop("validation_error", None)
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


def interrupt_incomplete_runs():
    with get_session_factory()() as session:
        # Recovery changes status only; retained inputs can be many megabytes
        # per interrupted run and must not be hydrated during API startup.
        session.execute(update(ResearchEntry).where(ResearchEntry.kind == "analysis",
            ResearchEntry.status.in_(["queued", "running"])).values(status="failed",
            body="服务重新启动，本次分析未完成。输入快照已保留，可重新发起。",
            completed_at=datetime.now(UTC)))
        session.commit()


def run_analysis(run_id: str, token: str | None = None, issuer=None):
    tokens = []
    try:
        if token is None:
            issuer = issuer or service_principal("watchlist")
            token = authorize_run(issuer, run_id)
        tokens.append(token)
        with ExitStack() as identities:
            identities.enter_context(principal_context(resolve_token(token, "watchlist")))

            def execution_authorization():
                # Input materialization can outlast the initial one-hour grant.
                # Revalidate the original subject after preparation; never renew
                # from an expired run token or substitute a different service.
                if issuer is not None:
                    execution_token = authorize_run(issuer, run_id)
                    tokens.append(execution_token)
                    identities.enter_context(principal_context(resolve_token(execution_token, "watchlist")))

            with operation("watchlist_research", run_id=run_id):
                _run_analysis(run_id, execution_authorization=execution_authorization)
    except (IdentityError, HTTPException) as error:
        _fail_authorization(run_id, error)
    except Exception as error:
        logging.getLogger(__name__).exception("Research run did not finish: %s", run_id)
        summary = "研究运行未完成，本轮没有发布成果；输入与草稿已保留，可重新发起。"
        _fail_run(run_id, summary, runtime_error={"type": type(error).__name__, "summary": summary})
    finally:
        for issued_token in dict.fromkeys(reversed(tokens)):
            try:
                revoke_delegation(issued_token, issuer)
            except IdentityError:
                pass  # Credentials also expire server-side; never publish on an auth failure.


def _fail_authorization(run_id, error):
    message = "账号或研究范围授权不可用，本轮没有发布成果；恢复授权后可重新发起。"
    # Preserve the failure class without storing credentials, response bodies or
    # private provider details. A generic message alone cannot distinguish a
    # rejected credential/scope from an unavailable identity service afterwards.
    _fail_run(run_id, message, runtime_error={"type": "AuthorizationUnavailable",
        "status_code": error.status_code, "summary": message})


def _fail_run(run_id, message, *, runtime_error=None):
    """Finish an abandoned dispatch/publication in a fresh transaction after rollback."""
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id, with_for_update=True)
        if run and run.status in {"queued", "running"}:
            run.status, run.body = "failed", message
            if runtime_error:
                run.context_json = {**run.context_json, "runtime_error": runtime_error}
            run.completed_at = datetime.now(UTC)
            session.commit()


def authorize_run(principal, run_id):
    """A persisted queue item must reach a terminal state if dispatch is rejected."""
    try:
        return issue_delegation(principal, audience="watchlist", resource_scope={"kind": "run", "id": run_id})
    except (IdentityError, HTTPException) as error:
        _fail_authorization(run_id, error)
        raise


def _run_analysis(run_id: str, *, execution_authorization=None):
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id, with_for_update=True)
        if not run or run.status != "queued":
            return
        from watchlist_app.services.research_access import require_entry_access
        require_entry_access(session, run)
        run.status = "running"
        sector_run = bool(run.context_json.get("sector_run"))
        risk_run = bool(run.context_json.get("risk_run"))
        context = dict(run.context_json)
        execution = dict(context.get("execution") or {})
        resuming = bool(execution.get("resume"))
        prepared = resuming and bool(context.get("input_snapshot_cutoff"))
        resume_review = sector_run and prepared and bool(context.get("submitted_draft"))
        checkpoint = review_checkpoint(context) if resume_review else None
        execution.update(attempt=execution.get("attempt", 0) + 1,
                         stage="publication" if checkpoint else "review" if resume_review else "generation" if prepared else "preparation",
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
        elif risk_run:
            from watchlist_app.services.risk_officer import prepare_run
            prepare_run(run_id)
        if execution_authorization is not None:
            execution_authorization()
        # The pinned headless CLI returns its last assistant text.
        # Research/risk drafts use structured tool submissions; conversations retain prose.
        mode = ["sector"] if sector_run else ["risk"] if risk_run else []
        if checkpoint is not None:
            reply, errors, returncode = json.dumps(checkpoint, ensure_ascii=False), "", 0
        else:
            process = subprocess.Popen(["/bin/bash", str(SCRIPT), run_id, *mode], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
                env={**os.environ, "INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN": current_principal().credential,
                     "INVESTMENT_STUDIO_RESEARCH_RESUME_GENERATION": "1" if sector_run and prepared and not resume_review else "0",
                     "INVESTMENT_STUDIO_RESEARCH_RESUME_REVIEW": "1" if resume_review else "0"})
            reply, errors = process.communicate(timeout=1800 if not risk_run else 900)
            returncode = process.returncode
        if returncode:
            runtime_error = {"type": "ProcessExit", "summary": "研究运行进程退出，未生成有效结果。", "exit_code": process.returncode}
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
                runtime_error = {**provider_error, "exit_code": process.returncode}
                if provider_error["type"] == "OutputLimitExceeded":
                    runtime_error["stage"] = "review" if resume_review else "generation"
            outcome = ("事实核证失败：" if runtime_error["type"] not in {"ProcessExit", "InsufficientBalance", "ModelUnavailable", "MissingResearchDraft", "OutputLimitExceeded"} else "") + runtime_error["summary"]
            if sector_run and reply.strip():
                rejected_reply = reply.strip()
        elif reply.strip() or risk_run:
            outcome = reply.strip()
            completed = True
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        outcome = "研究助手本次运行超时；可以缩小问题范围后重试。"
        runtime_error = {"type": "TimeoutExpired", "summary": outcome, "exit_code": process.returncode,
                         "retryable": resume_review, "stage": "review" if resume_review else "generation"}
    except (IdentityError, HTTPException):
        raise  # Preserve authorization status and do not start/publish a model result.
    except OSError as error:
        outcome = "无法启动研究运行进程。" if process is None else "读取研究运行进程结果失败。"
        runtime_error = {"type": type(error).__name__, "summary": outcome, "exit_code": process.returncode if process else None}
    except ValueError as error:
        outcome = str(error) if risk_run else "研究资料不符合本轮分析范围，本次检查未完成。"
        runtime_error = {"type": type(error).__name__, "summary": outcome}
    except Exception:
        logging.getLogger(__name__).exception("Research input preparation failed")
        outcome = "研究资料读取失败，本次没有完成检查。"
    with principal_context(resolve_token(current_principal().credential, "watchlist")), get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        if run and run.status == "running":
            require_entry_access(session, run)
            if runtime_error:
                run.context_json = {**run.context_json, "runtime_error": runtime_error}
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
                            apply_result(session, run, json.dumps(result, ensure_ascii=False))
                            publication = {"status": "published", "instrument_ids": list(run.context_json.get("reviews", {})),
                                           "message": "已更新共同研究记录，研究追踪与助手将使用同一版本。"}
                        except Exception as error:
                            session.rollback()
                            run = session.get(ResearchEntry, run_id)
                            if not isinstance(error, ValueError):
                                logging.getLogger(__name__).exception("Conversation research publication failed: %s", run_id)
                            publication = {"status": "conflict" if isinstance(error, ResearchVersionConflict) else "failed",
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
                    apply_result(session, run, run.context_json["submitted_risk_review"] if risk_run else outcome)
                    run.completed_at = datetime.now(UTC)
                    session.commit()
                    return
                except (ValueError, KeyError) as error:
                    session.rollback()
                    run = session.get(ResearchEntry, run_id)
                    run.context_json = {**run.context_json, "rejected_reply": outcome, "validation_error": str(error)}
                    completed = False
                    outcome = "风控未提交符合当前范围与证据的完整研判。" if risk_run else "研究追踪未提交有效的完整结果或时间证据，未发布新的风险与机会。"
            run.status = "draft" if completed else "failed"
            run.body = outcome
            run.completed_at = datetime.now(UTC)
            session.commit()
