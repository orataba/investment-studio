from datetime import date, datetime, UTC
from typing import Literal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from watchlist_app.db.session import get_db_session
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.services import sector_research as service
from watchlist_app.services.research_runner import harness_available, run_analysis
from watchlist_app.services.sector_estimates import read_estimate_evidence
from watchlist_app.services.research_user_commands import UserCommand

router = APIRouter()


@router.post("/research/runs/{run_id}/user-command")
def user_command(run_id: str, request: UserCommand, session: Session = Depends(get_db_session)):
    from watchlist_app.services.research_user_commands import apply_user_command
    run = current_run(session, run_id, writing=True)
    try:
        result = apply_user_command(session, run, request)
    except (ValueError, LookupError) as error:
        raise HTTPException(422, str(error)) from error
    session.commit()
    return result


@router.get("/sector-research/estimates")
def sector_estimates(instrument_id: str, session: Session = Depends(get_db_session)):
    return read_estimate_evidence(session, instrument_id)


@router.get("/sector-research")
def sector_research(instrument_id: str | None = None, watchlist_id: str | None = None, session: Session = Depends(get_db_session)):
    ids = service.scoped_ids(session, instrument_id, watchlist_id)
    states = service.review_states(session, instrument_ids=ids)
    reviews, completed = states["latest"], states["last_completed"]
    sectors = [{"instrument_id": iid, "ticker": iid.upper(), "sector_name": service.instrument_label(session, iid),
                "latest_review": reviews.get(iid), "last_completed_review": completed.get(iid)} for iid in ids]
    available = bool(ids)
    message = None if available else "当前范围没有可分析的已登记标的。"
    return {"available": available, "research_enabled": available, "sectors": sectors,
            "events": service.events_for_instruments(session, ids), "message": message}


class RunInput(BaseModel):
    instrument_ids: list[str] = Field(min_length=1, max_length=11)


@router.post("/sector-research/runs", status_code=202)
def start_run(request: RunInput, background: BackgroundTasks, session: Session = Depends(get_db_session)):
    if not harness_available():
        raise HTTPException(503, "研究助手尚未配置DeepSeek运行环境")
    try:
        run, created = service.begin_run(session, request.instrument_ids)
    except service.ReviewInProgress as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if created:
        from studio_identity import current_principal
        from watchlist_app.services.research_runner import authorize_run
        token = authorize_run(current_principal(), run.entry_id)
        background.add_task(run_analysis, run.entry_id, token, current_principal())
    return {"run_id": run.entry_id, "status": run.status}


def current_run(session, run_id, *, writing=False):
    query = select(ResearchEntry).where(ResearchEntry.entry_id == run_id)
    if writing:
        query = query.with_for_update()
    run = session.scalar(query)
    if not run or not (run.context_json.get("sector_run") or run.context_json.get("research_run")):
        raise HTTPException(404, "事件检查不存在")
    if writing and run.status not in {"queued", "running"}:
        raise HTTPException(409, "本轮检查已经结束")
    return run


class SourceCapture(BaseModel):
    operation: Literal["search", "fetch", "error", "review"]
    query: str = ""
    sources: list[dict] = Field(default_factory=list)
    coverage: list[str] = Field(default_factory=list)
    review: dict | None = None


@router.post("/research/runs/{run_id}/sector-evidence")
def save_evidence(run_id: str, request: SourceCapture, session: Session = Depends(get_db_session)):
    run = current_run(session, run_id, writing=True)
    evidence = {**request.model_dump(), "recorded_at": datetime.now(UTC).isoformat()}
    if request.operation == "fetch":
        from watchlist_app.services.market_evidence import capture_source, source_reference
        originals = [capture_source(source) for source in request.sources]
        evidence["sources"] = [source_reference(source) for source in originals]
    context = run.context_json
    # Live supplementary acquisition advances the run's knowledge horizon. The
    # already selected numerical/dossier snapshots keep their original timestamps.
    if request.operation == "fetch" and originals:
        context = {**context, "input_snapshot_cutoff": context.get("input_snapshot_cutoff", context["cutoff"]),
                   "cutoff": datetime.now(UTC).isoformat()}
    run.context_json = {**context, "web_evidence": [*context.get("web_evidence", []), evidence]}
    session.commit()
    return {**evidence, "sources": originals} if request.operation == "fetch" else evidence


class MarketSearch(BaseModel):
    instrument_id: str | None = None
    query: str = ""
    entities: list[str] = Field(default_factory=list)
    published_after: AwareDatetime | None = None
    observed_after: AwareDatetime | None = None
    received_after: AwareDatetime | None = None
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


def market_run(session, run_id):
    run = session.scalar(select(ResearchEntry).where(ResearchEntry.entry_id == run_id).with_for_update())
    if run is None or run.kind != "analysis":
        raise HTTPException(404, "研究任务不存在")
    if run.status not in {"queued", "running"}:
        raise HTTPException(409, "本轮研究已经结束")
    if run.context_json.get("risk_run"):
        raise HTTPException(422, "风控研判使用已绑定的风险快照")
    return run


@router.post("/research/runs/{run_id}/market-search")
def search_market_documents(run_id: str, request: MarketSearch, session: Session = Depends(get_db_session)):
    from watchlist_app.services.market_evidence import text_store
    run = market_run(session, run_id)
    context = run.context_json
    scope = set(context.get("instrument_ids", [])) | {i["instrument_id"] for i in context.get("catalogue", [])}
    if request.instrument_id and request.instrument_id not in scope:
        raise HTTPException(422, "检索标的不在本轮研究目录")
    cutoff = datetime.fromisoformat(context["cutoff"])
    result = text_store().search(request.query, entities=request.entities or None,
        published_after=request.published_after, observed_after=request.observed_after, received_after=request.received_after,
        as_of=cutoff, limit=request.limit, offset=request.offset)
    query = {**request.model_dump(mode="json"), "total": result["total"], "cutoff": context["cutoff"]}
    run.context_json = {**context, "market_queries": [*context.get("market_queries", []), query],
                        "market_coverage": result["coverage"]}
    session.commit()
    return result


