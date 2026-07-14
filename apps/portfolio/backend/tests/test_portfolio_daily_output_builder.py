from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from portfolio_app.calculations.portfolio_daily.db_models import DEPENDENCY_TABLES
from portfolio_app.calculations.portfolio_daily.ledger_contracts import (
    CostBasisMethod,
    ExternalCashFlowEvent,
    ExternalFlowKind,
    ExternalFlowTiming,
    FactLineage,
)
from portfolio_app.calculations.portfolio_daily.ledger_replay import (
    replay_ledger_series,
)
from portfolio_app.calculations.portfolio_daily.ledger_state import (
    BaseCoverageStatus,
    DailyLedgerSnapshot,
    DailyLedgerState,
    LedgerEffect,
    LedgerEffectKind,
    LedgerSeriesResult,
    LedgerStatus,
    LedgerTotals,
    LotDisposition,
    PendingComponentKind,
    PendingSettlement,
    PositionLot,
    PositionState,
)
from portfolio_app.calculations.portfolio_daily.manifest_repository import (
    ManifestDependencies,
)
from portfolio_app.calculations.portfolio_daily.output_builder import (
    PortfolioDailyOutputBuildError,
    _build_snapshot_row,
    _portfolio_contribution_row,
    build_portfolio_daily_financial_outputs,
)
from portfolio_app.calculations.portfolio_daily.sealed_manifest import (
    SealedPortfolioDailyManifest,
)
from portfolio_app.calculations.portfolio_daily.twr import (
    CoverageStatus,
    FxStatus,
    PortfolioDailyInput,
    TwrAccumulatorState,
    ValuationStatus,
    advance_daily_twr,
)
from portfolio_app.calculations.portfolio_daily.valuation_contracts import (
    BalanceComponentType,
    DailyValuationBook,
    ExactBalanceValuation,
    ExactBookPnl,
    ExactDailyPortfolioValuation,
    ExactHoldingValuation,
    ExactPortfolioValuationSeries,
    ValuationEndpointStatus,
    ValuationReasonCode,
    exact_decimal_product,
    exact_decimal_subtract,
    exact_decimal_sum,
)
from portfolio_app.calculations.portfolio_daily.valuation_engine import (
    calculate_exact_portfolio_valuation,
)


pytestmark = pytest.mark.no_database

PORTFOLIO_ID = "portfolio-output-exact"
BASE = "CNY"
DAY_ONE = date(2026, 7, 14)


def _lineage(transaction_id: str, revision_id: str | None = None) -> FactLineage:
    revision = revision_id or f"rev-{transaction_id}"
    return FactLineage(
        source_record_id=transaction_id,
        source_revision_id=revision,
        manifest_fact_key=(
            f"portfolio_daily_transaction_input/{transaction_id}/{revision}"
        ),
    )


def _transaction(
    transaction_id: str,
    transaction_type: str,
    *,
    account_id: str,
) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "revision_id": f"rev-{transaction_id}",
        "revision_number": 1,
        "transaction_type": transaction_type,
        "account_id": account_id,
    }


def _manifest(
    *,
    end: date,
    transactions: tuple[dict[str, object], ...],
    accounts: tuple[str, ...],
    config_extra: dict[str, object] | None = None,
    fx_paths: tuple[dict[str, object], ...] = (),
) -> SealedPortfolioDailyManifest:
    rows: dict[str, tuple[dict[str, object], ...]] = {
        table.name: () for table in DEPENDENCY_TABLES
    }
    config: dict[str, object] = {
        "portfolio_id": PORTFOLIO_ID,
        "range_start": DAY_ONE,
        "effective_as_of": end,
        "base_currency": BASE,
    }
    if config_extra:
        config.update(config_extra)
    rows["portfolio_daily_config_input"] = (config,)
    rows["portfolio_daily_transaction_input"] = transactions
    rows["portfolio_daily_account_input"] = tuple(
        {"account_id": account_id, "cost_basis_method": "fifo"}
        for account_id in accounts
    )
    rows["portfolio_daily_fx_path"] = fx_paths
    dependencies = ManifestDependencies(rows)
    return SealedPortfolioDailyManifest(
        run_id=uuid4(),
        manifest_id=uuid4(),
        portfolio_id=PORTFOLIO_ID,
        effective_as_of=end,
        cutoff_at=datetime(2026, 7, 14, 12, tzinfo=UTC),
        captured_generation=1,
        canonical_manifest_hash="0" * 64,
        dependency_counts=dependencies.counts,
        dependencies=dependencies,
    )


def _position(
    *,
    account_id: str,
    instrument_id: str,
    lot_id: str,
    lineage: FactLineage,
    custody_lineage: FactLineage | None = None,
    quantity: Decimal,
    cost: Decimal,
) -> PositionState:
    lot = PositionLot(
        lot_id=lot_id,
        opening_sequence=1,
        acquisition_date=DAY_ONE,
        quantity=quantity,
        local_cost=cost,
        historical_base_cost=cost,
        lineage=lineage,
        custody_lineage=custody_lineage or lineage,
        acquisition_fx_lineage=None,
        cost_source_lineages=(lineage,),
        cost_fx_lineages=(),
    )
    return PositionState(
        account_id=account_id,
        instrument_id=instrument_id,
        currency=BASE,
        base_currency=BASE,
        cost_basis_method=CostBasisMethod.FIFO,
        quantity=quantity,
        local_cost=cost,
        historical_base_cost=cost,
        lots=(lot,),
    )


def _state(
    *,
    as_of: date,
    positions: tuple[PositionState, ...],
    pending: tuple[PendingSettlement, ...] = (),
    dispositions: tuple[LotDisposition, ...] = (),
    daily_totals: tuple[LedgerTotals, ...] = (),
    daily_effects: tuple[LedgerEffect, ...] = (),
) -> DailyLedgerSnapshot:
    return DailyLedgerSnapshot(
        state=DailyLedgerState(
            as_of_date=as_of,
            cash_balances=(),
            pending_settlements=pending,
            positions=positions,
            cumulative_totals=(),
            daily_totals=daily_totals,
            dispositions=dispositions,
            daily_dispositions=dispositions,
            fx_conversions=(),
            dividend_reinvestments=(),
            opening_anchor=None,
            historical_base_coverage_status=BaseCoverageStatus.COMPLETE,
            historical_base_coverage_reasons=(),
        ),
        daily_effects=daily_effects,
    )


