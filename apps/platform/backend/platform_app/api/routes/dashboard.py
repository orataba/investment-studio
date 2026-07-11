from __future__ import annotations

from collections import Counter

from fastapi import APIRouter

from platform_app.core.settings import get_settings
from platform_app.services.instrument_store import list_instruments


router = APIRouter()


@router.get("")
def get_data_operations_dashboard() -> dict[str, object]:
    """Return a compact operations read model, not the full instrument registry."""
    settings = get_settings()
    instruments = list_instruments(include_inactive=True)
    active = [
        item
        for item in instruments
        if str(dict(item.get("lifecycle_state", {})).get("status") or "active") == "active"
    ]
    type_counts = Counter(str(item.get("instrument_type") or "other") for item in active)
    source_counts = Counter(
        str(dict(item.get("source_settings", {})).get("source_mode") or "manual")
        for item in active
    )
    coverage_counts = Counter(str(item.get("coverage_state") or "unavailable") for item in active)
    market_dates = [
        str(point.get("as_of_date"))
        for item in active
        for point in list(item.get("latest_market_data", []))
        if isinstance(point, dict) and point.get("as_of_date")
    ]
    refresh_times = [
        str(dict(item.get("refresh_status", {})).get("requested_at"))
        for item in active
        if dict(item.get("refresh_status", {})).get("requested_at")
    ]
    problems: list[dict[str, object]] = []
    for item in active:
        refresh_status = dict(item.get("refresh_status", {}))
        status = str(refresh_status.get("status") or "idle")
        has_quote = bool(item.get("latest_market_data"))
        if status not in {"failed", "blocked"} and has_quote:
            continue
        problems.append(
            {
                "instrument_id": item.get("instrument_id"),
                "instrument_name": item.get("instrument_name"),
                "instrument_type": item.get("instrument_type"),
                "issue": status if status in {"failed", "blocked"} else "missing_market_data",
                "message": refresh_status.get("message") or "No current market data.",
            }
        )

    return {
        "registry_name": "Portfolio Operations Shared Instruments",
        "counts": {
            "total": len(instruments),
            "active": len(active),
            "archived": len(instruments) - len(active),
            "missing_market_data": sum(1 for item in active if not item.get("latest_market_data")),
            "refresh_failures": sum(
                1
                for item in active
                if str(dict(item.get("refresh_status", {})).get("status") or "")
                in {"failed", "blocked"}
            ),
        },
        "instrument_types": dict(sorted(type_counts.items())),
        "sources": dict(sorted(source_counts.items())),
        "coverage": dict(sorted(coverage_counts.items())),
        "latest_market_date": max(market_dates) if market_dates else None,
        "last_refresh_at": max(refresh_times) if refresh_times else None,
        "sync_readiness": {
            "email": settings.email_sync_enabled and settings.email_sync_ready,
            "tushare": settings.tushare_ready,
        },
        "problems": problems[:12],
    }
