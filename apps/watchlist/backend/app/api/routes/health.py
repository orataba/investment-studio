from fastapi import APIRouter

from app.core.settings import get_settings


router = APIRouter()


@router.get("/health")
def get_health() -> dict[str, str | bool]:
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "database_configured": bool(settings.database_url),
    }
