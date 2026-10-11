"""Failed local supervision must end Harness ownership before releasing capacity."""
import os
import subprocess
import sys
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from watchlist_app.db.models.research_execution import ResearchExecution
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_execution as queue, research_runner as runner, sector_research

from .test_research_execution import save_run


@pytest.mark.skipif(os.name != "posix", reason="Harness uses POSIX process groups")
@pytest.mark.parametrize("failure", [
    OSError("injected local result-read failure"),
    OperationalError("SELECT status", {}, OSError("injected transient DB connection loss")),
])
def test_poll_failure_stops_real_child_before_failed_claim_releases_capacity(client, monkeypatch, failure):
    save_run("poll-error", context={"sector_run": True})
    save_run("next-task", context={"sector_run": True})
    claim = queue.claim_next("worker")
    assert claim.run_id == "poll-error"
    monkeypatch.setattr(sector_research, "prepare_run", lambda *a: None)
    monkeypatch.setattr(sector_research, "prepare_initialization_searches", lambda *a: None)
    original_popen, original_finish = subprocess.Popen, queue.finish_claim
    children, finished = [], []

    def offline_child(*a, **kw):
        child = original_popen([sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        children.append(child)
        return child

    def transient_poll_failure(*a, **kw):
        assert children[0].poll() is None  # Fault occurs with a live, owned process.
        raise failure

    def finish_after_cleanup(item):
        assert children[0].poll() is not None
        finished.append(item.attempt)
        original_finish(item)

    monkeypatch.setattr(runner.subprocess, "Popen", offline_child)
    monkeypatch.setattr(runner, "_accepted_submission", transient_poll_failure)
    monkeypatch.setattr(queue, "finish_claim", finish_after_cleanup)
    try:
        queue.execute_claim(claim, Event())
        with get_session_factory()() as session:
            run = session.get(ResearchEntry, claim.run_id)
            assert run.status == "failed"
            assert run.context_json["runtime_error"]["type"] == type(failure).__name__
            assert run.context_json["execution"]["attempt"] == claim.attempt
            assert session.get(ResearchExecution, claim.run_id).status == "finished"
        assert finished == [claim.attempt]
        next_claim = queue.claim_next("worker")
        assert next_claim and next_claim.run_id == "next-task"
    finally:
        for child in children:
            if child.poll() is None:
                runner._stop_process_group(child, grace_seconds=0.1)
            child.communicate(timeout=2)


@pytest.mark.parametrize("review", [False, True])
def test_direct_run_read_failure_cleans_generation_and_review_before_recording_failure(client, monkeypatch, review):
    save_run("direct-error", context={"sector_run": True})
    monkeypatch.setattr(sector_research, "prepare_run", lambda *a: None)
    monkeypatch.setattr(sector_research, "prepare_initialization_searches", lambda *a: None)
    launched, stopped = [], []

    def launch(*a, **kw):
        in_review = kw["env"]["INVESTMENT_STUDIO_RESEARCH_RESUME_REVIEW"] == "1"
        process = SimpleNamespace(returncode=None)
        launched.append((in_review, process))

        def communicate(timeout):
            if review and not in_review:
                with get_session_factory()() as session:
                    run = session.get(ResearchEntry, "direct-error")
                    run.context_json = {**run.context_json, "submitted_draft": {"reviews": []}}
                    session.commit()
                process.returncode = 0
                return "draft", ""
            raise OSError("injected pipe read failure")

        process.communicate = communicate
        return process

    def stop(process):
        with get_session_factory()() as session:
            # Cleanup precedes publishing a terminal run status.
            assert session.get(ResearchEntry, "direct-error").status == "running"
        stopped.append(process)
        process.returncode = -15

    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    monkeypatch.setattr(runner, "_stop_process_group", stop)
    runner.run_analysis("direct-error")
    assert [stage for stage, _ in launched] == ([False, True] if review else [False])
    assert stopped == [launched[-1][1]]
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "direct-error")
        assert run.status == "failed"
        assert run.context_json["runtime_error"]["type"] == "OSError"
        assert run.context_json["execution"]["attempt"] == 1


@pytest.mark.parametrize("kind", ["cancel", "timeout", "accepted", "accepted-read-error"])
def test_wait_cleanup_occurs_once_and_preserves_completion_semantics(monkeypatch, kind):
    stopped = []
    cancellation = Event()
    if kind == "cancel":
        cancellation.set()
    process = SimpleNamespace(args=["offline-harness"], returncode=None)
    failure = OSError("injected post-cleanup pipe read failure")

    def communicate(timeout=None):
        if process.returncode is None:
            raise subprocess.TimeoutExpired(process.args, timeout)
        if kind == "accepted-read-error":
            raise failure
        return "submitted", ""

    def stop(item):
        stopped.append(item)
        item.returncode = -15

    process.communicate = communicate
    monkeypatch.setattr(runner, "_stop_process_group", stop)
    kwargs = {"cancellation": cancellation, "accepted": lambda: True}
    if kind == "accepted":
        assert runner._wait_harness(process, **kwargs) == ("submitted", "", 0)
    elif kind == "cancel":
        with pytest.raises(runner.ResearchExecutionInterrupted):
            runner._wait_harness(process, **kwargs)
    elif kind == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            runner._wait_harness(process, **kwargs, timeout=0)
    else:
        with pytest.raises(OSError) as error:
            runner._wait_harness(process, **kwargs)
        assert error.value is failure
    assert stopped == [process]


def test_cleanup_error_does_not_replace_the_original_wait_failure_or_expose_its_text(monkeypatch):
    original = OSError("original local read failure")
    cleanup = RuntimeError("sensitive process details must not enter cleanup diagnostics")
    process = SimpleNamespace(returncode=None)
    diagnostics = []

    def communicate(**kw):
        raise original

    def stop(item):
        raise cleanup

    process.communicate = communicate
    monkeypatch.setattr(runner, "_stop_process_group", stop)
    monkeypatch.setattr(runner.logging.getLogger(runner.__name__), "error", lambda *args: diagnostics.append(args))
    with pytest.raises(OSError) as error:
        runner._wait_harness(process)
    assert error.value is original
    assert diagnostics == [("Harness process-group cleanup failed (%s)", "RuntimeError")]


def test_normal_completion_does_not_stop_the_exited_group(monkeypatch):
    process = SimpleNamespace(returncode=0, communicate=lambda **kw: ("completed", ""))
    monkeypatch.setattr(runner, "_stop_process_group", lambda item: pytest.fail("Successful wait must not stop twice"))
    assert runner._wait_harness(process) == ("completed", "", 0)


@pytest.mark.parametrize("returncode", [-15, 0])
@pytest.mark.parametrize("accepted", [False, True])
def test_cancellation_during_communicate_keeps_worker_interruption_semantics(monkeypatch, returncode, accepted):
    cancellation = Event()
    stopped = []
    process = SimpleNamespace(returncode=returncode)

    def communicate(**kwargs):
        # SIGTERM can arrive while communicate waits, including just as the
        # Harness exits. Neither a killed child nor a final reply owns recovery.
        cancellation.set()
        return "last reply", ""

    process.communicate = communicate
    monkeypatch.setattr(runner, "_stop_process_group", stopped.append)
    with pytest.raises(runner.ResearchExecutionInterrupted):
        runner._wait_harness(process, cancellation=cancellation, accepted=lambda: accepted)
    assert stopped == [process]


@pytest.mark.parametrize("cancel_during", ["acceptance", "cleanup", "final_read"])
def test_accepted_timeout_cleanup_preserves_cancellation_and_stops_once(monkeypatch, cancel_during):
    cancellation = Event()
    stopped = []
    process = SimpleNamespace(args=["offline-harness"], returncode=None)

    def communicate(timeout=None):
        if process.returncode is None:
            raise subprocess.TimeoutExpired(process.args, timeout)
        if cancel_during == "final_read":
            cancellation.set()
        return "submitted", ""

    def accepted():
        if cancel_during == "acceptance":
            cancellation.set()
        return True

    def stop(item):
        stopped.append(item)
        item.returncode = -15
        if cancel_during == "cleanup":
            cancellation.set()

    process.communicate = communicate
    monkeypatch.setattr(runner, "_stop_process_group", stop)
    with pytest.raises(runner.ResearchExecutionInterrupted):
        runner._wait_harness(process, cancellation=cancellation, accepted=accepted)
    assert stopped == [process]
