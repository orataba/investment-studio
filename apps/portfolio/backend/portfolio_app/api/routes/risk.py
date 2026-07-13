from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from portfolio_app.api.contracts import RiskWorkspaceResponse
from portfolio_app.services.fact_currency import PortfolioFactCurrencyError
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.ledger import LedgerDataIntegrityError
from portfolio_app.services.risk_workspace import (
    ALL_INSTRUMENTS_SCOPE,
    RiskWorkspaceNotFoundError,
    RiskWorkspaceRequestError,
    build_risk_workspace,
)


router = APIRouter()


@router.get("/{portfolio_id}/risk/workspace", response_model=RiskWorkspaceResponse)
def get_risk_workspace(
    portfolio_id: str,
    as_of_date: date = Query(...),
    rolling_lookback_days: int = Query(default=30),
    matrix_lookback_days: int = Query(default=30),
    matrix_scope_node_id: str = Query(default=ALL_INSTRUMENTS_SCOPE),
    matrix_as_of_date: date | None = Query(default=None),
    benchmark_instrument_id: str | None = Query(default=None),
) -> RiskWorkspaceResponse:
    try:
        workspace = build_risk_workspace(
            portfolio_id,
            as_of_date=as_of_date,
            rolling_lookback_days=rolling_lookback_days,
            matrix_lookback_days=matrix_lookback_days,
            matrix_scope_node_id=matrix_scope_node_id,
            matrix_as_of_date=matrix_as_of_date,
            benchmark_instrument_id=benchmark_instrument_id,
        )
    except RiskWorkspaceNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RiskWorkspaceRequestError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (PortfolioFactCurrencyError, LedgerDataIntegrityError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return RiskWorkspaceResponse.model_validate(workspace)
