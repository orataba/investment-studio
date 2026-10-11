"""The opt-in live probe itself is verified against a zero-charge provider."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_live_probe_uses_only_synthetic_scope_and_emits_safe_metadata():
    if not (ROOT / "infra/harness/node_modules/@deepseek-ai/dsh/lib/bin.js").exists() or not shutil.which("node"):
        pytest.skip("Pinned Harness and Node must be provisioned")
    spec = importlib.util.spec_from_file_location("harness_smoke", ROOT / "infra/scripts/probe_deepseek_harness.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            if len(requests) == 1:
                delta = {"role": "assistant", "reasoning_content": "Synthetic private reasoning that must never be printed.",
                         "tool_calls": [{"index": 0, "id": "fixture", "type": "function", "function": {"name": "mcp__briefing__read_briefing_context", "arguments": "{}"}}]}
                finish = "tool_calls"
            else:
                challenge = re.search(r"smoke_challenge[^a-z0-9]+([a-f0-9]{32})", json.dumps(request))[1]
                delta = {"role": "assistant", "content": f"SMOKE_OK:{challenge}:price_return:simple:USD"}
                finish = "stop"
            chunks = [{"id": "fixture", "object": "chat.completion.chunk", "created": 1, "model": "deepseek-v4.1-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"id": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}]
            wire = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR") if key in os.environ}
    env.update(DEEPSEEK_API_KEY="offline-only-secret", DEEPSEEK_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1")
    try:
        result = probe.run_probe(env, timeout=45)
    finally:
        server.shutdown()
        server.server_close()
    assert result["status"] == "passed", result
    assert result["tool_reads"] == 1 and result["reasoning_received"] and result["answer_verified"]
    assert len(requests) == 2
    assert requests[0]["max_tokens"] == 32768
    assert requests[0]["reasoning_effort"] == "high"
    assert "offline-only-secret" not in json.dumps(result)
    assert "private reasoning" not in json.dumps(result)
