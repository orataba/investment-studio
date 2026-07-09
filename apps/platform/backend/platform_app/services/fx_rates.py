from __future__ import annotations

from decimal import Decimal

from portfolio_ops_instrument_core import fx_rates as shared_fx_rates

from platform_app.db.session import get_session_factory


def supported_fx_currencies() -> list[str]:
    return shared_fx_rates.supported_fx_currencies()


def maintained_fx_pairs() -> list[str]:
    return shared_fx_rates.maintained_fx_pairs()


def list_fx_rates() -> list[dict[str, object]]:
    return shared_fx_rates.list_fx_rates(get_session_factory())


def upsert_fx_rate(
    *,
    base_currency: str,
    quote_currency: str,
    rate: Decimal,
    as_of_date: date,
    provider: str | None,
    status: str,
) -> dict[str, object]:
    return shared_fx_rates.upsert_fx_rate(
        get_session_factory(),
        base_currency=base_currency,
        quote_currency=quote_currency,
        rate=rate,
        as_of_date=as_of_date,
        provider=provider,
        status=status,
    )
