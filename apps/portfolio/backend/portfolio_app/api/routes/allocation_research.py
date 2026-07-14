from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from portfolio_app.api.contracts import (
    AllocationResearchArtifactContentResponse,
    PolicyReplayBenchmarkComparisonResponse,
    AllocationResearchRunCreateRequest,
    AllocationResearchRunRecord,
    AllocationResearchSettingsRecord,
    AllocationResearchSettingsUpdateRequest,
    AllocationResearchWorkbenchResponse,
)
from portfolio_app.core.operating_profiles import (
    OperatingProfileCapabilityError,
    require_allocation_research_profile,
)
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.portfolio_store import get_portfolio
from portfolio_app.services.allocation_research_workbench import (
    AllocationResearchRunNotFoundError,
    get_allocation_research_policy_replay_benchmark_comparison,
    get_allocation_research_run,
    get_allocation_research_workbench,
    read_allocation_research_artifact_content,
    run_portfolio_allocation_research,
    update_allocation_research_settings,
)


router = APIRouter()


def _require_allocation_research_portfolio(portfolio_id: str) -> dict[str, object]:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    try:
        require_allocation_research_profile(portfolio.get("operating_profile"))
    except OperatingProfileCapabilityError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "operating_profile_capability_not_applicable",
                "capability": "allocation_research",
                "operating_profile": error.operating_profile,
            },
        ) from error
    return portfolio


@router.get("/{portfolio_id}/allocation-research/workbench", response_model=AllocationResearchWorkbenchResponse)
def get_portfolio_allocation_research_workbench(
    portfolio_id: str,
    selected_run_id: str | None = Query(default=None),
) -> AllocationResearchWorkbenchResponse:
    _require_allocation_research_portfolio(portfolio_id)
    try:
        workbench = get_allocation_research_workbench(portfolio_id, selected_run_id=selected_run_id)
    except AllocationResearchRunNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if workbench is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return AllocationResearchWorkbenchResponse.model_validate(workbench)


@router.put("/{portfolio_id}/allocation-research/settings", response_model=AllocationResearchSettingsRecord)
def update_portfolio_allocation_research_settings(
    portfolio_id: str,
    payload: AllocationResearchSettingsUpdateRequest,
) -> AllocationResearchSettingsRecord:
    _require_allocation_research_portfolio(portfolio_id)
    try:
        settings = update_allocation_research_settings(
            portfolio_id,
            planning_taxonomy_id=payload.planning_taxonomy_id,
            comparator_taxonomy_node_id=payload.comparator_taxonomy_node_id,
            as_of_mode=payload.as_of_mode,
            as_of_date=payload.as_of_date,
            lookback_days=payload.lookback_days,
            calculation_frequency=payload.calculation_frequency,
            missing_return_policy=payload.missing_return_policy,
            covariance_model_id=payload.covariance_model_id,
            contribution_mode=payload.contribution_mode,
            target_dimension=payload.target_dimension,
            capital_mode=payload.capital_mode,
            gross_exposure=payload.gross_exposure,
            target_volatility=payload.target_volatility,
            max_gross_exposure=payload.max_gross_exposure,
            frozen_taxonomy_node_ids=payload.frozen_taxonomy_node_ids,
            top_sleeve_weight_bounds=(
                [item.model_dump() for item in payload.top_sleeve_weight_bounds]
                if payload.top_sleeve_weight_bounds is not None
                else None
            ),
            policy_replay_rebalance_frequency=payload.policy_replay_rebalance_frequency,
            policy_replay_benchmark_instrument_id=payload.policy_replay_benchmark_instrument_id,
            notes=payload.notes,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if settings is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return AllocationResearchSettingsRecord.model_validate(settings)


@router.post("/{portfolio_id}/allocation-research/runs", response_model=AllocationResearchRunRecord)
def create_portfolio_allocation_research_run(
    portfolio_id: str,
    payload: AllocationResearchRunCreateRequest,
) -> AllocationResearchRunRecord:
    _require_allocation_research_portfolio(portfolio_id)
    try:
        run = run_portfolio_allocation_research(
            portfolio_id,
            requested_by=payload.requested_by,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if run is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return AllocationResearchRunRecord.model_validate(run)


@router.get(
    "/{portfolio_id}/allocation-research/runs/{allocation_research_run_id}",
    response_model=AllocationResearchRunRecord,
)
def get_portfolio_allocation_research_run(
    portfolio_id: str,
    allocation_research_run_id: str,
) -> AllocationResearchRunRecord:
    _require_allocation_research_portfolio(portfolio_id)
    run = get_allocation_research_run(
        portfolio_id,
        allocation_research_run_id=allocation_research_run_id,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Allocation Research run not found")
    return AllocationResearchRunRecord.model_validate(run)


@router.get(
    "/{portfolio_id}/allocation-research/runs/{allocation_research_run_id}"
    "/policy-replay/benchmark-comparison",
    response_model=PolicyReplayBenchmarkComparisonResponse,
)
def get_portfolio_allocation_research_run_benchmark_comparison(
    portfolio_id: str,
    allocation_research_run_id: str,
    benchmark_instrument_id: str = Query(..., min_length=1),
) -> PolicyReplayBenchmarkComparisonResponse:
    _require_allocation_research_portfolio(portfolio_id)
    try:
        payload = get_allocation_research_policy_replay_benchmark_comparison(
            portfolio_id,
            allocation_research_run_id=allocation_research_run_id,
            benchmark_instrument_id=benchmark_instrument_id,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if payload is None:
        raise HTTPException(status_code=404, detail="Allocation Research run not found")
    return PolicyReplayBenchmarkComparisonResponse.model_validate(payload)


@router.get("/{portfolio_id}/allocation-research/artifacts/content", response_model=AllocationResearchArtifactContentResponse)
def get_portfolio_allocation_research_artifact_content(
    portfolio_id: str,
    path: str = Query(..., min_length=1),
) -> AllocationResearchArtifactContentResponse:
    _require_allocation_research_portfolio(portfolio_id)
    try:
        payload = read_allocation_research_artifact_content(portfolio_id, path=path)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return AllocationResearchArtifactContentResponse.model_validate(payload)
