import json
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from watchlist_app.db.session import get_db_session
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.services import risk_officer as service
from watchlist_app.services.research_runner import harness_available, run_analysis
from watchlist_app.services.risk_read_projection import delivered_page_keys, missing_required_reads, project_risk_read

router = APIRouter()


class RiskReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    section: Literal["instrument_overviews", "overview", "cases", "research_context", "comparisons", "sample_dates"]
    instrument_id: str | None = None
    offset: int = Field(default=0, ge=0)
    comparison_source_id: str | None = None


def _running_risk_run(session, run_id):
    # Access middleware may already have loaded this row. Refresh under the lock
    # so simultaneous page reads merge receipts instead of overwriting them.
    run = session.get(ResearchEntry, run_id, with_for_update=True, populate_existing=True)
    if not run or not run.context_json.get("risk_run"):
        raise HTTPException(404, "风险研判记录不存在。")
    if run.status != "running" or not run.context_json.get("risk_inputs"):
        raise HTTPException(409, "本轮风险研判尚未准备或已经结束。")
    return run


@router.post("/research/runs/{run_id}/risk-read")
def read_risk_page(run_id: str, request: RiskReadInput, session: Session = Depends(get_db_session)):
    run = _running_risk_run(session, run_id)
    try:
        packet = project_risk_read(run.context_json, **request.model_dump())
        # Materialize the complete response before recording delivery. Invalid,
        # oversized or unserializable pages cannot satisfy a required read.
        content = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
        if len(content.encode()) > 48000:
            raise ValueError("风控页超过工具返回上限；本页未记为已交付。")
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error)) from error
    delivered = list(run.context_json.get("risk_delivered_pages", []))
    for key in delivered_page_keys(packet):
        if key not in delivered:
            delivered.append(key)
    if delivered != run.context_json.get("risk_delivered_pages", []):
        run.context_json = {**run.context_json, "risk_delivered_pages": delivered}
        session.commit()
    else:
        session.rollback()  # Release the read lock without rewriting a large immutable snapshot.
    return Response(content=content, media_type="application/json")


@router.post("/research/runs/{run_id}/risk-draft")
def submit_review(run_id: str, request: service.RiskReview, session: Session = Depends(get_db_session)):
    run = _running_risk_run(session, run_id)
    try:
        service.validate_result(run, request)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    missing = missing_required_reads(run.context_json)
    if missing:
        raise HTTPException(422, {"error": "risk_reads_incomplete", "missing_reads": missing,
            "next_action": "按missing_reads的工具与参数补读全部缺页；完成后重新提交完整summary、priorities、limitations。本次未保存结果。"})
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
        from studio_identity import current_principal
        from watchlist_app.services.research_runner import authorize_run
        token = authorize_run(current_principal(), run.entry_id)
        background.add_task(run_analysis, run.entry_id, token, current_principal())
    return {"run_id": run.entry_id, "status": run.status}
