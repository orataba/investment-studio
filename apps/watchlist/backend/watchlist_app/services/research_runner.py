"""Run the existing pinned Harness with research-only tools and a bound input snapshot."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import duckdb
from sqlalchemy import select
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
        session.commit()


def run_analysis(run_id: str):
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id, with_for_update=True)
        if not run or run.status != "queued":
            return
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
        if sector_run:
            from watchlist_app.services.sector_research import prepare_run
            prepare_run(run_id)
        elif risk_run:
            from watchlist_app.services.risk_officer import prepare_run
            prepare_run(run_id)
        # The pinned headless CLI returns its last assistant text.
        # Research/risk drafts use structured tool submissions; conversations retain prose.
        mode = ["sector"] if sector_run else ["risk"] if risk_run else []
        process = subprocess.Popen(["/bin/bash", str(SCRIPT), run_id, *mode], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        reply, errors = process.communicate(timeout=1800 if sector_run else 900)
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
            outcome = ("事实核证失败：" if runtime_error["type"] not in {"ProcessExit", "InsufficientBalance"} else "") + runtime_error["summary"]
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
    except duckdb.IOException as error:
        if sector_run and "lock" in str(error).lower():
            with get_session_factory()() as session:
                run = session.get(ResearchEntry, run_id)
                if run and run.status == "running":
                    run.status = "queued"
                    run.body = "FMP数据库正在更新，等待只读访问后继续本次检查。"
                    session.commit()
            return
        outcome = "行业FMP数据库暂不可读，本次检查未完成。"
    except OSError as error:
        outcome = "无法启动研究运行进程。" if process is None else "读取研究运行进程结果失败。"
        runtime_error = {"type": type(error).__name__, "summary": outcome, "exit_code": process.returncode if process else None}
    except ValueError as error:
        outcome = str(error) if risk_run else "研究资料不符合本轮分析范围，本次检查未完成。"
        runtime_error = {"type": type(error).__name__, "summary": outcome}
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Research input preparation failed")
        outcome = "研究资料读取失败，本次没有完成检查。"
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        if run and run.status == "running":
            if runtime_error:
                run.context_json = {**run.context_json, "runtime_error": runtime_error}
            if rejected_reply:
                run.context_json = {**run.context_json, "rejected_reply": rejected_reply}
            if (sector_run or risk_run) and completed:
                if sector_run:
                    from watchlist_app.services.sector_research import apply_result
                else:
                    from watchlist_app.services.risk_officer import apply_result
                try:
                    apply_result(session, run, run.context_json["submitted_risk_review"] if risk_run else outcome)
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
            session.commit()
