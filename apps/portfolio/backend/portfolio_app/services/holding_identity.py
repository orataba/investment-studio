"""Canonical identities used by portfolio holding consumers."""

from __future__ import annotations

from portfolio_app.services.fact_currency import require_portfolio_fact_currency


_CASH_HOLDING_PREFIX = "cash:"


def cash_holding_instrument_id(currency: object) -> str:
    normalized = require_portfolio_fact_currency(
        currency,
        context="cash holding",
    )
    return f"{_CASH_HOLDING_PREFIX}{normalized}"


def is_cash_holding_instrument_id(instrument_id: object) -> bool:
    return str(instrument_id or "").strip().lower().startswith(
        _CASH_HOLDING_PREFIX
    )


__all__ = [
    "cash_holding_instrument_id",
    "is_cash_holding_instrument_id",
]
