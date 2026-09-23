from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from watchlist_app.db.session import get_db_session
from watchlist_app.db.models import InstrumentDetail, InstrumentRiskReadModel, InstrumentSummaryReadModel, InstrumentChartReadModel, Watchlist, WatchlistItem
from watchlist_app.db.models.workbench import ResearchTopic, ResearchEntry, RiskCase, RiskReviewRule
from watchlist_app.services.read_models import serialize_payload
from watchlist_app.services.research_workbench import catalogue, portfolio_options, external_json, conversation_context, instrument_evidence, compare_series
from watchlist_app.services.risk_workbench import refresh_risk_cases, event, now
from watchlist_app.services.price_risk import period_loss_readings, series_limitation

from studio_identity import current_principal
from watchlist_app.services.research_access import require_topic_access, require_entry_access, visible_topics, require_portfolio

router = APIRouter()


def dump(record):
    return serialize_payload({column.name: getattr(record, column.name) for column in record.__table__.columns})


def managed_topic(topic_id):
    return topic_id.startswith(("dossier:", "risk-officer:", "instrument-events:")) or topic_id == "us-sector-daily-review"


def require(session, model, identifier):
    record = session.get(model, identifier)
    if record is None:
        raise HTTPException(404, "Record not found")
    if model is ResearchTopic:
        require_topic_access(session, record)
    if model is ResearchEntry:
        require_entry_access(session, record)
    if model is ResearchTopic and managed_topic(record.topic_id):
        raise HTTPException(422, "此记录由研究追踪或风控模块维护，请使用对应页面")
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


class ResearchReferenceInput(BaseModel):
    research_update_id: str | None = None
    event_case_id: str | None = None
    event_version_id: str | None = None
    theme_id: str | None = None
    pm_note_id: str | None = None
    pm_note_revision: int | None = Field(default=None, ge=1)
    instrument_id: str
    notebook_version_id: str | None = None
    theme_version_id: str | None = None
    investment_view_version_id: str | None = None
    forecast_key: str | None = None
    forecast_version_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    risk_case_id: str | None = Field(default=None, min_length=1)
    risk_case_updated_at: datetime | None = None

    @model_validator(mode="after")
    def distinct_current_risk_reference(self):
        if bool(self.risk_case_id) != bool(self.risk_case_updated_at):
            raise ValueError("风险事项引用须同时包含事项标识和页面读取的更新时间，请刷新后重试")
        if self.risk_case_id and any((self.research_update_id, self.event_case_id, self.event_version_id,
                self.theme_id, self.theme_version_id, self.pm_note_id, self.pm_note_revision, self.notebook_version_id,
                self.investment_view_version_id, self.forecast_key, self.forecast_version_id, self.source_ids)):
            raise ValueError("当前风险快照不能与历史研究版本混为同一个引用")
        return self


class PageContextInput(BaseModel):
    surface: Literal["instrument", "watchlist", "portfolio"]
    instrument_id: str | None = None
    watchlist_id: str | None = None
    portfolio_id: str | None = None
    holding_id: str | None = None
    account_id: str | None = None
    as_of_date: date | None = None
    tab: str | None = None
    currency: str | None = None
    benchmark: str | None = None
    start: date | None = None
    end: date | None = None
    research_reference: ResearchReferenceInput | None = None


class MessageInput(BaseModel):
    question: str = Field(min_length=1, max_length=20000)
    watchlist_id: str | None = None
    page_context: PageContextInput | None = None


