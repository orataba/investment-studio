from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from portfolio_app.core.settings import get_settings


def portfolio_valuation_today(valuation_timezone: str | None) -> date:
    settings = get_settings()
    timezone_name = str(
        valuation_timezone or settings.default_trade_timezone
    ).strip()
    return datetime.now(ZoneInfo(timezone_name)).date()