def _unmeasured_opening_book() -> ExactBookPnl:
    return ExactBookPnl(
        economic_measured=False,
        measured=False,
        economic_pnl=None,
        realized_pnl=None,
        unrealized_beginning=None,
        unrealized_ending=None,
        unrealized_change=None,
        gross_income=None,
        expensed_fees=None,
        expensed_taxes=None,
        cash_fx_effect=None,
        pending_fx_effect=None,
        accrual_fx_effect=None,
        fx_conversion_effect=None,
        monetary_balance_fx_effect=None,
        component_closure_residual=None,
        nav_bridge_residual=None,
        reason_codes=(ValuationReasonCode.OPENING_MEASUREMENT_UNAVAILABLE,),
    )


def _zero_book() -> ExactBookPnl:
    return ExactBookPnl(
        economic_measured=True,
        measured=True,
        economic_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        unrealized_beginning=Decimal("0"),
        unrealized_ending=Decimal("0"),
        unrealized_change=Decimal("0"),
        gross_income=Decimal("0"),
        expensed_fees=Decimal("0"),
        expensed_taxes=Decimal("0"),
        cash_fx_effect=Decimal("0"),
        pending_fx_effect=Decimal("0"),
        accrual_fx_effect=Decimal("0"),
        fx_conversion_effect=Decimal("0"),
        monetary_balance_fx_effect=Decimal("0"),
        component_closure_residual=Decimal("0"),
        nav_bridge_residual=Decimal("0"),
        reason_codes=(),
    )


def _holding(
    position: PositionState,
    *,
    as_of: date,
    price: Decimal,
) -> ExactHoldingValuation:
    local = exact_decimal_product(position.quantity, price, Decimal("1"), Decimal("1"))
    return ExactHoldingValuation(
        as_of_date=as_of,
        account_id=position.account_id,
        instrument_id=position.instrument_id,
        currency=BASE,
        quantity=position.quantity,
        cost_basis_local=position.local_cost,
        cost_basis_base=position.historical_base_cost,
        price=price,
        contract_multiplier=Decimal("1"),
        price_factor=Decimal("1"),
        fx_rate_to_base=Decimal("1"),
        market_value_local=local,
        market_value_base=local,
        unrealized_pnl_base=local - position.local_cost,
        coverage_status=CoverageStatus.COMPLETE,
        endpoint_status=ValuationEndpointStatus.FRESH,
        reason_codes=(),
        endpoint_reason_codes=(),
        book_pnl_coverage_status=CoverageStatus.COMPLETE,
        book_pnl_reason_codes=(),
    )


def _first_day_valuation(
    holdings: tuple[ExactHoldingValuation, ...],
) -> ExactDailyPortfolioValuation:
    closing = sum(
        (
            holding.market_value_base
            for holding in holdings
            if holding.market_value_base is not None
        ),
        Decimal("0"),
    )
    twr = advance_daily_twr(
        PortfolioDailyInput(as_of_date=DAY_ONE, measured_nav=closing),
        TwrAccumulatorState.initial(),
    )
    return ExactDailyPortfolioValuation(
        as_of_date=DAY_ONE,
        base_currency=BASE,
        holdings=holdings,
        balances=(),
        opening_nav=None,
        closing_nav=closing,
        position_market_value=closing,
        settled_cash=Decimal("0"),
        pending_receivable=Decimal("0"),
        pending_payable=Decimal("0"),
        accrual_receivable=Decimal("0"),
        accrual_payable=Decimal("0"),
        external_flow_in=Decimal("0"),
        external_flow_out=Decimal("0"),
        coverage_status=CoverageStatus.COMPLETE,
        endpoint_status=ValuationEndpointStatus.FRESH,
        reason_codes=(),
        endpoint_reason_codes=(),
        nav_closure_residual=Decimal("0"),
        book_pnl=_unmeasured_opening_book(),
        twr=twr,
    )


def _build_one_day(
    *,
    positions: tuple[PositionState, ...],
    holdings: tuple[ExactHoldingValuation, ...],
    transactions: tuple[dict[str, object], ...],
):
    ledger = LedgerSeriesResult(
        status=LedgerStatus.SUCCEEDED,
        reason_codes=(),
        snapshots=(_state(as_of=DAY_ONE, positions=positions),),
        effects=(),
    )
    valuation_day = _first_day_valuation(holdings)
    valuation = ExactPortfolioValuationSeries(
        days=(valuation_day,),
        final_twr_state=valuation_day.twr.next_state,
    )
    return build_portfolio_daily_financial_outputs(
        manifest=_manifest(
            end=DAY_ONE,
            transactions=transactions,
            accounts=tuple(sorted({position.account_id for position in positions})),
        ),
        ledger_series=ledger,
        valuation_series=valuation,
    )


