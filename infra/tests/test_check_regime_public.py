from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import pwd
import subprocess
import threading
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_regime_public.py"
SPEC = importlib.util.spec_from_file_location("check_regime_public", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

INDEX = """<!doctype html><h1 id="dashboard-title">Regime</h1><div id="latest-grid"></div>
<link rel="stylesheet" href="/static/styles.css?v=release">
<script src="/static/studio.js?v=release"></script><script src="/static/app.js?v=release"></script>"""
AUDIT = '<link rel="stylesheet" href="/static/styles.css"><script src="/static/audit.js"></script>'


def _json(data):
    return 200, {"Content-Type": "application/json"}, json.dumps(data).encode()


def _responses():
    return {
        "/": (200, {"Content-Type": "text/html"}, INDEX.encode()),
        "/static/styles.css?v=release": (200, {"Content-Type": "text/css"}, b"body {}"),
        "/static/studio.js?v=release": (200, {"Content-Type": "text/javascript"}, b"let studio = {};"),
        "/static/app.js?v=release": (200, {"Content-Type": "application/javascript"}, b"let app = {};"),
        "/static/audit.html": (302, {"Location": "/audit"}, b""),
        "/audit": (302, {"Location": "https://home.example/login?next=https://regime.example/audit"}, b""),
        "/api/health": _json({"ok": True, "status": "healthy", "all_markets_fresh": True}),
        "/api/markets": _json({"default": "us", "markets": [{"id": "us"}]}),
        "/api/latest?market=us": _json({"latest": [{"symbol": "SPY"}], "row_count": 1, "latest_date": "2026-09-18"}),
    }


@contextmanager
def _server(responses):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Cookie"), self.headers.get("Authorization")))
            status, headers, body = responses.get(self.path, (404, {}, b"missing"))
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_public_checks_actual_html_assets_data_and_anonymous_audit_boundary(capsys, monkeypatch):
    # Ambient proxy settings must not add credentials or redirect local ingress.
    monkeypatch.setenv("http_proxy", "http://invalid.invalid:1")
    with _server(_responses()) as (url, requests):
        assert MODULE.main(["--url", url]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "check": "public_http", "assets": 3, "audit_requires_auth": True,
        "health": "healthy", "all_markets_fresh": True, "default_market": "us",
        "latest_rows": 1, "latest_date": "2026-09-18",
    }
    assert {path for path, _, _ in requests} == set(_responses())
    assert all(cookie is None and auth is None for _, cookie, auth in requests)


@pytest.mark.parametrize(("path", "response", "error"), [
    ("/", (404, {}, b"missing"), "expected HTTP 200, got 404"),
    ("/", (302, {"Location": "/login"}, b""), "expected HTTP 200, got 302"),
    ("/", (200, {"Content-Type": "text/html"}, b"<h1>Login</h1>"), "not the Regime dashboard"),
    ("/static/app.js?v=release", (403, {}, b"denied"), "expected HTTP 200, got 403"),
    ("/static/app.js?v=release", (200, {"Content-Type": "text/html"}, b"login"), "wrong Content-Type"),
    ("/static/styles.css?v=release", (200, {"Content-Type": "text/css"}, b""), "empty response"),
    ("/static/audit.html", (200, {"Content-Type": "text/html"}, AUDIT.encode()), "protected /audit route"),
    ("/audit", (200, {"Content-Type": "text/html"}, AUDIT.encode()), "require authentication"),
    ("/audit", (302, {"Location": "/audit"}, b""), "require authentication"),
    ("/api/health", _json({"ok": False, "status": "unavailable"}), "data service is unavailable"),
    ("/api/markets", _json({"default": "us", "markets": [{"id": "hk"}]}), "missing default market"),
    ("/api/latest?market=us", _json({"latest": [], "row_count": 0}), "latest rows"),
])
def test_public_rejects_false_readiness(path, response, error, capsys):
    responses = _responses()
    responses[path] = response
    with _server(responses) as (url, _):
        assert MODULE.main(["--url", url]) == 1
    assert error in capsys.readouterr().err


def test_freshness_is_reported_separately_from_public_availability(capsys):
    responses = _responses()
    responses["/api/health"] = _json({"ok": True, "status": "degraded", "all_markets_fresh": False})
    responses["/audit"] = (401, {}, b"")
    with _server(responses) as (url, _):
        assert MODULE.main(["--url", url]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["health"] == "degraded"
    assert summary["all_markets_fresh"] is False


def _stage(tmp_path):
    directory = tmp_path / "release" / "deploy" / "regime-ui"
    directory.mkdir(parents=True)
    for name, body in {
        "index.html": INDEX, "audit.html": AUDIT, "styles.css": "body {}",
        "studio.js": "let studio = {};", "app.js": "let app = {};", "audit.js": "let audit = {};",
    }.items():
        (directory / name).write_text(body)
    return tmp_path / "release", directory


def test_staged_checks_both_pages_resources_as_actual_worker(tmp_path, monkeypatch):
    release, directory = _stage(tmp_path)
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=33))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs == {"capture_output": True, "check": False, "timeout": 10}
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(MODULE.subprocess, "run", run)
    result = MODULE.check_staged(release, "www-data")
    assert result["readable_files"] == 6
    assert calls == [
        ["runuser", "-u", "www-data", "--", "test", "-r", str(path)]
        for path in sorted(directory.iterdir())
    ]


def test_worker_access_failure_is_not_hidden_by_root_readability(tmp_path, monkeypatch, capsys):
    release, _ = _stage(tmp_path)
    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 0)
    monkeypatch.setattr(MODULE.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=33))
    monkeypatch.setattr(MODULE.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1))
    assert MODULE.main(["--release-root", str(release), "--nginx-user", "www-data"]) == 1
    assert "Nginx worker www-data cannot read" in capsys.readouterr().err


def test_staged_can_check_current_user_without_privilege_escalation(tmp_path):
    release, _ = _stage(tmp_path)
    result = MODULE.check_staged(release, pwd.getpwuid(os.geteuid()).pw_name)
    assert result["readable_files"] == 6


@pytest.mark.parametrize("asset", ["https://example.com/app.js", "//example.com/app.js", "/static/../secret", "/static/%2e%2e/secret"])
def test_rejects_assets_outside_published_tree(asset):
    with pytest.raises(MODULE.CheckError, match="local /static/ paths"):
        MODULE.page_assets(INDEX.replace("/static/app.js?v=release", asset), dashboard=True)


def test_missing_audit_resource_fails_preflight(tmp_path):
    release, directory = _stage(tmp_path)
    (directory / "audit.js").unlink()
    with pytest.raises(MODULE.CheckError, match="missing or empty public file"):
        MODULE.check_staged(release, pwd.getpwuid(os.geteuid()).pw_name)
