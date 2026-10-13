"""The real pinned reviewer must stop once its MCP accepts the bound receipt."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from watchlist_app.services import research_review_agent, sector_fact_review


ROOT = Path(__file__).resolve().parents[4]


def test_native_reviewer_repairs_rejection_then_stops_before_acknowledgement(monkeypatch, tmp_path):
    if not (ROOT / "infra/harness/node_modules/@deepseek-ai/dsh/lib/bin.js").exists() or not shutil.which("node"):
        if os.environ.get("CI"):
            pytest.fail("CI must provision the pinned Harness and Node")
        pytest.skip("Pinned Harness and Node must be provisioned")
    requests = []

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            step = len(requests)
            if step in (1, 3):
                name, arguments = "mcp__review__submit_review_receipts", {"receipts": {"reviews": []}}
            else:
                name, arguments = "mcp__review__read_review_context", {"section": "draft_reviews"}
            delta = {"role": "assistant", "reasoning_content": "Offline completion fixture."}
            if step <= 3:
                delta["tool_calls"] = [{"index": 0, "id": f"call-{step}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)}}]
            else:
                # A regression exits promptly, but this unnecessary request fails
                # the assertion below rather than incurring a real model call.
                delta["content"] = "Unexpected acknowledgement."
            chunks = [{"id": "fixture", "object": "chat.completion.chunk", "created": 1,
                "model": "deepseek-v4.1-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"id": "fixture", "choices": [{"index": 0, "delta": {},
                    "finish_reason": "tool_calls" if step <= 3 else "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20}}]
            wire = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR") if key in os.environ}
    env.update({"DEEPSEEK_API_KEY": "offline-fixture-key",
        "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
        "INVESTMENT_STUDIO_RESEARCH_RUN_ID": "offline-review",
        "INVESTMENT_STUDIO_RESEARCH_API_BASE_URL": f"http://127.0.0.1:{server.server_port}/api",
        "INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN": "offline-run-token",
        "DSH_HOME": str(tmp_path / "home"), "DSH_TELEMETRY_DISABLED": "1",
        "DSH_TOOLS_MODE": "native", "DSH_PERMISSION_MODE": "read-only"})
    monkeypatch.setattr(os, "environ", env)
    completed = []
    run = subprocess.run

    def bounded_run(*args, **kwargs):
        process = run(*args, **kwargs, timeout=30)
        completed.append(process)
        return process

    monkeypatch.setattr(subprocess, "run", bounded_run)
    captures = []
    try:
        try:
            result = research_review_agent.run_review_agent(
                {"run_id": "offline-review", "cutoff": "2026-10-13T00:00:00Z", "draft_reviews": [],
                    "sources": [], "event_source_ids": []},
                sector_fact_review._review_schema([], []), "Use only the offline fixture tools.", captures.append)
        except research_review_agent.ReviewAgentError:
            pytest.fail(completed[0].stderr)
    finally:
        server.shutdown()
        server.server_close()
    assert result == {"reviews": []}
    assert len(requests) == 3
    assert "Read the complete draft_reviews" in json.dumps(requests[1])
    assert captures[0]["compact_result"] == {"reviews": []}
    assert captures[0]["evidence_reads"][0]["section"] == "draft_reviews"
    assert "error_type" not in captures[0]["agent_metadata"]


def test_completion_hook_ignores_other_modes_and_unpublished_temporary_file(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the Harness plugin")
    script = r"""
import assert from 'node:assert/strict';
import {writeFileSync, renameSync} from 'node:fs';
import {apply} from './apps/watchlist/backend/scripts/research_harness_outcome.mjs';
const output = process.argv[1];
const hooks = new Map();
const ctx = {on: (name, fn, options) => hooks.set(name, {fn, options})};
process.env.INVESTMENT_STUDIO_REVIEW_RESULT = output;
process.env.INVESTMENT_STUDIO_WATCHLIST_HARNESS_MODE = 'sector';
apply(ctx);
assert.equal(hooks.has('agent/pre-step'), false);
process.env.INVESTMENT_STUDIO_WATCHLIST_HARNESS_MODE = 'review';
apply(ctx);
const hook = hooks.get('agent/pre-step');
assert.equal(hook.options.prepend, true);
const proceed = () => ({kind: 'enter', messages: []});
assert.equal((await hook.fn({}, proceed)).kind, 'enter');
writeFileSync(output + '.tmp', '{}');
assert.equal((await hook.fn({}, proceed)).kind, 'enter');
renameSync(output + '.tmp', output);
assert.equal((await hook.fn({}, proceed)).kind, 'reject');
"""
    subprocess.run([node, "--input-type=module", "-e", script, str(tmp_path / "result.json")],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10)
