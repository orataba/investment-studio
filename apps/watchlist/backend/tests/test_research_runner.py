import json

import pytest

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner as runner
from watchlist_app.services import sector_research


@pytest.mark.parametrize("surface", ["conversation", "sector", "risk"])
def test_rejected_dispatch_does_not_leave_a_permanently_queued_run(client, monkeypatch, surface):
    from studio_identity import IdentityError
    from sqlalchemy import select
    from watchlist_app.db.models import InstrumentDetail
    from watchlist_app.api.routes import sector_research as sector_routes, risk_officer as risk_routes
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="dispatch-asset", instrument_type="etf",
            detail_view_type="etf", instrument_name="Dispatch Asset", metadata_json={}))
        session.commit()
    for module in (runner, sector_routes, risk_routes):
        monkeypatch.setattr(module, "harness_available", lambda: True)
    def reject(*args, **kwargs):
        raise IdentityError(503, "账号服务暂不可用")
    monkeypatch.setattr(runner, "issue_delegation", reject)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("A rejected dispatch must not start a model"))
    if surface == "conversation":
        topic = client.post("/api/research/topics", json={"title": "讨论", "instrument_ids": ["dispatch-asset"]}).json()
        path, body = f"/api/research/topics/{topic['topic_id']}/analysis", {"question": "当前风险如何？"}
    elif surface == "sector":
        path, body = "/api/sector-research/runs", {"instrument_ids": ["dispatch-asset"]}
    else:
        path, body = "/api/risk/review/runs", {"instrument_id": "dispatch-asset"}
    for _ in range(2):
        response = client.post(path, json=body)
        assert response.status_code == 503, response.text
    with get_session_factory()() as session:
        runs = list(session.scalars(select(ResearchEntry).where(ResearchEntry.kind == "analysis")))
        assert len(runs) == 2
        assert all(run.status == "failed" and run.completed_at is not None for run in runs)
        assert all("授权" in run.body for run in runs)


@pytest.mark.parametrize("failure_point", ["service_identity", "delegation"])
@pytest.mark.parametrize("status_code", [401, 403, 503])
def test_background_dispatch_failure_finishes_queued_record(client, monkeypatch, failure_point, status_code):
    from studio_identity import IdentityError
    from fastapi import HTTPException
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="dispatch-topic", title="研究"))
        session.flush()
        session.add(ResearchEntry(entry_id="dispatch-run", topic_id="dispatch-topic", kind="analysis",
                                 title="研究", status="queued", context_json={"sector_run": True}))
        session.commit()
    def reject(*args, **kwargs):
        error_type = IdentityError if failure_point == "service_identity" else HTTPException
        raise error_type(status_code, "private-response-with-fixture-credential")
    monkeypatch.setattr(runner, "service_principal" if failure_point == "service_identity" else "issue_delegation", reject)
    runner.run_analysis("dispatch-run")
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "dispatch-run")
        assert run.status == "failed" and run.completed_at is not None
        assert run.context_json["runtime_error"] == {"type": "AuthorizationUnavailable",
            "status_code": status_code, "summary": run.body}
        assert "private-response" not in json.dumps(run.context_json) + run.body


def test_every_harness_explicitly_delegates_the_single_run_credential():
    for name in ("research", "sector", "risk"):
        patch = (runner.ROOT / f"apps/watchlist/backend/config/{name}_harness.patch.yml").read_text()
        assert "INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN: !!js process.env.INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN" in patch
        assert "DEEPSEEK_BASE_URL: !!js process.env.DEEPSEEK_BASE_URL" in patch
        assert "DEEPSEEK_SEARCH_URL: !!js process.env.DEEPSEEK_SEARCH_URL" in patch
        assert "INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME: !!js process.env.INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME" in patch


def test_availability_uses_the_same_explicit_runtime_paths_as_the_launcher(monkeypatch, tmp_path):
    env_file = tmp_path / "research.env"
    env_file.write_text("DEEPSEEK_API_KEY=fixture\n")
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\n")
    node.chmod(0o700)
    entry = tmp_path / "infra/harness/node_modules/@deepseek-ai/dsh/lib/bin.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// installed runtime")
    launcher = tmp_path / "infra/harness/run.sh"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o700)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner.shutil, "which", lambda name: str(node))
    monkeypatch.setenv("INVESTMENT_STUDIO_SECRET_ROOT", str(tmp_path / "missing-default"))
    monkeypatch.setenv("INVESTMENT_STUDIO_PORTFOLIO_COPILOT_ENV_FILE", str(env_file))
    assert runner.harness_available()
    entry.unlink()
    assert not runner.harness_available()


