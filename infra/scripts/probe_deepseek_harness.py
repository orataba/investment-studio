"""Opt-in live connectivity probe: synthetic facts, actual MCP, temporary sessions.

Use probe_deepseek_harness.sh to reuse the existing external credential file.
The normal test suite calls run_probe with a localhost fake streaming provider.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
MARKER = "HARNESS_SMOKE "
OBSERVER = r"""
export const name = 'harness-smoke-observer';
export function apply(ctx) {
  const result = {reasoning_received: false, tool_calls: [], tool_error: false, model: null, reasoning_effort: null};
  ctx.on('session/event', (_session, event) => {
    if (event.type === 'request/header') result.reasoning_effort = event.data.header.config.reasoningEffort;
    if (event.type === 'request/context') result.model = event.data.model;
    if (event.type === 'assistant/message') result.reasoning_received ||= event.data.message.content.some(block => block.type === 'reasoning' && block.text.length > 0);
    if (event.type === 'tool/call') result.tool_calls.push(event.data.name);
    if (event.type === 'tool/result') result.tool_error ||= Boolean(event.data.message.isError);
    if (event.type === 'turn/end') process.stderr.write('HARNESS_SMOKE ' + JSON.stringify({...result, finish: event.data.reason.kind}) + '\n');
  });
}
"""


def run_probe(environment: dict[str, str], *, timeout: int = 120) -> dict:
    if not environment.get("DEEPSEEK_API_KEY"):
        return {"status": "failed", "reason": "credential_not_configured"}
    challenge = uuid4().hex
    expected = f"SMOKE_OK:{challenge}:price_return:simple:USD"
    reads, rejected = [], []

    class FixtureAPI(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != "/api/reports/synthetic-smoke/context" or self.headers.get("Authorization") != "Bearer synthetic-smoke-token":
                rejected.append(self.path)
                self.send_error(404)
                return
            reads.append(self.path)
            facts = {"synthetic": True, "smoke_challenge": challenge, "source_id": "smoke:public-synthetic",
                     "quote_basis": "close", "return_kind": "price_return", "return_type": "simple", "currency": "USD",
                     "start_date": "2026-01-02", "end_date": "2026-01-05", "value": 0.02, "coverage_status": "complete"}
            wire = json.dumps(facts).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

        def do_POST(self):
            rejected.append(self.path)
            self.send_error(405)

    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="studio-harness-smoke-") as temporary:
            work = Path(temporary)
            observer = work / "observer.mjs"
            observer.write_text(OBSERVER)
            patch = work / "smoke.patch.json"
            patch.write_text(json.dumps([
                {"id": "system-prompt", "config": {"persona": "This is a connectivity probe with synthetic public facts. Read read_briefing_context exactly once. Do not call any other tool or write a report. Reply only using the requested exact verification format."}},
                {"insert": [{"id": "harness-smoke-observer", "name": str(observer)}]},
            ]))
            allowed = ("HOME", "PATH", "TMPDIR", "LANG", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME",
                       "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS")
            env = {key: environment[key] for key in allowed if key in environment}
            env.update({"DSH_HOME": str(work / "home"), "DSH_PERMISSION_MODE": "read-only", "DSH_TOOLS_MODE": "native", "DSH_TELEMETRY_DISABLED": "1",
                        "INVESTMENT_STUDIO_BRIEFING_PROJECT_ROOT": str(ROOT), "INVESTMENT_STUDIO_BRIEFING_API_BASE_URL": f"http://127.0.0.1:{server.server_port}/api",
                        "INVESTMENT_STUDIO_BRIEFING_REPORT_ID": "synthetic-smoke", "INVESTMENT_STUDIO_BRIEFING_RUN_TOKEN": "synthetic-smoke-token", "INVESTMENT_STUDIO_BRIEFING_HARNESS_MODE": "write"})
            command = [str(ROOT / "infra/harness/run.sh"), "--profile", "headless", "--patch", str(ROOT / "infra/config/deepseek_harness.patch.yml"),
                       "--patch", str(ROOT / "apps/briefing/backend/config/briefing_harness.patch.yml"), "--patch", str(patch),
                       "Read read_briefing_context once. Then reply exactly SMOKE_OK:<smoke_challenge>:<return_kind>:<return_type>:<currency>, replacing the four fields with their tool values. Do not calculate or submit a report."]
            process = subprocess.Popen(command, env=env, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                return {"status": "failed", "reason": "timeout", "tool_reads": len(reads)}
            summaries = [line[len(MARKER):] for line in stderr.splitlines() if line.startswith(MARKER)]
            result = json.loads(summaries[-1]) if summaries else {}
            verified = (process.returncode == 0 and result.get("finish") == "completed" and result.get("reasoning_received")
                        and result.get("reasoning_effort") == "high" and result.get("tool_calls") == ["mcp__briefing__read_briefing_context"]
                        and not result.get("tool_error") and len(reads) == 1 and not rejected and stdout.strip() == expected)
            # Only typed metadata is emitted, never provider bodies, thoughts, credentials or URLs.
            return {"status": "passed" if verified else "failed", "reason": None if verified else "harness_contract_not_satisfied",
                    "exit_code": process.returncode, "tool_reads": len(reads), "rejected_tools": len(rejected), "answer_verified": stdout.strip() == expected,
                    **result}
    except (OSError, ValueError, KeyError):
        return {"status": "failed", "reason": "runtime_unavailable_or_invalid_output"}
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    result = run_probe(dict(os.environ))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "passed" else 1)
