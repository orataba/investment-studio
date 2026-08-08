"""Replayable written-option obligation subledger.

Written options are not represented as negative long lots.  This module keeps
an auditable, deterministic read model that can be rebuilt from canonical
transaction facts at any as-of date.  The carrying liability is the remaining
unearned premium basis (no daily option quote is required in P0).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Iterable

from portfolio_app.services.option_actions import resolve_option_action
from portfolio_app.services.transaction_dates import (
    transaction_performance_effective_date,
    transaction_sort_key,
)


EPSILON = 1e-9


def _float(value: object, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _contract(transaction: dict[str, object]) -> dict[str, object] | None:
    ref = transaction.get("instrument_ref")
    if not isinstance(ref, dict):
        return None
    raw = ref.get("option_contract")
    return raw if isinstance(raw, dict) else None


def option_contract_identity(
    transaction: dict[str, object],
) -> dict[str, object]:
    """Return the complete normalized option identity embedded in a transaction."""

    contract = _contract(transaction)
    if contract is None:
        raise ValueError("Option contract identity is required for option transactions.")
    required_fields = (
        "underlying_instrument_id",
        "option_type",
        "expiry_date",
        "strike",
        "contract_multiplier",
        "settlement_type",
        "contract_currency",
    )
    if any(contract.get(field) in (None, "") for field in required_fields):
        raise ValueError("Option contract identity is incomplete for option transactions.")
    underlying = str(contract.get("underlying_instrument_id") or "").strip()
    option_id = str(transaction.get("instrument_id") or "").strip()
    if not underlying or underlying == option_id:
        raise ValueError("Option contract underlying instrument must differ from option instrument.")
    option_type = str(contract.get("option_type") or "").strip().lower()
    if option_type not in {"call", "put"}:
        raise ValueError("Option contract option_type must be call or put.")
    settlement_type = str(contract.get("settlement_type") or "").strip().lower()
    if settlement_type not in {"physical", "cash"}:
        raise ValueError("Option contract settlement_type must be physical or cash.")
    multiplier = _float(contract.get("contract_multiplier"))
    strike = _float(contract.get("strike"))
    if multiplier <= EPSILON or strike <= EPSILON:
        raise ValueError("Option contract strike and multiplier must be positive.")
    expiry = _date(contract.get("expiry_date"))
    if expiry is None:
        raise ValueError("Option contract expiry_date is required.")
    currency = str(
        contract.get("contract_currency") or transaction.get("currency") or ""
    ).strip().upper()
    transaction_currency = str(transaction.get("currency") or "").strip().upper()
    if not currency or (transaction_currency and currency != transaction_currency):
        raise ValueError("Option contract currency must match transaction currency.")
    return {
        **contract,
        "underlying_instrument_id": underlying,
        "option_type": option_type,
        "expiry_date": expiry.isoformat(),
        "strike": strike,
        "contract_multiplier": multiplier,
        "settlement_type": settlement_type,
        "contract_currency": currency,
    }


def _validate_writer_contract(
    transaction: dict[str, object],
    *,
    key: tuple[str, str, str],
    covered_quantity: float,
) -> dict[str, object]:
    """Validate the P0 covered-call convention against Registry identity.

    Writer quantity is the number of covered underlying units. The contract
    multiplier makes the inferred exchange-contract count integral and auditable.
    """

    identity = option_contract_identity(transaction)
    if identity.get("option_type") != "call":
        raise ValueError("Only covered call writer transactions are supported in P0.")
    if identity.get("settlement_type") != "physical":
        raise ValueError(
            "Only physically settled covered call writer transactions are supported in P0."
        )
    if str(identity.get("underlying_instrument_id") or "") != key[2]:
        raise ValueError(
            "Option contract underlying does not match related_instrument_id."
        )
    multiplier = _float(identity.get("contract_multiplier"))
    if covered_quantity > EPSILON:
        inferred_contracts = covered_quantity / multiplier
        if abs(inferred_contracts - round(inferred_contracts)) > 1e-6:
            raise ValueError(
                "Option writer quantity must be an integral contract deliverable."
            )
    return identity


def _covered_quantity(transaction: dict[str, object], quantity: float) -> tuple[float, float | None]:
    """Return covered units and inferred exchange contracts.

    The transaction schema defines option quantity as covered underlying units.
    """

    multiplier = _float(option_contract_identity(transaction).get("contract_multiplier"))
    return quantity, quantity / multiplier


def _key(transaction: dict[str, object]) -> tuple[str, str, str]:
    return (
        str(transaction.get("account_id") or "").strip(),
        str(transaction.get("instrument_id") or "").strip(),
        str(transaction.get("related_instrument_id") or "").strip(),
    )


def _new_obligation(transaction: dict[str, object], quantity: float) -> dict[str, object]:
    covered_quantity, inferred_contracts = _covered_quantity(transaction, quantity)
    contract = option_contract_identity(transaction)
    gross = _float(transaction.get("gross_amount"))
    opened_at = _date(
        transaction_performance_effective_date(transaction)
        or transaction.get("trade_date")
    )
    expiry_date = _date(contract.get("expiry_date"))
    transaction_id = str(transaction.get("transaction_id") or "")
    account_id, option_id, underlying_id = _key(transaction)
    return {
        "obligation_id": f"obl-{transaction_id}",
        "portfolio_id": transaction.get("portfolio_id"),
        "account_id": account_id,
        "option_instrument_id": option_id,
        "instrument_id": option_id,  # convenient holding/read-model alias
        "instrument_ref": deepcopy(transaction.get("instrument_ref"))
        if isinstance(transaction.get("instrument_ref"), dict)
        else None,
        "related_underlying_id": underlying_id,
        "open_contract_quantity": inferred_contracts,
        "covered_underlying_quantity": covered_quantity,
        "remaining_quantity": covered_quantity,
        "premium_received_gross": gross,
        "premium_basis_remaining": gross,
        "carrying_liability": gross,
        "opened_at": opened_at.isoformat() if opened_at else transaction.get("trade_date"),
        "expiry_date": expiry_date.isoformat() if expiry_date else None,
        "status": "open",
        "coverage_type": "covered_call",
        "option_type": contract.get("option_type"),
        "strike": contract.get("strike"),
        "contract_multiplier": contract.get("contract_multiplier"),
        "settlement_type": contract.get("settlement_type"),
        "contract_currency": contract.get("contract_currency") or transaction.get("currency"),
        # Premium is deferred in the liability subledger.  Attached charges
        # are recognized separately by the transaction buckets; opening an
        # obligation itself never creates premium income.
        "realized_pnl": 0.0,
        "opening_fee_expense": _float(transaction.get("fees"))
        + _float(transaction.get("taxes")),
        "realizations": [],
        "_opened_by_transaction_id": transaction_id,
    }


def _consume(
    obligation: dict[str, object],
    *,
    quantity: float,
    transaction: dict[str, object],
    reason: str,
    close_cost: float = 0.0,
    fees: float = 0.0,
    taxes: float = 0.0,
) -> dict[str, object]:
    remaining_quantity = _float(obligation.get("remaining_quantity"))
    if quantity <= EPSILON:
        quantity = remaining_quantity
    if quantity > remaining_quantity + EPSILON:
        raise ValueError("Option writer close quantity exceeds the open obligation.")
    quantity = min(quantity, remaining_quantity)
    basis_remaining = _float(obligation.get("premium_basis_remaining"))
    released_basis = (
        basis_remaining
        if quantity >= remaining_quantity - EPSILON
        else basis_remaining * quantity / remaining_quantity
    )
    # A buy-to-close has an explicit close cost.  Expiry/assignment do not.
    realized_pnl = released_basis - close_cost - fees - taxes
    obligation["remaining_quantity"] = max(remaining_quantity - quantity, 0.0)
    obligation["covered_underlying_quantity"] = obligation["remaining_quantity"]
    if _float(obligation.get("open_contract_quantity")) > EPSILON:
        multiplier = _float(obligation.get("contract_multiplier"), 0.0)
        if multiplier > EPSILON:
            obligation["open_contract_quantity"] = obligation["remaining_quantity"] / multiplier
        else:
            obligation["open_contract_quantity"] = obligation["remaining_quantity"]
    obligation["premium_basis_remaining"] = max(basis_remaining - released_basis, 0.0)
    obligation["carrying_liability"] = obligation["premium_basis_remaining"]
    obligation["realized_pnl"] = _float(obligation.get("realized_pnl")) + realized_pnl
    if obligation["remaining_quantity"] <= EPSILON:
        obligation["remaining_quantity"] = 0.0
        obligation["covered_underlying_quantity"] = 0.0
        obligation["premium_basis_remaining"] = 0.0
        obligation["carrying_liability"] = 0.0
        obligation["status"] = reason

    transaction_id = str(transaction.get("transaction_id") or "")
    realization = {
        "realization_id": f"{obligation['obligation_id']}-r{len(obligation.get('realizations') or []) + 1}",
        "transaction_id": transaction_id,
        "transaction_type": transaction.get("transaction_type"),
        "option_action": resolve_option_action(transaction),
        "lifecycle_event_type": transaction.get("lifecycle_event_type"),
        "trade_date": transaction.get("trade_date"),
        "quantity": quantity,
        "released_premium_basis": released_basis,
        "close_cost": close_cost,
        "fees": fees,
        "taxes": taxes,
        "realized_pnl": realized_pnl,
        "reason": reason,
    }
    obligation.setdefault("realizations", []).append(realization)
    return {
        "obligation_id": obligation["obligation_id"],
        "transaction_id": transaction_id,
        "event_type": reason,
        "released_quantity": quantity,
        "released_premium_basis": released_basis,
        "liability_delta": -released_basis,
        "realized_pnl_delta": realized_pnl,
        "obligation": deepcopy(obligation),
        "option_action": resolve_option_action(transaction),
        "event_date": transaction_performance_effective_date(transaction)
        or _date(transaction.get("trade_date")),
        "currency": transaction.get("currency")
        or obligation.get("contract_currency"),
    }


def derive_option_obligation_events(
    transactions: Iterable[dict[str, object]],
    *,
    as_of_date: date | None = None,
) -> list[dict[str, object]]:
    """Replay writer facts and return one lifecycle event per affected row."""

    ordered = sorted(list(transactions), key=transaction_sort_key)
    obligations: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    events: list[dict[str, object]] = []
    for transaction in ordered:
        effective_date = transaction_performance_effective_date(transaction) or _date(
            transaction.get("trade_date")
        )
        if as_of_date is not None and (effective_date is None or effective_date > as_of_date):
            continue
        action = resolve_option_action(transaction)
        lifecycle = str(transaction.get("lifecycle_event_type") or "")
        key = _key(transaction)
        quantity = _float(transaction.get("quantity"))
        if action == "sell_to_open":
            if quantity <= EPSILON:
                raise ValueError("Option writer open requires positive quantity.")
            identity = _validate_writer_contract(
                transaction,
                key=key,
                covered_quantity=quantity,
            )
            opened_at = effective_date or _date(transaction.get("trade_date"))
            expiry_date = _date(identity.get("expiry_date"))
            if (
                opened_at is not None
                and expiry_date is not None
                and opened_at > expiry_date
            ):
                raise ValueError("Option writer open date must not be after contract expiry.")
            obligation = _new_obligation(transaction, quantity)
            obligations.setdefault(key, []).append(obligation)
            events.append(
                {
                    "obligation_id": obligation["obligation_id"],
                    "transaction_id": transaction.get("transaction_id"),
                    "event_type": "open",
                    "released_quantity": 0.0,
                    "released_premium_basis": 0.0,
                    "liability_delta": _float(transaction.get("gross_amount")),
                    # Fees and taxes are ordinary expenses and are surfaced by
                    # the transaction buckets exactly once.  Do not also put
                    # them in the lifecycle realized bucket.
                    "realized_pnl_delta": 0.0,
                    "expense_delta": -_float(transaction.get("fees"))
                    - _float(transaction.get("taxes")),
                    "obligation": deepcopy(obligation),
                    "option_action": action,
                    "event_date": effective_date,
                    "currency": transaction.get("currency"),
                }
            )
            continue

        close_reason: str | None = None
        if action == "buy_to_close":
            close_reason = "closed"
        elif lifecycle == "option_writer_expiry":
            close_reason = "expired"
        elif lifecycle == "option_assignment":
            close_reason = "assigned"
        if close_reason is None:
            continue

        open_rows = obligations.get(key, [])
        available = sum(_float(row.get("remaining_quantity")) for row in open_rows)
        requested = quantity if quantity > EPSILON else available
        if requested > available + EPSILON:
            raise ValueError("Option writer close quantity exceeds the open obligation.")
        identity = _validate_writer_contract(
            transaction,
            key=key,
            covered_quantity=requested,
        )
        if lifecycle == "option_writer_expiry":
            expiry_date = _date(identity.get("expiry_date"))
            if (
                effective_date is not None
                and expiry_date is not None
                and effective_date < expiry_date
            ):
                raise ValueError(
                    "Option writer expiry event must not precede contract expiry."
                )
        remaining = requested
        for obligation in open_rows:
            if remaining <= EPSILON:
                break
            take = min(remaining, _float(obligation.get("remaining_quantity")))
            if take <= EPSILON:
                continue
            # Allocate close cost and charges across FIFO quantity slices.
            ratio = take / requested if requested > EPSILON else 0.0
            event = _consume(
                obligation,
                quantity=take,
                transaction=transaction,
                reason=close_reason,
                close_cost=_float(transaction.get("gross_amount")) * ratio
                if action == "buy_to_close"
                else 0.0,
                fees=_float(transaction.get("fees")) * ratio
                if action == "buy_to_close"
                else 0.0,
                taxes=_float(transaction.get("taxes")) * ratio
                if action == "buy_to_close"
                else 0.0,
            )
            events.append(event)
            remaining -= take
        obligations[key] = [row for row in open_rows if _float(row.get("remaining_quantity")) > EPSILON]

    return events


def build_option_obligations(
    transactions: Iterable[dict[str, object]],
    *,
    as_of_date: date | None = None,
) -> list[dict[str, object]]:
    """Return open and closed obligation rows at an as-of boundary."""

    events = derive_option_obligation_events(transactions, as_of_date=as_of_date)
    latest_by_id: dict[str, dict[str, object]] = {}
    for event in events:
        obligation = event.get("obligation")
        if isinstance(obligation, dict):
            latest_by_id[str(obligation.get("obligation_id") or "")] = obligation
    rows = [row for key, row in latest_by_id.items() if key]
    rows.sort(
        key=lambda row: (
            str(row.get("opened_at") or ""),
            str(row.get("obligation_id") or ""),
        )
    )
    return rows


def open_option_obligations(
    transactions: Iterable[dict[str, object]],
    *,
    as_of_date: date | None = None,
) -> list[dict[str, object]]:
    return [
        row
        for row in build_option_obligations(transactions, as_of_date=as_of_date)
        if _float(row.get("remaining_quantity")) > EPSILON and row.get("status") == "open"
    ]


def summarize_option_obligations(rows: Iterable[dict[str, object]]) -> dict[str, object]:
    items = list(rows)
    return {
        "obligation_count": len(items),
        "open_obligation_count": sum(1 for row in items if row.get("status") == "open"),
        "open_contract_quantity": sum(
            _float(row.get("open_contract_quantity"))
            for row in items
            if row.get("status") == "open"
        ),
        "covered_underlying_quantity": sum(
            _float(row.get("covered_underlying_quantity"))
            for row in items
            if row.get("status") == "open"
        ),
        "premium_basis_remaining": sum(
            _float(row.get("premium_basis_remaining"))
            for row in items
            if row.get("status") == "open"
        ),
        "carrying_liability": sum(
            _float(row.get("carrying_liability"))
            for row in items
            if row.get("status") == "open"
        ),
        "realized_pnl": sum(_float(row.get("realized_pnl")) for row in items),
    }


__all__ = [
    "build_option_obligations",
    "derive_option_obligation_events",
    "open_option_obligations",
    "option_contract_identity",
    "summarize_option_obligations",
]
