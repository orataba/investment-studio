from __future__ import annotations

from portfolio_ops_instrument_core.fx_universe import SUPPORTED_FX_CURRENCIES


SUPPORTED_PORTFOLIO_FACT_CURRENCIES = frozenset(SUPPORTED_FX_CURRENCIES)


class PortfolioFactCurrencyError(ValueError):
    """A persisted portfolio fact has no canonical calculation currency."""


def require_portfolio_fact_currency(value: object, *, context: str) -> str:
    """Return an exact canonical currency or fail before valuation.

    API ingress may normalize user text before persistence. Once a value is a
    portfolio fact, silently trimming, upper-casing, or substituting a base
    currency would change the economic meaning of that fact. Calculation
    boundaries therefore require the persisted representation itself to be
    canonical.
    """

    if not isinstance(value, str):
        raise PortfolioFactCurrencyError(
            f"{context} requires an explicit canonical currency."
        )
    normalized = value.strip().upper()
    if value != normalized or normalized not in SUPPORTED_PORTFOLIO_FACT_CURRENCIES:
        supported = ", ".join(sorted(SUPPORTED_PORTFOLIO_FACT_CURRENCIES))
        raise PortfolioFactCurrencyError(
            f"{context} currency must be stored canonically as one of: {supported}."
        )
    return normalized
