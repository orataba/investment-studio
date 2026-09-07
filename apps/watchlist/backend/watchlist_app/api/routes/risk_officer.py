from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from watchlist_app.db.session import get_db_session
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.services import risk_officer as service
from watchlist_app.services.research_runner import harness_available, run_analysis

router = APIRouter()


@router.post("/research/runs/{run_id}/risk-draft")
def submit_review(run_id: str, request: service.RiskReview, session: Session = Depends(get_db_session)):
    run = session.get(ResearchEntry, run_id, with_for_update=True)
    if not run or not run.context_json.get("risk_run"):
        raise HTTPException(404, "风险研判记录不存在。")
    if run.status != "running" or not run.context_json.get("risk_inputs"):
        raise HTTPException(409, "本轮风险研判尚未准备或已经结束。")
    try:
        service.validate_result(run, request)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    run.context_json = {**run.context_json, "submitted_risk_review": request.model_dump(mode="json")}
    session.commit()
    return {"run_id": run_id, "status": "accepted"}


class ScopeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    watchlist_id: str | None = None
    instrument_id: str | None = None
    portfolio_id: str | None = None


@router.get("/risk/review")
def risk_review(watchlist_id: str | None = None, instrument_id: str | None = None, portfolio_id: str | None = None, session: Session = Depends(get_db_session)):
    try:
        return service.review_workspace(session, watchlist_id=watchlist_id, instrument_id=instrument_id, portfolio_id=portfolio_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/risk/review/runs", status_code=202)
def start_review(request: ScopeInput, background: BackgroundTasks, session: Session = Depends(get_db_session)):
    if not harness_available():
        raise HTTPException(503, "风险研判尚未配置运行环境。")
    try:
        scope = service.normalize_scope(**request.model_dump())
        run, created = service.begin_run(session, **scope)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if created:
        background.add_task(run_analysis, run.entry_id)
    return {"run_id": run.entry_id, "status": run.status}
