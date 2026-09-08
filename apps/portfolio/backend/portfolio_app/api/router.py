from fastapi import APIRouter, Depends
from portfolio_app.api.authorization import portfolio_request_context

from portfolio_app.api.routes import (
    accounts,
    concentration,
    fx_rates,
    health,
    instrument_events,
    instrument_risk,
    ledger_postings,
    performance,
    portfolios,
    portfolio_members,
    portfolio_risk_context,
    positions,
    research_assistant,
    table_views,
    tail_risk,
    taxonomies,
    transaction_captures,
    transactions,
    workspace,
)


api_router = APIRouter()
protected = {"dependencies": [Depends(portfolio_request_context)]}
api_router.include_router(health.router, tags=["health"])
api_router.include_router(portfolios.router, prefix="/portfolios", tags=["portfolios"], **protected)
api_router.include_router(portfolio_risk_context.router, prefix="/portfolios", tags=["portfolio-risk-context"], **protected)
api_router.include_router(concentration.router, prefix="/portfolios", tags=["concentration"], **protected)
api_router.include_router(tail_risk.router, prefix="/portfolios", tags=["tail-risk"], **protected)
api_router.include_router(workspace.router, prefix="/workspace", tags=["workspace"], **protected)
api_router.include_router(accounts.router, prefix="/portfolios", tags=["accounts"], **protected)
api_router.include_router(
    instrument_events.router,
    prefix="/portfolios",
    tags=["instrument-events"], **protected)
api_router.include_router(transactions.router, prefix="/portfolios", tags=["transactions"], **protected)
api_router.include_router(
    transaction_captures.router,
    prefix="/portfolios",
    tags=["transaction-captures"], **protected)
api_router.include_router(ledger_postings.router, prefix="/portfolios", tags=["ledger-postings"], **protected)
api_router.include_router(positions.router, prefix="/portfolios", tags=["positions"], **protected)
api_router.include_router(performance.router, prefix="/portfolios", tags=["performance"], **protected)
api_router.include_router(fx_rates.router, prefix="/portfolios", tags=["fx-rates"], **protected)
api_router.include_router(table_views.router, prefix="/portfolios", tags=["table-views"], **protected)
api_router.include_router(taxonomies.router, prefix="/portfolios", tags=["taxonomies"], **protected)

api_router.include_router(instrument_risk.router, prefix="/instrument-risk", tags=["instrument-risk"], **protected)
api_router.include_router(research_assistant.router, prefix="/research-assistant", tags=["research-assistant"], **protected)

api_router.include_router(portfolio_members.router, prefix="/portfolios", tags=["portfolio-members"], **protected)