class ResearchToolInput(BaseModel):
    tool: Literal["instruments", "comparison", "portfolio", "market", "search", "source", "dossier", "risk_review"]
    instrument_ids: list[str] = Field(default_factory=list, max_length=30)
    start_date: date | None = None
    end_date: date | None = None
    target_id: str | None = None
    benchmark_id: str | None = None
    query: str | None = Field(default=None, min_length=1, max_length=2000)
    url: str | None = Field(default=None, min_length=1, max_length=8000)
    public_result: dict | None = None
    source_id: str | None = None


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
    records = visible_topics(session)
    return [dump(x) for x in records if not managed_topic(x.topic_id) and (not instrument_id or instrument_id in x.instrument_ids)]


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
    actor = current_principal()
    record = ResearchTopic(topic_id=uuid4().hex, **request.model_dump(), conclusion="", team_id=actor.team_id,
                           created_by_user_id=actor.user_id, visibility="private")
    session.add(record)
    session.commit()
    return dump(record)


@router.put("/research/topics/{topic_id}")
def update_topic(topic_id: str, request: TopicInput, session: Session = Depends(get_db_session)):
    record = require(session, ResearchTopic, topic_id)
    validate_scope(session, request)
    if record.portfolio_id != request.portfolio_id and session.scalar(select(ResearchEntry.entry_id).where(ResearchEntry.topic_id == topic_id).limit(1)):
        raise HTTPException(422, "已有记录的对话不能更换或解除组合范围，请新建对话")
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
    record = ResearchEntry(entry_id=uuid4().hex, topic_id=topic_id, **request.model_dump(), status="recorded", context_json={},
                           team_id=topic.team_id, author_user_id=current_principal().user_id)
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
    if managed_topic(record.topic_id):
        raise HTTPException(422, "此记录由研究追踪或风控模块维护，请使用对应页面")
    if record.kind == "analysis":
        raise HTTPException(422, "研究助手草稿不是待办事项")
    record.completed_at = now() if request.completed else None
    session.commit()
    return dump(record)


@router.post("/research/topics/{topic_id}/files")
async def upload_material(topic_id: str, file: UploadFile = File(...), session: Session = Depends(get_db_session)):
    topic = require(session, ResearchTopic, topic_id)
    return dump(await _save_uploaded_material(topic, file, session))


