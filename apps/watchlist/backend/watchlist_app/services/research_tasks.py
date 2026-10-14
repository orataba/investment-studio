"""Native child findings retained inside their parent's one frozen research run.

Only the trusted harness host calls these write contracts. Child identity comes
from the native host's agent map, never from a model's structured result.
"""
from copy import deepcopy
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import GenerateJsonSchema

from watchlist_app.services.research_themes import AnalystThemeUpdate
from watchlist_app.services.sector_research import SectorEventCheck, SectorEventProposal

ResearchRole = Literal["recent_updates", "themes", "quantitative"]
TERMINAL_STATUSES = {"completed", "limited", "not_applicable", "failed"}


class ResearchTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    parent_task_id: str | None = None
    role: ResearchRole
    instrument_id: str = Field(min_length=1)
    instruction: str = Field(min_length=1, max_length=8000)
    theme_ids: list[str] = Field(default_factory=list)


class TaskRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    explanation: str = Field(min_length=1, max_length=3000)
    severity: Literal["important", "critical"] = "important"
    source_ids: list[str] = Field(min_length=1)


class ResearchTaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "limited", "not_applicable"]
    summary: str = Field(min_length=1, max_length=8000)
    source_ids: list[str] = Field(default_factory=list)
    risks: list[TaskRisk] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    events: list[SectorEventProposal] = Field(default_factory=list)
    themes: list[AnalystThemeUpdate] = Field(default_factory=list)

    @model_validator(mode="after")
    def explain_limitations(self):
        if self.status != "completed" and not any(item.strip() for item in self.limitations):
            raise ValueError("资料不足或不适用须说明具体原因")
        return self


class TaskResultInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: ResearchTaskResult


class TaskFailureInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=2000)


class _ResultSchema(GenerateJsonSchema):
    def model_schema(self, schema):
        result = super().model_schema(schema)
        result["additionalProperties"] = False
        return result


def result_schema(role):
    schema = ResearchTaskResult.model_json_schema(schema_generator=_ResultSchema)
    # Do not ask a quantitative child to construct event/theme publication drafts.
    for field, owner in (("events", "recent_updates"), ("themes", "themes")):
        if role != owner:
            schema["properties"].pop(field)
    definitions = schema.pop("$defs", {})
    reachable = set()
    def visit(value):
        if isinstance(value, dict):
            reference = value.get("$ref", "")
            if reference.startswith("#/$defs/"):
                name = reference.removeprefix("#/$defs/")
                if name not in reachable:
                    reachable.add(name)
                    visit(definitions[name])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(schema)
    if reachable:
        schema["$defs"] = {key: value for key, value in definitions.items() if key in reachable}
    return schema


def current_tasks(context):
    cutoff = context.get("input_snapshot_cutoff")
    return {key: value for key, value in (context.get("research_tasks") or {}).items()
            if cutoff and value.get("input_snapshot_cutoff") == cutoff}


def task_receipt(task):
    result = task.get("result") or {}
    return {**{key: task.get(key) for key in ("task_id", "parent_task_id", "role", "instrument_id", "theme_ids", "status")},
        "summary": result.get("summary", ""), "source_ids": result.get("source_ids", []),
        "limitations": result.get("limitations", []),
        "result_read": {"tool": "read_research_context", "section": "task_results", "path": [task["task_id"], "result"]}}


def task_directory(context, instrument_id=None):
    return {key: task_receipt(task) for key, task in current_tasks(context).items()
            if instrument_id is None or task["instrument_id"] == instrument_id}


def require_finished_tasks(run):
    context = run.context_json or {}
    if not context.get("sector_run"):
        return
    if (context.get("execution") or {}).get("research_section") != "overview":
        raise ValueError("最终研究草稿只由主研究员提交")
    pending = [key for key, task in current_tasks(context).items() if task["status"] not in TERMINAL_STATUSES]
    if pending:
        raise ValueError("已委派的研究任务尚未结束：" + "、".join(pending))


def _locked_context(session, run):
    from watchlist_app.services.research_access import require_entry_access
    session.refresh(run, with_for_update=True)
    require_entry_access(session, run, tool_write=True)
    context = run.context_json or {}
    if run.status != "running" or not context.get("sector_run") or not context.get("input_snapshot_cutoff"):
        raise ValueError("仅运行中的共享研究可委派本轮冻结范围内的任务")
    if context.get("submitted_draft"):
        raise ValueError("最终草稿已提交，不能再改写委派任务")
    return context