def test_portfolio_contribution_uses_full_sparse_endpoint_window() -> None:
    def valuation_day(
        *,
        as_of: date,
        opening: Decimal | None,
        closing: Decimal,
        flow_out: Decimal,
        endpoint: ValuationEndpointStatus,
        endpoint_reasons: tuple[ValuationReasonCode, ...],
        book: ExactBookPnl,
        twr,
    ) -> ExactDailyPortfolioValuation:
        balance = ExactBalanceValuation(
            as_of_date=as_of,
            account_id="cash",
            component_type=BalanceComponentType.SETTLED_CASH,
            component_key="cash",
            currency=BASE,
            local_amount=closing,
            fx_rate_to_base=Decimal("1"),
            base_amount=closing,
            coverage_status=CoverageStatus.COMPLETE,
            endpoint_status=ValuationEndpointStatus.FRESH,
            reason_codes=(),
            endpoint_reason_codes=(),
        )
        return ExactDailyPortfolioValuation(
            as_of_date=as_of,
            base_currency=BASE,
            holdings=(),
            balances=(balance,),
            opening_nav=opening,
            closing_nav=closing,
            position_market_value=Decimal("0"),
            settled_cash=closing,
            pending_receivable=Decimal("0"),
            pending_payable=Decimal("0"),
            accrual_receivable=Decimal("0"),
            accrual_payable=Decimal("0"),
            external_flow_in=Decimal("0"),
            external_flow_out=flow_out,
            coverage_status=CoverageStatus.COMPLETE,
            endpoint_status=endpoint,
            reason_codes=(),
            endpoint_reason_codes=endpoint_reasons,
            nav_closure_residual=Decimal("0"),
            book_pnl=book,
            twr=twr,
        )

    anchor_twr = advance_daily_twr(
        PortfolioDailyInput(as_of_date=DAY_ONE, measured_nav=Decimal("100")),
        TwrAccumulatorState.initial(),
    )
    gap_date = date(2026, 7, 15)
    gap_twr = advance_daily_twr(
        PortfolioDailyInput(
            as_of_date=gap_date,
            measured_nav=Decimal("100"),
            valuation_status=ValuationStatus.CARRY_FORWARD,
        ),
        anchor_twr.next_state,
    )
    endpoint_date = date(2026, 7, 16)
    endpoint_twr = advance_daily_twr(
        PortfolioDailyInput(
            as_of_date=endpoint_date,
            measured_nav=Decimal("80"),
            external_flow_out=Decimal("20"),
        ),
        gap_twr.next_state,
    )
    anchor = valuation_day(
        as_of=DAY_ONE,
        opening=None,
        closing=Decimal("100"),
        flow_out=Decimal("0"),
        endpoint=ValuationEndpointStatus.FRESH,
        endpoint_reasons=(),
        book=_unmeasured_opening_book(),
        twr=anchor_twr,
    )
    gap = valuation_day(
        as_of=gap_date,
        opening=Decimal("100"),
        closing=Decimal("100"),
        flow_out=Decimal("0"),
        endpoint=ValuationEndpointStatus.CARRY_FORWARD,
        endpoint_reasons=(ValuationReasonCode.MARKET_DATA_QUOTE_CARRIED,),
        book=_zero_book(),
        twr=gap_twr,
    )
    endpoint = valuation_day(
        as_of=endpoint_date,
        opening=Decimal("100"),
        closing=Decimal("80"),
        flow_out=Decimal("20"),
        endpoint=ValuationEndpointStatus.FRESH,
        endpoint_reasons=(),
        book=_zero_book(),
        twr=endpoint_twr,
    )

    row = _portfolio_contribution_row(
        portfolio_id=PORTFOLIO_ID,
        day=endpoint,
        anchor=anchor,
        period_days=(gap, endpoint),
    )

    assert row["measured"] is True
    assert row["opening_nav_exact"] == Decimal("100")
    assert row["closing_nav_exact"] == Decimal("80")
    assert row["external_flow_in_exact"] == 0
    assert row["external_flow_out_exact"] == Decimal("20")
    assert row["economic_pnl_exact"] == 0
    assert (
        row["contribution_method50"]
        == endpoint_twr.subperiod_twr_method50
        == 0
    )


def test_recurring_unit_cost_keeps_exact_total_and_explicit_residual() -> None:
    lineage = _lineage("buy-1")
    position = _position(
        account_id="account-a",
        instrument_id="instrument-a",
        lot_id="lot-a",
        lineage=lineage,
        quantity=Decimal("3"),
        cost=Decimal("100"),
    )
    holding = _holding(position, as_of=DAY_ONE, price=Decimal("33.333333333333"))
    outputs = _build_one_day(
        positions=(position,),
        holdings=(holding,),
        transactions=(_transaction("buy-1", "buy", account_id="account-a"),),
    )
    lot = outputs.rows_by_table["portfolio_daily_lot_output"][0]
    assert lot["cost_basis_local_exact"] == Decimal("100")
    assert lot["unit_cost_local"] == Decimal("33.333333333333")
    assert lot["unit_cost_local_rounding_residual_exact"] == Decimal("0.000000000001")
    assert lot["unit_cost_base_rounding_residual_exact"] == Decimal("0.000000000001")
    run = outputs.rows_by_table["portfolio_daily_run_output"][0]
    snapshot = outputs.rows_by_table["portfolio_daily_snapshot_output"][0]
    holding_row = outputs.rows_by_table["portfolio_daily_holding_output"][0]
    contributions = outputs.rows_by_table["portfolio_daily_contribution_output"]
    assert snapshot["cumulative_twr_method50"] == 0
    assert snapshot["wealth_index_method50"] == 1
    assert snapshot["peak_wealth_index_method50"] == 1
    assert snapshot["wealth_chain_rounding_adjustment_exact"] is None
    assert run["unavailable_component_count"] == (
        snapshot["unavailable_component_count"]
        + holding_row["unavailable_component_count"]
        + sum(not row["measured"] for row in contributions)
    )


def test_mixed_lot_base_coverage_publishes_measured_subset_without_approximation() -> (
    None
):
    path_id = "00000000-0000-0000-0000-000000000702"
    fx_lineage = FactLineage(
        source_record_id=path_id,
        source_revision_id="sha256:" + "8" * 64,
        manifest_fact_key=f"portfolio_daily_fx_path/{DAY_ONE.isoformat()}/USD/{BASE}",
    )
    measured_lineage = _lineage("buy-measured")
    unavailable_lineage = _lineage("buy-unavailable")
    measured_lot = PositionLot(
        lot_id="lot-measured",
        opening_sequence=1,
        acquisition_date=DAY_ONE,
        quantity=Decimal("1"),
        local_cost=Decimal("10"),
        historical_base_cost=Decimal("70"),
        lineage=measured_lineage,
        custody_lineage=measured_lineage,
        acquisition_fx_lineage=fx_lineage,
        cost_source_lineages=(measured_lineage,),
        cost_fx_lineages=(fx_lineage,),
    )
    unavailable_lot = PositionLot(
        lot_id="lot-unavailable",
        opening_sequence=2,
        acquisition_date=DAY_ONE,
        quantity=Decimal("2"),
        local_cost=Decimal("20"),
        historical_base_cost=None,
        lineage=unavailable_lineage,
        custody_lineage=unavailable_lineage,
        acquisition_fx_lineage=None,
        cost_source_lineages=(unavailable_lineage,),
        cost_fx_lineages=(),
    )
    position = PositionState(
        account_id="account-a",
        instrument_id="instrument-a",
        currency="USD",
        base_currency=BASE,
        cost_basis_method=CostBasisMethod.FIFO,
        quantity=Decimal("3"),
        local_cost=Decimal("30"),
        historical_base_cost=None,
        lots=(measured_lot, unavailable_lot),
    )
    holding = ExactHoldingValuation(
        as_of_date=DAY_ONE,
        account_id="account-a",
        instrument_id="instrument-a",
        currency="USD",
        quantity=Decimal("3"),
        cost_basis_local=Decimal("30"),
        cost_basis_base=None,
        price=Decimal("10"),
        contract_multiplier=Decimal("1"),
        price_factor=Decimal("1"),
        fx_rate_to_base=Decimal("7"),
        market_value_local=Decimal("30"),
        market_value_base=Decimal("210"),
        unrealized_pnl_base=None,
        coverage_status=CoverageStatus.COMPLETE,
        endpoint_status=ValuationEndpointStatus.FRESH,
        reason_codes=(),
        endpoint_reason_codes=(),
        book_pnl_coverage_status=CoverageStatus.UNAVAILABLE,
        book_pnl_reason_codes=(ValuationReasonCode.HISTORICAL_BASE_COST_UNAVAILABLE,),
    )
    day = _first_day_valuation((holding,))
    outputs = build_portfolio_daily_financial_outputs(
        manifest=_manifest(
            end=DAY_ONE,
            transactions=(
                _transaction("buy-measured", "buy", account_id="account-a"),
                _transaction("buy-unavailable", "buy", account_id="account-a"),
            ),
            accounts=("account-a",),
            fx_paths=(
                {
                    "fx_path_id": path_id,
                    "valuation_date": DAY_ONE,
                    "from_currency": "USD",
                    "to_currency": BASE,
                    "resolution_status": "resolved",
                    "resolved_rate": Decimal("7"),
                },
            ),
        ),
        ledger_series=LedgerSeriesResult(
            status=LedgerStatus.SUCCEEDED,
            reason_codes=(),
            snapshots=(_state(as_of=DAY_ONE, positions=(position,)),),
            effects=(),
        ),
        valuation_series=ExactPortfolioValuationSeries(
            days=(day,),
            final_twr_state=day.twr.next_state,
        ),
    )
    rows = {
        row["lot_id"]: row
        for row in outputs.rows_by_table["portfolio_daily_lot_output"]
    }
    assert rows["lot-measured"]["measured_base_cost"] is True
    assert rows["lot-measured"]["cost_basis_base"] == Decimal("70.00000000")
    assert rows["lot-unavailable"]["measured_base_cost"] is False
    assert rows["lot-unavailable"]["cost_basis_base"] is None
    assert rows["lot-unavailable"]["base_cost_reason_codes"] == (
        "historical_base_cost_unavailable",
    )