async def _save_uploaded_material(topic: ResearchTopic, file: UploadFile, session: Session,
                                  *, title: str | None = None, metadata: dict | None = None):
    """Save a file after the owning conversation or dossier route resolves its topic."""
    from watchlist_app.api.routes.funds import _persist_uploaded_document, _safe_file_segment
    from watchlist_app.core.settings import get_settings
    entry_id = uuid4().hex
    name = _safe_file_segment(file.filename, fallback="document")
    folder = get_settings().document_storage_root / "research" / topic.topic_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{entry_id}-{name}"
    await _persist_uploaded_document(file, path)
    try:
        from watchlist_app.services.research_dossier import _file_text
        text, extraction_status, extraction = _file_text(path)
        if extraction_status in {"failed", "missing"}:
            raise ValueError("Uploaded research material could not be read")
        record = ResearchEntry(entry_id=entry_id, topic_id=topic.topic_id, team_id=topic.team_id, author_user_id=current_principal().user_id, kind="evidence", title=(title or "").strip() or name, body=text,
            source=f"/api/research/entries/{entry_id}/file", status="recorded",
            context_json={**(metadata or {}), "file_name": name, "extraction": extraction, "extraction_status": extraction_status})
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
    return record


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
    require_topic_access(session, topic)
    from watchlist_app.services.research_access import topic_portfolio_ids
    if topic_portfolio_ids(session, topic) - {topic.portfolio_id}:
        raise HTTPException(422, "此旧对话包含其他组合的历史资料；可查阅历史，请新建绑定组合的对话继续研究")
    if managed_topic(topic.topic_id):
        raise HTTPException(422, "此记录由研究追踪或风控模块维护，请使用对应页面")
    if not request.question.strip():
        raise HTTPException(422, "请输入问题")
    if session.scalar(select(ResearchEntry.entry_id).where(ResearchEntry.topic_id == topic_id, ResearchEntry.status.in_(["queued", "running"]))):
        raise HTTPException(409, "助手正在回复，请等本轮完成后继续提问")
    if request.watchlist_id:
        require(session, Watchlist, request.watchlist_id)
    page = request.page_context
    if page:
        if page.surface != "portfolio" and any((page.holding_id, page.account_id, page.as_of_date)):
            raise HTTPException(422, "持仓、账户与组合估值日只能用于已绑定组合的页面")
        if page.surface == "portfolio" and (not page.portfolio_id or page.portfolio_id != topic.portfolio_id):
            raise HTTPException(422, "当前页面与对话关联的组合不一致，请重新打开该组合的研究助手")
        if page.surface == "instrument" and (not page.instrument_id or page.instrument_id not in topic.instrument_ids):
            raise HTTPException(422, "当前页面与对话关联的标的不一致，请重新打开该标的的研究助手")
        if page.research_reference and page.research_reference.instrument_id not in topic.instrument_ids:
            raise HTTPException(422, "引用的研究记录不在对话关联标的内")
        if page.research_reference:
            from watchlist_app.services.research_activity import resolve_research_update, resolve_event_reference
            ref = page.research_reference
            try:
                linked = resolve_research_update(session, ref.instrument_id, ref.research_update_id) if ref.research_update_id else None
                if ref.event_case_id or ref.event_version_id:
                    event = resolve_event_reference(session, ref.instrument_id, ref.event_case_id, ref.event_version_id)
                    if linked and linked["reference"].get("event_version_id") != event["reference"]["event_version_id"]:
                        raise ValueError("引用的研究更新与事件版本不一致")
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
        if page.surface == "watchlist":
            if not page.watchlist_id or (request.watchlist_id and page.watchlist_id != request.watchlist_id):
                raise HTTPException(422, "当前页面与对话关联的观察列表不一致")
            require(session, Watchlist, page.watchlist_id)
            request.watchlist_id = page.watchlist_id
    if not session.scalar(select(ResearchEntry.entry_id).where(ResearchEntry.topic_id == topic_id, ResearchEntry.kind == "analysis")):
        topic.title = request.question.strip()[:80]
    try:
        context = conversation_context(session, topic, request.question, request.watchlist_id,
                                       request.page_context.model_dump(mode="json", exclude_none=True) if request.page_context else None)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    record = ResearchEntry(entry_id=uuid4().hex, topic_id=topic_id, team_id=topic.team_id, kind="analysis", title=request.question, body="", source="DeepSeek Harness", status="queued", created_at=now(), context_json=context)
    session.add(record)
    topic.updated_at = now()
    session.commit()
    from watchlist_app.services.research_runner import authorize_run
    token = authorize_run(current_principal(), record.entry_id)
    background.add_task(run_analysis, record.entry_id, token, current_principal())
    return dump(record)


def _run_source_context(session, run_id):
    from types import SimpleNamespace
    from sqlalchemy import JSON, true
    from watchlist_app.services.research_access import research_context_projection, research_projection_rows
    from watchlist_app.services.research_read_projection import run_source_index
    relation, values = research_context_projection(session, {
        "research_actor": JSON, "web_evidence": JSON, "market_text_sources": JSON})
    query = select(ResearchEntry.entry_id, ResearchEntry.topic_id, ResearchEntry.team_id, ResearchEntry.kind,
        *(value.label(key) for key, value in values.items())).select_from(ResearchEntry)
    if relation is not None:
        query = query.join(relation, true())
    rows = research_projection_rows(session, query.where(ResearchEntry.entry_id == run_id),
                                   {name: (name,) for name in values})
    row = rows[0] if rows else None
    if row is None:
        raise HTTPException(404, "Record not found")
    record = SimpleNamespace(entry_id=row.entry_id, topic_id=row.topic_id, team_id=row.team_id,
        context_json={"research_actor": row.research_actor})
    require_entry_access(session, record)
    if row.kind != "analysis":
        raise HTTPException(404, "Analysis not found")
    return {"sources": run_source_index(row._mapping)}


