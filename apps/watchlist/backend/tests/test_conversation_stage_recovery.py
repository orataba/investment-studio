"""Authorized shared publication preserves the private answer across worker loss."""
from copy import deepcopy
import json
from types import SimpleNamespace
import subprocess

import pytest
from studio_identity import current_principal

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner as runner, sector_research, sector_fact_review


ANSWER = "私人讨论结论保持；共同研究是否已更新以发布状态为准。"
DRAFT = {"reviews": []}


@pytest.fixture
def private_run(client, monkeypatch):
    context = {"research_run": True, "cutoff": "2026-10-01T00:00:00Z",
               "input_snapshot_cutoff": "2026-10-01T00:00:00Z", "instrument_ids": ["asset"]}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="private-stage", title="Private", visibility="private"))
        session.flush()
        session.add(ResearchEntry(entry_id="private-stage", topic_id="private-stage", kind="analysis",
                                 title="Discuss", status="queued", context_json=context))
        session.commit()
    monkeypatch.setattr(runner, "resolve_token", lambda *a: current_principal())
    monkeypatch.setattr(sector_research, "prepare_run", lambda *a: None)
    monkeypatch.setattr(sector_research, "prepare_initialization_searches", lambda *a: None)
    return "private-stage"


def update_context(run_id, **changes):
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        run.context_json = {**run.context_json, **changes}
        session.commit()


def read_run(run_id):
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        return run.status, run.body, deepcopy(run.context_json)


def test_authorized_conversation_has_independent_deadlines_and_saved_answer(private_run, monkeypatch):
    launches, publications = [], []
    def launch(*a, **kw):
        review = kw["env"]["INVESTMENT_STUDIO_RESEARCH_RESUME_REVIEW"] == "1"
        launches.append(review)
        def communicate(timeout):
            assert timeout == 1800
            if not review:
                assert kw["env"]["INVESTMENT_STUDIO_RESEARCH_GENERATION_ONLY"] == "1"
                update_context(private_run, submitted_draft=DRAFT)
                return ANSWER, ""
            _, _, context = read_run(private_run)
            assert context["conversation_answer"] == ANSWER
            assert context["execution"]["stage"] == "review"
            return json.dumps({"answer": ANSWER, "research_result": DRAFT}), ""
        return SimpleNamespace(communicate=communicate, returncode=0)
    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    monkeypatch.setattr(sector_research, "apply_result", lambda session, run, raw: publications.append(json.loads(raw)))
    runner._run_analysis(private_run)
    status, answer, context = read_run(private_run)
    assert launches == [False, True] and publications == [DRAFT]
    assert status == "draft" and answer == ANSWER
    assert context["research_publication"]["status"] == "published"


@pytest.mark.parametrize("checkpoint", [False, True])
def test_worker_recovery_reuses_private_answer_and_bound_review(private_run, monkeypatch, checkpoint):
    extra = {"submitted_draft": DRAFT, "conversation_answer": ANSWER,
             "execution": {"attempt": 1, "resume": True}}
    if checkpoint:
        extra["web_evidence"] = [{"review": {"checkpoint": {"draft": DRAFT,
            "cutoff": "2026-10-01T00:00:00Z", "result": DRAFT}}}]
    update_context(private_run, **extra)
    monkeypatch.setattr(sector_research, "prepare_run", lambda *a: pytest.fail("Frozen inputs must not change"))
    def launch(*a, **kw):
        assert not checkpoint
        assert kw["env"]["INVESTMENT_STUDIO_RESEARCH_RESUME_REVIEW"] == "1"
        return SimpleNamespace(communicate=lambda timeout: (json.dumps({"answer": ANSWER, "research_result": DRAFT}), ""), returncode=0)
    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    calls = []
    monkeypatch.setattr(sector_research, "apply_result", lambda *a: calls.append(json.loads(a[-1])))
    runner._run_analysis(private_run)
    status, answer, context = read_run(private_run)
    assert calls == [DRAFT] and status == "draft" and answer == ANSWER
    assert context["execution"]["stage"] == "publication"
    assert context["submitted_draft"] == DRAFT


