from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from app.api.contracts import (
    AssetPriceChartResponse,
    PositionListResponse,
    PositionListSummary,
    PositionLotListResponse,
    PositionLotListSummary,
    PositionLotRecord,
    PositionRecord,
)
from app.services.asset_charts import build_asset_price_chart, normalize_chart_range_key
from app.services.instrument_registry import InstrumentRegistryError
from app.services.ledger import (
    build_portfolio_positions,
    build_position_lots,
    summarize_position_lots,
    summarize_positions,
)
from app.services.portfolio_store import get_portfolio, list_accounts, list_transactions


router = APIRouter()


@router.get("/{portfolio_id}/positions", response_model=PositionListResponse)
def list_portfolio_positions(
    portfolio_id: str,
    as_of_date: date | None = None,
) -> PositionListResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    accounts = list_accounts(portfolio_id)
    transactions = list_transactions(portfolio_id, end_date=as_of_date)
    try:
        positions = build_portfolio_positions(
            portfolio_id,
            accounts,
            transactions,
            as_of_date=as_of_date,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return PositionListResponse(
        portfolio_id=portfolio_id,
        summary=PositionListSummary.model_validate(summarize_positions(positions)),
        positions=[PositionRecord.model_validate(item) for item in positions],
    )


@router.get("/{portfolio_id}/position-lots", response_model=PositionLotListResponse)
def list_portfolio_position_lots(
    portfolio_id: str,
    account_id: str | None = None,
    asset_id: str | None = None,
    status: str | None = None,
    as_of_date: date | None = None,
) -> PositionLotListResponse:
    if get_portfolio(portfolio_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    accounts = list_accounts(portfolio_id)
    transactions = list_transactions(portfolio_id, end_date=as_of_date)
    try:
        position_lots = build_position_lots(
            portfolio_id,
            accounts,
            transactions,
            account_id=account_id,
            asset_id=asset_id,
            status=status,
            as_of_date=as_of_date,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return PositionLotListResponse(
        portfolio_id=portfolio_id,
        summary=PositionLotListSummary.model_validate(summarize_position_lots(position_lots)),
        position_lots=[PositionLotRecord.model_validate(item) for item in position_lots],
    )


@router.get("/{portfolio_id}/assets/{asset_id}/price-chart", response_model=AssetPriceChartResponse)
def get_portfolio_asset_price_chart(
    portfolio_id: str,
    asset_id: str,
    as_of_date: date | None = None,
    range: str | None = None,
) -> AssetPriceChartResponse:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    resolved_as_of_date = (
        as_of_date
        or (
            date.fromisoformat(str(portfolio.get("as_of_date")))
            if portfolio.get("as_of_date")
            else None
        )
        or date.today()
    )
    try:
        chart = build_asset_price_chart(
            asset_id,
            as_of_date=resolved_as_of_date,
            range_key=normalize_chart_range_key(range),
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if chart is None:
        raise HTTPException(status_code=404, detail="Instrument not found in shared registry.")

    return AssetPriceChartResponse.model_validate(
        {
            "portfolio_id": portfolio_id,
            **chart,
        }
    )
