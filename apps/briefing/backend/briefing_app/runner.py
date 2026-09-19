"""Application-owned headless DeepSeek run; only its bound report tools are exposed."""
from datetime import datetime, UTC
import logging
from studio_runtime import operation
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess

from sqlalchemy import select, update

from briefing_app.contracts import ReportDraft
from briefing_app.db import Report, get_session_factory
from briefing_app.evidence import build_input
from briefing_app.reports import validate_draft
from briefing_app.settings import get_settings
from briefing_app.access import require_report_access
from studio_identity import IdentityError, Principal, issue_delegation, principal_context, resolve_token, revoke_delegation

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "apps/briefing/backend/scripts/run_briefing_harness.sh"


def harness_available() -> bool:
    secret_root = Path(os.environ.get("INVESTMENT_STUDIO_SECRET_ROOT", str(Path.home() / ".config/orataba/secrets/investment-studio")))
    env_file = Path(os.environ.get("INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ENV_FILE", str(secret_root / "portfolio-copilot.env")))
    node = shutil.which("node")
    entry = ROOT / "infra/harness/node_modules/@deepseek-ai/dsh/lib/bin.js"
    launcher = ROOT / "infra/harness/run.sh"
    return bool(env_file.is_file() and SCRIPT.is_file() and node
                and entry.is_file() and launcher.is_file() and os.access(launcher, os.X_OK))


def interrupt_incomplete_runs():
    with get_session_factory()() as session:
        session.execute(update(Report).where(Report.status.in_(["queued", "running"])).values(
            status="failed", error="服务已重启，本轮报告未完成。此前完成版本仍可阅读。"))
        session.commit()


def _run_harness(report_id: str, mode: str, token: str) -> dict:
    label = "校稿" if mode == "review" else "首稿"
    process = subprocess.Popen(["/bin/bash", str(SCRIPT), report_id, mode], cwd=ROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
                               env={**os.environ, "INVESTMENT_STUDIO_BRIEFING_RUN_TOKEN": token})
    try:
        _reply, errors = process.communicate(timeout=1800)
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise ValueError(f"本轮{label}超时，未发布报告。") from exc
    if process.returncode:
        diagnostic = re.sub(r"(?im)^.*(?:api[_-]?key|authorization|password|secret|token).*$", "[credential-bearing diagnostic omitted]", errors)
        diagnostic = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", diagnostic)
        diagnostic = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", diagnostic)
        logging.getLogger(__name__).error("Briefing harness failed report_id=%s mode=%s exit_code=%s stderr_tail=%s",
                                         report_id, mode, process.returncode, "\n".join(diagnostic.splitlines()[-20:])[-4000:])
        if "dsh: QUOTA: Insufficient Balance" in errors:
            raise ValueError("DeepSeek 账户余额不足，本轮报告未完成。")
        if "model_not_found" in errors or "no available channel for model" in errors.lower():
            raise ValueError("DeepSeek 服务商当前没有可用的模型通道，请检查服务商的模型和通道配置；本轮报告未发布。")
        raise ValueError(f"DeepSeek {label}未正常结束，本轮报告未发布。")
    with get_session_factory()() as session:
        report = session.get(Report, report_id)
        if report.status != "running":
            raise ValueError("本轮报告已中断。")
        if not report.draft_json or report.draft_json["mode"] != mode:
            raise ValueError(f"模型未提交{label}的结构化报告，本轮未发布。")
        return validate_draft(ReportDraft.model_validate(report.draft_json["report"]), report.input_json)


def run_report(report_id: str, token: str, issuer: Principal):
    tokens = [token]
    try:
        with operation("briefing_generation", report_id=report_id):
            _execute_report(report_id, token, issuer, tokens)
    finally:
        for issued_token in dict.fromkeys(reversed(tokens)):
            try:
                revoke_delegation(issued_token, issuer)
            except IdentityError:
                logging.getLogger(__name__).warning("Report task credential could not be revoked; its expiry remains enforced")


def _authorize(report, token):
    principal = resolve_token(token, "briefing")
    if principal.resource_scope != {"kind": "report", "id": report.report_id}:
        raise IdentityError(403, "报告任务身份与本轮范围不一致")
    with principal_context(principal):
        require_report_access(report, write=True)


def _execute_report(report_id: str, token: str, issuer: Principal, tokens: list[str]):
    with get_session_factory()() as session:
        report = session.scalar(select(Report).where(Report.report_id == report_id).with_for_update())
        if not report or report.status != "queued":
            return
        try:
            _authorize(report, token)
        except IdentityError:
            report.status, report.error = "failed", "本轮账号或权限已失效，未发布报告。"
            session.commit()
            return
        report.status = "running"
        session.commit()
        cutoff = report.cutoff
        if cutoff.tzinfo is None:  # SQLite tests have no timezone storage.
            cutoff = cutoff.replace(tzinfo=UTC)
        report_type = report.report_type
    error = None
    try:
        snapshot = build_input(report_type, cutoff, get_settings())
        with get_session_factory()() as session:
            report = session.get(Report, report_id)
            report.input_json = snapshot
            session.commit()
        if not snapshot["source_count"]:
            raise ValueError("当前窗口没有可阅读的新闻或事件原文；本轮未生成报告。")
        for mode in ("write", "review"):
            # Preparation and the two allowed 30-minute stages must not share
            # an aging one-hour grant. Home revalidates the original issuer for
            # each stage; never renew from the report grant or another service.
            stage_token = issue_delegation(issuer, "briefing", {"kind": "report", "id": report_id})
            tokens.append(stage_token)
            with get_session_factory()() as session:
                report = session.get(Report, report_id)
                if report.status != "running":
                    return
                _authorize(report, stage_token)
            reviewed = _run_harness(report_id, mode, stage_token)
        with get_session_factory()() as session:
            report = session.scalar(select(Report).where(Report.report_id == report_id).with_for_update())
            if report.status != "running":
                return
            _authorize(report, stage_token)
            report.result_json = reviewed
            report.status = "completed"
            report.completed_at = datetime.now(UTC)
            session.commit()
    except IdentityError:
        error = "本轮账号或权限已失效，未发布报告。"
    except ValueError as exc:
        error = str(exc)
    except Exception:
        logging.getLogger(__name__).exception("Briefing generation failed")
        error = "本轮资料读取或报告运行失败，未发布草稿。"
    if error:
        with get_session_factory()() as session:
            report = session.get(Report, report_id)
            if report.status == "running":
                report.status, report.error = "failed", error
                session.commit()