@pytest.mark.parametrize("saved", [False, True])
def test_plain_private_answer_never_starts_review_or_publication(private_run, monkeypatch, saved):
    if saved:
        update_context(private_run, conversation_answer=ANSWER, execution={"attempt": 1, "resume": True})
        monkeypatch.setattr(sector_research, "prepare_initialization_searches", lambda *a: pytest.fail("Saved answer needs no acquisition"))
    calls = []
    def launch(*a, **kw):
        assert not saved and kw["env"]["INVESTMENT_STUDIO_RESEARCH_RESUME_REVIEW"] == "0"
        calls.append(True)
        return SimpleNamespace(communicate=lambda timeout: (ANSWER, ""), returncode=0)
    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    monkeypatch.setattr(sector_research, "apply_result", lambda *a: pytest.fail("Private answer must not publish"))
    runner._run_analysis(private_run)
    status, answer, context = read_run(private_run)
    assert status == "draft" and answer == ANSWER and len(calls) == (0 if saved else 1)
    assert context["research_publication"] == {"status": "not_requested"}


def test_review_deadline_preserves_answer_and_explicit_failed_publication(private_run, monkeypatch):
    update_context(private_run, conversation_answer=ANSWER, submitted_draft=DRAFT,
                   execution={"attempt": 1, "resume": True})
    def timeout(**kw):
        assert kw["timeout"] == 1800
        raise subprocess.TimeoutExpired("review", 1800)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(communicate=timeout, returncode=-15))
    monkeypatch.setattr(runner, "_stop_process_group", lambda *a: None)
    monkeypatch.setattr(sector_research, "apply_result", lambda *a: pytest.fail("Timeout is not accepted review"))
    runner._run_analysis(private_run)
    status, answer, context = read_run(private_run)
    assert status == "draft" and answer == ANSWER and context["submitted_draft"] == DRAFT
    publication = context["research_publication"]
    assert publication["status"] == "failed" and publication["stage"] == "review"
    assert publication["automatic_retry"] is False and publication["error"]["type"] == "TimeoutExpired"


def test_reviewer_resume_restores_private_answer_instead_of_blank_stdin(monkeypatch, capsys):
    import io
    context = {"research_run": True, "submitted_draft": DRAFT, "conversation_answer": ANSWER}
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RESUME_REVIEW", "1")
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "bound")
    monkeypatch.setattr(sector_fact_review.sys, "stdin", io.StringIO("\n"))
    monkeypatch.setattr(sector_fact_review.sys, "argv", ["review", "--conversation"])
    monkeypatch.setattr(sector_fact_review, "_api_request", lambda *a: context)
    monkeypatch.setattr(sector_fact_review, "review_output", lambda *a, **kw: DRAFT)
    sector_fact_review.main()
    assert json.loads(capsys.readouterr().out) == {"answer": ANSWER, "research_result": DRAFT}


def test_legacy_running_conversation_recovers_saved_prose_without_inventing_an_answer():
    assert runner.conversation_answer({"web_evidence": [{"review": {"raw_draft": ANSWER}}]}) == ANSWER
    assert "私人答复在完成前中断" in runner.conversation_answer({"submitted_draft": DRAFT})


def test_completed_private_publication_is_not_replayed_after_lost_worker_ack(private_run, monkeypatch):
    update_context(private_run, conversation_answer=ANSWER, submitted_draft=DRAFT,
        execution={"attempt": 1, "resume": True}, web_evidence=[{"review": {"checkpoint": {
            "draft": DRAFT, "cutoff": "2026-10-01T00:00:00Z", "result": DRAFT}}}])
    calls = []
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: pytest.fail("Publication checkpoint needs no model"))
    monkeypatch.setattr(sector_research, "apply_result", lambda *a: calls.append(True))
    runner._run_analysis(private_run)
    runner._run_analysis(private_run)
    assert calls == [True] and read_run(private_run)[:2] == ("draft", ANSWER)


def test_checkpoint_publication_still_revalidates_and_rolls_back_conflicts(private_run, monkeypatch):
    update_context(private_run, conversation_answer=ANSWER, submitted_draft=DRAFT,
        execution={"attempt": 1, "resume": True}, web_evidence=[{"review": {"checkpoint": {
            "draft": DRAFT, "cutoff": "2026-10-01T00:00:00Z", "result": DRAFT}}}])
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: pytest.fail("Publication checkpoint needs no model"))
    def reject(session, run, raw):
        run.context_json = {**run.context_json, "should_rollback": True}
        session.flush()
        raise sector_research.ResearchVersionConflict("研究版本已更新")
    monkeypatch.setattr(sector_research, "apply_result", reject)
    runner._run_analysis(private_run)
    status, answer, context = read_run(private_run)
    assert status == "draft" and answer == ANSWER and "should_rollback" not in context
    assert context["research_publication"]["status"] == "conflict"
    assert context["research_publication"]["stage"] == "publication"
