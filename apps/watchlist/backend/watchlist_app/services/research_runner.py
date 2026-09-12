"""Run the existing pinned Harness with research-only tools and a bound input snapshot."""
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
from contextlib import ExitStack
from datetime import UTC, datetime
from fastapi import HTTPException
from sqlalchemy import select
from studio_identity import (IdentityError, current_principal, issue_delegation, principal_context,
                             resolve_token, revoke_delegation, service_principal)
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory

ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "apps/watchlist/backend/scripts/run_research_harness.sh"


def harness_available():
    secret_root = Path(os.environ.get("INVESTMENT_STUDIO_SECRET_ROOT", str(Path.home() / ".config/orataba/secrets/investment-studio")))
    env_file = Path(os.environ.get("INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ENV_FILE", str(secret_root / "portfolio-copilot.env")))
    pnpm = os.environ.get("INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PNPM") or shutil.which("pnpm") or "/opt/homebrew/bin/pnpm"
    return env_file.is_file() and SCRIPT.is_file() and Path(pnpm).is_file() and os.access(pnpm, os.X_OK)


def interrupt_incomplete_runs():
    with get_session_factory()() as session:
        for run in session.scalars(select(ResearchEntry).where(ResearchEntry.kind == "analysis", ResearchEntry.status.in_(["queued", "running"]))):
            run.status = "failed"
            run.body = "服务重新启动，本次分析未完成。输入快照已保留，可重新发起。"
            run.completed_at = datetime.now(UTC)
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
        session.commit()
    outcome = "研究助手未返回回答，请重试。"
    completed = False
    process = None
    runtime_error = None
    rejected_reply = None
    try:
        if not risk_run:
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
        process = subprocess.Popen(["/bin/bash", str(SCRIPT), run_id, *mode], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
            env={**os.environ, "INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN": current_principal().credential})
        reply, errors = process.communicate(timeout=1800 if not risk_run else 900)
        if process.returncode:
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
                    break
            if any(line.strip() == "dsh: QUOTA: Insufficient Balance" for line in (errors or "").splitlines()):
                runtime_error = {"type": "InsufficientBalance", "summary": "DeepSeek账户余额不足，本次分析未完成。充值后可重新更新。", "exit_code": process.returncode}
            outcome = ("事实核证失败：" if runtime_error["type"] not in {"ProcessExit", "InsufficientBalance", "MissingResearchDraft"} else "") + runtime_error["summary"]
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
        runtime_error = {"type": "TimeoutExpired", "summary": outcome, "exit_code": process.returncode}
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
