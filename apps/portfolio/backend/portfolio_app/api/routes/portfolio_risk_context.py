from datetime import date
from fastapi import APIRouter

from portfolio_app.api.financial_read import FinancialReadRoute
from portfolio_app.services.portfolio_risk_context import read_portfolio_risk_context

router = APIRouter(route_class=FinancialReadRoute)


@router.get("/{portfolio_id}/risk-context")
def portfolio_risk_context(portfolio_id: str, as_of_date: date | None = None):
    return read_portfolio_risk_context(portfolio_id, as_of_date=as_of_date)
