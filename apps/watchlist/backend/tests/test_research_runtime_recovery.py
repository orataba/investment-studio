from datetime import UTC, datetime, timedelta
from copy import deepcopy
from types import SimpleNamespace

import pytest
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner as runner, sector_research


def save_run(context, *, status="failed", completed=None):
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="recovery", title="Research"))
        session.flush()
        session.add(ResearchEntry(entry_id="recovery", topic_id="recovery", kind="analysis", title="Research",
            status=status, completed_at=completed, context_json=context))
        session.commit()


def test_retry_preserves_failure_history_and_original_cutoff_and_requires_backoff(client):
    stamp = datetime.now(UTC)
    context = {"sector_run": True, "cutoff": "2026-09-23T00:00:00Z", "input_snapshot_cutoff": "2026-09-23T00:00:00Z",
        "submitted_draft": {"reviews": []}, "runtime_error": {"type": "ProviderRateLimit", "retryable": True},
        "instrument_inputs": [{"instrument_id": "stock", "retained_value": 7}]}
    save_run(context, completed=stamp)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "recovery")
        assert not runner.queue_retry(session, run, now=stamp + timedelta(seconds=59))
        assert runner.queue_retry(session, run, now=stamp + timedelta(seconds=60))
        assert run.status == "queued" and run.completed_at is None
        assert run.context_json["execution"]["failures"][0]["error"] == context["runtime_error"]
        assert run.context_json["input_snapshot_cutoff"] == context["input_snapshot_cutoff"]
        assert run.context_json["instrument_inputs"] == context["instrument_inputs"]
        assert "runtime_error" not in run.context_json


@pytest.mark.parametrize("error,attempt", [({"type": "ContextLimitExceeded", "retryable": False}, 1),
    ({"type": "ProviderUnavailable", "retryable": True}, 3)])
def test_deterministic_fault_or_exhausted_retry_requires_manual_recovery(client, error, attempt):
    stamp = datetime.now(UTC) - timedelta(hours=1)
    save_run({"sector_run": True, "execution": {"attempt": attempt}, "runtime_error": error}, completed=stamp)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "recovery")
        assert not runner.queue_retry(session, run)
        assert runner.queue_retry(session, run, manual=True)
        assert run.context_json["execution"]["failures"][0]["manual_recovery"]


def test_recovery_does_not_adopt_another_users_research(client, monkeypatch):
    from studio_identity import Principal
    save_run({"sector_run": True, "research_actor": {"kind": "user", "user_id": "original"}})
    monkeypatch.setattr(runner, "current_principal", lambda: Principal("other", "Other", "default"))
    with get_session_factory()() as session:
        assert not runner.queue_retry(session, session.get(ResearchEntry, "recovery"), manual=True)


def test_checkpoint_belongs_to_exact_draft_and_cutoff():
    context = {"submitted_draft": {"reviews": []}, "cutoff": "clock"}
    result = {"reviews": []}
    context["web_evidence"] = [{"review": {"checkpoint": {"draft": deepcopy(context["submitted_draft"]), "cutoff": "clock", "result": result}}}]
    assert runner.review_checkpoint(context) == result
    assert runner.review_checkpoint({**context, "cutoff": "new clock"}) is None
    assert runner.review_checkpoint({**context, "submitted_draft": {"reviews": [{"instrument_id": "new"}]}}) is None


@pytest.mark.parametrize("checkpoint", [False, True])
def test_recovery_skips_generation_and_never_rebinds_frozen_inputs(client, monkeypatch, checkpoint):
    from studio_identity import current_principal
    context = {"sector_run": True, "input_snapshot_cutoff": "2026-09-23T00:00:00Z", "cutoff": "2026-09-23T00:00:00Z",
        "submitted_draft": {"reviews": []}, "execution": {"attempt": 1, "resume": True}}
    if checkpoint:
        context["web_evidence"] = [{"review": {"checkpoint": {"draft": context["submitted_draft"], "cutoff": context["cutoff"], "result": {"reviews": []}}}}]
    save_run(context, status="queued")
    monkeypatch.setattr(sector_research, "prepare_run", lambda *args: pytest.fail("A frozen run must not acquire new inputs"))
    def launch(*args, **kwargs):
        assert not checkpoint, "A validated review must publish without a model process"
        assert kwargs["env"]["INVESTMENT_STUDIO_RESEARCH_RESUME_REVIEW"] == "1"
        return SimpleNamespace(communicate=lambda **kw: ('{"reviews": []}', ""), returncode=0)
    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    monkeypatch.setattr(runner, "resolve_token", lambda *args: current_principal())
    publications = []
    def publish(session, run, reply):
        publications.append(reply)
        run.status = "completed"
    monkeypatch.setattr(sector_research, "apply_result", publish)
    runner._run_analysis("recovery")
    assert publications == ['{"reviews": []}']
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "recovery")
        assert run.status == "completed" and run.context_json["execution"]["attempt"] == 2
        assert run.context_json["input_snapshot_cutoff"] == context["input_snapshot_cutoff"]