def test_holding_publication_uses_deterministic_balanced_rounding() -> None:
    first = _position(
        account_id="account-a",
        instrument_id="instrument-a",
        lot_id="lot-a",
        lineage=_lineage("buy-a"),
        quantity=Decimal("1"),
        cost=Decimal("0"),
    )
    second = _position(
        account_id="account-a",
        instrument_id="instrument-b",
        lot_id="lot-b",
        lineage=_lineage("buy-b"),
        quantity=Decimal("1"),
        cost=Decimal("0"),
    )
    outputs = _build_one_day(
        positions=(first, second),
        holdings=(
            _holding(first, as_of=DAY_ONE, price=Decimal("0.000000005")),
            _holding(second, as_of=DAY_ONE, price=Decimal("0.000000005")),
        ),
        transactions=(
            _transaction("buy-a", "buy", account_id="account-a"),
            _transaction("buy-b", "buy", account_id="account-a"),
        ),
    )
    holdings = outputs.rows_by_table["portfolio_daily_holding_output"]
    by_instrument = {row["instrument_id"]: row for row in holdings}
    assert by_instrument["instrument-a"]["market_value_base"] == Decimal("0.00000001")
    assert by_instrument["instrument-a"]["base_rounding_adjustment"] == Decimal(
        "0.00000001"
    )
    assert by_instrument["instrument-b"]["market_value_base"] == Decimal("0E-8")
    assert sum((row["market_value_base"] for row in holdings), Decimal("0")) == Decimal(
        "0.00000001"
    )
    snapshot = outputs.rows_by_table["portfolio_daily_snapshot_output"][0]
    assert snapshot["position_market_value"] == Decimal("0.00000001")


def test_missing_price_is_unmeasured_and_never_published_as_zero() -> None:
    position = _position(
        account_id="account-a",
        instrument_id="instrument-a",
        lot_id="lot-a",
        lineage=_lineage("buy-a"),
        quantity=Decimal("1"),
        cost=Decimal("100"),
    )
    reason = (ValuationReasonCode.MARKET_DATA_QUOTE_UNAVAILABLE,)
    holding = ExactHoldingValuation(
        as_of_date=DAY_ONE,
        account_id=position.account_id,
        instrument_id=position.instrument_id,
        currency=BASE,
        quantity=position.quantity,
        cost_basis_local=position.local_cost,
        cost_basis_base=position.historical_base_cost,
        price=None,
        contract_multiplier=Decimal("1"),
        price_factor=Decimal("1"),
        fx_rate_to_base=Decimal("1"),
        market_value_local=None,
        market_value_base=None,
        unrealized_pnl_base=None,
        coverage_status=CoverageStatus.UNAVAILABLE,
        endpoint_status=ValuationEndpointStatus.UNAVAILABLE,
        reason_codes=reason,
        endpoint_reason_codes=reason,
        book_pnl_coverage_status=CoverageStatus.UNAVAILABLE,
        book_pnl_reason_codes=reason,
    )
    twr = advance_daily_twr(
        PortfolioDailyInput(
            as_of_date=DAY_ONE,
            measured_nav=None,
            coverage_status=CoverageStatus.UNAVAILABLE,
            valuation_status=ValuationStatus.FRESH,
            fx_status=FxStatus.AVAILABLE,
        ),
        TwrAccumulatorState.initial(),
    )
    book = ExactBookPnl(
        economic_measured=False,
        measured=False,
        economic_pnl=None,
        realized_pnl=None,
        unrealized_beginning=None,
        unrealized_ending=None,
        unrealized_change=None,
        gross_income=None,
        expensed_fees=None,
        expensed_taxes=None,
        cash_fx_effect=None,
        pending_fx_effect=None,
        accrual_fx_effect=None,
        fx_conversion_effect=None,
        monetary_balance_fx_effect=None,
        component_closure_residual=None,
        nav_bridge_residual=None,
        reason_codes=reason,
    )
    day = ExactDailyPortfolioValuation(
        as_of_date=DAY_ONE,
        base_currency=BASE,
        holdings=(holding,),
        balances=(),
        opening_nav=None,
        closing_nav=None,
        position_market_value=None,
        settled_cash=Decimal("0"),
        pending_receivable=Decimal("0"),
        pending_payable=Decimal("0"),
        accrual_receivable=Decimal("0"),
        accrual_payable=Decimal("0"),
        external_flow_in=Decimal("0"),
        external_flow_out=Decimal("0"),
        coverage_status=CoverageStatus.UNAVAILABLE,
        endpoint_status=ValuationEndpointStatus.UNAVAILABLE,
        reason_codes=reason,
        endpoint_reason_codes=reason,
        nav_closure_residual=None,
        book_pnl=book,
        twr=twr,
    )
    outputs = build_portfolio_daily_financial_outputs(
        manifest=_manifest(
            end=DAY_ONE,
            transactions=(_transaction("buy-a", "buy", account_id="account-a"),),
            accounts=("account-a",),
        ),
        ledger_series=LedgerSeriesResult(
            status=LedgerStatus.SUCCEEDED,
            reason_codes=(),
            snapshots=(_state(as_of=DAY_ONE, positions=(position,)),),
            effects=(),
        ),
        valuation_series=ExactPortfolioValuationSeries(
            days=(day,),
            final_twr_state=twr.next_state,
        ),
    )
    holding_row = outputs.rows_by_table["portfolio_daily_holding_output"][0]
    snapshot = outputs.rows_by_table["portfolio_daily_snapshot_output"][0]
    assert holding_row["measured_market_value"] is False
    assert holding_row["market_value_local"] is None
    assert holding_row["market_value_base"] is None
    assert snapshot["measured_nav"] is False
    assert snapshot["closing_nav"] is None
    assert "market_data_quote_unavailable" in snapshot["nav_reason_codes"]


