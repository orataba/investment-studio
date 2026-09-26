from datetime import date
from fastapi import APIRouter

from portfolio_app.api.financial_read import FinancialReadRoute
from portfolio_app.services.portfolio_risk_context import read_portfolio_risk_context, read_portfolio_risk_version

router = APIRouter()
financial_router = APIRouter(route_class=FinancialReadRoute)


@router.get("/{portfolio_id}/risk-context/version")
def portfolio_risk_version(portfolio_id: str):
    # Status reads never enqueue accounting or rebuild risk analysis. The shared
    # request dependency still authenticates and checks this portfolio's ACL.
    return read_portfolio_risk_version(portfolio_id)


@financial_router.get("/{portfolio_id}/risk-context")
def portfolio_risk_context(portfolio_id: str, as_of_date: date | None = None):
    return read_portfolio_risk_context(portfolio_id, as_of_date=as_of_date)


router.include_router(financial_router)
