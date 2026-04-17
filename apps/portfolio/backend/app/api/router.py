from fastapi import APIRouter

from app.api.routes import (
    accounts,
    asset_core,
    fx_rates,
    health,
    ledger_postings,
    performance,
    portfolios,
    positions,
    research,
    taxonomies,
    transactions,
    workspace,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(asset_core.router, prefix="/asset-core", tags=["asset-core"])
api_router.include_router(portfolios.router, prefix="/portfolios", tags=["portfolios"])
api_router.include_router(workspace.router, prefix="/workspace", tags=["workspace"])
api_router.include_router(accounts.router, prefix="/portfolios", tags=["accounts"])
api_router.include_router(transactions.router, prefix="/portfolios", tags=["transactions"])
api_router.include_router(ledger_postings.router, prefix="/portfolios", tags=["ledger-postings"])
api_router.include_router(positions.router, prefix="/portfolios", tags=["positions"])
api_router.include_router(performance.router, prefix="/portfolios", tags=["performance"])
api_router.include_router(fx_rates.router, prefix="/portfolios", tags=["fx-rates"])
api_router.include_router(taxonomies.router, prefix="/portfolios", tags=["taxonomies"])
api_router.include_router(research.router, prefix="/portfolios", tags=["research"])
