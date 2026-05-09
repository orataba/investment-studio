from __future__ import annotations


USABLE_MARKET_DATA_STATUS = "complete"


def market_data_status(point: dict[str, object]) -> str:
    return str(point.get("status") or "").strip().lower()


def is_usable_market_data_point(point: object) -> bool:
    return isinstance(point, dict) and market_data_status(point) == USABLE_MARKET_DATA_STATUS
