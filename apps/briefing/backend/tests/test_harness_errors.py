from types import SimpleNamespace

import pytest

from briefing_app import runner


@pytest.mark.parametrize("mode", ["write", "review"])
def test_missing_provider_model_channel_is_actionable_without_fallback(monkeypatch, caplog, mode):
    calls = []

    def popen(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(returncode=1, communicate=lambda **kwargs: (
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
