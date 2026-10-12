from io import StringIO
import subprocess
from types import SimpleNamespace

import pytest

from briefing_app import runner


@pytest.mark.parametrize("mode", ["write", "review"])
def test_missing_provider_model_channel_is_actionable_without_fallback(monkeypatch, caplog, mode):
    calls = []

    def popen(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(returncode=1, stdout=StringIO(), stderr=StringIO(), communicate=lambda **kwargs: (
            "", 'dsh: SERVER: 503: {"code":"model_not_found",'
            '"message":"No available channel for model deepseek-v4.1-flash"}\n'
            'authorization: Bearer private-value\nsk-testcredential',
        ))

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    with pytest.raises(ValueError, match="服务商当前没有可用的模型通道"):
        runner._run_harness("report-1", mode, "test-grant")
    assert len(calls) == 1
    assert calls[0][-1] == mode
    assert "model_not_found" in caplog.text
    assert "private-value" not in caplog.text
    assert "sk-testcredential" not in caplog.text


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("offline-harness", 1), OSError("pipe read failed")])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_wait_cleans_group_and_pipes_without_masking_original_failure(monkeypatch, caplog, failure, cleanup_fails):
    monkeypatch.setattr(runner, "communicate_model_process", lambda process, **kwargs: process.communicate(**kwargs))
    process = SimpleNamespace(returncode=None, stdout=StringIO(), stderr=StringIO())
    stopped = []

    def communicate(*, timeout):
        assert timeout == 1800
        raise failure

    def stop(item):
        assert not item.stdout.closed and not item.stderr.closed
        stopped.append(item)
        if cleanup_fails:
            raise RuntimeError("credential-bearing-cleanup-details")

    process.communicate = communicate
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(runner, "_stop_process_group", stop)
    if isinstance(failure, subprocess.TimeoutExpired):
        with pytest.raises(ValueError, match="本轮校稿超时，未发布报告") as caught:
            runner._run_harness("report-1", "review", "offline")
        assert caught.value.__cause__ is failure
    else:
        with pytest.raises(OSError) as caught:
            runner._run_harness("report-1", "review", "offline")
        assert caught.value is failure
    assert stopped == [process]
    assert process.stdout.closed and process.stderr.closed
    assert ("cleanup failed (RuntimeError)" in caplog.text) == cleanup_fails
    assert "credential-bearing-cleanup-details" not in caplog.text
