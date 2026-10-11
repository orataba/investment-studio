import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only
from types import SimpleNamespace

from watchlist_app.db.session import get_db_session
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.services import risk_officer as service
from watchlist_app.services.research_runner import harness_available
from watchlist_app.services.risk_read_projection import delivered_page_keys, missing_required_reads, project_risk_read

router = APIRouter()


class RiskReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    section: Literal["instrument_overviews", "overview", "cases", "research_context", "comparisons", "sample_dates"]
    instrument_id: str | None = None
    offset: int = Field(default=0, ge=0)
    comparison_source_id: str | None = None
    page_offset: int | None = Field(default=None, ge=0)
    path: list[str | int] | None = None


def _running_risk_run(session, run_id):
    # Lock only the small projection. A tool read must not decode/rewrite the
    # original snapshots merely to record that a bounded page was delivered.
    row = session.scalar(select(ResearchEntry).where(ResearchEntry.entry_id == run_id)
        .options(load_only(ResearchEntry.entry_id, ResearchEntry.topic_id, ResearchEntry.team_id,
            ResearchEntry.kind, ResearchEntry.status, ResearchEntry.read_context_json))
        .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "风险研判记录不存在。")
    run = SimpleNamespace(**{key: getattr(row, key) for key in ("entry_id", "topic_id", "team_id", "kind", "status")},
        context_json=dict(row.read_context_json))
    if not run.context_json.get("risk_run"):
        raise HTTPException(404, "风险研判记录不存在。")
    from watchlist_app.services.research_access import require_entry_access, run_attempt
    require_entry_access(session, run, tool_write=True)
    if run.status != "running" or not run.context_json.get("risk_inputs"):
        raise HTTPException(409, "本轮风险研判尚未准备或已经结束。")
    from watchlist_app.services.research_risk_state import risk_read_state
    run.read_state = risk_read_state(session, run_id, run_attempt(run))
    run.context_json["risk_delivered_pages"] = run.read_state.delivered_pages
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
        run.read_state.delivered_pages = delivered
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
    run.read_state.submitted_review = request.model_dump(mode="json")
    session.commit()
    return {"run_id": run_id, "status": "accepted"}


class RiskEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    snapshot: Literal["current", "previous"] = "current"
    path: list[str | int] = Field(default_factory=list)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


@router.post("/research/runs/{run_id}/risk-evidence")
def read_risk_evidence(run_id: str, request: RiskEvidenceInput, session: Session = Depends(get_db_session)):
    from watchlist_app.services.research_run_context import load_run_fields
    from watchlist_app.services.research_read_projection import read_page
    field = "risk_inputs" if request.snapshot == "current" else "prior_inputs"
    run = load_run_fields(session, run_id, {"risk_run", "cutoff", field})
    if not run.context_json.get("risk_run"):
        raise HTTPException(404, "风险研判记录不存在。")
    value = run.context_json.get(field)
    if value is None:
        raise HTTPException(404, "本轮没有该前后快照。")
    def resolve_bound_source(item, path):
        # Only server-owned source containers carry locators. Arbitrary output
        # JSON (especially quant data) is never interpreted as a cross-run link.
        source_position = (len(path) >= 2 and path[-2] in {"sources", "source_views", "input_sources"}
            and type(path[-1]) is int and "data" not in path)
        if source_position and isinstance(item, dict) and item.get("source_type") == "computed_metric" and item.get("retained_reference"):
            from watchlist_app.services.research_run_evidence import resolve_reference
            return resolve_reference(item, session=session, bound_source=True)
        return item
    try:
        return read_page(value, {"run_id": run_id, "snapshot": request.snapshot,
            "cutoff": run.context_json["cutoff"]}, path=request.path, offset=request.offset, limit=request.limit,
            resolve=resolve_bound_source)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


class PortfolioRiskReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    section: Literal["portfolio_metrics", "targets", "targets_by_taxonomy", "comparisons", "derivatives", "concentration", "tail_risk"]
    holding_id: str | None = None
    offset: int = Field(default=0, ge=0)
    source_offset: int | None = Field(default=None, ge=0)
    path: list[str | int] | None = None
    limit: int = Field(default=20, ge=1, le=100)


@router.post("/research/runs/{run_id}/risk-portfolio")
def read_portfolio_risk(run_id: str, request: PortfolioRiskReadInput, session: Session = Depends(get_db_session)):
    from watchlist_app.services.research_run_context import load_run_fields
    from watchlist_app.services.risk_portfolio_projection import project_portfolio_risk
    run = load_run_fields(session, run_id, {"risk_run", "cutoff", "risk_inputs"}, summary_only=True)
    try:
        return project_portfolio_risk(run.context_json, **request.model_dump())
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


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
def start_review(request: ScopeInput, session: Session = Depends(get_db_session)):
    if not harness_available():
        raise HTTPException(503, "风险研判尚未配置运行环境。")
    try:
        scope = service.normalize_scope(**request.model_dump())
        run, created = service.begin_run(session, **scope)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if created or run.status == "queued":
        from studio_identity import current_principal
        from watchlist_app.services.research_execution import enqueue_run
        from watchlist_app.services.research_access import run_attempt
        attempt = run_attempt(run)
        enqueue_run(current_principal(), run.entry_id, attempt=attempt)
    return {"run_id": run.entry_id, "status": run.status}
