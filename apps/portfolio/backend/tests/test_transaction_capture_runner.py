from io import StringIO
import subprocess
from types import SimpleNamespace

import pytest

from portfolio_app.services import transaction_capture_runner as runner


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("offline-harness", 1), OSError("pipe read failed")])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_wait_stops_group_once_closes_pipe_and_preserves_cause(monkeypatch, caplog, failure, cleanup_fails):
    # Polling/pauses are covered by runtime tests; this test owns process cleanup.
    monkeypatch.setattr(runner, "communicate_model_process", lambda process, **kwargs: process.communicate(**kwargs))
    waits, stopped = [], []
    process = SimpleNamespace(returncode=None, stderr=StringIO())

    def communicate(*, timeout):
        waits.append(timeout)
        raise failure

    def stop(item):
        assert item is process
        assert not item.stderr.closed
        stopped.append(item)
        if cleanup_fails:
            raise RuntimeError("credential-bearing-cleanup-details")
        item.returncode = -15

    process.communicate = communicate
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(runner, "_stop_process_group", stop)
    if isinstance(failure, subprocess.TimeoutExpired):
        assert runner._run_harness_process(portfolio_id="p", batch_id="b", run_token="offline") == (-15 if not cleanup_fails else -1, True, None)
    else:
        with pytest.raises(OSError) as caught:
            runner._run_harness_process(portfolio_id="p", batch_id="b", run_token="offline")
        assert caught.value is failure
    assert len(waits) == 1  # Never enter a second, unbounded communicate.
    assert waits[0] > 0
    assert stopped == [process]
    assert process.stderr.closed
    assert ("cleanup failed (RuntimeError)" in caplog.text) == cleanup_fails
    assert "credential-bearing-cleanup-details" not in caplog.text


def test_completed_wait_preserves_failure_classification_and_closes_pipe(monkeypatch):
    process = SimpleNamespace(returncode=78, stderr=StringIO(), communicate=lambda **kw: (None, ""))
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(runner, "_stop_process_group", lambda *a: pytest.fail("Completed process must not be stopped again"))
    result = runner._run_harness_process(portfolio_id="p", batch_id="b", run_token="offline")
    assert result == (78, False, runner._harness_failure("", 78))
    assert process.stderr.closed


def test_capture_timeout_retains_numeric_usage_in_structured_log_with_attempt(monkeypatch, tmp_path):
    import json
    from pathlib import Path
    import studio_runtime
    records = []
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(studio_runtime, "emit", lambda event, **fields: records.append((event, fields)))
    monkeypatch.setattr(runner, "communicate_model_process", lambda process, **kwargs: process.communicate(**kwargs))
    def start(*args, **kwargs):
        env = kwargs["env"]
        common = {"invocation_id": env["INVESTMENT_STUDIO_MODEL_USAGE_INVOCATION"], "session_id": "offline", "turn": 0, "step": 0}
        Path(env["INVESTMENT_STUDIO_MODEL_USAGE_FILE"]).write_text("\n".join(json.dumps({**common, **row}) for row in (
            {"kind": "start"}, {"kind": "usage", "tokens": {"uncached_input_tokens": 17,
                "output_tokens": 3, "cache_read_tokens": 0, "cache_write_tokens": 0}})))
        def communicate(**_):
            raise subprocess.TimeoutExpired("offline", 2)
        return SimpleNamespace(returncode=-15, stderr=StringIO(), communicate=communicate)
    monkeypatch.setattr(runner.subprocess, "Popen", start)
    monkeypatch.setattr(runner, "_stop_process_group", lambda process: None)
    assert runner._run_harness_process(portfolio_id="p", batch_id="b", run_token="private-token", attempt=4)[1]
    event, receipt = records[0]
    assert event == "model_provider_usage" and receipt["batch_id"] == "b" and receipt["attempt"] == 4
    assert receipt["invocations"][0]["status"] == "partial"
    assert receipt["invocations"][0]["tokens"]["uncached_input_tokens"] == 17
    assert "private-token" not in json.dumps(records)
    assert list((tmp_path / "investment-studio/model-usage").iterdir()) == []
