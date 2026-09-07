from fastapi import APIRouter

from watchlist_app.api.routes import (
    attributes,
    facts,
    field_registry,
    funds,
    health,
    instruments,
    monitoring,
    recalc,
    research,
    screener,
    taxonomies,
    watchlists,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(watchlists.router, prefix="/watchlists", tags=["watchlists"])
api_router.include_router(instruments.router, prefix="/instruments", tags=["instruments"])
api_router.include_router(funds.router, prefix="/instruments", tags=["instruments"])
api_router.include_router(research.router, prefix="/instruments", tags=["research"])
api_router.include_router(attributes.router, prefix="/instrument-attributes", tags=["instrument-attributes"])
api_router.include_router(taxonomies.router, prefix="/taxonomies", tags=["taxonomies"])
api_router.include_router(facts.router, prefix="/facts", tags=["facts"])
api_router.include_router(field_registry.router, prefix="/field-registry", tags=["field-registry"])
api_router.include_router(screener.router, prefix="/screener", tags=["screener"])
api_router.include_router(recalc.router, prefix="/recalc", tags=["recalc"])
api_router.include_router(monitoring.router, prefix="/monitoring", tags=["monitoring"])

from watchlist_app.api.routes import workbench
api_router.include_router(workbench.router, tags=["research-workbench"])
from watchlist_app.api.routes import sector_research
api_router.include_router(sector_research.router, tags=["sector-research"])
from watchlist_app.api.routes import research_dossier
api_router.include_router(research_dossier.router, tags=["research-dossier"])
from watchlist_app.api.routes import risk_officer
api_router.include_router(risk_officer.router, tags=["risk-officer"])