@router.get("/research/runs/{run_id}/market-source")
def read_market_document(run_id: str, document_id: str, version_id: str | None = None,
                         session: Session = Depends(get_db_session)):
    from watchlist_app.services.market_evidence import original_source, source_reference, text_store
    run = market_run(session, run_id)
    context = run.context_json
    document = text_store().read(document_id, version_id=version_id,
                                 as_of=datetime.fromisoformat(context["cutoff"]))
    if document is None:
        raise HTTPException(404, "原文版本在本轮截止时间不可用")
    source = original_source(document)
    references = {row["source_id"]: row for row in context.get("market_text_sources", [])}
    references[source["source_id"]] = source_reference(source)
    run.context_json = {**context, "market_text_sources": list(references.values())}
    session.commit()
    return source


class NumericResearchInput(BaseModel):
    action: Literal["catalogue", "series", "compare", "price_risk"] = "catalogue"
    instrument_id: str | None = None
    dataset: str | None = None
    series_ids: list[str] = Field(default_factory=list, max_length=6)
    field: str | None = None
    start: date | None = None
    end: date | None = None


class FinancialResearchInput(BaseModel):
    instrument_id: str
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    statement_type: Literal["income", "balance_sheet", "cash_flow"] | None = None
    fiscal_period: Literal["FY", "Q1", "Q2", "Q3", "Q4"] | None = None
    period_end: date | None = None
    view: Literal["statements", "facts"] = "statements"


@router.post("/research/runs/{run_id}/financials")
def financial_research(run_id: str, request: FinancialResearchInput, session: Session = Depends(get_db_session)):
    from watchlist_app.services.research_financials import financial_page
    run = market_run(session, run_id)
    try:
        service.bind_research_instruments(session, run, [request.instrument_id])
        context = run.context_json
        asset = next(row for row in context["instrument_inputs"] if row["instrument_id"] == request.instrument_id)
        # A subsequent web fetch does not advance a previously bound numerical snapshot.
        cutoff = asset.get("snapshot_cutoff") or context.get("input_snapshot_cutoff") or context["cutoff"]
        filters = request.model_dump(mode="json", exclude={"instrument_id"})
        prior = next((row for row in context.get("financial_sources", [])
                      if row["instrument_id"] == request.instrument_id and row["request"] == filters
                      and datetime.fromisoformat(row["run_cutoff"]) == datetime.fromisoformat(cutoff)), None)
        if prior is not None:
            return prior
        result = {**financial_page(asset, as_of=cutoff, **filters), "source_run_id": run_id}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    run.context_json = {**context, "financial_sources": [*context.get("financial_sources", []), result]}
    session.commit()
    return result


@router.post("/research/runs/{run_id}/numeric")
def numeric_research(run_id: str, request: NumericResearchInput, session: Session = Depends(get_db_session)):
    from watchlist_app.services.research_metrics import research_numeric_data, instrument_price_risk
    run = market_run(session, run_id)
    try:
        if request.action == "price_risk":
            from watchlist_app.services.sector_research import bind_research_instruments
            if not request.instrument_id:
                raise ValueError("请选择需要观察风险的标的")
            bind_research_instruments(session, run, [request.instrument_id])
            result = instrument_price_risk(session, request.instrument_id, as_of=run.context_json["cutoff"])
        else:
            result = research_numeric_data(action=request.action, as_of=run.context_json["cutoff"],
                dataset=request.dataset, series_ids=request.series_ids, field=request.field,
                start=request.start, end=request.end)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    run.context_json = {**run.context_json, "computed_metrics": [*run.context_json.get("computed_metrics", []), result]}
    session.commit()
    return result


@router.post("/research/runs/{run_id}/sector-draft")
def submit_draft(run_id: str, request: service.ReviewResult, session: Session = Depends(get_db_session)):
    run = current_run(session, run_id, writing=True)
    try:
        service.validate_result(session, run, request)
        # Enforce the current automatic submission contract without rewriting
        # historical runs or requiring a daily receipt from a conversation.
        if run.context_json.get("sector_run"):
            missing = [review.instrument_id for review in request.reviews if review.reflection is None]
            if missing:
                raise ValueError("自动研究每个标的都必须提交 reflection 复核记录；以下标的缺少："
                    + "、".join(missing)
                    + "。请补充 status=reviewed 或 insufficient_evidence，并说明实际复核结果或证据缺口后重新提交。")
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    run.context_json = {**run.context_json, "submitted_draft": service.draft_payload(request)}
    session.commit()
    return {"status": "pending_fact_review", "run_id": run_id,
            "instrument_ids": run.context_json["instrument_ids"],
            "message": "草稿已验证并保存，仍待独立复核；尚未发布研究结论或事件。"}


@router.get("/research/runs/{run_id}/sector-company/{instrument_id}/{symbol}")
def sector_company(run_id: str, instrument_id: str, symbol: str, session: Session = Depends(get_db_session)):
    run = current_run(session, run_id)
    company = run.context_json.get("sector_company_data", {}).get(instrument_id, {}).get(symbol.upper())
    if company is None:
        raise HTTPException(404, "本次成分快照没有该股票的公司资料")
    return {"source_id": f"fmp:{run_id}:{instrument_id}:{symbol.upper()}", "company": company}
