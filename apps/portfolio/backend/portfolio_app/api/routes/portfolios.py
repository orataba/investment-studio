from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from portfolio_app.api.contracts import (
    PortfolioRiskPolicyRecord,
    PortfolioRiskPolicyUpdateRequest,
    SupportedCurrency,
)
from portfolio_app.services.portfolio_store import (
    copy_portfolio,
    create_portfolio,
    delete_portfolio,
    list_portfolios,
    reorder_portfolios,
)
from portfolio_app.services.risk_model import get_portfolio_risk_policy, update_portfolio_risk_policy


router = APIRouter()


class PortfolioCreateRequest(BaseModel):
    name: str | None = None
    base_currency: SupportedCurrency


class PortfolioReorderRequest(BaseModel):
    portfolio_ids: list[str] = Field(default_factory=list)


@router.get("")
def list_portfolio_records() -> list[dict[str, object]]:
    return list_portfolios()


@router.post("")
def create_portfolio_record(payload: PortfolioCreateRequest) -> dict[str, object]:
    return create_portfolio(payload.name, base_currency=payload.base_currency)


@router.post("/reorder")
def reorder_portfolio_records(payload: PortfolioReorderRequest) -> list[dict[str, object]]:
    return reorder_portfolios(payload.portfolio_ids)


@router.post("/{portfolio_id}/copy")
def copy_portfolio_record(portfolio_id: str) -> dict[str, object]:
    try:
        record = copy_portfolio(portfolio_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return record


@router.get("/{portfolio_id}/risk-policy", response_model=PortfolioRiskPolicyRecord)
def get_portfolio_production_risk_policy(portfolio_id: str) -> PortfolioRiskPolicyRecord:
    policy = get_portfolio_risk_policy(portfolio_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return PortfolioRiskPolicyRecord.model_validate(policy)


@router.put("/{portfolio_id}/risk-policy", response_model=PortfolioRiskPolicyRecord)
def update_portfolio_production_risk_policy(
    portfolio_id: str,
    payload: PortfolioRiskPolicyUpdateRequest,
) -> PortfolioRiskPolicyRecord:
    try:
        policy = update_portfolio_risk_policy(portfolio_id, payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if policy is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return PortfolioRiskPolicyRecord.model_validate(policy)


@router.delete("/{portfolio_id}")
def delete_portfolio_record(portfolio_id: str) -> dict[str, object]:
    try:
        deleted = delete_portfolio(portfolio_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not deleted:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"portfolio_id": portfolio_id, "deleted": True}
