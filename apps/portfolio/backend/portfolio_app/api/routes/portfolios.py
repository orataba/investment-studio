from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

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
    update_portfolio_base_currency,
)
from portfolio_app.services.portfolio_access import visible_ids, require_access, get_preference, save_preference, actor
from portfolio_app.services.risk_model import get_portfolio_risk_policy, update_portfolio_risk_policy


router = APIRouter()


class PortfolioCreateRequest(BaseModel):
    name: str | None = None
    base_currency: SupportedCurrency
    inception_date: date

    @field_validator("inception_date")
    @classmethod
    def validate_inception_date(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("inception_date must not be in the future.")
        return value


class PortfolioReorderRequest(BaseModel):
    portfolio_ids: list[str] = Field(default_factory=list)


class PortfolioSettingsUpdateRequest(BaseModel):
    base_currency: SupportedCurrency


@router.get("/session")
def current_session():
    principal = actor()
    return {"user_id": principal.user_id, "display_name": principal.display_name, "session_id": principal.session_id, "can_create": principal.local_unrestricted or (principal.kind == "user" and principal.team_role != "reader"), "can_write_team_research": principal.local_unrestricted or (principal.kind == "user" and principal.team_role in {"admin", "member"}), "is_team_owner": principal.is_team_owner, "local_unrestricted": principal.local_unrestricted}


@router.get("")
def list_portfolio_records() -> list[dict[str, object]]:
    records = list_portfolios(portfolio_ids=visible_ids())
    order = (get_preference("portfolio-order") or {}).get("portfolio_ids", [])
    order_index = {value: index for index, value in enumerate(order)}
    records.sort(key=lambda row: order_index.get(row["portfolio_id"], len(order)))
    return [{**record, "sort_order": index, "access": require_access(str(record["portfolio_id"]))} for index, record in enumerate(records)]


@router.post("")
def create_portfolio_record(payload: PortfolioCreateRequest) -> dict[str, object]:
    record = create_portfolio(
        payload.name,
        base_currency=payload.base_currency,
        inception_date=payload.inception_date,
    )
    return {**record, "sort_order": 0, "access": require_access(str(record["portfolio_id"]))}


@router.post("/reorder")
def reorder_portfolio_records(payload: PortfolioReorderRequest) -> list[dict[str, object]]:
    visible = set(visible_ids())
    if len(set(payload.portfolio_ids)) != len(payload.portfolio_ids) or not set(payload.portfolio_ids) <= visible:
        raise HTTPException(422, "排序只能包含有权访问的组合且不能重复")
    save_preference("portfolio-order", {"portfolio_ids": payload.portfolio_ids})
    return list_portfolio_records()


@router.post("/{portfolio_id}/copy")
def copy_portfolio_record(portfolio_id: str) -> dict[str, object]:
    try:
        record = copy_portfolio(portfolio_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {**record, "sort_order": 0, "access": require_access(str(record["portfolio_id"]))}


@router.patch("/{portfolio_id}")
def update_portfolio_settings(
    portfolio_id: str,
    payload: PortfolioSettingsUpdateRequest,
) -> dict[str, object]:
    try:
        record = update_portfolio_base_currency(
            portfolio_id,
            base_currency=payload.base_currency,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
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
