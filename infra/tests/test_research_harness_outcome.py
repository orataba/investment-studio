"""Exercise the pinned native loop against a local, zero-charge streaming provider."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('mode,briefing_mode,budget,finish', [
    ('sector', '', 65536, 'length'), ('review', '', 65536, 'length'),
    ('sector', '', 65536, 'content_filter'),
    ('risk', '', 32768, 'stop'), ('', '', 32768, 'stop'), ('', 'review', 65536, 'stop'),
])
def test_native_output_boundary_and_product_budget_scope(tmp_path, mode, briefing_mode, budget, finish):
    if not (ROOT / 'infra/harness/node_modules/@deepseek-ai/dsh/lib/bin.js').exists() or not shutil.which('node'):
        pytest.skip('Pinned Harness and Node must be provisioned for the native contract probe')
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            chunks = [
                {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1,
                 'model': 'deepseek-v4.1-flash', 'choices': [{'index': 0,
                 'delta': {'role': 'assistant', 'content': 'fixture'}, 'finish_reason': None}]},
                {'id': 'fixture', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                 'usage': {'prompt_tokens': 100, 'completion_tokens': budget if finish == 'length' else 1}},
            ]
            wire = (''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    patch = tmp_path / 'offline.patch.json'
    patch.write_text(json.dumps([{'id': 'mcp-watchlist-research', 'disabled': True},
        {'insert': [{'id': 'research-harness-outcome',
                     'name': str(ROOT / 'apps/watchlist/backend/scripts/research_harness_outcome.mjs')}]}]))
    env = {key: os.environ[key] for key in ('HOME', 'PATH', 'TMPDIR') if key in os.environ}
    env.update(DEEPSEEK_API_KEY='offline-fixture', DEEPSEEK_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1',
        DSH_HOME=str(tmp_path / 'home'), DSH_TOOLS_MODE='native', DSH_PERMISSION_MODE='read-only',
        DSH_TELEMETRY_DISABLED='1', INVESTMENT_STUDIO_RESEARCH_PROJECT_ROOT=str(ROOT),
        INVESTMENT_STUDIO_RESEARCH_PERSONA='Reply briefly. This is an offline test.',
        INVESTMENT_STUDIO_WATCHLIST_HARNESS_MODE=mode, INVESTMENT_STUDIO_BRIEFING_HARNESS_MODE=briefing_mode)
    try:
        result = subprocess.run([str(ROOT / 'infra/harness/run.sh'), '--profile', 'headless',
            '--patch', str(ROOT / 'infra/config/deepseek_harness.patch.yml'),
            '--patch', str(ROOT / 'apps/watchlist/backend/config/sector_harness.patch.yml'),
            '--patch', str(patch), 'Reply briefly.'], env=env, cwd=tmp_path,
            capture_output=True, text=True, timeout=45)
    finally:
        server.shutdown()
    assert len(requests) == 1, result.stderr
    assert requests[0]['stream'] is True
    assert requests[0]['max_tokens'] == budget
    marker = 'RESEARCH_HARNESS_END {"reason":"max-tokens"}'
    if finish == 'length':
        assert result.returncode == 1
        assert marker in result.stderr.splitlines()
        assert 'fixture' not in result.stderr  # No response or reasoning in the marker.
    elif finish == 'content_filter':
        assert result.returncode == 1
        assert 'RESEARCH_HARNESS_END {"reason":"content-filter"}' in result.stderr.splitlines()
    else:
        assert result.returncode == 0, result.stderr
        assert marker not in result.stderr
