from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from portfolio_app.api.contracts import (
    CalculationRunStatusResponse,
    PortfolioDailyCalculationAccepted,
    PortfolioDailyCalculationRequest,
)
from portfolio_app.calculations.numeric import CalculationNumericError
from portfolio_app.calculations.portfolio_daily.capture_common import (
    ManifestCaptureError,
)
from portfolio_app.calculations.portfolio_daily.commands import (
    PortfolioDailyCommandError,
    enqueue_portfolio_daily_unit_of_work,
    read_portfolio_daily_run,
)
from portfolio_app.core.settings import get_settings
from portfolio_app.db.session import get_engine, get_session_factory
from portfolio_ops_calculation_core import LifecycleRepositoryError


router = APIRouter()


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


@router.post(
    "/portfolio-daily",
    response_model=PortfolioDailyCalculationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_portfolio_daily_calculation(
    payload: PortfolioDailyCalculationRequest,
) -> PortfolioDailyCalculationAccepted:
    try:
        handle = enqueue_portfolio_daily_unit_of_work(
            get_engine(),
            portfolio_id=payload.portfolio_id,
            as_of_date=payload.as_of_date,
            requested_by="portfolio-api",
            settings=get_settings(),
        )
    except PortfolioDailyCommandError as exc:
        status_code = 404 if "portfolio not found" in str(exc) else 409
        raise HTTPException(
            status_code=status_code,
            detail=_detail("portfolio_daily_command_rejected", str(exc)),
        ) from exc
    except (ManifestCaptureError, CalculationNumericError) as exc:
        raise HTTPException(
            status_code=422,
            detail=_detail("portfolio_daily_input_unavailable", str(exc)),
        ) from exc
    except LifecycleRepositoryError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": f"calculation_registry_{exc.code.value}",
                "message": str(exc),
            },
        ) from exc

    return PortfolioDailyCalculationAccepted(
        run_id=str(handle.run_id),
        manifest_id=str(handle.manifest_id) if handle.manifest_id else None,
        portfolio_id=handle.portfolio_id,
        status=handle.status.value,
        requested_as_of=handle.requested_as_of,
        effective_as_of=handle.effective_as_of,
        cutoff_at=handle.cutoff_at,
        captured_generation=handle.captured_generation,
        deduplicated=handle.deduplicated,
        canonical_manifest_hash=handle.canonical_manifest_hash,
    )


@router.get(
    "/runs/{run_id}",
    response_model=CalculationRunStatusResponse,
)
def get_calculation_run(run_id: UUID) -> CalculationRunStatusResponse:
    with get_session_factory()() as session:
        lineage = read_portfolio_daily_run(session, run_id=run_id)
    if lineage is None:
        raise HTTPException(
            status_code=404,
            detail=_detail("calculation_run_not_found", "calculation run not found"),
        )
    return CalculationRunStatusResponse(
        run_id=str(lineage.run_id),
        portfolio_id=lineage.scope.scope_id,
        calculation_kind=lineage.scope.calculation_kind,
        status=lineage.status.value,
        requested_as_of=lineage.requested_as_of,
        effective_as_of=lineage.effective_as_of,
        cutoff_at=lineage.cutoff_at,
        captured_generation=lineage.captured_generation,
        manifest_id=str(lineage.manifest_id) if lineage.manifest_id else None,
        publication_id=(
            str(lineage.publication_id) if lineage.publication_id else None
        ),
        published_output_hash=lineage.published_output_hash,
        published_fencing_token=lineage.published_fencing_token,
        created_at=lineage.created_at,
        started_at=lineage.started_at,
        completed_at=lineage.completed_at,
    )


__all__ = ["router"]
