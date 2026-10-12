"""Automatic research admission follows the provider's published off-peak window.

Source: https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
Beijing weekdays 09:00–12:00 and 14:00–18:00 are peak. We conservatively
treat all Monday–Friday dates alike, without a holiday calendar dependency.
This is an admission policy, not a guarantee about a gateway's actual bill or
the completion time of an already-running request. Manual work is unaffected.
"""
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")


def automatic_research_allowed(now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Research scheduling requires a timezone-aware instant")
    local = now.astimezone(BEIJING)
    if local.weekday() >= 5:
        return True
    clock = local.time()
    return not (time(9) <= clock < time(12) or time(14) <= clock < time(18))
