"""Run the independent factual reviewer through the pinned DeepSeek Harness."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


class ReviewAgentError(RuntimeError):
    def __init__(self, message, diagnostic, *, retryable=False):
        super().__init__(message)
        self.review_transport = diagnostic
        self.review_stage = "review_agent"
        self.retryable = retryable


def run_review_agent(packet, schema, instructions, record):
    root = Path(__file__).resolve().parents[5]
    started = time.monotonic()
    metadata = {"engine": "deepseek_harness", "protocol": "bound_draft_v1", "stage": "agent_start"}
    with tempfile.TemporaryDirectory(prefix="investment-review-") as directory:
        directory = Path(directory)
        input_path, output_path, reads_path = (directory / name for name in ("packet.json", "result.json", "reads.jsonl"))
        input_path.write_text(json.dumps({"packet": packet, "response_schema": schema}, ensure_ascii=False))
        input_path.chmod(0o600)
        outcome_patch = directory / "outcome.patch.json"
        outcome_patch.write_text(json.dumps([{"insert": [{"id": "research-harness-outcome",
            "name": str(root / "apps/watchlist/backend/scripts/research_harness_outcome.mjs")}]}]))
        env = {**os.environ, "INVESTMENT_STUDIO_RESEARCH_PROJECT_ROOT": str(root),
            "INVESTMENT_STUDIO_WATCHLIST_HARNESS_MODE": "review",
            "INVESTMENT_STUDIO_REVIEW_PACKET": str(input_path), "INVESTMENT_STUDIO_REVIEW_RESULT": str(output_path),
            "INVESTMENT_STUDIO_REVIEW_READS": str(reads_path), "INVESTMENT_STUDIO_REVIEW_INSTRUCTIONS": instructions}
        command = [str(root / "infra/harness/run.sh"), "--profile", "headless",
            "--patch", str(root / "infra/config/deepseek_harness.patch.yml"),
            "--patch", str(root / "apps/watchlist/backend/config/sector_harness.patch.yml"),
            "--patch", str(root / "apps/watchlist/backend/config/review_harness.patch.yml"),
            "--patch", str(outcome_patch),
            "Start with read_review_context overview. Read the draft and its bound response_schema, acquisition limits, "
            "applicable previous judgments and relevant complete originals through paged tools. Check all proposed "
            "objects, numerical units and information clocks, plus contradictions across objects. Work iteratively "
            "with tools; after automatic compaction reread exact original source_id/path when needed. Submit one "
            "complete receipt object through submit_review_receipts, repair any validation errors, then briefly acknowledge acceptance."]
        try:
            # The outer research process owns the wall-clock deadline and kills
            # this entire process group on timeout. Harness retains its native
            # streaming, tool loop and automatic context compaction.
            process = subprocess.run(command, cwd=root / "apps/watchlist/backend", env=env,
                                     capture_output=True, text=True, check=False)
            metadata.update(exit_code=process.returncode, stage="agent_finished")
            if process.returncode:
                from watchlist_app.services.research_runner import _provider_failure
                classified = _provider_failure(process.stderr)
                metadata["error_type"] = classified["type"] if classified else "HarnessProcessExit"
                raise ReviewAgentError(classified["summary"] if classified else "独立核证代理未完成，原草稿及证据已保留。", metadata,
                                       retryable=bool(classified and classified.get("retryable")))
            if not output_path.exists():
                metadata["error_type"] = "MissingReviewReceipt"
                raise ReviewAgentError("独立核证代理没有提交有效收据，未发布研究。", metadata)
            output = json.loads(output_path.read_text())
            return output["result"]
        finally:
            reads = [json.loads(line) for line in reads_path.read_text().splitlines()] if reads_path.exists() else []
            metadata.update(elapsed_seconds=round(time.monotonic() - started, 3), read_pages=len(reads),
                source_ids=sorted({row["source_id"] for row in reads if row.get("source_id")}))
            receipt = {"protocol": "bound_draft_v1", "agent_metadata": metadata, "evidence_reads": reads}
            if output_path.exists():
                receipt["compact_result"] = json.loads(output_path.read_text())["receipts"]
            record(receipt)