def test_builder_rejects_float_anywhere_in_sealed_dependencies() -> None:
    position = _position(
        account_id="account-a",
        instrument_id="instrument-a",
        lot_id="lot-a",
        lineage=_lineage("buy-a"),
        quantity=Decimal("1"),
        cost=Decimal("1"),
    )
    holding = _holding(position, as_of=DAY_ONE, price=Decimal("1"))
    day = _first_day_valuation((holding,))
    with pytest.raises(PortfolioDailyOutputBuildError, match="float"):
        build_portfolio_daily_financial_outputs(
            manifest=_manifest(
                end=DAY_ONE,
                transactions=(_transaction("buy-a", "buy", account_id="account-a"),),
                accounts=("account-a",),
                config_extra={"canonical_config": {"unsafe": 1.0}},
            ),
            ledger_series=LedgerSeriesResult(
                status=LedgerStatus.SUCCEEDED,
                reason_codes=(),
                snapshots=(_state(as_of=DAY_ONE, positions=(position,)),),
                effects=(),
            ),
            valuation_series=ExactPortfolioValuationSeries(
                days=(day,),
                final_twr_state=day.twr.next_state,
            ),
        )


def test_missing_external_flow_fx_is_published_as_partial_not_zero() -> None:
    flow = ExternalCashFlowEvent(
        event_id="usd-deposit",
        sequence=1,
        lineage=_lineage("usd-deposit"),
        value_date=DAY_ONE,
        account_id="usd-cash",
        currency="USD",
        base_currency=BASE,
        kind=ExternalFlowKind.DEPOSIT,
        timing=ExternalFlowTiming.BEGINNING_OF_DAY,
        amount=Decimal("10"),
        local_to_base_rate=None,
        local_to_base_lineage=None,
    )
    ledger = replay_ledger_series(
        (flow,),
        start_date=DAY_ONE,
        end_date=DAY_ONE,
    )
    valuation = calculate_exact_portfolio_valuation(
        ledger,
        DailyValuationBook(instruments=(), quotes=(), fx_rates=()),
        base_currency=BASE,
    )

    row = _build_snapshot_row(
        portfolio_id=PORTFOLIO_ID,
        day=valuation.days[0],
        realized_pnl_published=None,
    )

    assert row["external_flow_in"] is None
    assert row["external_flow_out"] == Decimal("0E-8")
    assert row["measured_external_flows"] is False
    assert row["flow_coverage_state"] == CoverageStatus.PARTIAL.value
    assert row["flow_reason_codes"] == ("external_flow_base_measurement_unavailable",)


