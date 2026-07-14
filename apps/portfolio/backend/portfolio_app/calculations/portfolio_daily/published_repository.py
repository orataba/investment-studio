"""Read-only access to the currently published Portfolio Daily attempt.

The current-publication pointer is the only supported read entry point.  A
``portfolio_id`` and date range are not sufficient identities because output
rows from abandoned worker attempts deliberately remain in storage.  Every
query in this module therefore joins the registry pointer to its immutable
publication and constrains both ``run_id`` and ``published_fencing_token``.

All functions run in the caller's transaction.  They never begin, commit,
roll back, or materialize calculations on a read path.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, Literal, TypeVar, cast
from uuid import UUID

from sqlalchemy import Table, and_, func, select
from sqlalchemy.engine import Connection, Result, RowMapping
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select
from sqlalchemy.sql.sqltypes import Numeric

from portfolio_app.calculations.portfolio_daily.constants import (
    CALCULATION_KIND,
    SCOPE_KIND,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_balance_output,
    portfolio_daily_contribution_output,
    portfolio_daily_holding_output,
    portfolio_daily_lot_disposition_output,
    portfolio_daily_lot_output,
    portfolio_daily_run_output,
    portfolio_daily_snapshot_output,
)
from portfolio_ops_calculation_core.models import (
    CalculationCurrentPublication,
    CalculationInputManifest,
    CalculationPublication,
    CalculationRecomputeIntent,
    CalculationRun,
    CalculationScopeGeneration,
)
from portfolio_ops_calculation_core.state import CalculationRunStatus


type PublishedReadExecutor = Connection | Session
type CoverageState = Literal["complete", "partial", "unavailable"]
type ValuationEndpointStatus = Literal[
    "fresh", "carry_forward", "stale", "unavailable"
]


_current_publication = cast(Table, CalculationCurrentPublication.__table__)
_publication = cast(Table, CalculationPublication.__table__)
_run = cast(Table, CalculationRun.__table__)
_manifest = cast(Table, CalculationInputManifest.__table__)
_generation = cast(Table, CalculationScopeGeneration.__table__)
_recompute_intent = cast(Table, CalculationRecomputeIntent.__table__)
_pending_run_ranked = (
    select(
        _run.c.run_id,
        _run.c.calculation_kind,
        _run.c.scope_kind,
        _run.c.scope_id,
        _run.c.captured_generation,
        _run.c.status,
        func.row_number()
        .over(
            partition_by=(
                _run.c.calculation_kind,
                _run.c.scope_kind,
                _run.c.scope_id,
            ),
            order_by=(
                _run.c.captured_generation.desc(),
                _run.c.created_at.desc(),
                _run.c.run_id.desc(),
            ),
        )
        .label("freshness_rank"),
    )
    .where(_run.c.status.in_(("capturing", "queued", "running", "succeeded")))
    .subquery("pending_portfolio_daily_run")
)
_pending_intent_ranked = (
    select(
        _recompute_intent.c.intent_id,
        _recompute_intent.c.calculation_kind,
        _recompute_intent.c.scope_kind,
        _recompute_intent.c.scope_id,
        _recompute_intent.c.requested_generation,
        _recompute_intent.c.status,
        func.row_number()
        .over(
            partition_by=(
                _recompute_intent.c.calculation_kind,
                _recompute_intent.c.scope_kind,
                _recompute_intent.c.scope_id,
            ),
            order_by=(
                _recompute_intent.c.requested_generation.desc(),
                _recompute_intent.c.created_at.desc(),
                _recompute_intent.c.intent_id.desc(),
            ),
        )
        .label("freshness_rank"),
    )
    .where(_recompute_intent.c.status == "pending")
    .subquery("pending_portfolio_daily_intent")
)

type PortfolioDailyPublishedTable = Literal[
    "snapshots",
    "holdings",
    "balances",
    "lots",
    "lot_dispositions",
    "contributions",
]

_ALL_PUBLISHED_TABLES: Final[frozenset[PortfolioDailyPublishedTable]] = frozenset(
    {
        "snapshots",
        "holdings",
        "balances",
        "lots",
        "lot_dispositions",
        "contributions",
    }
)


class PortfolioDailyPublishedReadError(RuntimeError):
    """Base class for publication-read contract failures."""


class PortfolioDailyPublishedReadIntegrityError(PortfolioDailyPublishedReadError):
    """The registry pointer and immutable output identity are inconsistent."""


class PortfolioDailyCurrentPublicationChanged(PortfolioDailyPublishedReadError):
    """The current pointer changed while a multi-table result was being read."""

    def __init__(
        self,
        *,
        expected_publication_id: UUID,
        actual_publication_id: UUID | None,
    ) -> None:
        self.expected_publication_id = expected_publication_id
        self.actual_publication_id = actual_publication_id
        super().__init__(
            "Portfolio Daily current publication changed during read: "
            f"expected={expected_publication_id}, actual={actual_publication_id}"
        )


@dataclass(frozen=True, slots=True)
class PortfolioDailyPublishedOutputIdentity:
    run_id: UUID
    output_fencing_token: int
    worker_id: str
    portfolio_id: str
    calculated_at: datetime


@dataclass(frozen=True, slots=True)
class PortfolioDailySnapshot(PortfolioDailyPublishedOutputIdentity):
    as_of_date: date
    base_currency: str
    measured_nav: bool
    measured_position_market_value: bool
    measured_book_pnl: bool
    measured_return: bool
    measured_external_flows: bool
    unavailable_component_count: int
    opening_nav: Decimal | None
    closing_nav: Decimal | None
    position_market_value: Decimal | None
    settled_cash: Decimal | None
    pending_receivable: Decimal | None
    pending_payable: Decimal | None
    accrual_receivable: Decimal | None
    accrual_payable: Decimal | None
    external_flow_in: Decimal | None
    external_flow_out: Decimal | None
    economic_pnl: Decimal | None
    realized_pnl_daily: Decimal | None
    unrealized_pnl_beginning: Decimal | None
    unrealized_pnl_ending: Decimal | None
    unrealized_pnl_change: Decimal | None
    gross_income_daily: Decimal | None
    return_of_capital_daily: Decimal | None
    capitalized_fee_daily: Decimal | None
    capitalized_tax_daily: Decimal | None
    expensed_fee_daily: Decimal | None
    expensed_tax_daily: Decimal | None
    disposal_fee_in_realized_daily: Decimal | None
    disposal_tax_in_realized_daily: Decimal | None
    local_price_effect_daily: Decimal | None
    position_fx_effect_daily: Decimal | None
    position_attribution_residual_exact: Decimal | None
    position_attribution_rounding_adjustment: Decimal
    cash_fx_effect_daily: Decimal | None
    pending_fx_effect_daily: Decimal | None
    accrual_fx_effect_daily: Decimal | None
    fx_conversion_effect_daily: Decimal | None
    pnl_component_rounding_adjustment: Decimal
    subperiod_twr_method50: Decimal | None
    subperiod_twr_published: Decimal | None
    cumulative_twr_method50: Decimal | None
    cumulative_twr_published: Decimal | None
    wealth_index_method50: Decimal | None
    wealth_index_published: Decimal | None
    peak_wealth_index_method50: Decimal | None
    peak_wealth_index_published: Decimal | None
    drawdown_method50: Decimal | None
    drawdown_published: Decimal | None
    wealth_chain_rounding_adjustment_exact: Decimal | None
    reliable_anchor_date: date | None
    reliable_anchor_nav_exact: Decimal | None
    reliable_anchor_nav: Decimal | None
    return_period_start_date: date | None
    return_period_end_date: date | None
    return_period_day_count: int | None
    calculation_status: str
    return_chain_status: str
    nav_rounding_adjustment: Decimal
    pnl_rounding_adjustment: Decimal
    nav_coverage_state: CoverageState
    nav_reason_codes: tuple[str, ...]
    book_pnl_coverage_state: CoverageState
    book_pnl_reason_codes: tuple[str, ...]
    return_coverage_state: CoverageState
    return_reason_codes: tuple[str, ...]
    flow_coverage_state: CoverageState
    flow_reason_codes: tuple[str, ...]
    position_attribution_coverage_state: CoverageState
    position_attribution_reason_codes: tuple[str, ...]
    valuation_endpoint_status: ValuationEndpointStatus
    valuation_reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PortfolioDailyHolding(PortfolioDailyPublishedOutputIdentity):
    as_of_date: date
    account_id: str
    instrument_id: str
    currency: str
    quantity_exact: Decimal
    quantity: Decimal
    measured_price: bool
    measured_market_value: bool
    measured_book_pnl: bool
    unavailable_component_count: int
    adopted_price_exact: Decimal | None
    price: Decimal | None
    contract_multiplier_exact: Decimal | None
    contract_multiplier: Decimal | None
    price_factor_exact: Decimal | None
    price_factor: Decimal | None
    adopted_fx_rate_exact: Decimal | None
    fx_rate_to_base: Decimal | None
    market_value_local_exact: Decimal | None
    market_value_base_exact: Decimal | None
    market_value_local: Decimal | None
    market_value_base: Decimal | None
    cost_basis_local: Decimal | None
    cost_basis_base: Decimal | None
    economic_pnl_daily_base: Decimal | None
    realized_pnl_daily_base: Decimal | None
    unrealized_pnl_beginning_base: Decimal | None
    unrealized_pnl_ending_base: Decimal | None
    unrealized_pnl_change_base: Decimal | None
    gross_income_daily_base: Decimal | None
    return_of_capital_daily_base: Decimal | None
    capitalized_fee_daily_base: Decimal | None
    capitalized_tax_daily_base: Decimal | None
    expensed_fee_daily_base: Decimal | None
    expensed_tax_daily_base: Decimal | None
    disposal_fee_in_realized_daily_base: Decimal | None
    disposal_tax_in_realized_daily_base: Decimal | None
    local_price_effect_daily_base: Decimal | None
    position_fx_effect_daily_base: Decimal | None
    fx_conversion_effect_daily_base: Decimal | None
    pnl_component_rounding_adjustment_base: Decimal
    position_attribution_residual_exact: Decimal | None
    position_attribution_rounding_adjustment_base: Decimal
    portfolio_weight: Decimal | None
    return_contribution: Decimal | None
    local_rounding_adjustment: Decimal
    base_rounding_adjustment: Decimal
    valuation_coverage_state: CoverageState
    valuation_coverage_reason_codes: tuple[str, ...]
    book_pnl_coverage_state: CoverageState
    book_pnl_reason_codes: tuple[str, ...]
    position_attribution_coverage_state: CoverageState
    position_attribution_reason_codes: tuple[str, ...]
    valuation_endpoint_status: ValuationEndpointStatus
    valuation_reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PortfolioDailyBalance(PortfolioDailyPublishedOutputIdentity):
    as_of_date: date
    account_id: str
    component_type: str
    component_key: str
    currency: str
    measured_base_amount: bool
    local_amount: Decimal
    adopted_fx_rate_exact: Decimal | None
    fx_rate_to_base: Decimal | None
    base_amount_exact: Decimal | None
    base_amount: Decimal | None
    base_rounding_adjustment: Decimal
    coverage_state: CoverageState
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PortfolioDailyLot(PortfolioDailyPublishedOutputIdentity):
    as_of_date: date
    account_id: str
    instrument_id: str
    lot_id: str
    source_transaction_id: str
    source_revision_id: str
    source_revision_number: int
    custody_transaction_id: str
    custody_revision_id: str
    custody_revision_number: int
    acquisition_date: date
    currency: str
    open_quantity_exact: Decimal
    open_quantity: Decimal
    measured_base_cost: bool
    acquisition_fx_rate_exact: Decimal | None
    acquisition_fx_rate: Decimal | None
    cost_basis_local_exact: Decimal
    cost_basis_local: Decimal
    unit_cost_local: Decimal
    unit_cost_local_rounding_residual_exact: Decimal
    cost_basis_base_exact: Decimal | None
    cost_basis_base: Decimal | None
    unit_cost_base: Decimal | None
    unit_cost_base_rounding_residual_exact: Decimal | None
    local_cost_rounding_adjustment: Decimal
    base_cost_rounding_adjustment: Decimal | None
    base_cost_coverage_state: Literal["complete", "unavailable"]
    base_cost_reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PortfolioDailyLotDisposition(PortfolioDailyPublishedOutputIdentity):
    as_of_date: date
    account_id: str
    instrument_id: str
    lot_id: str
    acquisition_transaction_id: str
    acquisition_revision_id: str
    acquisition_revision_number: int
    custody_transaction_id: str
    custody_revision_id: str
    custody_revision_number: int
    match_sequence: int
    disposition_transaction_id: str
    disposition_revision_id: str
    disposition_revision_number: int
    disposition_date: date
    disposition_kind: str
    matching_method: str
    matching_policy_version: str
    currency: str
    disposed_quantity_exact: Decimal
    disposed_quantity: Decimal
    proceeds_local_exact: Decimal
    proceeds_local: Decimal
    allocated_cost_local_exact: Decimal
    allocated_cost_local: Decimal
    realized_pnl_local_exact: Decimal
    realized_pnl_local: Decimal
    proceeds_local_rounding_adjustment: Decimal
    allocated_cost_local_rounding_adjustment: Decimal
    realized_pnl_local_rounding_adjustment: Decimal
    measured_base_pnl: bool
    disposition_fx_rate_exact: Decimal | None
    disposition_fx_rate: Decimal | None
    proceeds_base_exact: Decimal | None
    proceeds_base: Decimal | None
    allocated_cost_base_exact: Decimal | None
    allocated_cost_base: Decimal | None
    realized_pnl_base_exact: Decimal | None
    realized_pnl_base: Decimal | None
    proceeds_base_rounding_adjustment: Decimal | None
    allocated_cost_base_rounding_adjustment: Decimal | None
    realized_pnl_base_rounding_adjustment: Decimal | None
    base_pnl_coverage_state: Literal["complete", "unavailable"]
    base_pnl_reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PortfolioDailyContribution(PortfolioDailyPublishedOutputIdentity):
    as_of_date: date
    axis: Literal["portfolio", "account", "instrument", "currency", "taxonomy"]
    group_key: str
    group_label: str
    measured: bool
    opening_nav_exact: Decimal | None
    opening_nav: Decimal | None
    opening_nav_rounding_adjustment: Decimal | None
    closing_nav_exact: Decimal | None
    closing_nav: Decimal | None
    closing_nav_rounding_adjustment: Decimal | None
    external_flow_in_exact: Decimal | None
    external_flow_in: Decimal | None
    external_flow_in_rounding_adjustment: Decimal | None
    external_flow_out_exact: Decimal | None
    external_flow_out: Decimal | None
    external_flow_out_rounding_adjustment: Decimal | None
    internal_flow_in_exact: Decimal | None
    internal_flow_in: Decimal | None
    internal_flow_in_rounding_adjustment: Decimal | None
    internal_flow_out_exact: Decimal | None
    internal_flow_out: Decimal | None
    internal_flow_out_rounding_adjustment: Decimal | None
    economic_pnl_exact: Decimal | None
    economic_pnl: Decimal | None
    economic_pnl_rounding_adjustment: Decimal | None
    contribution_method50: Decimal | None
    contribution_published: Decimal | None
    contribution_division_adjustment_exact: Decimal
    contribution_rounding_adjustment: Decimal | None
    closure_residual_exact: Decimal
    rounding_adjustment_base: Decimal
    coverage_state: CoverageState
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PortfolioDailyRunMetadata:
    publication_id: UUID
    run_id: UUID
    manifest_id: UUID
    portfolio_id: str
    published_fencing_token: int
    canonical_output_hash: str
    published_at: datetime
    current_pointer_updated_at: datetime
    run_status: CalculationRunStatus
    requested_as_of: date
    effective_as_of: date
    cutoff_at: datetime
    timezone_name: str
    methodology_version: str
    input_schema_version: str
    output_schema_version: str
    captured_generation: int
    requested_by: str
    run_created_at: datetime
    run_started_at: datetime
    run_completed_at: datetime
    manifest_hash: str
    manifest_sealed_at: datetime
    worker_id: str
    calculated_at: datetime
    output_range_start: date
    output_range_end: date
    closure_status: Literal["passed"]
    snapshot_count: int
    measured_nav_count: int
    holding_count: int
    balance_count: int
    lot_count: int
    lot_disposition_count: int
    contribution_count: int
    unavailable_component_count: int
    ledger_balance_residual_exact: Decimal
    nav_bridge_residual_exact: Decimal
    pnl_residual_exact: Decimal
    twr_residual_exact: Decimal
    lot_residual_exact: Decimal
    rounding_adjustment_base: Decimal
    coverage_state: CoverageState
    reason_codes: tuple[str, ...]
    current_generation: int
    pending_generation: int | None
    pending_run_id: UUID | None
    pending_run_status: CalculationRunStatus | None
    pending_intent_id: UUID | None
    pending_intent_status: str | None

    @property
    def stale(self) -> bool:
        """Whether source facts are newer than this immutable publication."""

        return self.current_generation > self.captured_generation

    @property
    def pending(self) -> bool:
        """Whether a newer generation has an active intent or run."""

        return self.pending_generation is not None


@dataclass(frozen=True, slots=True)
class CurrentPortfolioDailyPublication:
    metadata: PortfolioDailyRunMetadata
    requested_range_start: date
    requested_range_end: date
    snapshots: tuple[PortfolioDailySnapshot, ...]
    holdings: tuple[PortfolioDailyHolding, ...]
    balances: tuple[PortfolioDailyBalance, ...]
    lots: tuple[PortfolioDailyLot, ...]
    lot_dispositions: tuple[PortfolioDailyLotDisposition, ...]
    contributions: tuple[PortfolioDailyContribution, ...]


@dataclass(frozen=True, slots=True)
class PortfolioDailyLatestSummary:
    """Minimal current-publication projection for portfolio list/workspace reads."""

    metadata: PortfolioDailyRunMetadata
    snapshot: PortfolioDailySnapshot | None
    instrument_count: int


_T = TypeVar("_T")
_REASON_CODE_COLUMNS: Final[frozenset[str]] = frozenset(
    column.name
    for table in (
        portfolio_daily_run_output,
        portfolio_daily_snapshot_output,
        portfolio_daily_holding_output,
        portfolio_daily_balance_output,
        portfolio_daily_lot_output,
        portfolio_daily_lot_disposition_output,
        portfolio_daily_contribution_output,
    )
    for column in table.c
    if column.name == "reason_codes" or column.name.endswith("_reason_codes")
)


def _one_or_none(result: Result[Any]) -> RowMapping | None:
    return result.mappings().one_or_none()


def _validate_request(
    portfolio_id: str,
    range_start: date,
    range_end: date,
) -> None:
    if (
        not portfolio_id
        or portfolio_id != portfolio_id.strip()
        or len(portfolio_id) > 255
    ):
        raise ValueError("portfolio_id must be a trimmed non-empty value")
    if isinstance(range_start, datetime) or not isinstance(range_start, date):
        raise TypeError("range_start must be a date, not datetime")
    if isinstance(range_end, datetime) or not isinstance(range_end, date):
        raise TypeError("range_end must be a date, not datetime")
    if range_start > range_end:
        raise ValueError("range_start must be on or before range_end")


def _validated_tables(
    tables: Collection[PortfolioDailyPublishedTable],
) -> frozenset[PortfolioDailyPublishedTable]:
    selected = frozenset(tables)
    unknown = selected - _ALL_PUBLISHED_TABLES
    if unknown:
        raise ValueError(
            "unknown Portfolio Daily published table selection: "
            + ", ".join(sorted(unknown))
        )
    return selected


def _pointer_predicate(portfolio_id: str):
    return and_(
        _current_publication.c.calculation_kind == CALCULATION_KIND,
        _current_publication.c.scope_kind == SCOPE_KIND,
        _current_publication.c.scope_id == portfolio_id,
    )


def _published_join(output_table: Table):
    return (
        _current_publication.join(
            _publication,
            and_(
                _publication.c.publication_id
                == _current_publication.c.publication_id,
                _publication.c.calculation_kind
                == _current_publication.c.calculation_kind,
                _publication.c.scope_kind == _current_publication.c.scope_kind,
                _publication.c.scope_id == _current_publication.c.scope_id,
            ),
        ).join(
            output_table,
            and_(
                output_table.c.run_id == _publication.c.run_id,
                output_table.c.output_fencing_token
                == _publication.c.published_fencing_token,
            ),
        )
    )


def _metadata_statement(portfolio_id: str) -> Select[Any]:
    output = portfolio_daily_run_output
    return (
        select(
            _publication.c.publication_id,
            _publication.c.run_id,
            _publication.c.manifest_id,
            _publication.c.published_fencing_token,
            _publication.c.canonical_output_hash,
            _publication.c.output_schema_version.label(
                "publication_output_schema_version"
            ),
            _publication.c.published_at,
            _current_publication.c.updated_at.label("current_pointer_updated_at"),
            _run.c.status.label("run_status"),
            _run.c.requested_as_of,
            _run.c.effective_as_of,
            _run.c.cutoff_at,
            _run.c.timezone.label("timezone_name"),
            _run.c.methodology_version,
            _run.c.input_schema_version,
            _run.c.output_schema_version,
            _run.c.published_output_hash.label("run_published_output_hash"),
            _run.c.captured_generation,
            _run.c.requested_by,
            _run.c.created_at.label("run_created_at"),
            _run.c.started_at.label("run_started_at"),
            _run.c.completed_at.label("run_completed_at"),
            _generation.c.generation.label("current_generation"),
            _pending_run_ranked.c.run_id.label("pending_run_id"),
            _pending_run_ranked.c.captured_generation.label(
                "pending_run_generation"
            ),
            _pending_run_ranked.c.status.label("pending_run_status"),
            _pending_intent_ranked.c.intent_id.label("pending_intent_id"),
            _pending_intent_ranked.c.requested_generation.label(
                "pending_intent_generation"
            ),
            _pending_intent_ranked.c.status.label("pending_intent_status"),
            _manifest.c.canonical_manifest_hash.label("manifest_hash"),
            _manifest.c.sealed_at.label("manifest_sealed_at"),
            output.c.portfolio_id,
            output.c.worker_id,
            output.c.calculated_at,
            output.c.methodology_version.label("output_methodology_version"),
            output.c.input_schema_version.label("output_input_schema_version"),
            output.c.output_schema_version.label("stored_output_schema_version"),
            output.c.canonical_output_hash.label("output_canonical_output_hash"),
            output.c.range_start.label("output_range_start"),
            output.c.range_end.label("output_range_end"),
            output.c.closure_status,
            output.c.snapshot_count,
            output.c.measured_nav_count,
            output.c.holding_count,
            output.c.balance_count,
            output.c.lot_count,
            output.c.lot_disposition_count,
            output.c.contribution_count,
            output.c.unavailable_component_count,
            output.c.ledger_balance_residual_exact,
            output.c.nav_bridge_residual_exact,
            output.c.pnl_residual_exact,
            output.c.twr_residual_exact,
            output.c.lot_residual_exact,
            output.c.rounding_adjustment_base,
            output.c.coverage_state,
            output.c.reason_codes,
        )
        .select_from(
            _current_publication.join(
                _publication,
                and_(
                    _publication.c.publication_id
                    == _current_publication.c.publication_id,
                    _publication.c.calculation_kind
                    == _current_publication.c.calculation_kind,
                    _publication.c.scope_kind
                    == _current_publication.c.scope_kind,
                    _publication.c.scope_id == _current_publication.c.scope_id,
                ),
            )
            .join(_run, _run.c.run_id == _publication.c.run_id)
            .join(
                _generation,
                and_(
                    _generation.c.calculation_kind == _run.c.calculation_kind,
                    _generation.c.scope_kind == _run.c.scope_kind,
                    _generation.c.scope_id == _run.c.scope_id,
                ),
            )
            .outerjoin(
                _pending_run_ranked,
                and_(
                    _pending_run_ranked.c.calculation_kind
                    == _run.c.calculation_kind,
                    _pending_run_ranked.c.scope_kind == _run.c.scope_kind,
                    _pending_run_ranked.c.scope_id == _run.c.scope_id,
                    _pending_run_ranked.c.freshness_rank == 1,
                    _pending_run_ranked.c.captured_generation
                    > _run.c.captured_generation,
                ),
            )
            .outerjoin(
                _pending_intent_ranked,
                and_(
                    _pending_intent_ranked.c.calculation_kind
                    == _run.c.calculation_kind,
                    _pending_intent_ranked.c.scope_kind == _run.c.scope_kind,
                    _pending_intent_ranked.c.scope_id == _run.c.scope_id,
                    _pending_intent_ranked.c.freshness_rank == 1,
                    _pending_intent_ranked.c.requested_generation
                    > _run.c.captured_generation,
                ),
            )
            .join(
                _manifest,
                and_(
                    _manifest.c.manifest_id == _publication.c.manifest_id,
                    _manifest.c.run_id == _publication.c.run_id,
                ),
            )
            .outerjoin(
                output,
                and_(
                    output.c.run_id == _publication.c.run_id,
                    output.c.output_fencing_token
                    == _publication.c.published_fencing_token,
                    output.c.portfolio_id == _current_publication.c.scope_id,
                ),
            )
        )
        .where(
            _pointer_predicate(portfolio_id),
        )
    )


def _output_statement(
    table: Table,
    *,
    portfolio_id: str,
    range_start: date,
    range_end: date,
    publication_id: UUID,
    run_id: UUID,
    fencing_token: int,
    order_by: tuple[str, ...],
) -> Select[Any]:
    return (
        select(table)
        .select_from(_published_join(table))
        .where(
            _pointer_predicate(portfolio_id),
            _current_publication.c.publication_id == publication_id,
            _publication.c.run_id == run_id,
            _publication.c.published_fencing_token == fencing_token,
            table.c.portfolio_id == portfolio_id,
            table.c.as_of_date.between(range_start, range_end),
        )
        .order_by(*(table.c[name] for name in order_by))
    )


def _pointer_confirmation_statement(portfolio_id: str) -> Select[Any]:
    return (
        select(
            _current_publication.c.publication_id,
            _publication.c.run_id,
            _publication.c.published_fencing_token,
        )
        .select_from(
            _current_publication.join(
                _publication,
                and_(
                    _publication.c.publication_id
                    == _current_publication.c.publication_id,
                    _publication.c.calculation_kind
                    == _current_publication.c.calculation_kind,
                    _publication.c.scope_kind == _current_publication.c.scope_kind,
                    _publication.c.scope_id == _current_publication.c.scope_id,
                ),
            )
        )
        .where(_pointer_predicate(portfolio_id))
    )


def _require_decimal_storage(table: Table, row: Mapping[str, object]) -> None:
    for column in table.c:
        value = row.get(column.name)
        if value is None or not isinstance(column.type, Numeric):
            continue
        if not isinstance(value, Decimal):
            raise PortfolioDailyPublishedReadIntegrityError(
                f"{table.name}.{column.name} must be returned as Decimal; "
                f"received {type(value).__name__}"
            )
        if not value.is_finite():
            raise PortfolioDailyPublishedReadIntegrityError(
                f"{table.name}.{column.name} is not finite"
            )


def _typed_output_row(
    row_type: type[_T],
    table: Table,
    row: Mapping[str, object],
    *,
    publication: PortfolioDailyRunMetadata,
) -> _T:
    if (
        row.get("run_id") != publication.run_id
        or row.get("output_fencing_token")
        != publication.published_fencing_token
        or row.get("portfolio_id") != publication.portfolio_id
    ):
        raise PortfolioDailyPublishedReadIntegrityError(
            f"{table.name} returned a row outside the current publication attempt"
        )
    _require_decimal_storage(table, row)
    values: dict[str, object] = {}
    for field in fields(row_type):
        if field.name not in row:
            raise PortfolioDailyPublishedReadIntegrityError(
                f"{table.name} did not return required column {field.name}"
            )
        value = row[field.name]
        if field.name in _REASON_CODE_COLUMNS:
            value = tuple(value or ())  # PostgreSQL ARRAY -> immutable boundary.
        values[field.name] = value
    return row_type(**values)  # type: ignore[arg-type]


def _metadata_from_row(row: Mapping[str, object]) -> PortfolioDailyRunMetadata:
    values = dict(row)
    values["reason_codes"] = tuple(values.get("reason_codes") or ())
    status = values.get("run_status")
    values["run_status"] = CalculationRunStatus(
        status.value if hasattr(status, "value") else str(status)
    )
    pending_run_status = values.get("pending_run_status")
    values["pending_run_status"] = (
        CalculationRunStatus(
            pending_run_status.value
            if hasattr(pending_run_status, "value")
            else str(pending_run_status)
        )
        if pending_run_status is not None
        else None
    )
    pending_intent_status = values.get("pending_intent_status")
    if pending_intent_status is None:
        values["pending_intent_status"] = None
    else:
        values["pending_intent_status"] = (
            pending_intent_status.value
            if hasattr(pending_intent_status, "value")
            else str(pending_intent_status)
        )
    if values["run_status"] is not CalculationRunStatus.PUBLISHED:
        raise PortfolioDailyPublishedReadIntegrityError(
            "current Portfolio Daily publication does not reference a published run"
        )
    if values.get("closure_status") != "passed":
        raise PortfolioDailyPublishedReadIntegrityError(
            "current Portfolio Daily publication does not reference passed output"
        )
    canonical_output_hash = values.get("canonical_output_hash")
    if not (
        canonical_output_hash
        == values.get("run_published_output_hash")
        == values.get("output_canonical_output_hash")
    ):
        raise PortfolioDailyPublishedReadIntegrityError(
            "current Portfolio Daily publication output hashes do not agree"
        )
    if not (
        values.get("output_schema_version")
        == values.get("publication_output_schema_version")
        == values.get("stored_output_schema_version")
    ):
        raise PortfolioDailyPublishedReadIntegrityError(
            "current Portfolio Daily publication output schema versions do not agree"
        )
    if values.get("methodology_version") != values.get(
        "output_methodology_version"
    ) or values.get("input_schema_version") != values.get(
        "output_input_schema_version"
    ):
        raise PortfolioDailyPublishedReadIntegrityError(
            "current Portfolio Daily run and stored output versions do not agree"
        )
    for name in (
        "ledger_balance_residual_exact",
        "nav_bridge_residual_exact",
        "pnl_residual_exact",
        "twr_residual_exact",
        "lot_residual_exact",
        "rounding_adjustment_base",
    ):
        value = values.get(name)
        if not isinstance(value, Decimal) or not value.is_finite():
            raise PortfolioDailyPublishedReadIntegrityError(
                f"Portfolio Daily run metadata {name} must be a finite Decimal"
            )
    token = values.get("published_fencing_token")
    if (
        values.get("run_id") is None
        or not isinstance(token, int)
        or isinstance(token, bool)
        or token <= 0
    ):
        raise PortfolioDailyPublishedReadIntegrityError(
            "current Portfolio Daily publication has an invalid attempt identity"
        )
    captured_generation = values.get("captured_generation")
    current_generation = values.get("current_generation")
    if (
        not isinstance(captured_generation, int)
        or isinstance(captured_generation, bool)
        or not isinstance(current_generation, int)
        or isinstance(current_generation, bool)
        or current_generation < captured_generation
    ):
        raise PortfolioDailyPublishedReadIntegrityError(
            "current Portfolio Daily publication has an invalid generation identity"
        )
    pending_generations = tuple(
        generation
        for generation in (
            values.pop("pending_run_generation", None),
            values.pop("pending_intent_generation", None),
        )
        if isinstance(generation, int) and not isinstance(generation, bool)
    )
    values["pending_generation"] = (
        max(pending_generations) if pending_generations else None
    )
    if (
        values["pending_generation"] is not None
        and (
            values["pending_generation"] <= captured_generation
            or values["pending_generation"] > current_generation
        )
    ):
        raise PortfolioDailyPublishedReadIntegrityError(
            "pending Portfolio Daily generation is outside the publication/current range"
        )
    if (values.get("pending_run_id") is None) != (
        values.get("pending_run_status") is None
    ):
        raise PortfolioDailyPublishedReadIntegrityError(
            "pending Portfolio Daily run identity/status is incomplete"
        )
    if (values.get("pending_intent_id") is None) != (
        values.get("pending_intent_status") is None
    ) or values.get("pending_intent_status") not in (None, "pending"):
        raise PortfolioDailyPublishedReadIntegrityError(
            "pending Portfolio Daily intent identity/status is invalid"
        )
    return PortfolioDailyRunMetadata(
        **{field.name: values[field.name] for field in fields(PortfolioDailyRunMetadata)}
    )


def _read_rows(
    executor: PublishedReadExecutor,
    table: Table,
    row_type: type[_T],
    *,
    publication: PortfolioDailyRunMetadata,
    range_start: date,
    range_end: date,
    order_by: tuple[str, ...],
) -> tuple[_T, ...]:
    statement = _output_statement(
        table,
        portfolio_id=publication.portfolio_id,
        range_start=range_start,
        range_end=range_end,
        publication_id=publication.publication_id,
        run_id=publication.run_id,
        fencing_token=publication.published_fencing_token,
        order_by=order_by,
    )
    return tuple(
        _typed_output_row(
            row_type,
            table,
            row,
            publication=publication,
        )
        for row in executor.execute(statement).mappings()
    )


def _confirm_current_pointer(
    executor: PublishedReadExecutor,
    metadata: PortfolioDailyRunMetadata,
) -> None:
    confirmation = _one_or_none(
        executor.execute(_pointer_confirmation_statement(metadata.portfolio_id))
    )
    if (
        confirmation is None
        or confirmation["publication_id"] != metadata.publication_id
        or confirmation["run_id"] != metadata.run_id
        or int(confirmation["published_fencing_token"])
        != metadata.published_fencing_token
    ):
        raise PortfolioDailyCurrentPublicationChanged(
            expected_publication_id=metadata.publication_id,
            actual_publication_id=(
                confirmation["publication_id"] if confirmation is not None else None
            ),
        )


def _read_selected_publication(
    executor: PublishedReadExecutor,
    *,
    metadata: PortfolioDailyRunMetadata,
    range_start: date,
    range_end: date,
    selected_tables: frozenset[PortfolioDailyPublishedTable],
) -> CurrentPortfolioDailyPublication:
    snapshots = _read_rows(
        executor,
        portfolio_daily_snapshot_output,
        PortfolioDailySnapshot,
        publication=metadata,
        range_start=range_start,
        range_end=range_end,
        order_by=("as_of_date",),
    ) if "snapshots" in selected_tables else ()
    holdings = _read_rows(
        executor,
        portfolio_daily_holding_output,
        PortfolioDailyHolding,
        publication=metadata,
        range_start=range_start,
        range_end=range_end,
        order_by=("as_of_date", "account_id", "instrument_id"),
    ) if "holdings" in selected_tables else ()
    balances = _read_rows(
        executor,
        portfolio_daily_balance_output,
        PortfolioDailyBalance,
        publication=metadata,
        range_start=range_start,
        range_end=range_end,
        order_by=(
            "as_of_date",
            "account_id",
            "component_type",
            "component_key",
            "currency",
        ),
    ) if "balances" in selected_tables else ()
    lots = _read_rows(
        executor,
        portfolio_daily_lot_output,
        PortfolioDailyLot,
        publication=metadata,
        range_start=range_start,
        range_end=range_end,
        order_by=("as_of_date", "account_id", "instrument_id", "lot_id"),
    ) if "lots" in selected_tables else ()
    lot_dispositions = _read_rows(
        executor,
        portfolio_daily_lot_disposition_output,
        PortfolioDailyLotDisposition,
        publication=metadata,
        range_start=range_start,
        range_end=range_end,
        order_by=(
            "as_of_date",
            "account_id",
            "instrument_id",
            "lot_id",
            "disposition_transaction_id",
            "match_sequence",
        ),
    ) if "lot_dispositions" in selected_tables else ()
    contributions = _read_rows(
        executor,
        portfolio_daily_contribution_output,
        PortfolioDailyContribution,
        publication=metadata,
        range_start=range_start,
        range_end=range_end,
        order_by=("as_of_date", "axis", "group_key"),
    ) if "contributions" in selected_tables else ()

    _confirm_current_pointer(executor, metadata)

    return CurrentPortfolioDailyPublication(
        metadata=metadata,
        requested_range_start=range_start,
        requested_range_end=range_end,
        snapshots=snapshots,
        holdings=holdings,
        balances=balances,
        lots=lots,
        lot_dispositions=lot_dispositions,
        contributions=contributions,
    )


def read_current_portfolio_daily_publication(
    executor: PublishedReadExecutor,
    *,
    portfolio_id: str,
    range_start: date,
    range_end: date,
    tables: Collection[PortfolioDailyPublishedTable] = _ALL_PUBLISHED_TABLES,
) -> CurrentPortfolioDailyPublication | None:
    """Read one coherent current publication, or ``None`` when none exists.

    The function optimistically verifies the immutable pointer identity after
    reading only the selected tables.  Under PostgreSQL ``READ COMMITTED``
    this detects a concurrent pointer switch instead of returning a
    cross-publication mix; callers should retry the whole transaction on
    :class:`PortfolioDailyCurrentPublicationChanged`.
    """

    _validate_request(portfolio_id, range_start, range_end)
    selected_tables = _validated_tables(tables)
    metadata_row = _one_or_none(executor.execute(_metadata_statement(portfolio_id)))
    if metadata_row is None:
        return None
    return _read_selected_publication(
        executor,
        metadata=_metadata_from_row(metadata_row),
        range_start=range_start,
        range_end=range_end,
        selected_tables=selected_tables,
    )


def read_current_portfolio_daily_metadata(
    executor: PublishedReadExecutor,
    *,
    portfolio_id: str,
) -> PortfolioDailyRunMetadata | None:
    """Read and fence-confirm only the current publication metadata."""

    today = date.today()
    publication = read_current_portfolio_daily_publication(
        executor,
        portfolio_id=portfolio_id,
        range_start=today,
        range_end=today,
        tables=(),
    )
    return publication.metadata if publication is not None else None


def read_current_portfolio_daily_range(
    executor: PublishedReadExecutor,
    *,
    portfolio_id: str,
    range_start: date | None = None,
    range_end: date | None = None,
    tables: Collection[PortfolioDailyPublishedTable],
) -> CurrentPortfolioDailyPublication | None:
    """Read selected tables, defaulting omitted boundaries to published range."""

    today = date.today()
    _validate_request(portfolio_id, today, today)
    selected_tables = _validated_tables(tables)
    metadata_row = _one_or_none(executor.execute(_metadata_statement(portfolio_id)))
    if metadata_row is None:
        return None
    metadata = _metadata_from_row(metadata_row)
    resolved_start = range_start or metadata.output_range_start
    resolved_end = range_end or metadata.output_range_end
    _validate_request(portfolio_id, resolved_start, resolved_end)
    return _read_selected_publication(
        executor,
        metadata=metadata,
        range_start=resolved_start,
        range_end=resolved_end,
        selected_tables=selected_tables,
    )


def read_latest_current_portfolio_daily_publication(
    executor: PublishedReadExecutor,
    *,
    portfolio_id: str,
    as_of_date: date | None = None,
    tables: Collection[PortfolioDailyPublishedTable],
) -> CurrentPortfolioDailyPublication | None:
    """Read selected latest-date tables from one current publication.

    The metadata read and output reads use the same immutable attempt identity;
    a final pointer confirmation rejects a concurrent publication switch.
    """

    today = date.today()
    _validate_request(portfolio_id, today, today)
    selected_tables = _validated_tables(tables)
    metadata_row = _one_or_none(executor.execute(_metadata_statement(portfolio_id)))
    if metadata_row is None:
        return None
    metadata = _metadata_from_row(metadata_row)
    selected_date = as_of_date or metadata.output_range_end
    _validate_request(portfolio_id, selected_date, selected_date)
    return _read_selected_publication(
        executor,
        metadata=metadata,
        range_start=selected_date,
        range_end=selected_date,
        selected_tables=selected_tables,
    )


def read_current_portfolio_daily_latest_summary(
    executor: PublishedReadExecutor,
    *,
    portfolio_id: str,
) -> PortfolioDailyLatestSummary | None:
    """Return the latest snapshot and current distinct-instrument count.

    ``PortfolioDailyRunMetadata.holding_count`` is the immutable row count for
    the entire published output range.  It must not be exposed as a current
    portfolio security count.
    """

    publication = read_latest_current_portfolio_daily_publication(
        executor,
        portfolio_id=portfolio_id,
        tables=("snapshots", "holdings"),
    )
    if publication is None:
        return None
    if len(publication.snapshots) > 1:
        raise PortfolioDailyPublishedReadIntegrityError(
            "latest Portfolio Daily snapshot projection returned multiple rows"
        )
    return PortfolioDailyLatestSummary(
        metadata=publication.metadata,
        snapshot=publication.snapshots[0] if publication.snapshots else None,
        instrument_count=len(
            {holding.instrument_id for holding in publication.holdings}
        ),
    )


class PortfolioDailyPublishedRepository:
    """Stateless namespace for caller-transaction publication reads."""

    read_current = staticmethod(read_current_portfolio_daily_publication)
    read_metadata = staticmethod(read_current_portfolio_daily_metadata)
    read_range = staticmethod(read_current_portfolio_daily_range)
    read_latest = staticmethod(read_latest_current_portfolio_daily_publication)
    read_latest_summary = staticmethod(read_current_portfolio_daily_latest_summary)


__all__ = [
    "CurrentPortfolioDailyPublication",
    "PortfolioDailyBalance",
    "PortfolioDailyContribution",
    "PortfolioDailyCurrentPublicationChanged",
    "PortfolioDailyHolding",
    "PortfolioDailyLot",
    "PortfolioDailyLotDisposition",
    "PortfolioDailyLatestSummary",
    "PortfolioDailyPublishedReadError",
    "PortfolioDailyPublishedReadIntegrityError",
    "PortfolioDailyPublishedRepository",
    "PortfolioDailyPublishedTable",
    "PortfolioDailyRunMetadata",
    "PortfolioDailySnapshot",
    "read_current_portfolio_daily_publication",
    "read_current_portfolio_daily_metadata",
    "read_current_portfolio_daily_range",
    "read_current_portfolio_daily_latest_summary",
    "read_latest_current_portfolio_daily_publication",
]
