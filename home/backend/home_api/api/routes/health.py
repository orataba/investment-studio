from fastapi import APIRouter

from home_api.core.settings import get_settings


router = APIRouter()


@router.get("/health")
def get_health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}