def test_cross_currency_recurring_lot_allocations_preserve_exact_fx_closure() -> None:
    rate = Decimal("7.123456789012345678")
    path_id = "00000000-0000-0000-0000-000000000701"
    fx_lineage = FactLineage(
        source_record_id=path_id,
        source_revision_id="sha256:" + "7" * 64,
        manifest_fact_key=f"portfolio_daily_fx_path/{DAY_ONE.isoformat()}/USD/{BASE}",
    )
    first_local = Decimal("33." + "3" * 48)
    second_local = exact_decimal_subtract(Decimal("100"), first_local)
    first_base = exact_decimal_product(first_local, rate)
    second_base = exact_decimal_product(second_local, rate)
    sell_lineage = _lineage("sell-two-lots")
    first_acquisition = _lineage("buy-first")
    second_acquisition = _lineage("buy-second")

    def disposition(
        *,
        sequence: int,
        acquisition: FactLineage,
        quantity: Decimal,
        local_proceeds: Decimal,
        base_proceeds: Decimal,
    ) -> LotDisposition:
        return LotDisposition(
            disposition_id=(
                "transaction:sell-two-lots:rev-sell-two-lots:"
                f"lot-{sequence}:{sequence - 1}"
            ),
            event_id="transaction:sell-two-lots:rev-sell-two-lots",
            disposition_date=DAY_ONE,
            account_id="account-a",
            instrument_id="instrument-a",
            currency="USD",
            base_currency=BASE,
            lot_id=f"lot-{sequence}",
            quantity=quantity,
            released_local_cost=Decimal("0"),
            released_historical_base_cost=Decimal("0"),
            allocated_local_net_proceeds=local_proceeds,
            allocated_base_net_proceeds=base_proceeds,
            realized_pnl_local=local_proceeds,
            realized_pnl_base=base_proceeds,
            source_lineage=acquisition,
            source_custody_lineage=acquisition,
            source_acquisition_fx_lineage=fx_lineage,
            source_cost_lineages=(acquisition,),
            source_cost_fx_lineages=(fx_lineage,),
            disposition_lineage=sell_lineage,
            disposition_fx_lineage=fx_lineage,
        )

    dispositions = (
        disposition(
            sequence=1,
            acquisition=first_acquisition,
            quantity=Decimal("1"),
            local_proceeds=first_local,
            base_proceeds=first_base,
        ),
        disposition(
            sequence=2,
            acquisition=second_acquisition,
            quantity=Decimal("2"),
            local_proceeds=second_local,
            base_proceeds=second_base,
        ),
    )
    event_id = dispositions[0].event_id
    total_base = exact_decimal_product(Decimal("100"), rate)
    pending = PendingSettlement(
        settlement_id=f"{event_id}:trade",
        component_kind=PendingComponentKind.TRADE_SETTLEMENT,
        recognition_date=DAY_ONE,
        settlement_date=date(2026, 7, 16),
        account_id="account-a",
        currency="USD",
        base_currency=BASE,
        local_amount=Decimal("100"),
        recognition_base_amount=total_base,
        instrument_id="instrument-a",
        lineage=sell_lineage,
        event_id=event_id,
    )
    effect = LedgerEffect(
        effective_date=DAY_ONE,
        event_id=event_id,
        kind=LedgerEffectKind.SELL_TRADE,
        account_id="account-a",
        currency="USD",
        base_currency=BASE,
        lineage=sell_lineage,
        instrument_id="instrument-a",
        pending_component_kind=PendingComponentKind.TRADE_SETTLEMENT,
        pending_delta_local=Decimal("100"),
        pending_delta_base=total_base,
        quantity_delta=Decimal("-3"),
        cost_basis_delta_local=Decimal("0"),
        cost_basis_delta_base=Decimal("0"),
        realized_pnl_delta_local=Decimal("100"),
        realized_pnl_delta_base=total_base,
    )
    balance = ExactBalanceValuation(
        as_of_date=DAY_ONE,
        account_id="account-a",
        component_type=BalanceComponentType.PENDING_RECEIVABLE,
        component_key=pending.settlement_id,
        currency="USD",
        local_amount=Decimal("100"),
        fx_rate_to_base=rate,
        base_amount=total_base,
        coverage_status=CoverageStatus.COMPLETE,
        endpoint_status=ValuationEndpointStatus.FRESH,
        reason_codes=(),
        endpoint_reason_codes=(),
    )
    twr = advance_daily_twr(
        PortfolioDailyInput(as_of_date=DAY_ONE, measured_nav=total_base),
        TwrAccumulatorState.initial(),
    )
    valuation_day = ExactDailyPortfolioValuation(
        as_of_date=DAY_ONE,
        base_currency=BASE,
        holdings=(),
        balances=(balance,),
        opening_nav=None,
        closing_nav=total_base,
        position_market_value=Decimal("0"),
        settled_cash=Decimal("0"),
        pending_receivable=total_base,
        pending_payable=Decimal("0"),
        accrual_receivable=Decimal("0"),
        accrual_payable=Decimal("0"),
        external_flow_in=Decimal("0"),
        external_flow_out=Decimal("0"),
        coverage_status=CoverageStatus.COMPLETE,
        endpoint_status=ValuationEndpointStatus.FRESH,
        reason_codes=(),
        endpoint_reason_codes=(),
        nav_closure_residual=Decimal("0"),
        book_pnl=_unmeasured_opening_book(),
        twr=twr,
    )
    outputs = build_portfolio_daily_financial_outputs(
        _manifest(
            end=DAY_ONE,
            transactions=(
                _transaction("buy-first", "buy", account_id="account-a"),
                _transaction("buy-second", "buy", account_id="account-a"),
                _transaction("sell-two-lots", "sell", account_id="account-a"),
            ),
            accounts=("account-a",),
            fx_paths=(
                {
                    "fx_path_id": path_id,
                    "valuation_date": DAY_ONE,
                    "from_currency": "USD",
                    "to_currency": BASE,
                    "resolution_status": "resolved",
                    "resolved_rate": rate,
                },
            ),
        ),
        LedgerSeriesResult(
            status=LedgerStatus.SUCCEEDED,
            reason_codes=(),
            snapshots=(
                _state(
                    as_of=DAY_ONE,
                    positions=(),
                    pending=(pending,),
                    dispositions=dispositions,
                    daily_totals=(
                        LedgerTotals(
                            currency="USD",
                            realized_pnl_local=Decimal("100"),
                            realized_pnl_base=total_base,
                        ),
                    ),
                    daily_effects=(effect,),
                ),
            ),
            effects=(effect,),
        ),
        ExactPortfolioValuationSeries(
            days=(valuation_day,),
            final_twr_state=twr.next_state,
        ),
    )
    rows = outputs.rows_by_table["portfolio_daily_lot_disposition_output"]
    assert len(rows) == 2
    for row in rows:
        assert row["proceeds_base_exact"] == exact_decimal_product(
            row["proceeds_local_exact"],
            rate,
        )
    assert exact_decimal_sum(
        tuple(row["proceeds_base_exact"] for row in rows)
    ) == exact_decimal_product(Decimal("100"), rate)
    assert sum((row["proceeds_base"] for row in rows), Decimal("0")) == Decimal(
        "712.34567890"
    )


