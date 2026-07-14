from fastapi import APIRouter

from portfolio_app.api.routes import (
    accounts,
    calculations,
    fx_rates,
    health,
    performance,
    portfolios,
    positions,
    allocation_research,
    risk,
    table_views,
    taxonomies,
    transactions,
    workspace,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(
    calculations.router,
    prefix="/calculations",
    tags=["calculations"],
)
api_router.include_router(portfolios.router, prefix="/portfolios", tags=["portfolios"])
api_router.include_router(workspace.router, prefix="/workspace", tags=["workspace"])
api_router.include_router(accounts.router, prefix="/portfolios", tags=["accounts"])
api_router.include_router(transactions.router, prefix="/portfolios", tags=["transactions"])
api_router.include_router(positions.router, prefix="/portfolios", tags=["positions"])
api_router.include_router(performance.router, prefix="/portfolios", tags=["performance"])
api_router.include_router(fx_rates.router, prefix="/portfolios", tags=["fx-rates"])
api_router.include_router(table_views.router, prefix="/portfolios", tags=["table-views"])
api_router.include_router(taxonomies.router, prefix="/portfolios", tags=["taxonomies"])
api_router.include_router(risk.router, prefix="/portfolios", tags=["risk"])
api_router.include_router(
    allocation_research.router,
    prefix="/portfolios",
    tags=["allocation-research"],
)
