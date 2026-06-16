from fastapi import APIRouter

from platform_app.core.settings import get_settings


router = APIRouter()


@router.get("/health")
def get_health() -> dict[str, object]:
    settings = get_settings()
    missing_email_settings: list[str] = []
    if not settings.email_imap_host:
        missing_email_settings.append("YUNGU_PLATFORM_EMAIL_IMAP_HOST")
    if not settings.email_imap_username:
        missing_email_settings.append("YUNGU_PLATFORM_EMAIL_IMAP_USERNAME")
    if not settings.email_imap_password:
        missing_email_settings.append("YUNGU_PLATFORM_EMAIL_IMAP_PASSWORD")

    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "email_sync": {
            "enabled": settings.email_sync_enabled,
            "ready": settings.email_sync_enabled and settings.email_sync_ready,
            "imap_folder": settings.email_imap_folder,
            "imap_use_ssl": settings.email_imap_use_ssl,
            "max_messages": settings.email_imap_max_messages,
            "missing_required_settings": missing_email_settings,
        },
        "tushare_sync": {
            "ready": settings.tushare_ready,
            "api_url": settings.tushare_api_url,
        },
    }
