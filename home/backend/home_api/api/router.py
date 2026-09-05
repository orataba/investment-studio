from fastapi import APIRouter

from home_api.api.routes import auth, apps, health


api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(apps.router, prefix="/apps", tags=["apps"])
