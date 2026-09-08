from datetime import date

from fastapi import APIRouter, Query

from portfolio_app.api.financial_read import FinancialReadRoute
from portfolio_app.services.tail_risk import read_portfolio_tail_risk


router = APIRouter(route_class=FinancialReadRoute)


@router.get("/{portfolio_id}/tail-risk")
def portfolio_tail_risk(portfolio_id: str, as_of_date: date | None = None,
                        confidence: float = Query(0.95, gt=0, lt=1),
                        lookback_days: int = Query(1095, ge=1, le=3650)):
    return read_portfolio_tail_risk(portfolio_id, as_of_date=as_of_date,
                                   confidence=confidence, lookback_days=lookback_days)