@pytest.mark.parametrize("marker,expected", [
    ({"type": "ValidationError", "summary": "事实核证结果格式不完整。", "diagnostic": "missing: reviews"},
     {"type": "ValidationError", "summary": "事实核证结果格式不完整。", "diagnostic": "missing: reviews", "exit_code": 1}),
    ({"type": "FactReviewProcessExit", "summary": "本地事实核证进程退出，未生成核证结果；请检查研究运行环境。"},
     {"type": "FactReviewProcessExit", "summary": "本地事实核证进程退出，未生成核证结果；请检查研究运行环境。", "exit_code": 1}),
    ({"type": "MissingResearchDraft", "summary": "研究员未提交结构化草稿，本轮未进入事实核证或发布研究；已有研究记录保持不变。"},
     {"type": "MissingResearchDraft", "summary": "研究员未提交结构化草稿，本轮未进入事实核证或发布研究；已有研究记录保持不变。", "exit_code": 1}),
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
    monkeypatch.setattr(sector_research, "apply_result", lambda *args: pytest.fail("A failed process must not publish research"))
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
        if marker and marker["type"] != "MissingResearchDraft":
            assert "事实核证失败" in run.body
            assert "missing: reviews" not in run.body
        else:
            assert "事实核证失败" not in run.body
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


@pytest.mark.parametrize("publication_state", ["published", "failed", "conflict"])
def test_chat_runner_keeps_answer_and_shared_publication_outcome_independent(client, monkeypatch, publication_state):
    answer = "中期判断保持，当前风险有所增加。"
    payload = {"reviews": [{"instrument_id": "stock", "change_kind": "investment", "research": {
        "investment_view": {"risk": "波动上升"}}}]}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="shared-chat", title="研究讨论", instrument_ids=["stock"]))
        session.flush()
        session.add(ResearchEntry(entry_id="chat-run", topic_id="shared-chat", kind="analysis", title="当前怎么看",
            status="queued", context_json={"research_run": True, "instrument_ids": ["stock"], "retained": "original-context"}))
        session.commit()
    calls = []
    monkeypatch.setattr(sector_research, "prepare_run", lambda run_id: calls.append(("prepare", run_id)))

    class CompletedProcess:
        returncode = 0
        def communicate(self, timeout):
            assert timeout == 1800
            return json.dumps({"answer": answer, "research_result": payload}), ""

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: CompletedProcess())
    def publish(session, run, raw):
        assert calls == [("prepare", "chat-run")]
        assert json.loads(raw) == payload
        run.context_json = {**run.context_json, "reviews": {"stock": {"status": "completed", "research": payload["reviews"][0]["research"]}}}
        run.body = "共同研究发布的摘要，不应替代对话回答"
        session.flush()
        if publication_state != "published":
            error_type = sector_research.ResearchVersionConflict if publication_state == "conflict" else ValueError
            raise error_type("研究版本已更新" if publication_state == "conflict" else "本轮来源引用不完整")
        run.status = "completed"
    monkeypatch.setattr(sector_research, "apply_result", publish)
    runner.run_analysis("chat-run")
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "chat-run")
        assert run.status == "draft" and run.body == answer
        assert run.completed_at is not None
        assert run.context_json["research_publication"]["status"] == publication_state
        assert run.context_json["retained"] == "original-context"
        if publication_state == "published":
            assert run.context_json["reviews"]["stock"]["status"] == "completed"
            assert run.context_json["research_publication"]["instrument_ids"] == ["stock"]
        else:
            assert "reviews" not in run.context_json  # Failed publication rolls back partial research writes.


def test_chat_runner_retains_answer_when_independent_reviewer_declines_publication(client, monkeypatch):
    answer = "先保留这个工作假设，尚不能把它当作已核实结论。"
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="review-failed-chat", title="研究讨论"))
        session.flush()
        session.add(ResearchEntry(entry_id="review-failed-chat", topic_id="review-failed-chat", kind="analysis", title="讨论",
            status="queued", context_json={"research_run": True}))
        session.commit()
    monkeypatch.setattr(sector_research, "prepare_run", lambda run_id: None)
    monkeypatch.setattr(sector_research, "apply_result", lambda *args: pytest.fail("A rejected review must not publish"))
    class CompletedProcess:
        returncode = 0
        def communicate(self, timeout):
            return json.dumps({"answer": answer, "research_result": None,
                "research_publication": {"status": "failed", "message": "核证未完成，研究记录未更新。"}}), ""
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: CompletedProcess())
    runner.run_analysis("review-failed-chat")
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "review-failed-chat")
        assert run.status == "draft" and run.body == answer
        assert run.context_json["research_publication"]["status"] == "failed"
        assert "reviews" not in run.context_json


