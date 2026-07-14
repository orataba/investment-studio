from fastapi import APIRouter

from platform_app.api.routes import apps, dashboard, fx_rates, health, instruments


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(apps.router, prefix="/apps", tags=["apps"])
api_router.include_router(instruments.router, prefix="/instruments", tags=["instruments"])
api_router.include_router(fx_rates.router, prefix="/fx-rates", tags=["fx-rates"])