def _task(context, task_id):
    task = current_tasks(context).get(task_id)
    if task is None:
        raise ValueError("任务不属于本轮冻结研究范围")
    return task


def create_task(session, run, payload: ResearchTaskInput):
    context = _locked_context(session, run)
    if payload.instrument_id not in context.get("instrument_ids", []):
        raise ValueError("任务标的不在本轮研究范围")
    if payload.theme_ids and payload.role != "themes":
        raise ValueError("仅主题任务可指定主题范围")
    theme_ids = sorted(set(payload.theme_ids))
    dossier = next((row for row in context.get("research_dossiers", []) if row["instrument_id"] == payload.instrument_id), {})
    known_themes = {row["theme_id"] for row in dossier.get("themes", [])}
    if set(theme_ids) - known_themes:
        raise ValueError("主题不在标的冻结档案中")
    depth = 1
    if payload.parent_task_id is not None:
        parent = _task(context, payload.parent_task_id)
        if parent["role"] != "themes" or payload.role != "themes" or parent["depth"] != 1:
            raise ValueError("只有一级主题专员可再委派主题子任务")
        if parent["status"] != "running" or parent["attempt"] != context["execution"]["attempt"]:
            raise ValueError("父任务已结束或不属于当前执行尝试")
        if parent["instrument_id"] != payload.instrument_id:
            raise ValueError("子任务不能改变父任务标的")
        if parent["theme_ids"] and (not theme_ids or set(theme_ids) - set(parent["theme_ids"])):
            raise ValueError("子任务不能扩大父任务主题范围")
        depth = 2
    identity = {**payload.model_dump(), "theme_ids": theme_ids}
    for existing in current_tasks(context).values():
        if all(existing.get(key) == value for key, value in identity.items()) and existing["status"] != "failed":
            if existing["status"] in TERMINAL_STATUSES or existing["attempt"] == context["execution"]["attempt"]:
                return {**task_receipt(existing), "reused": True, "context": assigned_context(context, existing["task_id"]),
                        "result_schema": result_schema(payload.role)}
    task_id = uuid4().hex
    task = {**identity, "task_id": task_id, "depth": depth, "status": "running",
        "attempt": context["execution"]["attempt"], "input_snapshot_cutoff": context["input_snapshot_cutoff"],
        "created_at": datetime.now(UTC).isoformat()}
    run.context_json = {**context, "research_tasks": {**context.get("research_tasks", {}), task_id: task}}
    session.commit()
    return {**task_receipt(task), "reused": False, "context": assigned_context(run.context_json, task_id),
            "result_schema": result_schema(payload.role)}


def assigned_context(context, task_id, *, full_results=False):
    from watchlist_app.services.research_sections import section_context
    task = _task(context, task_id)
    result = section_context(context, task["instrument_id"], task["role"])
    # Children have no generic context tool. Expose retained numeric selectors
    # locally without copying raw series, figures, or unrelated role histories.
    result.pop("retained_computations_read", None)
    result["retained_computations"] = [{"source_id": row["source_id"], "title": row.get("title"),
        "instrument_id": row.get("instrument_id"), "as_of": row.get("as_of"),
        "read": {"tool": "compare_instruments" if "input_series" in row else
            "read_quant_analysis" if (row.get("data") or {}).get("analysis_kind") == "python_quant" else "read_research_numbers",
            "source_id": row["source_id"],
            "section": "data" if (row.get("data") or {}).get("analysis_kind") == "python_quant" else "overview"}}
        for row in context.get("computed_metrics", [])]
    result["assignment"] = {key: deepcopy(task.get(key)) for key in (
        "task_id", "parent_task_id", "role", "instruction", "theme_ids", "depth", "status")}
    children = {key: child for key, child in current_tasks(context).items() if child.get("parent_task_id") == task_id}
    result["task_results"] = deepcopy(children) if full_results else {
        key: {**task_receipt(child), "result_read": {"tool": "read_assigned_context",
            "path": ["task_results", key, "result"]}} for key, child in children.items()}
    if task["role"] == "themes" and task["theme_ids"]:
        scope = set(task["theme_ids"])
        result["active_themes"] = [row for row in result["active_themes"] if row["theme_id"] in scope]
        result["required_theme_ids"] = [key for key in result["required_theme_ids"] if key in scope]
        result["prior_theme_assessments"] = [row for row in result["prior_theme_assessments"] if row.get("theme_id") in scope]
        result["related_updates"] = [row for row in result["related_updates"] if scope.intersection(row.get("theme_ids", []))]
    return result


