from io import StringIO
import subprocess
from types import SimpleNamespace

import pytest

from portfolio_app.services import transaction_capture_runner as runner


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("offline-harness", 1), OSError("pipe read failed")])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_wait_stops_group_once_closes_pipe_and_preserves_cause(monkeypatch, caplog, failure, cleanup_fails):
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
