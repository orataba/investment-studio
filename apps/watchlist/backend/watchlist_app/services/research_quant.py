"""Source-bound Python analysis and durable, inspectable research outputs."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
import math
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from watchlist_app.services import quant_sandbox
from watchlist_app.services.research_notebook import ResearchNotebook, _original_source, research_sources, validate_notebook


Key = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$")]
Cell = StrictStr | StrictInt | StrictFloat | StrictBool | None


class QuantAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(min_length=1, max_length=12)
    code: str = Field(min_length=1, max_length=40000)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    methodology: str = Field(min_length=1, max_length=6000,
        description="Explain the question, assumptions, units, alignment, missingness and calculation. Parameters are assumptions, not observed facts.")
    version_id: str | None = Field(default=None, min_length=1, max_length=250,
        description="Optional exact version from referenced_research_versions. All input IDs then resolve only inside that frozen version, never current sources.")


class QuantColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Key
    label: str = Field(min_length=1, max_length=120)
    unit: str = Field(default="", max_length=80)


class QuantTable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Key
    title: str = Field(min_length=1, max_length=200)
    columns: list[QuantColumn] = Field(min_length=1, max_length=32)
    rows: list[dict[str, Cell]] = Field(max_length=10000)

    @model_validator(mode="after")
    def valid_table(self):
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError("量化表格的列标识不能重复")
        for row in self.rows:
            if set(row) != set(keys):
                raise ValueError("每行须明确给出全部列；缺失值使用 null，不填充或省略")
            for value in row.values():
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError("量化表格不接受 NaN 或无穷值，请明确使用 null")
                if isinstance(value, str) and len(value) > 4000:
                    raise ValueError("量化表格的单元格文本过长")
        return self


class QuantChartSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Key
    label: str = Field(min_length=1, max_length=120)


class QuantChart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Key
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["line", "bar"]
    table_key: Key
    x_key: Key
    series: list[QuantChartSeries] = Field(min_length=1, max_length=6)
    x_label: str = Field(min_length=1, max_length=120)
    y_label: str = Field(min_length=1, max_length=120)


class QuantOutput(BaseModel):
    """Declarative charts reference retained table values; never active HTML/SVG."""
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=4000)
    metrics: dict[str, Cell] = Field(default_factory=dict, max_length=100)
    tables: list[QuantTable] = Field(default_factory=list, max_length=12)
    charts: list[QuantChart] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def valid_outputs(self):
        tables = {table.key: table for table in self.tables}
        if len(tables) != len(self.tables) or len({chart.key for chart in self.charts}) != len(self.charts):
            raise ValueError("量化表格与图表标识须分别唯一")
        if sum(len(table.rows) for table in self.tables) > 20000:
            raise ValueError("量化表格合计不得超过 20000 行；请先聚合或缩小分析范围")
        if any(isinstance(value, float) and not math.isfinite(value) for value in self.metrics.values()):
            raise ValueError("量化指标不接受 NaN 或无穷值")
        if any(len(key) > 120 or (isinstance(value, str) and len(value) > 4000) for key, value in self.metrics.items()):
            raise ValueError("量化指标文本过长")
        if any(len(value) > 2000 for value in self.limitations):
            raise ValueError("量化局限说明过长")
        for chart in self.charts:
            table = tables.get(chart.table_key)
            if table is None:
                raise ValueError("量化图表须引用本产物中实际留存的表格")
            keys = {column.key for column in table.columns}
            series = [item.key for item in chart.series]
            if chart.x_key not in keys or not set(series).issubset(keys) or len(series) != len(set(series)) or chart.x_key in series:
                raise ValueError("量化图表坐标和序列须引用实际且不重复的表格列")
            if not table.rows:
                raise ValueError("无观察值的表格不能绘制图表")
            for row in table.rows:
                if row[chart.x_key] is None or isinstance(row[chart.x_key], bool):
                    raise ValueError("图表横坐标须为明确的日期、分类或数值")
                if any(isinstance(row[key], (str, bool)) for key in series):
                    raise ValueError("图表纵坐标须为数值或明确的缺失值 null")
        return self


def _source_namespace(context, run_id, instrument_id, version_id=None):
    """An explicit historical version is a separate namespace, never an overlay."""
    if context.get("risk_run"):
        raise ValueError("风控研判只使用已绑定快照，不执行 Python 量化研究")
    if instrument_id not in context.get("instrument_ids", []):
        raise ValueError("量化标的不在本轮已绑定的研究范围")
    cutoff = context["cutoff"]
    if version_id is None:
        sources = research_sources(context, run_id)
    else:
        matches = [version for version in context.get("referenced_research_versions", [])
                   if version.get("version_id") == version_id and version.get("instrument_id") == instrument_id]
        if len(matches) != 1:
            raise ValueError("该历史版本未在本轮上下文中明确绑定，请从原研究版本发起追问")
        version = matches[0]
        # Unknown historical cutoff stays unknown; do not relabel it as today's
        # knowledge horizon. The selected source versions remain frozen.
        cutoff = version.get("information_cutoff")
        originals = version.get("sources") or []
        sources = {item["source_id"]: deepcopy(item) for item in originals}
        if len(sources) != len(originals):
            raise ValueError("历史版本包含重复的来源标识，不能确定本次量化的原始输入")
    allowed = {}
    for source in sources.values():
        if not _original_source(source, instrument_id, datetime.fromisoformat(cutoff) if cutoff else None):
            continue
        try:
            validate_notebook(ResearchNotebook(source_ids=[source["source_id"]]), instrument_id, sources)
        except ValueError:
            continue
        allowed[source["source_id"]] = source
    return allowed, cutoff


def input_catalogue(context, run_id, instrument_id, version_id=None):
    """List only the sources this run has actually retained and may use here."""
    sources, _ = _source_namespace(context, run_id, instrument_id, version_id)
    return [{**{key: source.get(key) for key in (
        "source_id", "source_type", "title", "instrument_id", "scope", "as_of", "run_cutoff", "published_at")},
        "version_id": version_id} for source in sources.values()]


def execute_analysis(run, request: QuantAnalysisInput):
    """No fetch/live query occurs here; only selected retained source IDs enter."""
    context = run.context_json
    sources, input_cutoff = _source_namespace(context, run.entry_id, request.instrument_id, request.version_id)
    if len(set(request.source_ids)) != len(request.source_ids) or not set(request.source_ids).issubset(sources):
        raise ValueError("量化输入重复、未在本轮留存或不属于本轮授权标的；请先读取量化输入目录")
    inputs = {sid: deepcopy(sources[sid]) for sid in request.source_ids}
    # A JSON round trip fixes the exact snapshot that entered the sandbox. It
    # rejects nonfinite source values rather than silently changing them.
    inputs = json.loads(json.dumps(inputs, ensure_ascii=False, allow_nan=False))
    output, capability = quant_sandbox.execute(request.code, inputs, request.params)
    output = QuantOutput.model_validate(output)
    now = datetime.now(UTC).isoformat()
    evidence = {
        "source_id": f"computed:{uuid4().hex}", "source_type": "computed_metric",
        "scope": "instrument", "instrument_id": request.instrument_id, "source_run_id": run.entry_id,
        "title": request.title, "as_of": context["cutoff"], "retrieved_at": now,
        "source_ids": list(request.source_ids),
        "methodology": {"analysis_kind": "python_quant", "description": request.methodology,
            "code": request.code, "params": deepcopy(request.params), "input_sources": list(inputs.values()),
            "input_version_id": request.version_id, "input_information_cutoff": input_cutoff,
            "runtime": capability, "executed_at": now,
            "result_status": "Executed and schema-validated; analytical validity requires factual review."},
        "data": {"analysis_kind": "python_quant", "status": "available", **output.model_dump(mode="json")},
    }
    # Kept with the same run/evidence path as other numeric tools. Publication
    # still passes the existing independent review and ownership boundaries.
    run.context_json = {**context, "computed_metrics": [*context.get("computed_metrics", []), evidence]}
    return evidence


def analysis_overview(source):
    """Avoid returning multi-MiB snapshots through the harness's 50 KiB window."""
    data = source["data"]
    return {"source_id": source["source_id"], "title": source["title"], "as_of": source["as_of"],
            "instrument_id": source["instrument_id"], "summary": data["summary"], "metrics": data["metrics"],
            "tables": [{"key": table["key"], "title": table["title"], "rows": len(table["rows"]),
                        "columns": table["columns"]} for table in data["tables"]],
            "charts": data["charts"], "limitations": data["limitations"],
            "next_read": {"tool": "read_quant_analysis", "source_id": source["source_id"], "section": "data"},
            "review_note": "代码已在隔离环境执行；执行成功不等于方法、数据口径或投资结论已经成立。"}
