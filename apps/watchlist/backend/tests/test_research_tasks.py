"""Native task writes keep the run's evidence, scope and attempt boundaries."""
from dataclasses import replace

import pytest
from fastapi import HTTPException
from studio_identity import current_principal, principal_context

from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_tasks as tasks
from watchlist_app.services import sector_research
from .test_sector_research import seed_sector, add_sources, bind_policy_theme
from .test_research_activity import event


@pytest.fixture
def task_run(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = sector_research.begin_run(session, ["xlk"])
        theme = bind_policy_theme(session, run)
        add_sources(run)
        run.status = "running"
        run.context_json = {**run.context_json, "input_snapshot_cutoff": run.context_json["cutoff"],
            "execution": {"attempt": 1, "research_section": "overview"}}
        session.commit()
        with principal_context(replace(current_principal(), resource_scope={"kind": "run", "id": run.entry_id, "attempt": "1"})):
            yield session, run, theme


def create(session, run, role="quantitative", **kwargs):
    return tasks.create_task(session, run, tasks.ResearchTaskInput(role=role, instrument_id="xlk",
        instruction=kwargs.pop("instruction", "检查该问题的实际证据、投资影响和限制"), **kwargs))


def finding(**kwargs):
    return tasks.ResearchTaskResult(status="completed", summary="价格变化有证据，尚不能推断因果。",
        source_ids=kwargs.pop("source_ids", ["web-one"]), **kwargs)


def test_result_binds_actual_sources_without_identity_or_role_from_child(task_run):
    session, run, _ = task_run
    created = create(session, run)
    key = created["task_id"]
    with pytest.raises(ValueError, match="原始依据"):
        tasks.save_task_result(session, run, key, finding(source_ids=["invented"]))
    with pytest.raises(ValueError, match="绑定实际读取"):
        tasks.save_task_result(session, run, key, finding(source_ids=[]))
    for extra in ({"instrument_id": "other"}, {"section": "themes"}, {"task_id": "other"}):
        with pytest.raises(ValueError):
            tasks.ResearchTaskResult.model_validate({**finding().model_dump(), **extra})
    receipt = tasks.save_task_result(session, run, key, finding())
    assert receipt["status"] == "completed" and receipt["source_ids"] == ["web-one"]
    assert "result" not in receipt
    assert tasks.save_task_result(session, run, key, finding()) == receipt
    with pytest.raises(ValueError, match="不能被覆盖"):
        tasks.save_task_result(session, run, key, finding().model_copy(update={"summary": "replacement"}))
    assert create(session, run)["task_id"] == key
    assert create(session, run)["reused"]


def test_optional_tasks_and_pending_failure_are_distinct(task_run):
    session, run, _ = task_run
    tasks.require_finished_tasks(run)  # No unnecessary fixed three-specialist gate.
    first = create(session, run)
    assert create(session, run)["reused"]
    with pytest.raises(ValueError, match="尚未结束"):
        tasks.require_finished_tasks(run)
    failed = tasks.fail_task(session, run, first["task_id"], "数字来源缺失，分析未完成")
    assert failed["status"] == "failed" and failed["limitations"] == ["数字来源缺失，分析未完成"]
    tasks.require_finished_tasks(run)
    replacement = create(session, run)
    assert not replacement["reused"] and replacement["task_id"] != first["task_id"]
    with pytest.raises(ValueError, match="具体原因"):
        tasks.ResearchTaskResult(status="limited", summary="not enough")


def test_theme_parent_scope_depth_and_terminal_children(task_run):
    session, run, theme = task_run
    scoped = [theme["theme_id"]]
    parent = create(session, run, "themes", theme_ids=scoped)
    for kwargs, message in (({"theme_ids": []}, "扩大"),
                            ({"theme_ids": ["not-bound"]}, "冻结档案"),
                            ({"role": "quantitative", "theme_ids": []}, "只有一级")):
        args = {"role": "themes", **kwargs}
        with pytest.raises(ValueError, match=message):
            create(session, run, parent_task_id=parent["task_id"], **args)
    child = create(session, run, "themes", parent_task_id=parent["task_id"], theme_ids=scoped, instruction="单独核实该主题")
    with pytest.raises(ValueError, match="只有一级"):
        create(session, run, "themes", parent_task_id=child["task_id"], theme_ids=scoped)
    with pytest.raises(ValueError, match="先等待"):
        tasks.save_task_result(session, run, parent["task_id"], finding())
    with pytest.raises(ValueError, match="主题范围"):
        tasks.save_task_result(session, run, child["task_id"], finding(themes=[{
            "theme_key": "new-other-topic", "title": "Different question", "question": "outside assignment?"}]))
    tasks.save_task_result(session, run, child["task_id"], finding())
    context = tasks.assigned_context(run.context_json, parent["task_id"])
    assert context["task_results"][child["task_id"]]["result_read"] == {
        "tool": "read_assigned_context", "path": ["task_results", child["task_id"], "result"]}
    assert list(tasks.assigned_context(run.context_json, child["task_id"])["task_results"]) == []
    tasks.save_task_result(session, run, parent["task_id"], finding())
    tasks.require_finished_tasks(run)


@pytest.mark.parametrize("defect,message", [
    ("theme_figure", "主题图表必须绑定真实留存的数值来源"),
    ("missing_event", "更新或解除的事件没有原跟进记录"),
    ("event_figure", "市场反应必须引用当前标的留存的事件窗口计算"),
])
def test_task_and_final_share_evidence_and_action_contract(task_run, defect, message):
    session, run, theme = task_run
    role = "themes" if defect == "theme_figure" else "recent_updates"
    task = create(session, run, role)
    values = {}
    if defect == "theme_figure":
        values["themes"] = [{"theme_id": theme["theme_id"], "theme_key": theme["theme_key"], "figure_source_ids": ["web-one"]}]
    else:
        values["events"] = [event(source_ids=["web-one"], published_at=run.context_json["cutoff"],
            occurred_at=None, follow_up="none", action="updated" if defect == "missing_event" else "new",
            **({"market_reaction": {"status": "available", "figure_source_ids": ["web-one"]}}
               if defect == "event_figure" else {}))]
    payload = finding(**values)
    with pytest.raises(ValueError, match=message):
        tasks.save_task_result(session, run, task["task_id"], payload)
    final = sector_research.ReviewResult(reviews=[sector_research.SectorReview(instrument_id="xlk", events=payload.events, themes=payload.themes)])
    with pytest.raises(ValueError, match=message):
        sector_research.validate_result(session, run, final)
    if defect == "theme_figure":
        values["themes"][0].update(figure_source_ids=[], source_ids=["web-one"])
    else:
        values["events"][0].update(action="new", market_reaction=None)
    assert tasks.save_task_result(session, run, task["task_id"], finding(**values))["status"] == "completed"
    result = run.context_json["research_tasks"][task["task_id"]]["result"]
    assert "background" not in (result["themes"] or [{}])[0]


def test_task_writes_refresh_locked_run_and_attempt_fences(task_run):
    session, run, _ = task_run
    first = create(session, run)
    with get_session_factory()() as other_session:
        other = other_session.get(ResearchEntry, run.entry_id)
        second = create(other_session, other, "themes")
    # A stale identity-map row must not replace another task's accepted write.
    tasks.save_task_result(session, run, first["task_id"], finding())
    assert set(run.context_json["research_tasks"]) == {first["task_id"], second["task_id"]}
    interrupted = tasks.interrupt_unfinished_tasks(run.context_json)
    assert interrupted[first["task_id"]]["status"] == "completed"
    assert interrupted[second["task_id"]]["status"] == "failed"
    run.context_json = {**run.context_json, "research_tasks": interrupted,
        "execution": {"attempt": 2, "research_section": "overview"}}
    session.commit()
    with pytest.raises(HTTPException) as stale:
        tasks.fail_task(session, run, second["task_id"], "late process")
    assert stale.value.status_code == 409
    with principal_context(replace(current_principal(), resource_scope={"kind": "run", "id": run.entry_id, "attempt": "2"})):
        assert create(session, run)["reused"]
        assert not create(session, run, "themes")["reused"]
    assert not tasks.current_tasks({**run.context_json, "input_snapshot_cutoff": "different"})


def test_task_contract_checks_run_asset_role_and_final_draft(task_run):
    session, run, _ = task_run
    with pytest.raises(ValueError, match="标的"):
        tasks.create_task(session, run, tasks.ResearchTaskInput(role="themes", instrument_id="outside", instruction="out"))
    task = create(session, run)
    with pytest.raises(ValueError, match="主题更新"):
        tasks.save_task_result(session, run, task["task_id"], finding(themes=[{"theme_key": "new", "title": "New", "question": "Question?"}]))
    run.context_json = {**run.context_json, "submitted_draft": {"reviews": []}}
    session.commit()
    with pytest.raises(ValueError, match="最终草稿已提交"):
        tasks.fail_task(session, run, task["task_id"], "too late")


def test_task_http_paging_and_sparse_result_reads(task_run, client):
    session, run, theme = task_run
    created = client.post(f"/api/research/runs/{run.entry_id}/tasks", json={"role": "themes", "instrument_id": "xlk", "instruction": "检查政策主题"})
    assert created.status_code == 200, created.text
    key = created.json()["task_id"]
    payload = {"status": "completed", "summary": "明确待验证的影响", "source_ids": ["web-one"],
        "themes": [{"theme_id": theme["theme_id"], "theme_key": theme["theme_key"], "synthesis": "未来仍需确认", "source_ids": ["web-one"]}]}
    saved = client.post(f"/api/research/runs/{run.entry_id}/tasks/{key}/result", json={"result": payload})
    assert saved.status_code == 200, saved.text
    selected = client.post(f"/api/research/runs/{run.entry_id}/read", json={"resource": "context", "section": "task_results", "path": [key, "result"]})
    assert selected.status_code == 200, selected.text
    assert selected.json()["data"]["themes"] == payload["themes"]
    assigned = client.get(f"/api/research/runs/{run.entry_id}/tasks/{key}/context", params={"path": '["active_themes"]', "limit": 1})
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["data"][0]["theme_id"] == theme["theme_id"]
    wrong_attempt = client.post(f"/api/research/runs/{run.entry_id}/tasks/{key}/failure", json={"message": "late"}, headers={"X-Test-Run-Attempt": "2"})
    assert wrong_attempt.status_code == 409
    assert client.post(f"/api/research/runs/{run.entry_id}/section-report", json={}).status_code == 404


def test_role_schema_does_not_copy_irrelevant_event_or_theme_contracts():
    quantitative = tasks.result_schema("quantitative")
    assert set(quantitative["$defs"]) == {"TaskRisk"}
    assert "instrument_id" not in quantitative["properties"] and "section" not in quantitative["properties"]
    assert "events" not in quantitative["properties"] and "themes" not in quantitative["properties"]
    assert "events" in tasks.result_schema("recent_updates")["properties"]
    assert "themes" in tasks.result_schema("themes")["properties"]
