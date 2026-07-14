from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from watchlist_app.core.settings import get_settings
from watchlist_app.db.session import get_db_session
from watchlist_app.services.readiness import check_watchlist_readiness


router = APIRouter()


@router.get("/health")
def get_health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
    }


@router.get("/readiness")
def get_readiness(
    response: Response,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    report = check_watchlist_readiness(session, get_settings())
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report.as_dict()
