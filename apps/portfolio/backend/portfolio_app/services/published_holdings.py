"""Strict holdings projection from the current Portfolio Daily publication.

This service never rebuilds valuation from mutable live facts.  It reads one
fenced immutable publication, joins its sealed instrument/account inputs, and
fails closed when the requested date is outside that publication or when a
newer source generation is pending.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from portfolio_app.calculations.numeric import (
    exact_decimal_subtract,
    exact_decimal_sum,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_account_input,
    portfolio_daily_config_input,
    portfolio_daily_instrument_input,
)
from portfolio_app.calculations.portfolio_daily.published_repository import (
    CurrentPortfolioDailyPublication,
    PortfolioDailyCurrentPublicationChanged,
    PortfolioDailyHolding,
    PortfolioDailyLot,
    PortfolioDailyPublishedReadIntegrityError,
    read_current_portfolio_daily_metadata,
    read_latest_current_portfolio_daily_publication,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.fact_currency import require_portfolio_fact_currency


_CASH_ACCOUNT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "bank",
        "cash",
        "custody_cash",
        "deposit",
        "deposit_account",
        "settlement_cash",
    }
)


class PublishedHoldingsReadError(RuntimeError):
    """Base class for a strict published-holdings read failure."""


class PublishedHoldingsUnavailableError(PublishedHoldingsReadError, ValueError):
    """The requested current published valuation is not ready for use."""

    def __init__(
        self,
        *,
        portfolio_id: str,
        as_of_date: date,
        reason_codes: Sequence[str],
    ) -> None:
        resolved_reasons = tuple(dict.fromkeys(str(value) for value in reason_codes))
        if not resolved_reasons or any(not value for value in resolved_reasons):
            raise ValueError("Published holdings reason_codes must be non-empty strings.")
        self.portfolio_id = portfolio_id
        self.as_of_date = as_of_date
        self.reason_codes = resolved_reasons
        super().__init__(
            "Published holdings are unavailable for "
            f"{portfolio_id}/{as_of_date.isoformat()}: "
            f"{', '.join(resolved_reasons)}."
        )


class PublishedHoldingsIntegrityError(PublishedHoldingsReadError):
    """A fenced publication does not satisfy the holdings projection contract."""


@dataclass(frozen=True, slots=True)
class PublishedHoldingPosition:
    instrument_id: str
    instrument_name: str
    instrument_type: str
    currency: str
    quantity_exact: Decimal
    adopted_price_exact: Decimal | None
    market_value_local_exact: Decimal | None
    market_value_base_exact: Decimal | None
    cost_basis_local_exact: Decimal
    cost_basis_base_exact: Decimal | None
    portfolio_weight: Decimal | None
    account_ids: tuple[str, ...]
    valuation_coverage_state: str
    valuation_reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublishedCashAccountValue:
    account_id: str
    account_name: str
    account_type: str
    currency: str
    value_base_exact: Decimal | None
    coverage_state: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublishedHoldingsStatement:
    portfolio_id: str
    base_currency: str
    valuation_timezone: str
    as_of_date: date
    publication_id: UUID
    run_id: UUID
    manifest_id: UUID
    captured_generation: int
    positions: tuple[PublishedHoldingPosition, ...]
    cash_accounts: tuple[PublishedCashAccountValue, ...]
    position_market_value_base_exact: Decimal | None
    settled_cash_base_exact: Decimal | None
    pending_settlement_base_exact: Decimal | None
    total_nav_base: Decimal | None
    nav_coverage_state: str
    nav_reason_codes: tuple[str, ...]


def _unique_reason_codes(rows: Sequence[object], field_name: str) -> tuple[str, ...]:
    reasons: list[str] = []
    for row in rows:
        for reason in cast(Sequence[str], getattr(row, field_name)):
            if reason not in reasons:
                reasons.append(reason)
    return tuple(reasons)


def read_current_published_holdings_as_of_date(portfolio_id: str) -> date:
    """Return the latest date covered by the fenced current publication."""

    normalized_portfolio_id = str(portfolio_id or "").strip()
    if not normalized_portfolio_id:
        raise ValueError("portfolio_id is required")

    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            metadata = read_current_portfolio_daily_metadata(
                session,
                portfolio_id=normalized_portfolio_id,
            )
        except PortfolioDailyCurrentPublicationChanged as error:
            raise PublishedHoldingsReadError(
                "The current Portfolio Daily publication changed while resolving "
                "the holdings as-of date; retry the read."
            ) from error
        except PortfolioDailyPublishedReadIntegrityError as error:
            raise PublishedHoldingsIntegrityError(
                "The current Portfolio Daily publication failed metadata integrity checks."
            ) from error
    if metadata is None:
        raise ValueError(
            "Current published holdings are unavailable for "
            f"'{normalized_portfolio_id}': current_publication_missing."
        )
    return metadata.output_range_end


def _merge_coverage_states(rows: Sequence[object], field_name: str) -> str:
    states = tuple(str(getattr(row, field_name)) for row in rows)
    if states and all(value == "complete" for value in states):
        return "complete"
    if states and all(value == "unavailable" for value in states):
        return "unavailable"
    return "partial"


def _optional_exact_sum(values: Sequence[Decimal | None]) -> Decimal | None:
    resolved = tuple(values)
    if any(value is None for value in resolved):
        return None
    return exact_decimal_sum(tuple(cast(Decimal, value) for value in resolved))


def _require_one_value(
    values: Sequence[Decimal | None],
    *,
    field_name: str,
    instrument_id: str,
) -> Decimal | None:
    unique = frozenset(values)
    if len(unique) > 1:
        raise PublishedHoldingsIntegrityError(
            f"Published {field_name} differs across accounts for instrument "
            f"'{instrument_id}'."
        )
    return next(iter(unique)) if unique else None


def _frozen_inputs(
    session: Session,
    publication: CurrentPortfolioDailyPublication,
) -> tuple[RowMapping, tuple[RowMapping, ...], tuple[RowMapping, ...]]:
    metadata = publication.metadata
    config = session.execute(
        select(
            portfolio_daily_config_input.c.manifest_id,
            portfolio_daily_config_input.c.run_id,
            portfolio_daily_config_input.c.portfolio_id,
            portfolio_daily_config_input.c.base_currency,
        ).where(portfolio_daily_config_input.c.manifest_id == metadata.manifest_id)
    ).mappings().one_or_none()
    if config is None:
        raise PublishedHoldingsIntegrityError(
            "The current Portfolio Daily publication has no sealed config input."
        )

    instruments = tuple(
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
    accounts = tuple(
        session.execute(
            select(
                portfolio_daily_account_input.c.manifest_id,
                portfolio_daily_account_input.c.run_id,
                portfolio_daily_account_input.c.portfolio_id,
                portfolio_daily_account_input.c.account_id,
                portfolio_daily_account_input.c.account_name,
                portfolio_daily_account_input.c.account_type,
                portfolio_daily_account_input.c.currency,
            ).where(
                portfolio_daily_account_input.c.manifest_id == metadata.manifest_id
            )
        ).mappings()
    )
    for row in (config, *instruments, *accounts):
        if (
            row["manifest_id"] != metadata.manifest_id
            or row["run_id"] != metadata.run_id
            or row["portfolio_id"] != metadata.portfolio_id
        ):
            raise PublishedHoldingsIntegrityError(
                "A sealed Portfolio Daily input has inconsistent publication identity."
            )
    return config, instruments, accounts


def _position_lots(
    holdings: Sequence[PortfolioDailyHolding],
    lots: Sequence[PortfolioDailyLot],
) -> dict[tuple[str, str], tuple[PortfolioDailyLot, ...]]:
    grouped: dict[tuple[str, str], list[PortfolioDailyLot]] = defaultdict(list)
    for lot in lots:
        grouped[(lot.account_id, lot.instrument_id)].append(lot)
    expected_keys = {(row.account_id, row.instrument_id) for row in holdings}
    unexpected = sorted(set(grouped) - expected_keys)
    if unexpected:
        raise PublishedHoldingsIntegrityError(
            "The current publication contains lots without a matching holding: "
            f"{unexpected}."
        )
    resolved: dict[tuple[str, str], tuple[PortfolioDailyLot, ...]] = {}
    for holding in holdings:
        key = holding.account_id, holding.instrument_id
        rows = tuple(grouped.get(key, ()))
        if exact_decimal_sum(tuple(row.open_quantity_exact for row in rows)) != holding.quantity_exact:
            raise PublishedHoldingsIntegrityError(
                "Published lot quantity does not close to holding quantity for "
                f"{holding.account_id}/{holding.instrument_id}."
            )
        resolved[key] = rows
    return resolved


def _positions(
    publication: CurrentPortfolioDailyPublication,
    instrument_rows: Sequence[Mapping[str, object]],
) -> tuple[PublishedHoldingPosition, ...]:
    instruments = {
        str(row["instrument_id"]): row
        for row in instrument_rows
    }
    lots_by_position = _position_lots(publication.holdings, publication.lots)
    grouped: dict[str, list[PortfolioDailyHolding]] = defaultdict(list)
    for holding in publication.holdings:
        grouped[holding.instrument_id].append(holding)

    positions: list[PublishedHoldingPosition] = []
    for instrument_id, holding_rows in sorted(grouped.items()):
        instrument = instruments.get(instrument_id)
        if instrument is None:
            raise PublishedHoldingsIntegrityError(
                f"Published holding '{instrument_id}' has no sealed instrument input."
            )
        currency = require_portfolio_fact_currency(
            instrument["currency"],
            context=f"published instrument '{instrument_id}'",
        )
        if any(row.currency != currency for row in holding_rows):
            raise PublishedHoldingsIntegrityError(
                f"Published holding currency differs from the sealed instrument '{instrument_id}'."
            )

        position_lots = tuple(
            lot
            for holding in holding_rows
            for lot in lots_by_position[(holding.account_id, holding.instrument_id)]
        )
        base_cost = _optional_exact_sum(
            tuple(
                lot.cost_basis_base_exact if lot.measured_base_cost else None
                for lot in position_lots
            )
        )
        market_local = _optional_exact_sum(
            tuple(
                row.market_value_local_exact if row.measured_market_value else None
                for row in holding_rows
            )
        )
        market_base = _optional_exact_sum(
            tuple(
                row.market_value_base_exact if row.measured_market_value else None
                for row in holding_rows
            )
        )
        positions.append(
            PublishedHoldingPosition(
                instrument_id=instrument_id,
                instrument_name=str(instrument["instrument_name"]),
                instrument_type=str(instrument["instrument_type"]),
                currency=currency,
                quantity_exact=exact_decimal_sum(
                    tuple(row.quantity_exact for row in holding_rows)
                ),
                adopted_price_exact=_require_one_value(
                    tuple(row.adopted_price_exact for row in holding_rows),
                    field_name="price",
                    instrument_id=instrument_id,
                ),
                market_value_local_exact=market_local,
                market_value_base_exact=market_base,
                cost_basis_local_exact=exact_decimal_sum(
                    tuple(lot.cost_basis_local_exact for lot in position_lots)
                ),
                cost_basis_base_exact=base_cost,
                portfolio_weight=_optional_exact_sum(
                    tuple(row.portfolio_weight for row in holding_rows)
                ),
                account_ids=tuple(sorted({row.account_id for row in holding_rows})),
                valuation_coverage_state=_merge_coverage_states(
                    holding_rows,
                    "valuation_coverage_state",
                ),
                valuation_reason_codes=_unique_reason_codes(
                    holding_rows,
                    "valuation_coverage_reason_codes",
                ),
            )
        )
    return tuple(positions)


def _cash_accounts(
    publication: CurrentPortfolioDailyPublication,
    account_rows: Sequence[Mapping[str, object]],
) -> tuple[PublishedCashAccountValue, ...]:
    frozen_accounts = {str(row["account_id"]): row for row in account_rows}
    settled_by_account: dict[str, list[object]] = defaultdict(list)
    for balance in publication.balances:
        if balance.component_type == "settled_cash":
            settled_by_account[balance.account_id].append(balance)
    account_ids = {
        account_id
        for account_id, row in frozen_accounts.items()
        if str(row["account_type"]).strip().lower() in _CASH_ACCOUNT_TYPES
    } | set(settled_by_account)

    rendered: list[PublishedCashAccountValue] = []
    for account_id in sorted(account_ids):
        account = frozen_accounts.get(account_id)
        if account is None:
            raise PublishedHoldingsIntegrityError(
                f"Published cash balance '{account_id}' has no sealed account input."
            )
        balances = tuple(settled_by_account.get(account_id, ()))
        value = _optional_exact_sum(
            tuple(
                cast(Decimal | None, row.base_amount_exact)
                if row.measured_base_amount
                else None
                for row in balances
            )
        )
        rendered.append(
            PublishedCashAccountValue(
                account_id=account_id,
                account_name=str(account["account_name"]),
                account_type=str(account["account_type"]),
                currency=require_portfolio_fact_currency(
                    account["currency"],
                    context=f"published account '{account_id}'",
                ),
                value_base_exact=value,
                coverage_state=(
                    _merge_coverage_states(balances, "coverage_state")
                    if balances
                    else "complete"
                ),
                reason_codes=(
                    _unique_reason_codes(balances, "reason_codes")
                    if balances
                    else ()
                ),
            )
        )
    return tuple(rendered)


def _pending_settlement(
    publication: CurrentPortfolioDailyPublication,
) -> Decimal | None:
    receivables: list[Decimal | None] = []
    payables: list[Decimal | None] = []
    for row in publication.balances:
        if row.component_type not in {"pending_receivable", "pending_payable"}:
            continue
        value = row.base_amount_exact if row.measured_base_amount else None
        if row.component_type == "pending_receivable":
            receivables.append(value)
        else:
            payables.append(value)
    receivable = _optional_exact_sum(receivables)
    payable = _optional_exact_sum(payables)
    if receivable is None or payable is None:
        return None
    return exact_decimal_subtract(receivable, payable)


def _build_statement(
    publication: CurrentPortfolioDailyPublication,
    *,
    config: Mapping[str, object],
    instrument_rows: Sequence[Mapping[str, object]],
    account_rows: Sequence[Mapping[str, object]],
) -> PublishedHoldingsStatement:
    if len(publication.snapshots) != 1:
        raise PublishedHoldingsIntegrityError(
            "A single-date holdings read requires exactly one published snapshot."
        )
    snapshot = publication.snapshots[0]
    if (
        snapshot.as_of_date != publication.requested_range_start
        or snapshot.as_of_date != publication.requested_range_end
    ):
        raise PublishedHoldingsIntegrityError(
            "Published holdings snapshot date differs from the requested boundary."
        )
    base_currency = require_portfolio_fact_currency(
        config["base_currency"],
        context="published Portfolio Daily config",
    )
    if snapshot.base_currency != base_currency:
        raise PublishedHoldingsIntegrityError(
            "Published snapshot base currency differs from the sealed config."
        )
    positions = _positions(publication, instrument_rows)
    cash_accounts = _cash_accounts(publication, account_rows)
    return PublishedHoldingsStatement(
        portfolio_id=publication.metadata.portfolio_id,
        base_currency=base_currency,
        valuation_timezone=publication.metadata.timezone_name,
        as_of_date=snapshot.as_of_date,
        publication_id=publication.metadata.publication_id,
        run_id=publication.metadata.run_id,
        manifest_id=publication.metadata.manifest_id,
        captured_generation=publication.metadata.captured_generation,
        positions=positions,
        cash_accounts=cash_accounts,
        position_market_value_base_exact=_optional_exact_sum(
            tuple(row.market_value_base_exact for row in positions)
        ),
        settled_cash_base_exact=_optional_exact_sum(
            tuple(row.value_base_exact for row in cash_accounts)
        ),
        pending_settlement_base_exact=_pending_settlement(publication),
        total_nav_base=snapshot.closing_nav,
        nav_coverage_state=snapshot.nav_coverage_state,
        nav_reason_codes=snapshot.nav_reason_codes,
    )


def read_current_published_holdings(
    portfolio_id: str,
    *,
    as_of_date: date,
) -> PublishedHoldingsStatement:
    """Return one exact current published holdings statement or fail closed."""

    normalized_portfolio_id = str(portfolio_id or "").strip()
    if not normalized_portfolio_id:
        raise ValueError("portfolio_id is required")
    if isinstance(as_of_date, datetime) or not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date, not datetime")

    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            publication = read_latest_current_portfolio_daily_publication(
                session,
                portfolio_id=normalized_portfolio_id,
                as_of_date=as_of_date,
                tables=("snapshots", "holdings", "balances", "lots"),
            )
        except PortfolioDailyCurrentPublicationChanged as error:
            raise PublishedHoldingsUnavailableError(
                portfolio_id=normalized_portfolio_id,
                as_of_date=as_of_date,
                reason_codes=("publication_changed_retry",),
            ) from error
        except PortfolioDailyPublishedReadIntegrityError as error:
            raise PublishedHoldingsIntegrityError(
                "The current Portfolio Daily publication failed repository integrity checks."
            ) from error

        if publication is None:
            raise PublishedHoldingsUnavailableError(
                portfolio_id=normalized_portfolio_id,
                as_of_date=as_of_date,
                reason_codes=("current_publication_missing",),
            )
        metadata = publication.metadata
        unavailable_reasons: list[str] = []
        if not metadata.output_range_start <= as_of_date <= metadata.output_range_end:
            unavailable_reasons.append("requested_as_of_not_published")
        if metadata.stale:
            unavailable_reasons.append("current_publication_stale")
        if metadata.pending:
            unavailable_reasons.append("newer_calculation_pending")
        if unavailable_reasons:
            raise PublishedHoldingsUnavailableError(
                portfolio_id=normalized_portfolio_id,
                as_of_date=as_of_date,
                reason_codes=unavailable_reasons,
            )

        config, instruments, accounts = _frozen_inputs(session, publication)
        statement = _build_statement(
            publication,
            config=config,
            instrument_rows=instruments,
            account_rows=accounts,
        )
        try:
            current_metadata = read_current_portfolio_daily_metadata(
                session,
                portfolio_id=normalized_portfolio_id,
            )
        except PortfolioDailyCurrentPublicationChanged as error:
            raise PublishedHoldingsUnavailableError(
                portfolio_id=normalized_portfolio_id,
                as_of_date=as_of_date,
                reason_codes=("publication_changed_retry",),
            ) from error
        except PortfolioDailyPublishedReadIntegrityError as error:
            raise PublishedHoldingsIntegrityError(
                "The final Portfolio Daily pointer confirmation failed integrity checks."
            ) from error
        if (
            current_metadata is None
            or current_metadata.publication_id != metadata.publication_id
            or current_metadata.run_id != metadata.run_id
            or current_metadata.manifest_id != metadata.manifest_id
        ):
            raise PublishedHoldingsUnavailableError(
                portfolio_id=normalized_portfolio_id,
                as_of_date=as_of_date,
                reason_codes=("publication_changed_retry",),
            )
        final_reasons: list[str] = []
        if current_metadata.stale:
            final_reasons.append("current_publication_stale")
        if current_metadata.pending:
            final_reasons.append("newer_calculation_pending")
        if final_reasons:
            raise PublishedHoldingsUnavailableError(
                portfolio_id=normalized_portfolio_id,
                as_of_date=as_of_date,
                reason_codes=final_reasons,
            )
        return statement


__all__ = [
    "PublishedCashAccountValue",
    "PublishedHoldingPosition",
    "PublishedHoldingsIntegrityError",
    "PublishedHoldingsReadError",
    "PublishedHoldingsStatement",
    "PublishedHoldingsUnavailableError",
    "read_current_published_holdings",
    "read_current_published_holdings_as_of_date",
]
