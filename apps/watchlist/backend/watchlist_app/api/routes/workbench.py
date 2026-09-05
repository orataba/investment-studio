from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from watchlist_app.db.session import get_db_session
from watchlist_app.db.models import InstrumentDetail, InstrumentRiskReadModel, InstrumentSummaryReadModel, InstrumentChartReadModel, Watchlist, WatchlistItem
from watchlist_app.db.models.workbench import ResearchTopic, ResearchEntry, RiskCase, RiskReviewRule
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_workbench import catalogue, portfolio_options, external_json, conversation_context, instrument_evidence, compare_series
from watchlist_app.services.risk_workbench import refresh_risk_cases, event, now
from watchlist_app.services.price_risk import period_loss_readings, series_limitation

router = APIRouter()


def dump(record):
    return serialize_payload({column.name: getattr(record, column.name) for column in record.__table__.columns})


def require(session, model, identifier):
    record = session.get(model, identifier)
    if record is None:
        raise HTTPException(404, "Record not found")
    return record


class TopicInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    question: str = ""
    instrument_ids: list[str] = Field(default_factory=list)
    portfolio_id: str | None = None
    status: Literal["active", "concluded", "archived"] = "active"
    next_review_date: date | None = None


class EntryInput(BaseModel):
    kind: Literal["note", "evidence", "meeting", "decision", "task", "conclusion"] = "note"
    title: str = Field(min_length=1)
    body: str = ""
    source: str = ""
    follow_up_date: date | None = None


class CompletionInput(BaseModel):
    completed: bool


class MessageInput(BaseModel):
    question: str = Field(min_length=1, max_length=20000)
    watchlist_id: str | None = None


class ResearchToolInput(BaseModel):
    tool: Literal["instruments", "comparison", "portfolio", "market"]
    instrument_ids: list[str] = Field(default_factory=list, max_length=30)
    start_date: date | None = None
    end_date: date | None = None
    target_id: str | None = None
    benchmark_id: str | None = None


@router.get("/research/catalogue")
def get_catalogue(session: Session = Depends(get_db_session)):
    return {"instruments": catalogue(session)}


@router.get("/research/connections")
def get_connections():
    from watchlist_app.services.research_runner import harness_available
    return {**portfolio_options(), "assistant_available": harness_available()}


@router.get("/research/portfolios/{portfolio_id}/context")
def portfolio_context(portfolio_id: str):
    from urllib.parse import urlencode
    available = portfolio_options()
    if not any(p.get("portfolio_id") == portfolio_id for p in available["portfolios"]):
        raise HTTPException(404, "所选组合不可访问")
    try:
        return external_json("portfolio", "/workspace/holdings?" + urlencode({"portfolio_id": portfolio_id}))
    except (OSError, ValueError) as error:
        raise HTTPException(503, "暂时无法读取组合持仓，请稍后重试") from error


@router.get("/research/topics")
def topics(instrument_id: str | None = None, session: Session = Depends(get_db_session)):
    records = session.scalars(select(ResearchTopic).order_by(ResearchTopic.updated_at.desc())).all()
    return [dump(x) for x in records if not instrument_id or instrument_id in x.instrument_ids]


def validate_scope(session, request):
    request.title = request.title.strip()
    if not request.title:
        raise HTTPException(422, "请输入专题标题")
    request.instrument_ids = list(dict.fromkeys(request.instrument_ids))
    for iid in request.instrument_ids:
        require(session, InstrumentDetail, iid)
    if request.portfolio_id:
        available = portfolio_options()
        if not any(p.get("portfolio_id") == request.portfolio_id for p in available["portfolios"]):
            raise HTTPException(422, "所选组合当前不可访问")


@router.post("/research/topics", status_code=201)
def create_topic(request: TopicInput, session: Session = Depends(get_db_session)):
    validate_scope(session, request)
    record = ResearchTopic(topic_id=uuid4().hex, **request.model_dump(), conclusion="")
    session.add(record)
    session.commit()
    return dump(record)


@router.put("/research/topics/{topic_id}")
def update_topic(topic_id: str, request: TopicInput, session: Session = Depends(get_db_session)):
    record = require(session, ResearchTopic, topic_id)
    validate_scope(session, request)
    for key, value in request.model_dump().items():
        setattr(record, key, value)
    session.commit()
    return dump(record)


