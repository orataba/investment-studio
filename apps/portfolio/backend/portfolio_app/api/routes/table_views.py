from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from portfolio_app.services.portfolio_store import get_portfolio
from portfolio_app.services.table_views import (
    get_portfolio_table_view_store,
    upsert_portfolio_table_view_store,
)


router = APIRouter()


class PortfolioTableViewStoreUpdateRequest(BaseModel):
    store: dict[str, Any] = Field(default_factory=dict)


@router.get("/{portfolio_id}/table-views/{view_scope}")
def get_table_view_store(portfolio_id: str, view_scope: str) -> dict[str, object]:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        record = get_portfolio_table_view_store(portfolio_id, view_scope)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if record is None:
        return {
            "portfolio_id": portfolio_id,
            "view_scope": view_scope,
            "store": None,
            "created_at": None,
            "updated_at": None,
        }
    return record


@router.put("/{portfolio_id}/table-views/{view_scope}")
def put_table_view_store(
    portfolio_id: str,
    view_scope: str,
    payload: PortfolioTableViewStoreUpdateRequest,
) -> dict[str, object]:
    try:
        return upsert_portfolio_table_view_store(portfolio_id, view_scope, payload.store)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
