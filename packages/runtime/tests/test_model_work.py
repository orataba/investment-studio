import json
import subprocess
from unittest.mock import Mock

import pytest
from studio_runtime.model_work import ModelWorkPaused, communicate_model_process, model_work_status, require_model_work


def test_control_fails_closed_and_is_reloaded(tmp_path, monkeypatch):
    path = tmp_path / "control.json"
    monkeypatch.setenv("INVESTMENT_STUDIO_MODEL_WORK_CONTROL_FILE", str(path))
    for text in (None, "{}", '{"paused":"false"}', "[]", "x" * 4097):
        if text is not None:
            path.write_text(text)
        assert model_work_status()["paused"] is True
        with pytest.raises(ModelWorkPaused):
            require_model_work()
    path.write_text(json.dumps({"paused": False}))
    require_model_work()
    path.write_text(json.dumps({"paused": True}))
    with pytest.raises(ModelWorkPaused):
        require_model_work()


def test_running_wait_observes_pause(tmp_path, monkeypatch):
    path = tmp_path / "control.json"
    path.write_text('{"paused":false}')
    monkeypatch.setenv("INVESTMENT_STUDIO_MODEL_WORK_CONTROL_FILE", str(path))
    process = Mock()
    def pause(**_kwargs):
        path.write_text('{"paused":true}')
        raise subprocess.TimeoutExpired("test", 2)
    process.communicate.side_effect = pause
    with pytest.raises(ModelWorkPaused):
        communicate_model_process(process, timeout=30)
    assert process.communicate.call_count == 1
