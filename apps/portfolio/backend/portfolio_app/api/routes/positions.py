from __future__ import annotations

from dataclasses import replace
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from portfolio_app.api.contracts import (
    InstrumentPriceChartResponse,
    PortfolioDailyPublishedLotListResponse,
    PortfolioDailyPublishedPositionListResponse,
)
from portfolio_app.api.published_portfolio_daily import read_published_latest
from portfolio_app.calculations.portfolio_daily.published_views import (
    build_lots_response,
    build_positions_response,
)
from portfolio_app.db.session import get_db_session
from portfolio_app.services.instrument_charts import build_instrument_price_chart, normalize_chart_range_key
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.portfolio_store import get_portfolio


router = APIRouter()


@router.get(
    "/{portfolio_id}/positions",
    response_model=PortfolioDailyPublishedPositionListResponse,
)
def list_portfolio_positions(
    portfolio_id: str,
    as_of_date: date | None = None,
    session: Session = Depends(get_db_session),
) -> PortfolioDailyPublishedPositionListResponse:
    publication = read_published_latest(
        session,
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        tables=("holdings",),
    )
    return build_positions_response(publication)


@router.get(
    "/{portfolio_id}/position-lots",
    response_model=PortfolioDailyPublishedLotListResponse,
)
def list_portfolio_position_lots(
    portfolio_id: str,
    account_id: str | None = None,
    instrument_id: str | None = None,
    status: str | None = None,
    as_of_date: date | None = None,
    session: Session = Depends(get_db_session),
) -> PortfolioDailyPublishedLotListResponse:
    if status not in (None, "open"):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unsupported_published_lot_status",
                "supported_statuses": ["open"],
            },
        )
    publication = read_published_latest(
        session,
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        tables=("lots",),
    )
    filtered = tuple(
        row
        for row in publication.lots
        if (account_id is None or row.account_id == account_id)
        and (instrument_id is None or row.instrument_id == instrument_id)
    )
    return build_lots_response(replace(publication, lots=filtered))


@router.get("/{portfolio_id}/instruments/{instrument_id}/price-chart", response_model=InstrumentPriceChartResponse)
def get_portfolio_instrument_price_chart(
    portfolio_id: str,
    instrument_id: str,
    as_of_date: date | None = None,
    range: str | None = None,
) -> InstrumentPriceChartResponse:
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
        chart = build_instrument_price_chart(
            instrument_id,
            as_of_date=resolved_as_of_date,
            range_key=normalize_chart_range_key(range),
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if chart is None:
        raise HTTPException(status_code=404, detail="Instrument not found in shared registry.")

    return InstrumentPriceChartResponse.model_validate(
        {
            "portfolio_id": portfolio_id,
            **chart,
        }
    )
