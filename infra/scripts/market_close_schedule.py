"""Match a market refresh to the actual session close, including half-days."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

def is_refresh_due(market_scope: str, now: datetime) -> bool:
    import exchange_calendars
    from investment_studio_instrument_core.listing_contract import MARKET_SCOPE_TIMEZONES

    if now.tzinfo is None:
        raise ValueError("The schedule clock must include a timezone")
    calendar_name = {"hk": "XHKG", "us": "XNYS"}[market_scope]
    local_day = now.astimezone(ZoneInfo(MARKET_SCOPE_TIMEZONES[market_scope])).date()
    calendar = exchange_calendars.get_calendar(calendar_name)
    if not calendar.is_session(local_day):
        return False
    refresh_at = calendar.session_close(local_day).to_pydatetime() + timedelta(minutes=30)
    return refresh_at <= now < refresh_at + timedelta(minutes=30)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-scope", choices=("hk", "us"), required=True)
    parser.add_argument("--now", type=datetime.fromisoformat)
    args = parser.parse_args()
    return 0 if is_refresh_due(args.market_scope, args.now or datetime.now(timezone.utc)) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # systemd ExecCondition treats 1 as a skipped tick and 255 as a failure.
        print(f"Market schedule failed: {error}", file=sys.stderr)
        raise SystemExit(255) from error