@router.get("/research/topics/{topic_id}")
def topic_detail(topic_id: str, session: Session = Depends(get_db_session)):
    record = require(session, ResearchTopic, topic_id)
    return {"topic": dump(record), "entries": [dump(x) for x in session.scalars(select(ResearchEntry).where(ResearchEntry.topic_id == topic_id).order_by(ResearchEntry.created_at.desc()))]}


@router.post("/research/topics/{topic_id}/entries", status_code=201)
def add_entry(topic_id: str, request: EntryInput, session: Session = Depends(get_db_session)):
    topic = require(session, ResearchTopic, topic_id)
    record = ResearchEntry(entry_id=uuid4().hex, topic_id=topic_id, **request.model_dump(), status="recorded", context_json={})
    if request.kind == "conclusion":
        record.context_json = {"previous_conclusion": topic.conclusion}
        topic.conclusion = request.body
    topic.updated_at = now()
    session.add(record)
    session.commit()
    return dump(record)


@router.put("/research/entries/{entry_id}/completion")
def complete_entry(entry_id: str, request: CompletionInput, session: Session = Depends(get_db_session)):
    record = require(session, ResearchEntry, entry_id)
    if record.kind == "analysis":
        raise HTTPException(422, "研究助手草稿不是待办事项")
    record.completed_at = now() if request.completed else None
    session.commit()
    return dump(record)


@router.post("/research/topics/{topic_id}/files")
async def upload_material(topic_id: str, file: UploadFile = File(...), session: Session = Depends(get_db_session)):
    from watchlist_app.api.routes.funds import _persist_uploaded_document, _safe_file_segment
    from watchlist_app.core.settings import get_settings
    topic = require(session, ResearchTopic, topic_id)
    entry_id = uuid4().hex
    name = _safe_file_segment(file.filename, fallback="document")
    folder = get_settings().document_storage_root / "research" / topic_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{entry_id}-{name}"
    await _persist_uploaded_document(file, path)
    try:
        text, extraction = "", "未提取正文；请补充摘要或原文"
        if path.suffix.lower() in {".txt", ".md", ".csv"}:
            text = path.read_text(encoding="utf-8-sig")[:60000]
            extraction = "文本最多保留前 60000 字符；完整原件保留"
        elif path.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(path)
            text = "\n".join(f"[第 {i+1} 页]\n{page.extract_text() or ''}" for i, page in enumerate(reader.pages))[:60000]
            extraction = "PDF 文字层，最多前 60000 字符；扫描件需补充文字" if text.strip() else extraction
        record = ResearchEntry(entry_id=entry_id, topic_id=topic_id, kind="evidence", title=name, body=text,
            source=f"/api/research/entries/{entry_id}/file", status="recorded", context_json={"file_name": name, "extraction": extraction})
        topic.updated_at = now()
        session.add(record)
        session.commit()
    except (UnicodeError, ValueError) as error:
        path.unlink(missing_ok=True)
        raise HTTPException(422, "无法读取材料，请上传有效文件或补充文字摘要") from error
    except Exception:
        session.rollback()
        path.unlink(missing_ok=True)
        raise
    return dump(record)


@router.get("/research/entries/{entry_id}/file")
def download_material(entry_id: str, session: Session = Depends(get_db_session)):
    from watchlist_app.core.settings import get_settings
    record = require(session, ResearchEntry, entry_id)
    name = record.context_json.get("file_name")
    if not name:
        raise HTTPException(404, "File not found")
    path = get_settings().document_storage_root / "research" / record.topic_id / f"{entry_id}-{Path(name).name}"
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=name)


@router.post("/research/topics/{topic_id}/analysis", status_code=202)
def start_analysis(topic_id: str, request: MessageInput, background: BackgroundTasks, session: Session = Depends(get_db_session)):
    from watchlist_app.services.research_runner import harness_available, run_analysis
    if not harness_available():
        raise HTTPException(503, "研究助手尚未配置 DeepSeek 运行环境")
    topic = session.scalar(select(ResearchTopic).where(ResearchTopic.topic_id == topic_id).with_for_update())
    if not topic:
        raise HTTPException(404, "对话不存在")
    if not request.question.strip():
        raise HTTPException(422, "请输入问题")
    if session.scalar(select(ResearchEntry.entry_id).where(ResearchEntry.topic_id == topic_id, ResearchEntry.status.in_(["queued", "running"]))):
        raise HTTPException(409, "助手正在回复，请等本轮完成后继续提问")
    if request.watchlist_id:
        require(session, Watchlist, request.watchlist_id)
    if not session.scalar(select(ResearchEntry.entry_id).where(ResearchEntry.topic_id == topic_id, ResearchEntry.kind == "analysis")):
        topic.title = request.question.strip()[:80]
    context = conversation_context(session, topic, request.question, request.watchlist_id)
    record = ResearchEntry(entry_id=uuid4().hex, topic_id=topic_id, kind="analysis", title=request.question, body="", source="DeepSeek Harness", status="queued", context_json=context)
    session.add(record)
    topic.updated_at = now()
    session.commit()
    background.add_task(run_analysis, record.entry_id)
    return dump(record)