def test_same_day_disposition_rollup_closes_to_snapshot_after_balanced_rounding() -> (
    None
):
    day_two = date(2026, 7, 15)
    opening_price = Decimal("0.000000004")
    lot_cost = Decimal("0.000000004")
    exact_proceeds = Decimal("0.00000001")
    exact_realized = Decimal("0.000000006")
    first_position = _position(
        account_id="account-a",
        instrument_id="instrument-a",
        lot_id="lot-a",
        lineage=_lineage("buy-a"),
        quantity=Decimal("1"),
        cost=lot_cost,
    )
    second_position = _position(
        account_id="account-a",
        instrument_id="instrument-b",
        lot_id="lot-b",
        lineage=_lineage("buy-b"),
        quantity=Decimal("1"),
        cost=lot_cost,
    )
    first = _first_day_valuation(
        (
            _holding(first_position, as_of=DAY_ONE, price=opening_price),
            _holding(second_position, as_of=DAY_ONE, price=opening_price),
        )
    )

    def make_sale(
        *,
        event_id: str,
        instrument_id: str,
        lot_id: str,
        acquisition: FactLineage,
    ) -> tuple[LotDisposition, PendingSettlement, ExactBalanceValuation, LedgerEffect]:
        sale_lineage = _lineage(event_id)
        disposition = LotDisposition(
            disposition_id=f"{event_id}:{lot_id}:0",
            event_id=event_id,
            disposition_date=day_two,
            account_id="account-a",
            instrument_id=instrument_id,
            currency=BASE,
            base_currency=BASE,
            lot_id=lot_id,
            quantity=Decimal("1"),
            released_local_cost=lot_cost,
            released_historical_base_cost=lot_cost,
            allocated_local_net_proceeds=exact_proceeds,
            allocated_base_net_proceeds=exact_proceeds,
            realized_pnl_local=exact_realized,
            realized_pnl_base=exact_realized,
            source_lineage=acquisition,
            source_custody_lineage=acquisition,
            source_acquisition_fx_lineage=None,
            source_cost_lineages=(acquisition,),
            source_cost_fx_lineages=(),
            disposition_lineage=sale_lineage,
            disposition_fx_lineage=None,
        )
        pending = PendingSettlement(
            settlement_id=f"{event_id}:trade",
            component_kind=PendingComponentKind.TRADE_SETTLEMENT,
            recognition_date=day_two,
            settlement_date=date(2026, 7, 16),
            account_id="account-a",
            currency=BASE,
            base_currency=BASE,
            local_amount=exact_proceeds,
            recognition_base_amount=exact_proceeds,
            instrument_id=instrument_id,
            lineage=sale_lineage,
            event_id=event_id,
        )
        balance = ExactBalanceValuation(
            as_of_date=day_two,
            account_id="account-a",
            component_type=BalanceComponentType.PENDING_RECEIVABLE,
            component_key=pending.settlement_id,
            currency=BASE,
            local_amount=exact_proceeds,
            fx_rate_to_base=Decimal("1"),
            base_amount=exact_proceeds,
            coverage_status=CoverageStatus.COMPLETE,
            endpoint_status=ValuationEndpointStatus.FRESH,
            reason_codes=(),
            endpoint_reason_codes=(),
        )
        effect = LedgerEffect(
            effective_date=day_two,
            event_id=event_id,
            kind=LedgerEffectKind.SELL_TRADE,
            account_id="account-a",
            currency=BASE,
            base_currency=BASE,
            lineage=sale_lineage,
            instrument_id=instrument_id,
            pending_component_kind=PendingComponentKind.TRADE_SETTLEMENT,
            pending_delta_local=exact_proceeds,
            pending_delta_base=exact_proceeds,
            quantity_delta=Decimal("-1"),
            realized_pnl_delta_local=exact_realized,
            realized_pnl_delta_base=exact_realized,
        )
        return disposition, pending, balance, effect

    first_sale = make_sale(
        event_id="sell-a",
        instrument_id="instrument-a",
        lot_id="lot-a",
        acquisition=first_position.lots[0].lineage,
    )
    second_sale = make_sale(
        event_id="sell-b",
        instrument_id="instrument-b",
        lot_id="lot-b",
        acquisition=second_position.lots[0].lineage,
    )
    dispositions = first_sale[0], second_sale[0]
    pending = first_sale[1], second_sale[1]
    balances = first_sale[2], second_sale[2]
    effects = first_sale[3], second_sale[3]
    realized_total = Decimal("0.000000012")
    opening_nav = Decimal("0.000000008")
    closing_nav = Decimal("0.00000002")
    economic_pnl = realized_total
    second_book = ExactBookPnl(
        economic_measured=True,
        measured=True,
        economic_pnl=economic_pnl,
        realized_pnl=realized_total,
        unrealized_beginning=Decimal("0"),
        unrealized_ending=Decimal("0"),
        unrealized_change=Decimal("0"),
        gross_income=Decimal("0"),
        expensed_fees=Decimal("0"),
        expensed_taxes=Decimal("0"),
        cash_fx_effect=Decimal("0"),
        pending_fx_effect=Decimal("0"),
        accrual_fx_effect=Decimal("0"),
        fx_conversion_effect=Decimal("0"),
        monetary_balance_fx_effect=Decimal("0"),
        component_closure_residual=Decimal("0"),
        nav_bridge_residual=Decimal("0"),
        reason_codes=(),
    )
    second_twr = advance_daily_twr(
        PortfolioDailyInput(as_of_date=day_two, measured_nav=closing_nav),
        first.twr.next_state,
    )
    second = ExactDailyPortfolioValuation(
        as_of_date=day_two,
        base_currency=BASE,
        holdings=(),
        balances=balances,
        opening_nav=opening_nav,
        closing_nav=closing_nav,
        position_market_value=Decimal("0"),
        settled_cash=Decimal("0"),
        pending_receivable=closing_nav,
        pending_payable=Decimal("0"),
        accrual_receivable=Decimal("0"),
        accrual_payable=Decimal("0"),
        external_flow_in=Decimal("0"),
        external_flow_out=Decimal("0"),
        coverage_status=CoverageStatus.COMPLETE,
        endpoint_status=ValuationEndpointStatus.FRESH,
        reason_codes=(),
        endpoint_reason_codes=(),
        nav_closure_residual=Decimal("0"),
        book_pnl=second_book,
        twr=second_twr,
    )
    outputs = build_portfolio_daily_financial_outputs(
        manifest=_manifest(
            end=day_two,
            transactions=(
                _transaction("buy-a", "buy", account_id="account-a"),
                _transaction("buy-b", "buy", account_id="account-a"),
                _transaction("sell-a", "sell", account_id="account-a"),
                _transaction("sell-b", "sell", account_id="account-a"),
            ),
            accounts=("account-a",),
        ),
        ledger_series=LedgerSeriesResult(
            status=LedgerStatus.SUCCEEDED,
            reason_codes=(),
            snapshots=(
                _state(
                    as_of=DAY_ONE,
                    positions=(first_position, second_position),
                ),
                _state(
                    as_of=day_two,
                    positions=(),
                    pending=pending,
                    dispositions=dispositions,
                    daily_totals=(
                        LedgerTotals(
                            currency=BASE,
                            realized_pnl_local=realized_total,
                            realized_pnl_base=realized_total,
                        ),
                    ),
                    daily_effects=effects,
                ),
            ),
            effects=effects,
        ),
        valuation_series=ExactPortfolioValuationSeries(
            days=(first, second),
            final_twr_state=second.twr.next_state,
        ),
    )
    disposition_rows = outputs.rows_by_table["portfolio_daily_lot_disposition_output"]
    snapshot = next(
        row
        for row in outputs.rows_by_table["portfolio_daily_snapshot_output"]
        if row["as_of_date"] == day_two
    )
    published_disposition_total = exact_decimal_sum(
        tuple(row["realized_pnl_base"] for row in disposition_rows)
    )
    assert published_disposition_total == Decimal("0.00000001")
    assert snapshot["realized_pnl_daily"] == published_disposition_total


