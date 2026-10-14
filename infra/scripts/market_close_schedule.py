"""Match a market refresh to the actual session close, including half-days."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

def is_refresh_due(market_scope: str, now: datetime, *, collection: bool = False, collector_projection: bool = False) -> bool:
    import exchange_calendars
    from investment_studio_instrument_core.listing_contract import MARKET_SCOPE_TIMEZONES

    if now.tzinfo is None:
        raise ValueError("The schedule clock must include a timezone")
    calendar_name = {"hk": "XHKG", "us": "XNYS"}[market_scope]
    local_day = now.astimezone(ZoneInfo(MARKET_SCOPE_TIMEZONES[market_scope])).date()
    calendar = exchange_calendars.get_calendar(calendar_name)
    if not calendar.is_session(local_day):
        return False
    # US registered acquisition starts two hours after the actual session close.
    # Replicas project at the following half-hour tick; the collector's existing
    # acquisition runner already projects immediately after collecting.
    # HK's half-hour buffer also follows the closing auction (up to 16:10).
    delay = 120 if market_scope == "us" and collection else 150
    refresh_at = calendar.session_close(local_day).to_pydatetime() + timedelta(minutes=delay)
    if not collector_projection and refresh_at <= now < refresh_at + timedelta(minutes=30):
        return True
    # The HKEX daily quotation used to confirm no-trade sessions can still be
    # unpublished at the first close run. One late reconciliation uses the same
    # closed-session acceptance; it never treats an unavailable report as data.
    local_now = now.astimezone(ZoneInfo(MARKET_SCOPE_TIMEZONES[market_scope]))
    return not collection and market_scope == "hk" and local_now.hour == 23 and local_now.minute >= 30


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-scope", choices=("hk", "us"), required=True)
    parser.add_argument("--now", type=datetime.fromisoformat)
    parser.add_argument("--collection", action="store_true", help="Match the registered acquisition slot rather than a replica projection")
    parser.add_argument("--collector-projection", action="store_true", help="Only HK late reconciliation; acquisition already projects the close batch")
    args = parser.parse_args()
    return 0 if is_refresh_due(args.market_scope, args.now or datetime.now(timezone.utc),
                              collection=args.collection, collector_projection=args.collector_projection) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # systemd ExecCondition treats 1 as a skipped tick and 255 as a failure.
        print(f"Market schedule failed: {error}", file=sys.stderr)
        raise SystemExit(255) from error
