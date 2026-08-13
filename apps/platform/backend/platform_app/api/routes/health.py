from fastapi import APIRouter

from platform_app.core.settings import get_settings


router = APIRouter()


@router.get("/health")
def get_health() -> dict[str, object]:
    settings = get_settings()
    missing_email_settings: list[str] = []
    if not settings.email_imap_host:
        missing_email_settings.append("PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_HOST")
    if not settings.email_imap_username:
        missing_email_settings.append("PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_USERNAME")
    if not settings.email_imap_password:
        missing_email_settings.append("PORTFOLIO_OPS_PLATFORM_EMAIL_IMAP_PASSWORD")

    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "email_sync": {
            "enabled": settings.email_sync_enabled,
            "ready": settings.email_sync_enabled and settings.email_sync_ready,
            "imap_folders": settings.email_imap_folders,
            "imap_use_ssl": settings.email_imap_use_ssl,
            "history_start_date": settings.email_history_start_date.isoformat(),
            "header_fetch_batch_size": settings.email_header_fetch_batch_size,
            "message_fetch_batch_size": settings.email_message_fetch_batch_size,
            "ingestion_lease_seconds": settings.email_ingestion_lease_seconds,
            "missing_required_settings": missing_email_settings,
        },
        "datahub_tushare_sync": {
            "ready": settings.datahub_ready,
            "api_url": settings.datahub_tushare_api_url,
        },
    }