@router.get("/research/runs/{run_id}/context")
def run_context(run_id: str, originals: bool = False, session: Session = Depends(get_db_session),
                section: Literal["sources"] | None = None):
    if section == "sources":
        if originals:
            raise HTTPException(422, "来源目录不展开原文正文")
        return _run_source_context(session, run_id)
    record = require(session, ResearchEntry, run_id)
    if record.kind != "analysis":
        raise HTTPException(404, "Analysis not found")
    from watchlist_app.services.research_notebook import dossier_outline
    context = {key: value for key, value in record.context_json.items() if key != "sector_company_data"}
    if originals:
        from datetime import datetime
        from watchlist_app.services.market_evidence import hydrate_source
        cutoff = datetime.fromisoformat(context["cutoff"])
        context["market_text_sources"] = [hydrate_source(source, cutoff=cutoff)
            for source in context.get("market_text_sources", [])]
        context["web_evidence"] = [{**capture, "sources": [hydrate_source(source, cutoff=cutoff)
            for source in capture.get("sources", [])]} for capture in context.get("web_evidence", [])]
        dossiers = []
        for dossier in context.get("research_dossiers", []):
            notebook = dossier.get("notebook")
            dossiers.append({**dossier, "prior_sources": [hydrate_source(source, cutoff=cutoff)
                for source in dossier.get("prior_sources", [])],
                "notebook": {**notebook, "sources": [hydrate_source(source, cutoff=cutoff)
                    for source in notebook.get("sources", [])]} if notebook else None})
        context["research_dossiers"] = dossiers
    elif context.get("research_dossiers"):
        context["research_dossiers"] = [dossier_outline(d) for d in context["research_dossiers"]]
    return context


@router.get("/research/runs/{run_id}/computed-source")
def run_computed_source(run_id: str, source_id: str, session: Session = Depends(get_db_session)):
    """Read one already retained calculation; never calculate or refresh its inputs."""
    record = require(session, ResearchEntry, run_id)
    if record.kind != "analysis" or record.context_json.get("risk_run"):
        raise HTTPException(404, "本轮没有可读取的普通研究计算来源")
    source = next((item for item in record.context_json.get("computed_metrics", [])
                   if item.get("source_id") == source_id and item.get("source_type") == "computed_metric"), None)
    if source is None:
        raise HTTPException(404, "该计算来源未在本轮取得并留存")
    # Earlier comparison metrics kept target/benchmark selection only in the
    # matching tool receipt. Preserve that exact request when reading its source.
    if "input_series" in source and "request" not in source:
        receipt = next((item for item in record.context_json.get("tool_evidence", [])
                        if item.get("source_id") == source_id and item.get("tool") == "comparison"), None)
        if receipt:
            return {**source, "request": receipt["request"], "retrieved_at": receipt["retrieved_at"]}
    return source


