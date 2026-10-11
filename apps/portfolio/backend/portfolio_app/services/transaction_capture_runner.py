from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import subprocess
from studio_identity import resolve_token, revoke_delegation
from studio_runtime.processes import stop_process_group as _stop_process_group
from portfolio_app.services.portfolio_access import require_access

from portfolio_app.core.settings import get_settings
from portfolio_app.services.transaction_captures import (
    finish_transaction_capture_analysis_run,
    has_transaction_capture_agent_revision,
    mark_transaction_capture_analysis_run_started,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
HARNESS_RUNNER = BACKEND_ROOT / "scripts" / "run_portfolio_copilot_harness.sh"


def _harness_failure(stderr: str, return_code: int) -> str:
    """Classify provider/runtime failures without retaining their raw output."""
    text = stderr.replace('\\"', '"')
    if return_code == 78:
        return "截图分析运行环境尚未安装完整，请修复研究运行环境后重试。"
    if 'InvalidParameter' in text or re.search(r"^dsh: (?:INVALID_REQUEST|BAD_REQUEST):", text, re.M):
        return "模型服务拒绝了分析请求参数，本次执行未完成；请检查模型与工具接口配置。"
    if "insufficient_quota" in text or "insufficient_user_quota" in text or "Insufficient Balance" in text:
        return "DeepSeek账户余额不足，截图分析未完成。充值后可重新分析。"
    if "model_not_found" in text or "no available channel for model" in text.lower():
        return "当前模型通道不可用，截图分析未完成。请检查模型配置后重试。"
    if "rate_limit" in text.lower() or re.search(r"^dsh: (?:RATE|RATE_LIMIT):", text, re.M):
        return "模型服务暂时限流，截图已保留，请稍后重新分析。"
    if re.search(r"^dsh: (?:NETWORK|SERVER):", text, re.M):
        return "模型服务或网络暂时不可用，截图已保留，请稍后重新分析。"
    return "截图分析进程退出，本次执行未完成。请重新分析。"


def _run_harness_process(*, portfolio_id: str, batch_id: str, run_token: str) -> tuple[int, bool, str | None]:
    process = subprocess.Popen(
        [str(HARNESS_RUNNER), portfolio_id, batch_id],
        cwd=BACKEND_ROOT.parents[2],
        env={**os.environ, "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_RUN_TOKEN": run_token},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        _, stderr = process.communicate(
            timeout=get_settings().copilot_analysis_timeout_seconds,
        )
        return process.returncode, False, _harness_failure(stderr, process.returncode) if process.returncode else None
    except BaseException as exc:
        try:
            _stop_process_group(process)
        except Exception as cleanup_error:
            logging.getLogger(__name__).error(
                "Harness process-group cleanup failed (%s)", type(cleanup_error).__name__)
        if isinstance(exc, subprocess.TimeoutExpired):
            return process.returncode or -1, True, None
        raise
    finally:
        # Timeout output is not used. Do not wait again for inherited pipes.
        process.stderr.close()


def run_transaction_capture_analysis(
    *,
    portfolio_id: str,
    batch_id: str,
    attempt: int,
    run_token: str,
) -> None:
    started, starting_revision = mark_transaction_capture_analysis_run_started(
        portfolio_id=portfolio_id,
        batch_id=batch_id,
        attempt=attempt,
    )
    if not started:
        revoke_delegation(run_token)
        return

    try:
        principal = resolve_token(run_token, "portfolio")
        if principal.resource_scope != {"kind": "capture", "id": batch_id, "attempt": str(attempt)}:
            raise ValueError("Screenshot credential does not match the active analysis attempt")
        require_access(portfolio_id, "editor", principal)
        return_code, timed_out, failure = _run_harness_process(
            portfolio_id=portfolio_id,
            batch_id=batch_id,
            run_token=run_token,
        )
        revision_created = has_transaction_capture_agent_revision(
            portfolio_id=portfolio_id,
            batch_id=batch_id,
            after_revision=starting_revision,
        )
        if revision_created and return_code == 0 and not timed_out:
            finish_transaction_capture_analysis_run(
                portfolio_id=portfolio_id,
                batch_id=batch_id,
                attempt=attempt,
                succeeded=True,
            )
            return
        if timed_out:
            error = "截图分析超时，本次执行未完成。截图已保留，可重新分析。"
        elif return_code != 0:
            error = failure or _harness_failure("", return_code)
        else:
            error = "分析已结束，但没有提交可供复核的结果。请重新分析。"
        if revision_created:
            error += " 已提交的草稿仍保留，须人工复核；草稿不代表本次分析成功。"
        finish_transaction_capture_analysis_run(
            portfolio_id=portfolio_id,
            batch_id=batch_id,
            attempt=attempt,
            succeeded=False,
            error=error,
        )
    except Exception:
        finish_transaction_capture_analysis_run(
            portfolio_id=portfolio_id,
            batch_id=batch_id,
            attempt=attempt,
            succeeded=False,
            error="截图分析运行环境未能启动或完成，截图已保留。请检查运行环境后重试。",
        )

    finally:
        revoke_delegation(run_token)
