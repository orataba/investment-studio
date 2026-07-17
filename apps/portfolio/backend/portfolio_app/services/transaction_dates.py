from __future__ import annotations

from datetime import date


ENTITLEMENT_ACCRUAL_TRANSACTION_TYPES = frozenset({"dividend", "coupon"})
EXTERNAL_FLOW_TRANSACTION_TYPES = frozenset({"deposit", "withdrawal"})


def transaction_sort_key(
    transaction: dict[str, object],
) -> tuple[str, str, str, str, str]:
    """Return the canonical deterministic ordering key for transactions."""

    return (
        str(transaction.get("trade_date") or ""),
        str(transaction.get("trade_at") or ""),
        str(transaction.get("created_at") or ""),
        str(transaction.get("transaction_id") or ""),
        str(transaction.get("settlement_date") or ""),
    )


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            try:
                return date.fromisoformat(normalized[:10])
            except ValueError:
                return None
    return None


def transaction_precedes_entitlement_bod(
    transaction: dict[str, object],
    entitlement_date: date,
) -> bool:
    """Whether a position fact belongs to entitlement-date beginning of day.

    Same-day ordinary trades are excluded: a same-day purchase is not entitled,
    while a same-day sale has not reduced the opening position.  A same-day
    opening balance is included only when its acquisition date proves earlier
    ownership.
    """

    trade_date = _parse_date(transaction.get("trade_date"))
    if trade_date is None:
        return False
    if trade_date < entitlement_date:
        return True
    if trade_date > entitlement_date:
        return False
    if str(transaction.get("transaction_type") or "") != "opening_balance":
        return False
    acquisition_date = _parse_date(transaction.get("acquisition_date"))
    return acquisition_date is not None and acquisition_date < entitlement_date


def transaction_economic_date(transaction: dict[str, object]) -> date | None:
    """Return the date on which the economic fact is recognized.

    Security income is recognized from entitlement; other facts retain their
    trade-date economic boundary.  Cash settlement is deliberately separate.
    """

    transaction_type = str(transaction.get("transaction_type") or "").strip()
    if transaction_type in ENTITLEMENT_ACCRUAL_TRANSACTION_TYPES:
        entitlement_date = _parse_date(transaction.get("entitlement_date"))
        if entitlement_date is not None:
            return entitlement_date
    return _parse_date(transaction.get("trade_date"))


def transaction_external_flow_date(transaction: dict[str, object]) -> date | None:
    """Return the portfolio-boundary cash-flow date, if this is one.

    Deposits and withdrawals enter the cash ledger on their settlement date;
    they must be neutralized in TWR on that same date, not prematurely on the
    order/trade date.  ``external_flow_date`` remains an additive override for
    imported facts; records without one derive deterministically.
    """

    transaction_type = str(transaction.get("transaction_type") or "").strip()
    if transaction_type not in EXTERNAL_FLOW_TRANSACTION_TYPES:
        return None
    return (
        _parse_date(transaction.get("external_flow_date"))
        or _parse_date(transaction.get("settlement_date"))
        or _parse_date(transaction.get("trade_date"))
    )


def transaction_performance_effective_date(
    transaction: dict[str, object],
) -> date | None:
    """Return the canonical date used by NAV/performance recognition."""

    return transaction_external_flow_date(transaction) or transaction_economic_date(
        transaction
    )