@router.get("/research/runs/{run_id}/dossier/{instrument_id}")
def run_dossier(run_id: str, instrument_id: str, source_id: str | None = None, version_id: str | None = None, update_id: str | None = None,
                session: Session = Depends(get_db_session)):
    from watchlist_app.services.research_notebook import dossier_outline, dossier_source
    record = require(session, ResearchEntry, run_id)
    if not (record.context_json.get("sector_run") or record.context_json.get("research_run")):
        raise HTTPException(404, "本轮研究没有该标的的档案快照")
    dossier = next((d for d in record.context_json.get("research_dossiers", []) if d["instrument_id"] == instrument_id), None)
    if dossier is None:
        raise HTTPException(404, "请先读取该标的以绑定本轮研究档案")
    try:
        if sum(bool(value) for value in (source_id, version_id, update_id)) > 1:
            raise ValueError("请选择一个原文、底稿版本或研究更新读取")
        if update_id:
            from watchlist_app.services.research_activity import resolve_research_update
            from watchlist_app.services.research_identity import run_identity
            update = resolve_research_update(session, instrument_id, update_id, actor=run_identity(record.context_json))
            if datetime.fromisoformat(update["recorded_at"]) > datetime.fromisoformat(record.context_json["cutoff"]):
                raise ValueError("该研究更新在本轮截止时间之后形成，请在新一轮读取")
            # Current supersession/withdrawal flags may reflect later research.
            # Return the dated record, without asserting its state at an earlier cutoff.
            return {"kind": "research_update", "value": {key: value for key, value in update.items()
                    if key not in {"superseded", "withdrawn", "withdrawal_reason", "withdrawn_at"}}}
        if version_id:
            from watchlist_app.services.research_dossier import read_dossier_version
            from watchlist_app.services.research_identity import run_identity
            version = read_dossier_version(session, instrument_id, version_id, actor=run_identity(record.context_json))
            recorded = version["value"].get("recorded_at") or version["value"].get("updated_at") or version["value"].get("created_at")
            stamp = datetime.fromisoformat(recorded) if recorded else None
            bound_pm_version = version["kind"] == "pm_view" and any(
                item.get("version_id") == version_id for note in dossier.get("pm_views", []) for item in note.get("versions", []))
            if not bound_pm_version and stamp and stamp.replace(tzinfo=stamp.tzinfo or UTC) > datetime.fromisoformat(record.context_json["cutoff"]):
                raise ValueError("该版本在本轮截止时间之后才保存，请在新一轮读取")
            return version
        return dossier_source(dossier, source_id) if source_id else dossier_outline(dossier)
    except (ValueError, LookupError) as error:
        raise HTTPException(404, str(error)) from error