@router.get("/research/runs/{run_id}/context")
def run_context(run_id: str, session: Session = Depends(get_db_session)):
    record = require(session, ResearchEntry, run_id)
    if record.kind != "analysis":
        raise HTTPException(404, "Analysis not found")
    return record.context_json


@router.post("/research/runs/{run_id}/tools")
def research_tool(run_id: str, request: ResearchToolInput, session: Session = Depends(get_db_session)):
    record = session.scalar(select(ResearchEntry).where(ResearchEntry.entry_id == run_id).with_for_update())
    if not record or record.kind != "analysis":
        raise HTTPException(404, "对话回复不存在")
    if record.status not in {"queued", "running"}:
        raise HTTPException(409, "本轮对话已结束")
    context = record.context_json
    known = {x["instrument_id"] for x in context["catalogue"]}
    ids = list(dict.fromkeys([*request.instrument_ids, *([request.benchmark_id] if request.benchmark_id else [])]))
    if not set(ids).issubset(known) or (request.target_id and request.target_id not in ids):
        raise HTTPException(422, "请使用目录中的标的编码；目标须在本次比较内")
    if request.tool in {"instruments", "comparison"} and not ids:
        raise HTTPException(422, "请选择需要读取的标的")
    if request.tool == "instruments":
        result = instrument_evidence(session, ids)
    elif request.tool == "comparison":
        start, end = request.start_date, request.end_date
        if not start or not end or start >= end or end > date.today():
            raise HTTPException(422, "比较需要已发生的有效起止日期")
        series = {}
        for iid in ids:
            chart = session.get(InstrumentChartReadModel, iid)
            series[iid] = (chart.payload_json.get("research_returns") or {}) if chart else {}
        result = compare_series(series, start, end, request.target_id, request.benchmark_id)
    elif request.tool == "portfolio":
        if not context.get("portfolio_id"):
            result = {"available": False, "reason": "尚未关联组合，请用户在对话中选择组合后继续。"}
        else:
            try:
                from urllib.parse import urlencode
                result = external_json("portfolio", "/workspace/holdings?" + urlencode({"portfolio_id": context["portfolio_id"]}))
            except (OSError, ValueError):
                result = {"available": False, "reason": "未取得组合持仓，不能推断权重或实际持仓。"}
    else:
        try:
            result = {"regime": external_json("regime", "/latest"), "limitations": ["仅代表已配置市场，须核对数据日期；不等于完整宏观或全市场资料。"]}
        except (OSError, ValueError):
            result = {"available": False, "reason": "尚未取得市场状态，本次没有实时宏观证据。"}
    source_id = f"{request.tool}:{uuid4().hex[:12]}"
    evidence = serialize_payload({"source_id": source_id, "tool": request.tool, "request": request.model_dump(), "retrieved_at": now(), "result": result})
    record.context_json = {**context, "tool_evidence": [*context.get("tool_evidence", []), evidence]}
    session.commit()
    return evidence


class RuleInput(BaseModel):
    drawdown_limit: float | None = Field(default=None, gt=0, le=100)
    period_limits: dict[Literal["day", "week", "month", "quarter"], Annotated[float, Field(gt=0, le=100)] | None] | None = None


class RiskInput(BaseModel):
    instrument_id: str
    title: str = Field(min_length=1)
    body: str = ""
    follow_up_date: date | None = None
    importance: Literal["low", "medium", "high"] = "medium"
    source: str = ""


class RiskFollowUp(BaseModel):
    status: Literal["open", "investigating", "handled"]
    note: str = ""
    follow_up_date: date | None = None
    clear_manual_trigger: bool = False


