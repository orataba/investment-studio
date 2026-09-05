"""Run the existing pinned Harness with research-only tools and a bound input snapshot."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
from sqlalchemy import select
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory

ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "apps/watchlist/backend/scripts/run_research_harness.sh"


def harness_available():
    secret_root = Path(os.environ.get("INVESTMENT_STUDIO_SECRET_ROOT", str(Path.home() / ".config/orataba/secrets/investment-studio")))
    return (secret_root / "portfolio-copilot.env").is_file() and SCRIPT.is_file() and (shutil.which("pnpm") is not None or Path("/opt/homebrew/bin/pnpm").is_file())


def interrupt_incomplete_runs():
    with get_session_factory()() as session:
        for run in session.scalars(select(ResearchEntry).where(ResearchEntry.kind == "analysis", ResearchEntry.status.in_(["queued", "running"]))):
            run.status = "failed"
            run.body = "服务重新启动，本次分析未完成。输入快照已保留，可重新发起。"
        session.commit()


def run_analysis(run_id: str):
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        if not run or run.status != "queued":
            return
        run.status = "running"
        session.commit()
    outcome = "研究助手未返回回答，请重试。"
    completed = False
    process = None
    try:
        # The pinned headless CLI prints only its final assistant message to stdout.
        # Saving the reply is application work, not an extra action the model must remember.
        process = subprocess.Popen(["/bin/bash", str(SCRIPT), run_id], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        reply, _ = process.communicate(timeout=900)
        if process.returncode:
            outcome = "DeepSeek 运行失败。请检查模型连接与额度后重试；本次输入快照已保留。"
        elif reply.strip():
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
    except OSError:
        outcome = "无法启动 DeepSeek 运行环境。"
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        if run and run.status == "running":
            run.status = "draft" if completed else "failed"
            run.body = outcome
            session.commit()
