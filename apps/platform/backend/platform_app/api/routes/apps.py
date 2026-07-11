from fastapi import APIRouter

from platform_app.api.contracts import PlatformAppCard, PlatformAppsResponse


router = APIRouter()


@router.get("", response_model=PlatformAppsResponse)
def list_apps() -> PlatformAppsResponse:
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
        ],
    )