@router.post("/research/runs/{run_id}/tools")
def research_tool(run_id: str, request: ResearchToolInput, session: Session = Depends(get_db_session)):
    record = session.scalar(select(ResearchEntry).where(ResearchEntry.entry_id == run_id).with_for_update())
    if not record or record.kind != "analysis":
        raise HTTPException(404, "对话回复不存在")
    require_entry_access(session, record, tool_write=True)
    if record.status not in {"queued", "running"}:
        raise HTTPException(409, "本轮对话已结束")
    context = record.context_json
    if context.get("risk_run"):
        raise HTTPException(422, "风控研判只使用本轮已绑定的风险与持仓快照")
    if context.get("sector_run") and request.tool in {"portfolio", "risk_review"}:
        raise HTTPException(422, "标的研究只读取已绑定的标的范围；组合持仓需在关联组合的对话中读取")
    known = {x["instrument_id"] for x in context["catalogue"]}
    ids = list(dict.fromkeys([*request.instrument_ids, *([request.benchmark_id] if request.benchmark_id else [])]))
    if not set(ids).issubset(known) or (request.target_id and request.target_id not in ids):
        raise HTTPException(422, "请使用目录中的标的编码；目标须在本次比较内")
    if request.tool in {"instruments", "comparison"} and not ids:
        raise HTTPException(422, "请选择需要读取的标的")
    if request.tool == "instruments":
        from watchlist_app.services.sector_research import bind_research_instruments
        bind_research_instruments(session, record, ids)
        context = record.context_json
        result = {"assets": [asset for asset in context["instrument_inputs"] if asset["instrument_id"] in ids]}
    elif request.tool == "dossier":
        from watchlist_app.services.research_notebook import dossier_outline, dossier_source
        if len(ids) != 1:
            raise HTTPException(422, "请选择一个标的的研究档案")
        from watchlist_app.services.sector_research import bind_research_instruments
        bind_research_instruments(session, record, ids)
        context = record.context_json
        dossier = next(d for d in context["research_dossiers"] if d["instrument_id"] == ids[0])
        try:
            result = dossier_source(dossier, request.source_id) if request.source_id else dossier_outline(dossier)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
    elif request.tool == "risk_review":
        from watchlist_app.services.risk_officer import review_workspace
        page = context.get("page_context") or {}
        selected = context.get("selected_instrument_ids") or []
        if ids:
            if len(ids) != 1:
                raise HTTPException(422, "请选择一个标的的风控研判")
            scope = {"instrument_id": ids[0]}
        elif page.get("surface") == "instrument" and page.get("instrument_id"):
            scope = {"instrument_id": page["instrument_id"]}
        elif context.get("portfolio_id"):
            scope = {"portfolio_id": context["portfolio_id"]}
        elif context.get("watchlist_id"):
            scope = {"watchlist_id": context["watchlist_id"]}
        elif len(selected) == 1:
            scope = {"instrument_id": selected[0]}
        else:
            raise HTTPException(422, "当前对话尚未指定列表、组合或单个标的")
        result = review_workspace(session, **scope)
    elif request.tool == "comparison":
        start, end = request.start_date, request.end_date
        cutoff_date = datetime.fromisoformat(context["cutoff"]).date()
        if not start or not end or start >= end or end > cutoff_date:
            raise HTTPException(422, "比较需要已发生的有效起止日期")
        series = {}
        cutoff = datetime.fromisoformat(context["cutoff"])
        for iid in ids:
            chart = session.get(InstrumentChartReadModel, iid)
            known = chart.last_recalculated_at if chart else None
            known = known.replace(tzinfo=known.tzinfo or UTC) if known else None
            series[iid] = (chart.payload_json.get("research_returns") or {}) if known and known <= cutoff else {}
        result = compare_series(series, start, end, request.target_id, request.benchmark_id)
    elif request.tool == "portfolio":
        if not context.get("portfolio_id"):
            result = {"available": False, "reason": "尚未关联组合，请用户在对话中选择组合后继续。"}
        else:
            try:
                from watchlist_app.services.research_workbench import portfolio_page_evidence
                result = portfolio_page_evidence(context["portfolio_id"], context.get("page_context"))
            except (OSError, ValueError):
                result = {"available": False, "reason": "未取得组合持仓，不能推断权重或实际持仓。"}
    elif request.tool in {"search", "source"}:
        if request.tool == "search" and not (request.query or "").strip():
            raise HTTPException(422, "请提供公开信息检索问题")
        if request.tool == "source" and not request.url:
            raise HTTPException(422, "请提供原文链接")
        if request.public_result is None:
            raise HTTPException(422, "请提供本轮工具取得的公开来源结果")
        # Acquisition runs in the existing credential-bearing MCP process, as for
        # sector evidence. Retained source text is not an independently verified alert.
        result = request.public_result
    else:
        try:
            result = {"regime": external_json("regime", "/latest"), "limitations": ["仅代表已配置市场，须核对数据日期；不等于完整宏观或全市场资料。"]}
        except (OSError, ValueError):
            result = {"available": False, "reason": "尚未取得市场状态，本次没有实时宏观证据。"}
    source_id = f"{request.tool}:{uuid4().hex[:12]}"
    evidence = serialize_payload({"source_id": source_id, "tool": request.tool, "request": request.model_dump(exclude={"public_result"}), "retrieved_at": now(), "result": result})
    context = {**context, "tool_evidence": [*context.get("tool_evidence", []), evidence]}
    if request.tool == "comparison":
        metric = {"source_id": source_id, "source_type": "computed_metric", "scope": "public_market",
            "title": "已登记标的共同观察区间比较", "as_of": context["cutoff"], "data": result,
            "methodology": result["method"], "input_series": series,
            "request": evidence["request"], "retrieved_at": evidence["retrieved_at"]}
        context["computed_metrics"] = [*context.get("computed_metrics", []), metric]
    record.context_json = context
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
    cases = []
    from watchlist_app.services.sector_research import event_record
    for case in session.scalars(cases_query):
        value = dump(case)
        if case.signal.startswith("sector:"):
            value["evidence_json"] = {**value["evidence_json"], "event_version_id": event_record(case)["event_version_id"]}
        cases.append(value)
    return {"instruments": instruments, "cases": cases}


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