def test_validated_checkpoint_still_cannot_overwrite_a_newer_research_version(client, monkeypatch):
    from studio_identity import current_principal
    context = {"sector_run": True, "input_snapshot_cutoff": "2026-09-23T00:00:00Z", "cutoff": "2026-09-23T00:00:00Z",
        "submitted_draft": {"reviews": []}, "execution": {"attempt": 1, "resume": True}}
    context["web_evidence"] = [{"review": {"checkpoint": {"draft": context["submitted_draft"], "cutoff": context["cutoff"], "result": {"reviews": []}}}}]
    save_run(context, status="queued")
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No model after validated checkpoint"))
    monkeypatch.setattr(runner, "resolve_token", lambda *args: current_principal())
    def conflict(*args):
        raise sector_research.ResearchVersionConflict("研究版本已更新")
    monkeypatch.setattr(sector_research, "apply_result", conflict)
    runner._run_analysis("recovery")
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "recovery")
        assert run.status == "failed" and run.context_json["validation_error"] == "研究版本已更新"
        assert runner.review_checkpoint(run.context_json) == {"reviews": []}


def test_generation_recovery_reuses_saved_computations_and_keeps_original_clock(client, monkeypatch):
    from studio_identity import current_principal
    from watchlist_app import research_mcp as mcp
    context = {"sector_run": True, "run_id": "recovery", "cutoff": "2026-09-23T00:00:00Z",
        "input_snapshot_cutoff": "2026-09-23T00:00:00Z", "execution": {"attempt": 1, "resume": True},
        "computed_metrics": [{"source_id": "computed:saved", "source_type": "computed_metric", "title": "Saved figure",
            "instrument_id": "xlk", "as_of": "2026-09-23", "data": {"analysis_kind": "python_quant", "tables": [{"return": 0.2}]},
            "methodology": {"input_sources": [], "code": "retained-code"}}]}
    save_run(context, status="queued")
    monkeypatch.setattr(sector_research, "prepare_run", lambda *args: pytest.fail("Recovery must not rebind inputs"))
    def read(suffix, payload=None):
        assert suffix.startswith("computed-source?"), "Saved source reads must not calculate or acquire data"
        response = client.get("/api/research/runs/recovery/" + suffix)
        assert response.status_code == 200, response.text
        return response.json()
    monkeypatch.setattr(mcp, "request", read)
    def launch(*args, **kwargs):
        assert kwargs["env"]["INVESTMENT_STUDIO_RESEARCH_RESUME_GENERATION"] == "1"
        assert kwargs["env"]["INVESTMENT_STUDIO_RESEARCH_RESUME_REVIEW"] == "0"
        response = client.post("/api/research/runs/recovery/read", json={"resource": "context", "section": "computed_metrics"})
        assert response.status_code == 200, response.text
        index = response.json()["data"]
        assert "data" not in index[0] and "methodology" not in index[0]
        selector = dict(index[0]["read"])
        assert selector.pop("tool") == "read_quant_analysis"
        assert mcp.read_quant_analysis(**selector)["data"] == context["computed_metrics"][0]["data"]
        return SimpleNamespace(communicate=lambda **kw: ('{"reviews": []}', ""), returncode=0)
    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    monkeypatch.setattr(runner, "resolve_token", lambda *args: current_principal())
    def publish(session, run, reply):
        run.status = "completed"
    monkeypatch.setattr(sector_research, "apply_result", publish)
    runner._run_analysis("recovery")
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "recovery")
        assert run.status == "completed"
        assert run.context_json["computed_metrics"] == context["computed_metrics"]
        assert run.context_json["cutoff"] == context["cutoff"]
        assert run.context_json["input_snapshot_cutoff"] == context["input_snapshot_cutoff"]


def test_native_output_limit_marker_is_recorded_as_generation_failure(client, monkeypatch):
    from studio_identity import current_principal
    save_run({"sector_run": True}, status="queued")
    monkeypatch.setattr(sector_research, "prepare_run", lambda *args: None)
    monkeypatch.setattr(runner, "resolve_token", lambda *args: current_principal())
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(
        communicate=lambda **kw: ("", 'private provider text\nRESEARCH_HARNESS_END {"reason":"max-tokens"}\n'), returncode=1))
    runner._run_analysis("recovery")
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "recovery")
        assert run.status == "failed"
        assert run.context_json["runtime_error"]["type"] == "OutputLimitExceeded"
        assert run.context_json["runtime_error"]["stage"] == "generation"
        assert run.context_json["runtime_error"]["retryable"] is False
        assert "事实核证" not in run.body and "private provider" not in str(run.context_json)
    assert runner._provider_failure('model text mentions max-tokens') is None
