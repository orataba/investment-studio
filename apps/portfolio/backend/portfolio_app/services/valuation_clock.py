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


def planning_reference_date(valuation_date: date, *, pinned: bool = False) -> date:
    """Keep current planning separate from the last complete market valuation."""
    return valuation_date if pinned else max(valuation_date, portfolio_valuation_today(None))
