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
                app_id="watchlist",
                name="Watchlist",
                url=settings.watchlist_url,
                api_url=settings.watchlist_api_url,
                eyebrow="Research and monitoring",
                description=(
                    "Review funds, indexes, watchlists, and instrument research."
                ),
            ),
            PlatformAppCard(
                app_id="portfolio",
                name="Portfolio",
                url=settings.portfolio_url,
                api_url=settings.portfolio_api_url,
                eyebrow="Portfolio management",
                description=(
                    "Manage holdings, transactions, performance, risk, and research."
                ),
            ),
            PlatformAppCard(
                app_id="database_dashboard",
                name="Database Dashboard",
                url=settings.database_dashboard_url,
                api_url=settings.database_dashboard_api_url,
                eyebrow="Shared database ops",
                description=(
                    "Shared instruments, FX, NAV imports, email refresh rules, "
                    "and other shared market data operations."
                ),
            ),
            PlatformAppCard(
                app_id="regime",
                name="Asset Regime Dashboard",
                url=settings.regime_url,
                api_url=None,
                eyebrow="Market regime",
                description=(
                    "Review current market regimes, signals, and release evidence."
                ),
            ),
        ],
    )
