from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from portfolio_app.services.portfolio_store import get_portfolio
from portfolio_app.services.portfolio_access import get_preference, save_preference, now
from portfolio_app.services.table_views import normalize_table_view_scope


router = APIRouter()


class PortfolioTableViewStoreUpdateRequest(BaseModel):
    store: dict[str, Any] = Field(default_factory=dict)


@router.get("/{portfolio_id}/table-views/{view_scope}")
def get_table_view_store(portfolio_id: str, view_scope: str) -> dict[str, object]:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        normalize_table_view_scope(view_scope)
        record = get_preference(f"table:{portfolio_id}:{view_scope}")
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
        normalize_table_view_scope(view_scope)
        key = f"table:{portfolio_id}:{view_scope}"
        previous = get_preference(key) or {}
        timestamp = now()
        return save_preference(key, {"portfolio_id": portfolio_id, "view_scope": view_scope, "store": payload.store, "created_at": previous.get("created_at") or timestamp, "updated_at": timestamp})
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
