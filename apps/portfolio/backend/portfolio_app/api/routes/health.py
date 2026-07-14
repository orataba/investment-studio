import os

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from portfolio_app.core.settings import get_settings
from portfolio_app.db.session import get_db_session
from portfolio_app.services.readiness import check_portfolio_readiness

router = APIRouter()
PORTFOLIO_API_CONTRACT = "portfolio-api.exact-decimal.v1"


@router.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "api_contract": PORTFOLIO_API_CONTRACT,
        "release_id": os.environ.get(
            "PORTFOLIO_OPS_LOCAL_RELEASE_ID",
            "development",
        ),
    }


@router.get("/readiness")
def readiness(
    response: Response,
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    report = check_portfolio_readiness(session, get_settings())
    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report.as_dict()
