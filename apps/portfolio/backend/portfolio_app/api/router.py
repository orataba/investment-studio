from fastapi import APIRouter

from portfolio_app.api.routes import (
    accounts,
    fx_rates,
    health,
    instrument_events,
    instrument_risk,
    ledger_postings,
    performance,
    portfolios,
    portfolio_risk_context,
    positions,
    table_views,
    taxonomies,
    transaction_captures,
    transactions,
    workspace,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(portfolios.router, prefix="/portfolios", tags=["portfolios"])
api_router.include_router(portfolio_risk_context.router, prefix="/portfolios", tags=["portfolio-risk-context"])
api_router.include_router(workspace.router, prefix="/workspace", tags=["workspace"])
api_router.include_router(accounts.router, prefix="/portfolios", tags=["accounts"])
api_router.include_router(
    instrument_events.router,
    prefix="/portfolios",
    tags=["instrument-events"],
)
api_router.include_router(transactions.router, prefix="/portfolios", tags=["transactions"])
api_router.include_router(
    transaction_captures.router,
    prefix="/portfolios",
    tags=["transaction-captures"],
)
api_router.include_router(ledger_postings.router, prefix="/portfolios", tags=["ledger-postings"])
api_router.include_router(positions.router, prefix="/portfolios", tags=["positions"])
api_router.include_router(performance.router, prefix="/portfolios", tags=["performance"])
api_router.include_router(fx_rates.router, prefix="/portfolios", tags=["fx-rates"])
api_router.include_router(table_views.router, prefix="/portfolios", tags=["table-views"])
api_router.include_router(taxonomies.router, prefix="/portfolios", tags=["taxonomies"])

api_router.include_router(instrument_risk.router, prefix="/instrument-risk", tags=["instrument-risk"])
