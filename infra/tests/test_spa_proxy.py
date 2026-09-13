"""Exercise the real web proxy's identity boundary and error recovery."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


class EchoHeaders(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/interrupted':
            self.send_response(200)
            self.send_header('Content-Length', '100')
            self.end_headers()
            self.wfile.write(b'partial')
            self.wfile.flush()
            self.close_connection = True
            return
        payload = json.dumps(dict(self.headers)).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


@pytest.fixture
def proxy(tmp_path):
    backend = ThreadingHTTPServer(("127.0.0.1", 0), EchoHeaders)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    (tmp_path / "index.html").write_text("workspace")
    process = subprocess.Popen([
        "node", str(ROOT / "deploy/serve_spa_proxy.mjs"), "--dist", str(tmp_path),
        "--port", str(port), "--api-target", f"http://127.0.0.1:{backend.server_port}",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def request(path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                assert request("/") == (200, b"workspace")
                break
            except ConnectionRefusedError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        yield request, tmp_path, backend
    finally:
        process.terminate()
        process.communicate(timeout=5)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=2)


def test_proxy_preserves_browser_host_and_remote_peer(proxy):
    request, _, _ = proxy
    status, body = request("/api/auth/session", {"Host": "rebound.example", "X-Forwarded-For": "203.0.113.8"})
    headers = {key.lower(): value for key, value in json.loads(body).items()}
    assert status == 200
    assert headers["host"] == "rebound.example"
    assert headers["x-forwarded-for"] == "203.0.113.8, 127.0.0.1"
    # A normal loopback browser request still retains its local destination.
    _, body = request("/api/health")
    headers = {key.lower(): value for key, value in json.loads(body).items()}
    assert headers["host"].startswith("127.0.0.1:")
    assert headers["x-forwarded-for"] == "127.0.0.1"


def test_proxy_survives_invalid_path_missing_build_and_backend_failure(proxy):
    request, dist, backend = proxy
    assert request("/%00")[0] == 400
    assert request("/%ZZ")[0] == 400
    assert request("/" + "x" * 300)[0] == 400
    assert request("/assets/old-chunk.js")[0] == 404
    assert request("/instruments/600036.SH") == (200, b"workspace")
    (dist / "index.html").unlink()
    assert request("/missing")[0] == 404
    (dist / "index.html").write_text("rebuilt")
    assert request("/") == (200, b"rebuilt")
    backend.shutdown()
    backend.server_close()
    assert request("/api/health") == (502, b"API temporarily unavailable")
    assert request("/") == (200, b"rebuilt")


def test_proxy_ends_an_interrupted_upstream_response(proxy):
    request, _, _ = proxy
    with pytest.raises((http.client.IncompleteRead, http.client.RemoteDisconnected)):
        request('/api/interrupted')
    assert request('/') == (200, b'workspace')
