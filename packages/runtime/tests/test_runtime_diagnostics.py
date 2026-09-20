import json
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from studio_runtime import install_diagnostics, operation


def test_standalone_job_counts_successful_and_failed_sql_without_an_http_app():
    # A fresh interpreter is essential: HTTP tests otherwise install the global
    # SQL observers first and conceal missing CLI initialization.
    script = '''
from sqlalchemy import create_engine, text
engine = create_engine("sqlite://")
from studio_runtime import operation
try:
    with operation("maintenance_job"):
        with engine.connect() as connection:
            connection.execute(text("SELECT :secret"), {"secret": "private-value"})
            with operation("nested_job"):
                connection.execute(text("SELECT * FROM missing_table WHERE name=:secret"),
                                   {"secret": "private-value"})
except Exception:
    pass
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    events = [json.loads(line) for line in result.stderr.splitlines()]
    finished = {event["operation"]: event for event in events if event["event"] == "operation_finished"}
    assert finished["maintenance_job"]["sql_count"] == 2
    assert finished["nested_job"]["sql_count"] == 1
    assert finished["maintenance_job"]["sql_ms"] > 0
    assert all(event["outcome"] == "error" for event in finished.values())
    assert len({event["request_id"] for event in events}) == 1
    assert "private-value" not in result.stderr
    assert "missing_table" not in result.stderr


def test_request_timings_correlate_threaded_sql_without_recording_inputs(capsys):
    app = FastAPI()
    engine = create_engine("sqlite://")

    @app.get("/records/{record_id}")
    def read_record(record_id: str):
        with operation("read_record"):
            with engine.connect() as connection:
                connection.execute(text("SELECT :secret"), {"secret": "private-value"})
        return {"ok": True}

    install_diagnostics(app, "test")
    # Capture the diagnostic handler itself; pytest logging propagation is disabled.
    import studio_runtime
    output = []
    original = studio_runtime.emit
    studio_runtime.emit = lambda event, **fields: output.append({"event": event, **fields})
    try:
        response = TestClient(app).get("/records/private-id?token=secret-token", headers={"X-Request-ID": "request-1234"})
    finally:
        studio_runtime.emit = original
    assert response.headers["x-request-id"] == "request-1234"
    assert "db;dur=" in response.headers["server-timing"]
    event = output[-1]
    assert event["route"] == "/records/{record_id}"
    assert event["sql_count"] == 1
    assert event["sql_ms"] >= 0
    assert event["spans"]["read_record"] >= 0
    assert not any(value in json.dumps(output) for value in ["secret-token", "private-value", "private-id"])


def test_failures_record_location_not_exception_payload(monkeypatch):
    import studio_runtime
    output = []
    monkeypatch.setattr(studio_runtime, "emit", lambda event, **fields: output.append({"event": event, **fields}))
    app = FastAPI()

    @app.get("/fail")
    def fail():
        raise ValueError("secret credential must never enter diagnostics")

    install_diagnostics(app, "test")
    response = TestClient(app, raise_server_exceptions=False).get("/fail")
    assert response.status_code == 500
    assert output[-1]["error_type"] == "ValueError"
    assert output[-1]["frames"][-1]["function"] == "fail"
    assert "secret credential" not in json.dumps(output)


def test_asgi_server_exception_log_cannot_reveal_the_original_error():
    import traceback
    import pytest
    app = FastAPI()

    @app.get('/private-failure')
    def private_failure():
        raise ValueError('provider-token-private-value')

    install_diagnostics(app, 'test')
    with pytest.raises(RuntimeError, match='diagnostic request_id=') as caught:
        TestClient(app).get('/private-failure')
    rendered = ''.join(traceback.format_exception(caught.value))
    assert 'provider-token-private-value' not in rendered
    assert caught.value.__suppress_context__
