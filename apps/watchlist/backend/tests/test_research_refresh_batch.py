"""Operational completion must mean publication for the selected current member."""
from importlib.util import module_from_spec, spec_from_file_location
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest


def batch_module():
    spec = spec_from_file_location("research_refresh_batch", Path(__file__).parents[1] / "scripts/refresh_active_research.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_batch_rechecks_status_before_starting_research(client, monkeypatch):
    batch = batch_module()
    monkeypatch.setattr(batch.sector_research, "active_research_ids", lambda session: [])
    monkeypatch.setattr(batch.sector_research, "begin_run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("out-of-scope dispatch")))
    assert batch.refresh_one("formerly-invested")["status"] == "out_of_scope"


def test_completed_process_without_accepted_instrument_review_is_not_batch_success(client):
    batch = batch_module()
    state = {"status": "completed", "completed_at": "2026-09-24T00:00:00+00:00", "reviews": {}, "error": None}
    assert not batch.result_record("selected", "run", state)["published"]
    state["reviews"] = {"other": {"status": "completed"}}
    assert not batch.result_record("selected", "run", state)["published"]
    state["reviews"]["selected"] = {"status": "limited", "research": {"modules": [{"key": "rates-credit", "coverage": "insufficient"}]}}
    result = batch.result_record("selected", "run", state)
    assert result["published"] and result["coverage_status"] == "limited"
    state["status"] = "failed"
    assert not batch.result_record("selected", "run", state)["published"]


def test_batch_manifest_is_private_and_replaced_atomically(client, tmp_path):
    batch = batch_module()
    path = tmp_path / "result.json"
    batch.write_manifest(path, {"results": {"asset": {"published": False}}})
    batch.write_manifest(path, {"results": {"asset": {"published": True}}})
    assert json.loads(path.read_text())["results"]["asset"]["published"]
    assert path.stat().st_mode & 0o077 == 0
    assert not path.with_name(path.name + ".tmp").exists()


def ready_notebook():
    return {"investment_view": {"direction": "等待信用压力缓解"},
            "decision_brief": {"recommendation": "维持观察", "needs_review": False},
            "modules": [{"key": "rates-credit", "coverage": "partial", "analysis": "已获取利差扩大，缺少持仓明细；先验证信用暴露再决策。"}]}


@pytest.mark.parametrize("missing", ["investment_view", "decision_brief", "modules"])
def test_published_fragments_do_not_count_as_a_readable_report(client, missing):
    batch = batch_module()
    notebook = ready_notebook()
    notebook.pop(missing)
    assert not batch.report_ready(notebook)


def test_limited_report_is_ready_but_empty_or_stale_judgments_are_not(client):
    batch = batch_module()
    notebook = ready_notebook()
    assert batch.report_ready(notebook)
    stale = deepcopy(notebook)
    stale["decision_brief"]["needs_review"] = True
    assert not batch.report_ready(stale)
    empty = deepcopy(notebook)
    empty["modules"][0]["analysis"] = "  \n "
    assert not batch.report_ready(empty)
    empty["modules"][0]["analysis"] = ""
    empty["modules"][0]["summary"] = "已知信用利差扩大，待核实暴露"
    assert batch.report_ready(empty)


def test_no_change_publication_uses_retained_report_and_stale_current_overrides_old_run(client):
    batch = batch_module()
    state = {"status": "completed", "completed_at": "2026-09-24T00:00:00+00:00", "error": None,
             "reviews": {"selected": {"status": "limited", "change_kind": "no_change"}},
             "current_notebook": ready_notebook()}
    result = batch.result_record("selected", "run", state)
    assert result["published"] and result["report_ready"]
    state["reviews"]["selected"]["research"] = ready_notebook()
    state["current_notebook"]["decision_brief"]["needs_review"] = True
    assert not batch.result_record("selected", "run", state)["report_ready"]


def configure_resume(batch, monkeypatch, tmp_path, *, selected, states):
    path = tmp_path / "batch.json"
    prior = {"target": batch.target_identity(), "team_id": batch.service_principal("watchlist").team_id,
             "started_at": "2026-09-24T00:00:00+00:00", "instrument_ids": selected,
             "results": {iid: {"run_id": iid + "-run"} for iid in selected}}
    path.write_text(json.dumps(prior))
    monkeypatch.setattr(sys, "argv", ["refresh", "--execute", "--resume", "--output", str(path)])
    monkeypatch.setattr(batch.sector_research, "active_research_ids", lambda session: ["ready", "stale", "new"])
    monkeypatch.setattr(batch.sector_research, "instrument_label", lambda session, iid: iid)
    monkeypatch.setattr(batch, "run_state", lambda run_id, iid: deepcopy(states[iid]))
    monkeypatch.setattr(batch, "harness_available", lambda: True)
    return path


def test_resume_keeps_saved_current_scope_and_rebuilds_published_but_stale_report(client, monkeypatch, tmp_path):
    batch = batch_module()
    states = {iid: {"status": "completed", "completed_at": "2026-09-24T00:00:00+00:00", "error": None,
                    "reviews": {iid: {"status": "completed"}}, "current_notebook": ready_notebook()}
              for iid in ("ready", "stale")}
    states["stale"]["current_notebook"]["decision_brief"]["needs_review"] = True
    path = configure_resume(batch, monkeypatch, tmp_path, selected=["ready", "stale", "removed"], states=states)
    dispatched = []
    def refresh(iid, run_id, on_dispatch):
        dispatched.append((iid, run_id))
        on_dispatch(iid, iid + "-retry", "running")
        persisted = json.loads(path.read_text())
        assert persisted["results"][iid]["run_id"] == iid + "-retry"
        return {"instrument_id": iid, "run_id": iid + "-retry", "published": True, "report_ready": True}
    monkeypatch.setattr(batch, "refresh_one", refresh)
    assert batch.main() == 0
    manifest = json.loads(path.read_text())
    assert dispatched == [("stale", "stale-run")]
    assert manifest["instrument_ids"] == ["ready", "stale"]
    assert manifest["complete"] and manifest["ready_count"] == 2


def test_resuming_empty_batch_never_expands_to_new_members(client, monkeypatch, tmp_path):
    batch = batch_module()
    path = configure_resume(batch, monkeypatch, tmp_path, selected=[], states={})
    monkeypatch.setattr(batch, "refresh_one", lambda *args: pytest.fail("Resume expanded beyond saved scope"))
    assert batch.main() == 0
    assert json.loads(path.read_text())["instrument_ids"] == []


def test_resume_rejects_different_target_before_reading_runs(client, monkeypatch, tmp_path):
    batch = batch_module()
    path = configure_resume(batch, monkeypatch, tmp_path, selected=["ready"], states={})
    manifest = json.loads(path.read_text())
    manifest["target"]["database"] = "different-database"
    path.write_text(json.dumps(manifest))
    with pytest.raises(SystemExit) as error:
        batch.main()
    assert error.value.code == 2
