from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from portfolio_app.api.contracts import (
    InstrumentPriceChartResponse,
    PositionListResponse,
    PositionListSummary,
    PositionLotListResponse,
    PositionLotListSummary,
    PositionLotRecord,
    PositionRecord,
)
from portfolio_app.services.instrument_charts import build_instrument_price_chart, normalize_chart_range_key
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.ledger import (
    build_portfolio_positions,
    build_position_lots,
    summarize_position_lots,
    summarize_positions,
)
from portfolio_app.services.portfolio_store import get_portfolio, list_accounts, list_transactions


router = APIRouter()


def _parsed_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _resolve_positions_as_of_date(
    portfolio: dict[str, object],
    transactions: list[dict[str, object]],
    requested_as_of_date: date | None,
) -> date:
    """Resolve the current ledger boundary without hiding newer transactions.

    ``portfolio.as_of_date`` is a persisted valuation boundary, not an
    immutable transaction cut-off.  When the caller does not request a
    historical date, position reads must include newly entered or corrected
    facts and advance to their latest economic activity date.
    """

    if requested_as_of_date is not None:
        return requested_as_of_date
    candidates = [
        candidate
        for candidate in (
            _parsed_date(portfolio.get("as_of_date")),
            *(
                _parsed_date(transaction.get(field_name))
                for transaction in transactions
                for field_name in ("trade_date", "settlement_date", "entitlement_date")
            ),
        )
        if candidate is not None
    ]
    return max(candidates, default=date.today())


@router.get("/{portfolio_id}/positions", response_model=PositionListResponse)
def list_portfolio_positions(
    portfolio_id: str,
    as_of_date: date | None = None,
) -> PositionListResponse:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    accounts = list_accounts(portfolio_id)
    transactions = list_transactions(portfolio_id, end_date=as_of_date)
    resolved_as_of_date = _resolve_positions_as_of_date(
        portfolio,
        transactions,
        as_of_date,
    )
    try:
        positions = build_portfolio_positions(
            portfolio_id,
            accounts,
            transactions,
            as_of_date=resolved_as_of_date,
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
    instrument_id: str | None = None,
    status: str | None = None,
    as_of_date: date | None = None,
) -> PositionLotListResponse:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    accounts = list_accounts(portfolio_id)
    transactions = list_transactions(portfolio_id, end_date=as_of_date)
    resolved_as_of_date = _resolve_positions_as_of_date(
        portfolio,
        transactions,
        as_of_date,
    )
    try:
        position_lots = build_position_lots(
            portfolio_id,
            accounts,
            transactions,
            account_id=account_id,
            instrument_id=instrument_id,
            status=status,
            as_of_date=resolved_as_of_date,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return PositionLotListResponse(
        portfolio_id=portfolio_id,
        summary=PositionLotListSummary.model_validate(summarize_position_lots(position_lots)),
        position_lots=[PositionLotRecord.model_validate(item) for item in position_lots],
    )


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
