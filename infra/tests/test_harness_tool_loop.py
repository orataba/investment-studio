"""Real pinned Harness + real app MCP processes, with no external provider or DB."""
import json
import base64
import os
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("app,api_status", [("watchlist", 200), ("briefing", 200), ("portfolio", 200), ("portfolio-image", 200), ("watchlist", 422), ("watchlist", 500)])
def test_native_reasoning_tool_loop_preserves_bound_facts_and_errors(tmp_path, app, api_status):
    if not (ROOT / "infra/harness/node_modules/@deepseek-ai/dsh/lib/bin.js").exists() or not shutil.which("node"):
        pytest.skip("Pinned Harness and Node must be provisioned for the native contract probe")
    from studio_runtime.provider_usage import UsageCapture
    usage = UsageCapture(tmp_path)
    image_case = app == "portfolio-image"
    app = "portfolio" if image_case else app
    configuration = {
        "watchlist": ("apps/watchlist/backend/config/research_harness.patch.yml", "research", "read_research_context", "/api/research/runs/offline-run/read", {
            "INVESTMENT_STUDIO_RESEARCH_PROJECT_ROOT": str(ROOT), "INVESTMENT_STUDIO_RESEARCH_RUN_ID": "offline-run",
            "INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN": "offline-bound-token", "INVESTMENT_STUDIO_RESEARCH_PERSONA": "Read the bound fixture; report its limitations."}),
        "briefing": ("apps/briefing/backend/config/briefing_harness.patch.yml", "briefing", "read_briefing_context", "/api/reports/offline-report/context", {
            "INVESTMENT_STUDIO_BRIEFING_PROJECT_ROOT": str(ROOT), "INVESTMENT_STUDIO_BRIEFING_REPORT_ID": "offline-report",
            "INVESTMENT_STUDIO_BRIEFING_RUN_TOKEN": "offline-bound-token", "INVESTMENT_STUDIO_BRIEFING_HARNESS_MODE": "write"}),
        "portfolio": ("apps/portfolio/backend/config/portfolio_copilot_deepseek_harness.patch.yml", "portfolio", "get_screenshot_analysis_context", "/api/portfolios/offline-portfolio/transaction-capture-batches/offline-batch/agent-context", {
            "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PROJECT_ROOT": str(ROOT), "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PORTFOLIO_ID": "offline-portfolio",
            "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_BATCH_ID": "offline-batch", "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_RUN_TOKEN": "offline-bound-token"}),
    }
    patch, namespace, tool, api_path, app_env = configuration[app]
    arguments = "{}"
    image_data = "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAFElEQVR4nGP4TyJgGNUwqmH4agAAr639H708R/EAAAAASUVORK5CYII="
    if image_case:
        tool, arguments = "get_screenshot_image", '{"capture_id":"capture-one"}'
    model_requests, api_requests = [], []
    facts = {"source_id": "fixture:returns", "quote_basis": "adjusted_close", "return_kind": "total_return", "return_type": "simple", "currency": "HKD",
             "start_date": "2026-09-01", "end_date": "2026-09-03", "coverage_status": "unavailable", "reason": "missing_trading_session", "value": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def api_reply(self, body=None):
            api_requests.append((self.path, self.headers.get("Authorization"), body))
            if image_case and self.path.endswith("/image"):
                wire = base64.b64decode(image_data)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(wire)))
                self.end_headers()
                self.wfile.write(wire)
                return
            payload = facts if api_status == 200 else {"detail": [{"loc": ["body", "start_date"], "type": "value_error", "msg": "Requested interval has a missing trading session", "input": "private-invalid-input"}]}
            if image_case:
                payload = {"batch": {"captures": [{"capture_id": "capture-one"}]}}
            wire = json.dumps(payload).encode()
            self.send_response(api_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

        def do_GET(self):
            self.api_reply()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path != "/v1/chat/completions":
                self.api_reply(body)
                return
            model_requests.append(body)
            if len(model_requests) == 1:
                # Common gateway streaming shape: later deltas omit id/name.
                deltas = [
                    {"role": "assistant", "reasoning_content": "Check the bound evidence before answering."},
                    {"tool_calls": [{"index": 0, "id": "call_fixture", "type": "function", "function": {"name": f"mcp__{namespace}__{tool}", "arguments": arguments[:1]}}]},
                    {"tool_calls": [{"index": 0, "id": None, "function": {"name": "", "arguments": arguments[1:]}}]},
                ]
                finish = "tool_calls"
            else:
                deltas, finish = [{"role": "assistant", "content": "The requested interval is unavailable; no complete return can be reported."}], "stop"
            chunks = [{"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": "deepseek-v4.1-flash",
                       "choices": [{"index": 0, "delta": delta, "finish_reason": None}]} for delta in deltas]
            chunks.append({"id": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
            wire = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    api_env = {"watchlist": "INVESTMENT_STUDIO_RESEARCH_API_BASE_URL", "briefing": "INVESTMENT_STUDIO_BRIEFING_API_BASE_URL", "portfolio": "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_API_BASE_URL"}[app]
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR") if key in os.environ}
    env.update(app_env)
    env.update({api_env: base + "/api", "DEEPSEEK_API_KEY": "offline-provider-key", "DEEPSEEK_BASE_URL": base + "/v1", "DSH_HOME": str(tmp_path / "home"),
                "DSH_TOOLS_MODE": "native", "DSH_PERMISSION_MODE": "read-only", "DSH_TELEMETRY_DISABLED": "1", "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME": "deepseek-v4.1-flash"})
    env.update(usage.environment("capture" if app == "portfolio" else "generation"))
    try:
        result = subprocess.run([str(ROOT / "infra/harness/run.sh"), "--profile", "headless", "--patch", str(ROOT / "infra/config/deepseek_harness.patch.yml"),
                                 "--patch", str(ROOT / patch), "Read the bound context once and explain whether the requested return is available."],
                                env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == 0, result.stderr
    assert len(model_requests) == 2, result.stderr
    assert len(api_requests) == (2 if image_case else 1) and api_requests[0][0] == api_path
    assert all(request[1] == "Bearer offline-bound-token" for request in api_requests)
    first, second = model_requests
    names = {entry["function"]["name"] for entry in first["tools"]}
    assert names and all(name.startswith(f"mcp__{namespace}__") for name in names)
    assert not any("commit" in name.lower() for name in names)
    assert first["thinking"] == {"type": "enabled"}
    assert first["reasoning_effort"] == "high"
    assistant = next(message for message in second["messages"] if message.get("tool_calls"))
    assert assistant["reasoning_content"] == "Check the bound evidence before answering."
    assert assistant["tool_calls"][0]["id"] == "call_fixture"
    assert assistant["tool_calls"][0]["function"]["name"] == f"mcp__{namespace}__{tool}"
    reply = next(message for message in second["messages"] if message["role"] == "tool")
    assert reply["tool_call_id"] == "call_fixture"
    delivered = str(reply["content"])
    if image_case:
        image_parts = [part for message in second["messages"] if isinstance(message["content"], list)
                       for part in message["content"] if part.get("type") == "image_url"]
        assert len(image_parts) == 1, delivered
        assert image_parts[0]["image_url"]["url"] == "data:image/png;base64," + image_data
    elif api_status == 200:
        for key, value in facts.items():
            assert key in delivered
            if value is not None:
                assert str(value) in delivered
        assert '"value":null' in delivered.replace(" ", "")
    elif api_status == 422:
        assert "invalid_request" in delivered and "missing trading session" in delivered
        assert "private-invalid-input" not in delivered
    else:
        assert "HTTP Error 500" in delivered
        assert "private-invalid-input" not in delivered

    receipt = usage.read()["invocations"][0]
    assert receipt["status"] == "complete"
    assert receipt["reported_calls"] == receipt["started_calls"] == 2
    assert receipt["tokens"]["uncached_input_tokens"] == 200
    assert receipt["tokens"]["output_tokens"] == 40
    assert "bound facts" not in usage.path.read_text() and "call_fixture" not in usage.path.read_text()