@pytest.mark.parametrize("surface", ["sector", "risk", "conversation"])
def test_unexpected_publication_failure_rolls_back_and_finishes_the_run(client, monkeypatch, surface):
    from watchlist_app.services import risk_officer
    context = {"sector_run": surface == "sector", "risk_run": surface == "risk", "retained": "original-input"}
    if surface == "risk":
        context["submitted_risk_review"] = {"summary": "retained draft"}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="unexpected-failure-topic", title="研究"))
        session.flush()
        session.add(ResearchEntry(entry_id="unexpected-failure", topic_id="unexpected-failure-topic", kind="analysis",
            title="研究", status="queued", context_json=context))
        session.commit()
    monkeypatch.setattr(sector_research, "prepare_run", lambda _: None)
    monkeypatch.setattr(risk_officer, "prepare_run", lambda _: None)
    class CompletedProcess:
        returncode = 0
        def communicate(self, timeout):
            return json.dumps({"answer": "讨论完成", "research_result": {"reviews": []}}), ""
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: CompletedProcess())
    def broken_publish(session, run, result):
        run.context_json = {**run.context_json, "partially_published": True}
        session.flush()
        raise RuntimeError("private-provider-detail")
    monkeypatch.setattr(sector_research, "apply_result", broken_publish)
    monkeypatch.setattr(risk_officer, "apply_result", broken_publish)

    runner.run_analysis("unexpected-failure")

    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "unexpected-failure")
        assert run.completed_at is not None
        assert run.context_json["retained"] == "original-input"
        assert "partially_published" not in run.context_json
        if surface == "conversation":
            assert run.status == "draft" and run.body == "讨论完成"
            assert run.context_json["research_publication"]["status"] == "failed"
        else:
            assert run.status == "failed"
            assert run.context_json["runtime_error"]["type"] == "RuntimeError"
        assert "private-provider-detail" not in json.dumps(run.context_json) + run.body


def test_restart_marks_only_incomplete_runs_failed_with_a_terminal_clock(client):
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="restart-topic", title="研究"))
        session.flush()
        for status in ("queued", "running", "completed", "draft", "failed"):
            session.add(ResearchEntry(entry_id=f"restart-{status}", topic_id="restart-topic", kind="analysis",
                title="研究", status=status, body="original", context_json={"retained": True}))
        session.commit()

    runner.interrupt_incomplete_runs()

    with get_session_factory()() as session:
        for status in ("queued", "running"):
            run = session.get(ResearchEntry, f"restart-{status}")
            assert run.status == "failed" and run.completed_at is not None
            assert run.context_json["retained"] is True
        for status in ("completed", "draft", "failed"):
            run = session.get(ResearchEntry, f"restart-{status}")
            assert run.status == status and run.body == "original"


@pytest.mark.parametrize("issuer_revoked", [False, True])
def test_execution_revalidates_original_issuer_after_preparation_outlasts_first_grant(client, monkeypatch, issuer_revoked):
    from dataclasses import replace
    from studio_identity import IdentityError, Principal
    issuer = Principal("pm-one", "PM", "default", credential="original-session")
    run_id = "long-preparation"
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id=run_id, title="研究"))
        session.flush()
        session.add(ResearchEntry(entry_id=run_id, topic_id=run_id, kind="analysis", title="讨论", status="queued",
            context_json={"research_run": True, "retained": "original-input"}))
        session.commit()
    prepared, spawned, revoked = [], [], []
    def resolve(token, audience):
        if token == "initial-grant" and prepared:
            raise IdentityError(401, "expired fixture grant")
        assert token in {"initial-grant", "execution-grant"}
        return replace(issuer, credential=token, resource_scope={"kind": "run", "id": run_id})
    def issue(principal, **kwargs):
        assert prepared and principal is issuer
        assert kwargs == {"audience": "watchlist", "resource_scope": {"kind": "run", "id": run_id}}
        if issuer_revoked:
            raise IdentityError(403, "fixture issuer was revoked")
        return "execution-grant"
    class CompletedProcess:
        returncode = 0
        def communicate(self, timeout):
            return json.dumps({"answer": "原样本讨论完成", "research_result": None}), ""
    def launch(*args, **kwargs):
        assert not issuer_revoked
        assert kwargs["env"]["INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN"] == "execution-grant"
        spawned.append(True)
        return CompletedProcess()
    monkeypatch.setattr(sector_research, "prepare_run", lambda *_: prepared.append(True))
    monkeypatch.setattr(runner, "resolve_token", resolve)
    monkeypatch.setattr(runner, "issue_delegation", issue)
    monkeypatch.setattr(runner, "revoke_delegation", lambda token, principal: revoked.append((token, principal)))
    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    runner.run_analysis(run_id, "initial-grant", issuer)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        assert run.context_json["retained"] == "original-input"
        assert run.completed_at is not None
        if issuer_revoked:
            assert run.status == "failed" and not spawned
            assert run.context_json["runtime_error"]["status_code"] == 403
        else:
            assert run.status == "draft" and run.body == "原样本讨论完成" and spawned
        assert "original-session" not in json.dumps(run.context_json) + run.body
    assert {token for token, _ in revoked} == ({"initial-grant"} if issuer_revoked else {"initial-grant", "execution-grant"})
    assert all(principal is issuer for _, principal in revoked)
