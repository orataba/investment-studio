from fastapi import APIRouter

from platform_app.api.contracts import PlatformAppCard, PlatformAppsResponse
from platform_app.core.settings import get_settings


router = APIRouter()


@router.get("", response_model=PlatformAppsResponse)
def list_apps() -> PlatformAppsResponse:
    settings = get_settings()
    return PlatformAppsResponse(
        platform_name="Yungu",
        apps=[
            PlatformAppCard(
                app_id="watchlist",
                name="Watchlist",
                url=settings.watchlist_url,
                api_url=settings.watchlist_api_url,
                eyebrow="Research and monitoring",
                description=(
                    "Fund and asset watchlists, detail pages, facts ingest, "
                    "read models, and copilot-assisted review."
                ),
            ),
            PlatformAppCard(
                app_id="portfolio",
                name="Portfolio",
                url=settings.portfolio_url,
                api_url=settings.portfolio_api_url,
                eyebrow="Portfolio management",
                description=(
                    "Portfolio, account, transaction, risk, and review workflows "
                    "built on top of the shared asset core."
                ),
            ),
        ],
    )
