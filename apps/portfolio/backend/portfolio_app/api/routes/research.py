from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from portfolio_app.api.contracts import (
    ResearchArtifactContentResponse,
    ResearchRunCreateRequest,
    ResearchRunRecord,
    ResearchSettingsRecord,
    ResearchSettingsUpdateRequest,
    ResearchWorkbenchResponse,
)
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.portfolio_store import get_portfolio
from portfolio_app.services.research import (
    get_research_workbench,
    read_research_artifact_content,
    run_portfolio_research,
    update_research_settings,
)


router = APIRouter()


@router.get("/{portfolio_id}/research/workbench", response_model=ResearchWorkbenchResponse)
def get_portfolio_research_workbench(
    portfolio_id: str,
    selected_run_id: str | None = Query(default=None),
) -> ResearchWorkbenchResponse:
    try:
        workbench = get_research_workbench(portfolio_id, selected_run_id=selected_run_id)
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if workbench is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return ResearchWorkbenchResponse.model_validate(workbench)


@router.put("/{portfolio_id}/research/settings", response_model=ResearchSettingsRecord)
def update_portfolio_research_settings(
    portfolio_id: str,
    payload: ResearchSettingsUpdateRequest,
) -> ResearchSettingsRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    try:
        settings = update_research_settings(
            portfolio_id,
            planning_taxonomy_id=payload.planning_taxonomy_id,
            comparator_taxonomy_node_id=payload.comparator_taxonomy_node_id,
            as_of_date=payload.as_of_date,
            start_date=payload.start_date,
            lookback_days=payload.lookback_days,
            benchmark_mode=payload.benchmark_mode,
            run_template=payload.run_template,
            target_set_mode=payload.target_set_mode,
            target_dimension=payload.target_dimension,
            rebalance_frequency=payload.rebalance_frequency,
            notes=payload.notes,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if settings is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return ResearchSettingsRecord.model_validate(settings)


@router.post("/{portfolio_id}/research/runs", response_model=ResearchRunRecord)
def create_portfolio_research_run(
    portfolio_id: str,
    payload: ResearchRunCreateRequest,
) -> ResearchRunRecord:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    try:
        run = run_portfolio_research(
            portfolio_id,
            requested_by=payload.requested_by,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if run is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return ResearchRunRecord.model_validate(run)


@router.get("/{portfolio_id}/research/artifacts/content", response_model=ResearchArtifactContentResponse)
def get_portfolio_research_artifact_content(
    portfolio_id: str,
    path: str = Query(..., min_length=1),
) -> ResearchArtifactContentResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    try:
        payload = read_research_artifact_content(portfolio_id, path=path)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return ResearchArtifactContentResponse.model_validate(payload)