def test_transfer_custody_lineage_survives_lot_and_later_disposition() -> None:
    day_two = date(2026, 7, 15)
    acquisition = _lineage("buy-source")
    custody = _lineage("transfer-in")
    disposition_lineage = _lineage("sell-destination")
    position = _position(
        account_id="account-destination",
        instrument_id="instrument-a",
        lot_id="lot-source:xfer:transfer-in:0",
        lineage=acquisition,
        custody_lineage=custody,
        quantity=Decimal("1"),
        cost=Decimal("100"),
    )
    holding = _holding(position, as_of=DAY_ONE, price=Decimal("100"))
    first = _first_day_valuation((holding,))

    disposition = LotDisposition(
        disposition_id="sell-destination:lot-source:0",
        event_id="sell-destination",
        disposition_date=day_two,
        account_id="account-destination",
        instrument_id="instrument-a",
        currency=BASE,
        base_currency=BASE,
        lot_id=position.lots[0].lot_id,
        quantity=Decimal("1"),
        released_local_cost=Decimal("100"),
        released_historical_base_cost=Decimal("100"),
        allocated_local_net_proceeds=Decimal("100"),
        allocated_base_net_proceeds=Decimal("100"),
        realized_pnl_local=Decimal("0"),
        realized_pnl_base=Decimal("0"),
        source_lineage=acquisition,
        source_custody_lineage=custody,
        source_acquisition_fx_lineage=None,
        source_cost_lineages=(acquisition,),
        source_cost_fx_lineages=(),
        disposition_lineage=disposition_lineage,
        disposition_fx_lineage=None,
    )
    pending = PendingSettlement(
        settlement_id="sell-destination:trade",
        component_kind=PendingComponentKind.TRADE_SETTLEMENT,
        recognition_date=day_two,
        settlement_date=date(2026, 7, 16),
        account_id="account-destination",
        currency=BASE,
        base_currency=BASE,
        local_amount=Decimal("100"),
        recognition_base_amount=Decimal("100"),
        instrument_id="instrument-a",
        lineage=disposition_lineage,
        event_id="sell-destination",
    )
    sell_effect = LedgerEffect(
        effective_date=day_two,
        event_id="sell-destination",
        kind=LedgerEffectKind.SELL_TRADE,
        account_id="account-destination",
        currency=BASE,
        base_currency=BASE,
        lineage=disposition_lineage,
        instrument_id="instrument-a",
        pending_component_kind=PendingComponentKind.TRADE_SETTLEMENT,
        pending_delta_local=Decimal("100"),
        pending_delta_base=Decimal("100"),
        quantity_delta=Decimal("-1"),
        cost_basis_delta_local=Decimal("-100"),
        cost_basis_delta_base=Decimal("-100"),
        realized_pnl_delta_local=Decimal("0"),
        realized_pnl_delta_base=Decimal("0"),
    )
    balance = ExactBalanceValuation(
        as_of_date=day_two,
        account_id="account-destination",
        component_type=BalanceComponentType.PENDING_RECEIVABLE,
        component_key="sell-destination:trade",
        currency=BASE,
        local_amount=Decimal("100"),
        fx_rate_to_base=Decimal("1"),
        base_amount=Decimal("100"),
        coverage_status=CoverageStatus.COMPLETE,
        endpoint_status=ValuationEndpointStatus.FRESH,
        reason_codes=(),
        endpoint_reason_codes=(),
    )
    second_twr = advance_daily_twr(
        PortfolioDailyInput(as_of_date=day_two, measured_nav=Decimal("100")),
        first.twr.next_state,
    )
    second = ExactDailyPortfolioValuation(
        as_of_date=day_two,
        base_currency=BASE,
        holdings=(),
        balances=(balance,),
        opening_nav=Decimal("100"),
        closing_nav=Decimal("100"),
        position_market_value=Decimal("0"),
        settled_cash=Decimal("0"),
        pending_receivable=Decimal("100"),
        pending_payable=Decimal("0"),
        accrual_receivable=Decimal("0"),
        accrual_payable=Decimal("0"),
        external_flow_in=Decimal("0"),
        external_flow_out=Decimal("0"),
        coverage_status=CoverageStatus.COMPLETE,
        endpoint_status=ValuationEndpointStatus.FRESH,
        reason_codes=(),
        endpoint_reason_codes=(),
        nav_closure_residual=Decimal("0"),
        book_pnl=_zero_book(),
        twr=second_twr,
    )
    outputs = build_portfolio_daily_financial_outputs(
        manifest=_manifest(
            end=day_two,
            transactions=(
                _transaction("buy-source", "buy", account_id="account-source"),
                _transaction(
                    "transfer-in",
                    "transfer_in",
                    account_id="account-destination",
                ),
                _transaction(
                    "sell-destination",
                    "sell",
                    account_id="account-destination",
                ),
            ),
            accounts=("account-source", "account-destination"),
        ),
        ledger_series=LedgerSeriesResult(
            status=LedgerStatus.SUCCEEDED,
            reason_codes=(),
            snapshots=(
                _state(as_of=DAY_ONE, positions=(position,)),
                _state(
                    as_of=day_two,
                    positions=(),
                    pending=(pending,),
                    dispositions=(disposition,),
                    daily_effects=(sell_effect,),
                ),
            ),
            effects=(sell_effect,),
        ),
        valuation_series=ExactPortfolioValuationSeries(
            days=(first, second),
            final_twr_state=second.twr.next_state,
        ),
    )
    lot = outputs.rows_by_table["portfolio_daily_lot_output"][0]
    disposed = outputs.rows_by_table["portfolio_daily_lot_disposition_output"][0]
    assert lot["source_transaction_id"] == "buy-source"
    assert lot["custody_transaction_id"] == "transfer-in"
    assert disposed["acquisition_transaction_id"] == "buy-source"
    assert disposed["custody_transaction_id"] == "transfer-in"
    assert disposed["disposition_transaction_id"] == "sell-destination"
    assert disposed["matching_method"] == "fifo"
    contribution_rows = outputs.rows_by_table["portfolio_daily_contribution_output"]
    second_portfolio = next(
        row
        for row in contribution_rows
        if row["as_of_date"] == day_two and row["axis"] == "portfolio"
    )
    assert second_portfolio["measured"] is True
    assert second_portfolio["contribution_published"] == Decimal("0E-18")
    second_groups = [
        row
        for row in contribution_rows
        if row["as_of_date"] == day_two and row["axis"] != "portfolio"
    ]
    assert second_groups
    assert all(row["measured"] for row in second_groups)
    assert all(row["closure_residual_exact"] == 0 for row in second_groups)
    for axis in ("account", "instrument", "currency", "taxonomy"):
        axis_rows = [row for row in second_groups if row["axis"] == axis]
        assert sum(row["economic_pnl_exact"] for row in axis_rows) == 0
        assert sum(row["contribution_method50"] for row in axis_rows) == 0
        assert sum(row["opening_nav_exact"] for row in axis_rows) == Decimal("100")
        assert sum(row["closing_nav_exact"] for row in axis_rows) == Decimal("100")
        assert (
            sum(row["opening_nav"] for row in axis_rows)
            == second_portfolio["opening_nav"]
        )
        assert (
            sum(row["closing_nav"] for row in axis_rows)
            == second_portfolio["closing_nav"]
        )
        assert (
            sum(row["economic_pnl"] for row in axis_rows)
            == second_portfolio["economic_pnl"]
        )
        assert (
            sum(row["contribution_published"] for row in axis_rows)
            == second_portfolio["contribution_published"]
        )
        assert sum(row["internal_flow_in"] for row in axis_rows) == sum(
            row["internal_flow_out"] for row in axis_rows
        )