@router.get("/risk")
def risk_workspace(instrument_id: str | None = None, instrument_ids: str | None = None, watchlist_id: str | None = None, session: Session = Depends(get_db_session)):
    instruments = catalogue(session)
    cases_query = select(RiskCase).order_by(RiskCase.trigger_active.desc(), RiskCase.updated_at.desc())
    ids = None
    if watchlist_id:
        require(session, Watchlist, watchlist_id)
        ids = set(session.scalars(select(WatchlistItem.instrument_id).where(WatchlistItem.watchlist_id == watchlist_id)))
    if instrument_ids is not None:
        requested = set(filter(None, instrument_ids.split(",")))
        ids = requested if ids is None else ids & requested
    if instrument_id:
        ids = {instrument_id} if ids is None else ids & {instrument_id}
    if ids is not None:
        cases_query = cases_query.where(RiskCase.instrument_id.in_(ids))
        instruments = [x for x in instruments if x["instrument_id"] in ids]
    for item in instruments:
        iid = item["instrument_id"]
        risk = session.get(InstrumentRiskReadModel, iid)
        rule = session.get(RiskReviewRule, iid)
        item["risk"] = risk.payload_json if risk else None
        item["freshness"] = risk.data_freshness_status if risk else "missing"
        item["drawdown_limit"] = rule.drawdown_limit if rule else None
        item["drawdown_change_pp"] = None
        item["previous_observation_date"] = None
        chart = session.get(InstrumentChartReadModel, iid)
        series = (chart.payload_json.get("research_returns") or {}) if chart else {}
        points = series.get("points") or []
        item["period_limits"] = rule.period_limits_json if rule else {}
        item["price_risk_calibration"] = rule.calibration_json if rule else {}
        item["period_readings"] = period_loss_readings(series, item["period_limits"])
        item["price_risk_note"] = series_limitation(series) or ("不足 63 个日收益，尚未生成波动初值" if len(points) < 64 else None)
        item["return_kind"] = (series.get("metadata") or {}).get("return_kind")
        current = (risk.payload_json.get("current_drawdown") if risk else None)
        if current is not None and len(points) >= 3 and risk.payload_json.get("data_quality", {}).get("status") == "ready":
            from watchlist_app.services.canonical_recalc import _current_drawdown
            previous = _current_drawdown([{**p, "as_of_date": date.fromisoformat(p["date"])} for p in points[:-1]])
            if previous is not None:
                item["drawdown_change_pp"] = float(current) - previous
                item["previous_observation_date"] = points[-2]["date"]
    return {"instruments": instruments, "cases": [dump(x) for x in session.scalars(cases_query)]}


@router.put("/risk/rules/{instrument_id}")
def set_rule(instrument_id: str, request: RuleInput, session: Session = Depends(get_db_session)):
    require(session, InstrumentDetail, instrument_id)
    rule = session.get(RiskReviewRule, instrument_id)
    if rule is None:
        rule = RiskReviewRule(instrument_id=instrument_id)
        session.add(rule)
    if "drawdown_limit" in request.model_fields_set:
        rule.drawdown_limit = request.drawdown_limit
    if request.period_limits is not None:
        limits = {key: request.period_limits.get(key) for key in ("day", "week", "month", "quarter")}
        if limits != rule.period_limits_json:
            rule.calibration_json = {**(rule.calibration_json or {}), "manually_edited": True}
        rule.period_limits_json = limits
    refresh_risk_cases(session, [instrument_id])
    session.commit()
    return dump(rule)


@router.post("/risk/cases", status_code=201)
def add_risk(request: RiskInput, session: Session = Depends(get_db_session)):
    require(session, InstrumentDetail, request.instrument_id)
    case = RiskCase(
        case_id=uuid4().hex, instrument_id=request.instrument_id, title=request.title,
        body=request.body, follow_up_date=request.follow_up_date, signal="manual",
        severity="observation" if request.importance == "low" else "attention",
        trigger_active=True, status="open", observed_on=date.today(),
        evidence_json=request.model_dump(include={"importance", "source"}), history_json=[],
    )
    event(case, "triggered", request.body)
    session.add(case)
    session.commit()
    return dump(case)


@router.put("/risk/cases/{case_id}")
def follow_up(case_id: str, request: RiskFollowUp, session: Session = Depends(get_db_session)):
    case = require(session, RiskCase, case_id)
    case.status = request.status
    case.follow_up_date = request.follow_up_date
    if request.clear_manual_trigger:
        if case.signal != "manual":
            raise HTTPException(422, "自动触发事项由新数据判断是否解除；可以更新跟进状态")
        case.trigger_active = False
        case.resolved_at = now()
    event(case, request.status, request.note or "更新跟进状态")
    session.commit()
    return dump(case)
