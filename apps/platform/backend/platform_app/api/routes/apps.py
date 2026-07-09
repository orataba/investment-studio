from fastapi import APIRouter

from platform_app.api.contracts import PlatformAppCard, PlatformAppsResponse
from platform_app.core.settings import get_settings


router = APIRouter()


@router.get("", response_model=PlatformAppsResponse)
def list_apps() -> PlatformAppsResponse:
    settings = get_settings()
    return PlatformAppsResponse(
        platform_name="Portfolio Operations Workbench",
        apps=[
            PlatformAppCard(
                app_id="database_dashboard",
                name="Database Dashboard",
                url="/database-dashboard",
                api_url="/api/instruments",
                eyebrow="Shared database ops",
                description=(
                    "Shared instruments, FX, NAV imports, email refresh rules, "
                    "and other shared market data operations."
                ),
            ),
            PlatformAppCard(
                app_id="watchlist",
                name="Watchlist",
                url=settings.watchlist_url,
                api_url=settings.watchlist_api_url,
                eyebrow="Fund research and monitoring",
                description=(
                    "Fund-only watchlists, fund detail pages, facts ingest, "
                    "read models, and monitoring workflows."
                ),
            ),
            PlatformAppCard(
                app_id="portfolio",
                name="Portfolio",
                url=settings.portfolio_url,
                api_url=settings.portfolio_api_url,
                eyebrow="Portfolio management",
                description=(
                    "Portfolio, account, transaction, performance, risk, and research workflows "
                    "built on top of the shared instrument core."
                ),
            ),
        ],
    )