def save_task_result(session, run, task_id, payload: ResearchTaskResult):
    from watchlist_app.services.research_notebook import ResearchNotebook, research_sources, validate_notebook, notebook_source_ids
    from watchlist_app.services.sector_research import event_evidence_sources, validate_review_updates, SectorReview
    context = _locked_context(session, run)
    task = _task(context, task_id)
    payload = ResearchTaskResult.model_validate(payload.model_dump(mode="json", exclude_unset=True), extra="forbid")
    serialized = payload.model_dump(mode="json")
    serialized["events"] = [row.model_dump(mode="json", exclude_unset=True) for row in payload.events]
    serialized["themes"] = [row.model_dump(mode="json", exclude_unset=True) for row in payload.themes]
    if task["status"] in TERMINAL_STATUSES:
        if task.get("result") == serialized:
            return task_receipt(task)
        raise ValueError("已结束任务的结果不能被覆盖")
    if task["attempt"] != context["execution"]["attempt"]:
        raise ValueError("任务不属于当前执行尝试")
    if any(child.get("parent_task_id") == task_id and child["status"] not in TERMINAL_STATUSES
           for child in current_tasks(context).values()):
        raise ValueError("须先等待已委派的主题子任务结束")
    if payload.events and task["role"] != "recent_updates":
        raise ValueError("事件更新由标的动态专员提交")
    if payload.themes and task["role"] != "themes":
        raise ValueError("主题更新由主题专员提交")
    if task["theme_ids"] and any(row.theme_id not in task["theme_ids"] for row in payload.themes):
        raise ValueError("主题提案超出任务分配的主题范围")
    sources = research_sources(context, run.entry_id, session=session)
    refs = notebook_source_ids(serialized)
    validate_notebook(ResearchNotebook(source_ids=sorted(refs)), task["instrument_id"], sources)
    event_sources = event_evidence_sources(context, run.entry_id, notebook_evidence=sources)
    validate_review_updates(session, run, SectorReview(instrument_id=task["instrument_id"],
        events=payload.events, themes=payload.themes), sources, event_sources)
    unchanged_only = bool(payload.events) and all(isinstance(item, SectorEventCheck) for item in payload.events) and not payload.themes and not payload.risks
    if payload.status == "completed" and not refs and not unchanged_only:
        raise ValueError("完成的专员研究须绑定实际读取的依据；资料不足请提交 limited 并说明限制")
    updated = {**task, "status": payload.status, "result": serialized, "completed_at": datetime.now(UTC).isoformat()}
    run.context_json = {**context, "research_tasks": {**context["research_tasks"], task_id: updated}}
    session.commit()
    return task_receipt(updated)


def fail_task(session, run, task_id, message):
    context = _locked_context(session, run)
    task = _task(context, task_id)
    if task["status"] in TERMINAL_STATUSES:
        return task_receipt(task)
    if task["attempt"] != context["execution"]["attempt"]:
        raise ValueError("任务不属于当前执行尝试")
    updated = {**task, "status": "failed", "completed_at": datetime.now(UTC).isoformat(),
        "result": {"status": "limited", "summary": "委派任务未完成", "source_ids": [], "risks": [],
                   "limitations": [message], "events": [], "themes": []}}
    run.context_json = {**context, "research_tasks": {**context["research_tasks"], task_id: updated}}
    session.commit()
    return task_receipt(updated)


def interrupt_unfinished_tasks(context):
    """Resume keeps accepted findings; interrupted native processes cannot report later."""
    tasks = deepcopy(context.get("research_tasks") or {})
    for task in tasks.values():
        if task["status"] not in TERMINAL_STATUSES:
            task.update(status="failed", completed_at=datetime.now(UTC).isoformat(),
                result={"status": "limited", "summary": "原执行尝试已中断", "source_ids": [], "risks": [],
                    "limitations": ["运行恢复时原子任务已停止；尚未完成的分析不可视为已核实。"], "events": [], "themes": []})
    return tasks
