"""Account workspace projection over one immutable Portfolio Daily publication.

Account configuration and raw transaction facts may be read from their current
tables, but every financial state in this module comes exclusively from the
fenced holding, balance, and lot outputs selected by ``published_repository``.
No live-ledger replay or binary-float arithmetic is permitted here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from portfolio_app.calculations.numeric import (
    exact_decimal_negate,
    exact_decimal_sum,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_account_input,
    portfolio_daily_instrument_input,
)
from portfolio_app.calculations.portfolio_daily.published_repository import (
    CurrentPortfolioDailyPublication,
    PortfolioDailyBalance,
    PortfolioDailyHolding,
    PortfolioDailyLot,
)


class PublishedAccountsIntegrityError(RuntimeError):
    """A current Portfolio Daily publication cannot form an account view."""


def _exact_sum(values: Sequence[Decimal]) -> Decimal:
    return exact_decimal_sum(tuple(values))


def _optional_exact_sum(values: Sequence[Decimal | None]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    return _exact_sum(tuple(value for value in values if value is not None))


def _reason_codes(*groups: Sequence[str]) -> list[str]:
    return sorted({reason for group in groups for reason in group})


def _coverage_state(flags: Sequence[bool]) -> str:
    if not flags or all(flags):
        return "complete"
    if any(flags):
        return "partial"
    return "unavailable"


def _aggregate_coverage_states(states: Sequence[str]) -> str:
    if not states:
        return "unavailable"
    unique = set(states)
    if unique == {"complete"}:
        return "complete"
    if unique == {"unavailable"}:
        return "unavailable"
    return "partial"


def _validate_sealed_identity(
    row: Mapping[str, object],
    publication: CurrentPortfolioDailyPublication,
) -> None:
    metadata = publication.metadata
    if (
        row["manifest_id"] != metadata.manifest_id
        or row["run_id"] != metadata.run_id
        or row["portfolio_id"] != metadata.portfolio_id
    ):
        raise PublishedAccountsIntegrityError(
            "sealed account workspace input has inconsistent publication identity"
        )


def _sealed_inputs(
    session: Session,
    publication: CurrentPortfolioDailyPublication,
) -> tuple[dict[str, RowMapping], dict[str, RowMapping]]:
    metadata = publication.metadata
    account_rows = tuple(
        session.execute(
            select(
                portfolio_daily_account_input.c.manifest_id,
                portfolio_daily_account_input.c.run_id,
                portfolio_daily_account_input.c.portfolio_id,
                portfolio_daily_account_input.c.account_id,
                portfolio_daily_account_input.c.currency,
            ).where(
                portfolio_daily_account_input.c.manifest_id == metadata.manifest_id
            )
        ).mappings()
    )
    instrument_rows = tuple(
        session.execute(
            select(
                portfolio_daily_instrument_input.c.manifest_id,
                portfolio_daily_instrument_input.c.run_id,
                portfolio_daily_instrument_input.c.portfolio_id,
                portfolio_daily_instrument_input.c.instrument_id,
                portfolio_daily_instrument_input.c.instrument_name,
                portfolio_daily_instrument_input.c.instrument_type,
                portfolio_daily_instrument_input.c.currency,
            ).where(
                portfolio_daily_instrument_input.c.manifest_id
                == metadata.manifest_id
            )
        ).mappings()
    )
    for row in (*account_rows, *instrument_rows):
        _validate_sealed_identity(row, publication)
    sealed_accounts = {str(row["account_id"]): row for row in account_rows}
    sealed_instruments = {
        str(row["instrument_id"]): row for row in instrument_rows
    }
    if len(sealed_accounts) != len(account_rows) or len(sealed_instruments) != len(
        instrument_rows
    ):
        raise PublishedAccountsIntegrityError(
            "sealed account workspace inputs contain duplicate identities"
        )
    return sealed_accounts, sealed_instruments


def _transaction_counts_by_account(
    transactions: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    transaction_ids_by_account: dict[str, set[str]] = defaultdict(set)
    for transaction in transactions:
        transaction_id = str(transaction.get("transaction_id") or "")
        if not transaction_id:
            continue
        account_ids = {
            str(transaction.get("account_id") or ""),
            str(transaction.get("settlement_cash_account_id") or ""),
            str(transaction.get("counterparty_account_id") or ""),
        }
        account_ids.discard("")
        for account_id in account_ids:
            transaction_ids_by_account[account_id].add(transaction_id)
    return {
        account_id: len(transaction_ids)
        for account_id, transaction_ids in transaction_ids_by_account.items()
    }


def _published_rows_by_account(
    publication: CurrentPortfolioDailyPublication,
) -> tuple[
    dict[str, list[PortfolioDailyHolding]],
    dict[str, list[PortfolioDailyBalance]],
    dict[str, list[PortfolioDailyLot]],
]:
    holdings: dict[str, list[PortfolioDailyHolding]] = defaultdict(list)
    balances: dict[str, list[PortfolioDailyBalance]] = defaultdict(list)
    lots: dict[str, list[PortfolioDailyLot]] = defaultdict(list)
    for row in publication.holdings:
        holdings[row.account_id].append(row)
    for row in publication.balances:
        balances[row.account_id].append(row)
    for row in publication.lots:
        lots[row.account_id].append(row)
    return holdings, balances, lots


def _validate_output_accounts(
    publication: CurrentPortfolioDailyPublication,
    sealed_accounts: Mapping[str, RowMapping],
) -> None:
    for row in (*publication.holdings, *publication.balances, *publication.lots):
        account = sealed_accounts.get(row.account_id)
        if account is None:
            raise PublishedAccountsIntegrityError(
                f"published output account '{row.account_id}' is absent from sealed inputs"
            )
        if row.currency != str(account["currency"]):
            raise PublishedAccountsIntegrityError(
                f"published output currency differs from sealed account '{row.account_id}'"
            )


def _lots_by_position(
    publication: CurrentPortfolioDailyPublication,
) -> dict[tuple[str, str], tuple[PortfolioDailyLot, ...]]:
    grouped: dict[tuple[str, str], list[PortfolioDailyLot]] = defaultdict(list)
    for lot in publication.lots:
        grouped[(lot.account_id, lot.instrument_id)].append(lot)
    holding_keys = {
        (holding.account_id, holding.instrument_id)
        for holding in publication.holdings
    }
    orphaned = sorted(set(grouped) - holding_keys)
    if orphaned:
        raise PublishedAccountsIntegrityError(
            f"published lots have no matching holding: {orphaned}"
        )
    resolved: dict[tuple[str, str], tuple[PortfolioDailyLot, ...]] = {}
    for holding in publication.holdings:
        key = holding.account_id, holding.instrument_id
        rows = tuple(grouped.get(key, ()))
        if _exact_sum(tuple(row.open_quantity_exact for row in rows)) != (
            holding.quantity_exact
        ):
            raise PublishedAccountsIntegrityError(
                "published lot quantity does not close to holding quantity for "
                f"{holding.account_id}/{holding.instrument_id}"
            )
        resolved[key] = rows
    return resolved


def _position_records(
    publication: CurrentPortfolioDailyPublication,
    sealed_instruments: Mapping[str, RowMapping],
    lots_by_position: Mapping[tuple[str, str], tuple[PortfolioDailyLot, ...]],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for holding in sorted(
        publication.holdings,
        key=lambda row: (row.account_id, row.instrument_id),
    ):
        instrument = sealed_instruments.get(holding.instrument_id)
        if instrument is None:
            raise PublishedAccountsIntegrityError(
                f"published holding '{holding.instrument_id}' has no sealed instrument"
            )
        if holding.currency != str(instrument["currency"]):
            raise PublishedAccountsIntegrityError(
                f"published holding currency differs for '{holding.instrument_id}'"
            )
        lots = lots_by_position[(holding.account_id, holding.instrument_id)]
        measured_base_cost = all(lot.measured_base_cost for lot in lots)
        records.append(
            {
                "as_of_date": holding.as_of_date,
                "account_id": holding.account_id,
                "instrument_id": holding.instrument_id,
                "instrument_name": str(instrument["instrument_name"]),
                "instrument_type": str(instrument["instrument_type"]),
                "currency": holding.currency,
                "quantity_exact": holding.quantity_exact,
                "measured_price": holding.measured_price,
                "adopted_price_exact": holding.adopted_price_exact,
                "measured_market_value": holding.measured_market_value,
                "market_value_local_exact": holding.market_value_local_exact,
                "market_value_base_exact": holding.market_value_base_exact,
                "measured_base_cost": measured_base_cost,
                "cost_basis_local_exact": _exact_sum(
                    tuple(lot.cost_basis_local_exact for lot in lots)
                ),
                "cost_basis_base_exact": (
                    _optional_exact_sum(
                        tuple(lot.cost_basis_base_exact for lot in lots)
                    )
                    if measured_base_cost
                    else None
                ),
                "open_lot_count": len(lots),
                "valuation_coverage_state": holding.valuation_coverage_state,
                "valuation_reason_codes": list(
                    holding.valuation_coverage_reason_codes
                ),
                "base_cost_coverage_state": (
                    "complete" if measured_base_cost else "unavailable"
                ),
                "base_cost_reason_codes": _reason_codes(
                    *(lot.base_cost_reason_codes for lot in lots)
                ),
            }
        )
    return records


def _balance_records(
    publication: CurrentPortfolioDailyPublication,
) -> list[dict[str, object]]:
    return [
        {
            "as_of_date": row.as_of_date,
            "account_id": row.account_id,
            "component_type": row.component_type,
            "component_key": row.component_key,
            "currency": row.currency,
            "local_amount": row.local_amount,
            "measured_base_amount": row.measured_base_amount,
            "base_amount_exact": row.base_amount_exact,
            "coverage_state": row.coverage_state,
            "reason_codes": list(row.reason_codes),
        }
        for row in sorted(
            publication.balances,
            key=lambda item: (
                item.account_id,
                item.component_type,
                item.component_key,
                item.currency,
            ),
        )
    ]


def _component_local(
    balances: Sequence[PortfolioDailyBalance],
    component_types: frozenset[str],
) -> Decimal:
    return _exact_sum(
        tuple(
            row.local_amount
            for row in balances
            if row.component_type in component_types
        )
    )


def _component_base(
    balances: Sequence[PortfolioDailyBalance],
    component_types: frozenset[str],
) -> Decimal | None:
    rows = tuple(row for row in balances if row.component_type in component_types)
    if any(not row.measured_base_amount for row in rows):
        return None
    return _optional_exact_sum(tuple(row.base_amount_exact for row in rows))


def _account_value_base(
    *,
    position_market_value: Decimal | None,
    settled_cash: Decimal | None,
    pending_receivable: Decimal | None,
    pending_payable: Decimal | None,
    accrual_receivable: Decimal | None,
    accrual_payable: Decimal | None,
) -> Decimal | None:
    values = (
        position_market_value,
        settled_cash,
        pending_receivable,
        pending_payable,
        accrual_receivable,
        accrual_payable,
    )
    if any(value is None for value in values):
        return None
    measured = tuple(value for value in values if value is not None)
    return _exact_sum(
        (
            measured[0],
            measured[1],
            measured[2],
            exact_decimal_negate(measured[3]),
            measured[4],
            exact_decimal_negate(measured[5]),
        )
    )


def _account_record(
    *,
    account: Mapping[str, object],
    sealed: bool,
    holdings: Sequence[PortfolioDailyHolding],
    balances: Sequence[PortfolioDailyBalance],
    lots: Sequence[PortfolioDailyLot],
    linked_transaction_count: int,
    account_name_by_id: Mapping[str, str],
) -> dict[str, object]:
    if not sealed:
        return {
            "account": account,
            "default_settlement_cash_account_name": account_name_by_id.get(
                str(account.get("default_settlement_cash_account_id") or "")
            ),
            "linked_transaction_count": linked_transaction_count,
            "balance_component_count": 0,
            "position_line_count": 0,
            "open_lot_count": 0,
            "settled_cash_local": None,
            "settled_cash_base_exact": None,
            "pending_receivable_local": None,
            "pending_receivable_base_exact": None,
            "pending_payable_local": None,
            "pending_payable_base_exact": None,
            "accrual_receivable_local": None,
            "accrual_receivable_base_exact": None,
            "accrual_payable_local": None,
            "accrual_payable_base_exact": None,
            "position_market_value_local_exact": None,
            "position_market_value_base_exact": None,
            "cost_basis_local_exact": None,
            "cost_basis_base_exact": None,
            "account_value_base_exact": None,
            "valuation_coverage_state": "unavailable",
            "valuation_reason_codes": ["account_not_in_current_publication"],
            "cost_basis_coverage_state": "unavailable",
            "cost_basis_reason_codes": ["account_not_in_current_publication"],
        }

    settled_types = frozenset({"settled_cash"})
    pending_receivable_types = frozenset({"pending_receivable"})
    pending_payable_types = frozenset({"pending_payable"})
    accrual_receivable_types = frozenset({"income_accrual", "other_accrual"})
    accrual_payable_types = frozenset({"fee_accrual", "tax_accrual"})
    market_flags = tuple(row.measured_market_value for row in holdings)
    balance_flags = tuple(row.measured_base_amount for row in balances)
    measured_market = all(market_flags)
    position_market_local = (
        _optional_exact_sum(tuple(row.market_value_local_exact for row in holdings))
        if measured_market
        else None
    )
    position_market_base = (
        _optional_exact_sum(tuple(row.market_value_base_exact for row in holdings))
        if measured_market
        else None
    )
    measured_base_cost = all(row.measured_base_cost for row in lots)
    cost_basis_local = _exact_sum(tuple(row.cost_basis_local_exact for row in lots))
    cost_basis_base = (
        _optional_exact_sum(tuple(row.cost_basis_base_exact for row in lots))
        if measured_base_cost
        else None
    )

    settled_base = _component_base(balances, settled_types)
    pending_receivable_base = _component_base(
        balances, pending_receivable_types
    )
    pending_payable_base = _component_base(balances, pending_payable_types)
    accrual_receivable_base = _component_base(
        balances, accrual_receivable_types
    )
    accrual_payable_base = _component_base(balances, accrual_payable_types)
    account_value = _account_value_base(
        position_market_value=position_market_base,
        settled_cash=settled_base,
        pending_receivable=pending_receivable_base,
        pending_payable=pending_payable_base,
        accrual_receivable=accrual_receivable_base,
        accrual_payable=accrual_payable_base,
    )
    valuation_reasons = _reason_codes(
        *(row.valuation_coverage_reason_codes for row in holdings),
        *(row.reason_codes for row in balances),
    )
    cost_reasons = _reason_codes(*(row.base_cost_reason_codes for row in lots))
    return {
        "account": account,
        "default_settlement_cash_account_name": account_name_by_id.get(
            str(account.get("default_settlement_cash_account_id") or "")
        ),
        "linked_transaction_count": linked_transaction_count,
        "balance_component_count": len(balances),
        "position_line_count": len(holdings),
        "open_lot_count": len(lots),
        "settled_cash_local": _component_local(balances, settled_types),
        "settled_cash_base_exact": settled_base,
        "pending_receivable_local": _component_local(
            balances, pending_receivable_types
        ),
        "pending_receivable_base_exact": pending_receivable_base,
        "pending_payable_local": _component_local(
            balances, pending_payable_types
        ),
        "pending_payable_base_exact": pending_payable_base,
        "accrual_receivable_local": _component_local(
            balances, accrual_receivable_types
        ),
        "accrual_receivable_base_exact": accrual_receivable_base,
        "accrual_payable_local": _component_local(
            balances, accrual_payable_types
        ),
        "accrual_payable_base_exact": accrual_payable_base,
        "position_market_value_local_exact": position_market_local,
        "position_market_value_base_exact": position_market_base,
        "cost_basis_local_exact": cost_basis_local,
        "cost_basis_base_exact": cost_basis_base,
        "account_value_base_exact": account_value,
        "valuation_coverage_state": _coverage_state(
            (*market_flags, *balance_flags)
        ),
        "valuation_reason_codes": valuation_reasons,
        "cost_basis_coverage_state": (
            "complete" if measured_base_cost else "unavailable"
        ),
        "cost_basis_reason_codes": cost_reasons,
    }


def build_published_accounts_workspace(
    session: Session,
    publication: CurrentPortfolioDailyPublication,
    *,
    accounts: Sequence[Mapping[str, object]],
    transactions: Sequence[Mapping[str, object]],
    selected_account_id: str | None,
) -> dict[str, object]:
    """Build an exact current-publication projection for the Accounts page."""

    if len(publication.snapshots) != 1:
        raise PublishedAccountsIntegrityError(
            "account workspace requires exactly one published daily snapshot"
        )
    snapshot = publication.snapshots[0]
    sealed_accounts, sealed_instruments = _sealed_inputs(session, publication)
    _validate_output_accounts(publication, sealed_accounts)
    lots_by_position = _lots_by_position(publication)
    position_records = _position_records(
        publication,
        sealed_instruments,
        lots_by_position,
    )
    balance_records = _balance_records(publication)
    holdings_by_account, balances_by_account, lots_by_account = (
        _published_rows_by_account(publication)
    )
    account_name_by_id = {
        str(account["account_id"]): str(account["account_name"])
        for account in accounts
    }
    transaction_counts_by_account = _transaction_counts_by_account(transactions)
    account_rows = [
        _account_record(
            account=account,
            sealed=str(account["account_id"]) in sealed_accounts,
            holdings=tuple(holdings_by_account.get(str(account["account_id"]), ())),
            balances=tuple(balances_by_account.get(str(account["account_id"]), ())),
            lots=tuple(lots_by_account.get(str(account["account_id"]), ())),
            linked_transaction_count=transaction_counts_by_account.get(
                str(account["account_id"]), 0
            ),
            account_name_by_id=account_name_by_id,
        )
        for account in accounts
    ]
    resolved_selected = selected_account_id or (
        str(accounts[0]["account_id"]) if accounts else None
    )
    if resolved_selected is not None and resolved_selected not in account_name_by_id:
        raise PublishedAccountsIntegrityError(
            f"selected account '{resolved_selected}' is not configured"
        )
    selected_positions = [
        row for row in position_records if row["account_id"] == resolved_selected
    ]
    selected_balances = [
        row for row in balance_records if row["account_id"] == resolved_selected
    ]
    return {
        "base_currency": snapshot.base_currency,
        "as_of_date": snapshot.as_of_date,
        "summary": {
            "account_count": len(account_rows),
            "deposit_account_count": sum(
                1
                for row in accounts
                if row.get("account_type") == "deposit_account"
            ),
            "securities_account_count": sum(
                1
                for row in accounts
                if row.get("account_type") == "securities_account"
            ),
            "balance_component_count": len(publication.balances),
            "position_line_count": len(publication.holdings),
            "open_lot_count": len(publication.lots),
            "valuation_coverage_state": _aggregate_coverage_states(
                tuple(str(row["valuation_coverage_state"]) for row in account_rows)
            ),
        },
        "selected_account_id": resolved_selected,
        "accounts": account_rows,
        "balances": selected_balances,
        "positions": selected_positions,
    }


__all__ = [
    "PublishedAccountsIntegrityError",
    "build_published_accounts_workspace",
]
