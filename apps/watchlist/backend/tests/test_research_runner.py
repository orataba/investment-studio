import json

import pytest

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner as runner
from watchlist_app.services import sector_research


def test_availability_uses_the_same_explicit_runtime_paths_as_the_launcher(monkeypatch, tmp_path):
    env_file = tmp_path / "research.env"
    env_file.write_text("DEEPSEEK_API_KEY=fixture\n")
    pnpm = tmp_path / "pnpm"
    pnpm.write_text("#!/bin/sh\n")
    pnpm.chmod(0o700)
    monkeypatch.setenv("INVESTMENT_STUDIO_SECRET_ROOT", str(tmp_path / "missing-default"))
    monkeypatch.setenv("INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ENV_FILE", str(env_file))
    monkeypatch.setenv("INVESTMENT_STUDIO_PORTFOLIO_COPILOT_PNPM", str(pnpm))
    assert runner.harness_available()
    pnpm.chmod(0o600)
    assert not runner.harness_available()


@pytest.mark.parametrize("marker,expected", [
    ({"type": "ValidationError", "summary": "事实核证结果格式不完整。", "diagnostic": "missing: reviews"},
     {"type": "ValidationError", "summary": "事实核证结果格式不完整。", "diagnostic": "missing: reviews", "exit_code": 1}),
    (None, {"type": "ProcessExit", "summary": "研究运行进程退出，未生成有效结果。", "exit_code": 1}),
])
def test_failed_runner_retains_only_explicit_safe_error_marker(client, monkeypatch, marker, expected):
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="runner-topic", title="行业检查"))
        session.flush()
        session.add(ResearchEntry(entry_id="runner-test", topic_id="runner-topic", kind="analysis",
            title="行业检查", status="queued", context_json={"sector_run": True, "retained": "input"}))
        session.commit()
    monkeypatch.setattr(sector_research, "prepare_run", lambda run_id: None)
    private_stderr = "provider failure api_key=fixture-private-secret\nraw model contents must stay private"
    stderr = private_stderr
    if marker:
        stderr += "\nSECTOR_REVIEW_ERROR " + json.dumps(marker, ensure_ascii=False)

    class FailedProcess:
        returncode = 1

        def communicate(self, timeout):
            return '{"reviews":[]}', stderr

    def start_process(*args, **kwargs):
        assert kwargs["stderr"] == runner.subprocess.PIPE
        return FailedProcess()

    monkeypatch.setattr(runner.subprocess, "Popen", start_process)
    runner.run_analysis("runner-test")
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "runner-test")
        assert run.status == "failed"
        assert run.context_json["runtime_error"] == expected
        assert run.context_json["rejected_reply"] == '{"reviews":[]}'
        assert run.context_json["retained"] == "input"
        assert expected["summary"] in run.body
        assert "模型连接" not in run.body
        if marker:
            assert "事实核证失败" in run.body
            assert "missing: reviews" not in run.body
        stored = json.dumps(run.context_json, ensure_ascii=False) + run.body
        assert "fixture-private-secret" not in stored
        assert "raw model contents" not in stored


@pytest.mark.parametrize("sector_run", [True, False])
def test_insufficient_balance_is_explained_without_retaining_provider_stderr(client, monkeypatch, sector_run):
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="quota-topic", title="研究检查"))
        session.flush()
        session.add(ResearchEntry(entry_id="quota-test", topic_id="quota-topic", kind="analysis",
            title="研究检查", status="queued", context_json={"sector_run": sector_run}))
        session.commit()
    monkeypatch.setattr(sector_research, "prepare_run", lambda run_id: None)

    class FailedProcess:
        returncode = 1

        def communicate(self, timeout):
            return "", 'private-provider-data\ndsh: QUOTA: Insufficient Balance\nSECTOR_REVIEW_ERROR {"type":"ValueError","summary":"事实核证未完成"}'

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    runner.run_analysis("quota-test")
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "quota-test")
        assert run.status == "failed"
        assert run.context_json["runtime_error"]["type"] == "InsufficientBalance"
        assert "余额不足" in run.body
        assert "事实核证失败" not in run.body
        assert "private-provider-data" not in json.dumps(run.context_json) + run.body
