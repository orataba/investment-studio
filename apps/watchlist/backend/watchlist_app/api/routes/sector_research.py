from datetime import datetime, UTC
from typing import Literal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from watchlist_app.db.session import get_db_session
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.services import sector_research as service
from watchlist_app.services.research_runner import harness_available, run_analysis
from watchlist_app.services.sector_estimates import read_estimate_evidence

router = APIRouter()


@router.get("/sector-research/estimates")
def sector_estimates(instrument_id: str, session: Session = Depends(get_db_session)):
    return read_estimate_evidence(session, instrument_id)


@router.get("/sector-research")
def sector_research(instrument_id: str | None = None, watchlist_id: str | None = None, session: Session = Depends(get_db_session)):
    ids = service.scoped_ids(session, instrument_id, watchlist_id)
    reviews = service.latest_reviews(session)
    completed = service.latest_reviews(session, completed_only=True)
    sectors = [{"instrument_id": iid, "ticker": iid.upper(), "sector_name": service.instrument_label(session, iid),
                "latest_review": reviews.get(iid), "last_completed_review": completed.get(iid)} for iid in ids]
    available = bool(ids)
    message = None if available else "当前范围没有可分析的已登记标的。"
    return {"available": available, "sectors": sectors, "events": service.events_for_instruments(session, ids), "message": message}


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
        background.add_task(run_analysis, run.entry_id)
    return {"run_id": run.entry_id, "status": run.status}


def current_run(session, run_id, *, writing=False):
    query = select(ResearchEntry).where(ResearchEntry.entry_id == run_id)
    if writing:
        query = query.with_for_update()
    run = session.scalar(query)
    if not run or not run.context_json.get("sector_run"):
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
    run.context_json = {**run.context_json, "web_evidence": [*run.context_json.get("web_evidence", []), evidence]}
    session.commit()
    return evidence


@router.post("/research/runs/{run_id}/sector-draft")
def submit_draft(run_id: str, request: service.ReviewResult, session: Session = Depends(get_db_session)):
    run = current_run(session, run_id, writing=True)
    try:
        service.validate_result(session, run, request)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    run.context_json = {**run.context_json, "submitted_draft": request.model_dump(mode="json")}
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
