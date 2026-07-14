from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN, localcontext

import pytest

from portfolio_app.calculations.numeric import (
    canonical_decimal,
    exact_decimal_product,
    exact_decimal_subtract,
    exact_decimal_sum,
    method_decimal_add,
    method_decimal_multiply,
)
from portfolio_app.calculations.portfolio_daily.twr import (
    CoverageStatus,
    DailyCalculationStatus,
    FxStatus,
    PortfolioDailyExactError,
    PortfolioDailyInput,
    PortfolioDailyReasonCode,
    RollupCheckInput,
    RollupKind,
    RollupStatus,
    TwrAccumulatorState,
    TwrWindowStatus,
    ValuationStatus,
    advance_daily_twr,
    quantize_portfolio_daily_publication,
    validate_rollup,
)


pytestmark = pytest.mark.no_database


def _period(
    day: int,
    nav: str | None,
    *,
    flow_in: str = "0",
    flow_out: str = "0",
    coverage: CoverageStatus = CoverageStatus.COMPLETE,
    valuation: ValuationStatus = ValuationStatus.FRESH,
    fx: FxStatus = FxStatus.AVAILABLE,
) -> PortfolioDailyInput:
    return PortfolioDailyInput(
        as_of_date=date(2026, 7, day),
        measured_nav=None if nav is None else Decimal(nav),
        external_flow_in=Decimal(flow_in),
        external_flow_out=Decimal(flow_out),
        coverage_status=coverage,
        valuation_status=valuation,
        fx_status=fx,
    )


def test_first_fresh_measurement_is_an_anchor_not_a_manufactured_return() -> None:
    outcome = advance_daily_twr(_period(1, "100"), TwrAccumulatorState.initial())

    assert outcome.status is DailyCalculationStatus.REANCHORED
    assert outcome.reason_codes == (PortfolioDailyReasonCode.INITIAL_ANCHOR,)
    assert outcome.subperiod_twr_method50 is None
    assert outcome.cumulative_twr_method50 == Decimal("0")
    assert outcome.next_state.status is TwrWindowStatus.ACTIVE
    assert outcome.next_state.anchor_nav == Decimal("100")


def test_bod_inflow_and_eod_outflow_formula_is_exact() -> None:
    anchor = advance_daily_twr(
        _period(1, "100"),
        TwrAccumulatorState.initial(),
    )
    outcome = advance_daily_twr(
        _period(2, "102", flow_in="20", flow_out="30"),
        anchor.next_state,
    )

    assert outcome.status is DailyCalculationStatus.CALCULATED
    assert outcome.adjusted_beginning_value == Decimal("120")
    assert outcome.adjusted_ending_value == Decimal("132")
    assert outcome.subperiod_twr_method50 == Decimal("0.1")
    assert outcome.return_period_start_date == date(2026, 7, 1)
    assert outcome.return_period_day_count == 1


@pytest.mark.parametrize(
    ("nav", "flow_in", "flow_out"),
    [("150", "50", "0"), ("70", "0", "30"), ("120", "50", "30")],
)
def test_external_cash_only_changes_are_return_neutral(
    nav: str,
    flow_in: str,
    flow_out: str,
) -> None:
    anchor = advance_daily_twr(_period(1, "100"), TwrAccumulatorState.initial())
    outcome = advance_daily_twr(
        _period(2, nav, flow_in=flow_in, flow_out=flow_out),
        anchor.next_state,
    )
    assert outcome.subperiod_twr_method50 == Decimal("0")


def test_carry_and_stale_values_are_measured_but_not_fresh_endpoints() -> None:
    anchor = advance_daily_twr(_period(1, "100"), TwrAccumulatorState.initial())
    carried = advance_daily_twr(
        _period(2, "100", valuation=ValuationStatus.CARRY_FORWARD),
        anchor.next_state,
    )
    stale = advance_daily_twr(
        _period(3, "101", valuation=ValuationStatus.STALE),
        carried.next_state,
    )
    fresh = advance_daily_twr(_period(4, "110"), stale.next_state)

    assert carried.status is DailyCalculationStatus.NO_NEW_VALUATION
    assert carried.reason_codes == (PortfolioDailyReasonCode.CARRY_FORWARD_VALUATION,)
    assert carried.return_period_start_date is None
    assert carried.return_period_day_count is None
    assert stale.status is DailyCalculationStatus.NO_NEW_VALUATION
    assert stale.next_state.anchor_nav == Decimal("100")
    assert fresh.status is DailyCalculationStatus.CALCULATED
    assert fresh.subperiod_twr_method50 == Decimal("0.1")
    assert fresh.return_period_start_date == date(2026, 7, 1)
    assert fresh.return_period_day_count == 3


@pytest.mark.parametrize(
    ("flow_in", "flow_out"),
    [("20", "0"), ("0", "20")],
)
def test_flow_inside_nonfresh_gap_breaks_and_next_fresh_day_only_reanchors(
    flow_in: str,
    flow_out: str,
) -> None:
    anchor = advance_daily_twr(_period(1, "100"), TwrAccumulatorState.initial())
    broken = advance_daily_twr(
        _period(
            2,
            "120",
            flow_in=flow_in,
            flow_out=flow_out,
            valuation=ValuationStatus.CARRY_FORWARD,
        ),
        anchor.next_state,
    )
    reanchored = advance_daily_twr(_period(3, "150"), broken.next_state)
    resumed = advance_daily_twr(_period(4, "165"), reanchored.next_state)

    assert broken.status is DailyCalculationStatus.BROKEN
    assert broken.reason_codes == (
        PortfolioDailyReasonCode.CARRY_FORWARD_VALUATION,
        PortfolioDailyReasonCode.GAP_EXTERNAL_FLOW,
    )
    assert reanchored.status is DailyCalculationStatus.REANCHORED
    assert reanchored.reason_codes == (PortfolioDailyReasonCode.REANCHORED_AFTER_BREAK,)
    assert reanchored.subperiod_twr_method50 is None
    assert resumed.subperiod_twr_method50 == Decimal("0.1")


def test_bod_inflow_on_fresh_endpoint_after_nonfresh_gap_breaks() -> None:
    anchor = advance_daily_twr(_period(1, "100"), TwrAccumulatorState.initial())
    carried = advance_daily_twr(
        _period(2, "100", valuation=ValuationStatus.CARRY_FORWARD),
        anchor.next_state,
    )
    broken = advance_daily_twr(
        _period(3, "120", flow_in="20"),
        carried.next_state,
    )

    assert broken.status is DailyCalculationStatus.BROKEN
    assert broken.reason_codes == (PortfolioDailyReasonCode.GAP_EXTERNAL_FLOW,)
    assert broken.subperiod_twr_method50 is None
    assert broken.next_state.status is TwrWindowStatus.BROKEN


def test_eod_outflow_on_fresh_endpoint_after_nonfresh_gap_is_exact() -> None:
    anchor = advance_daily_twr(_period(1, "100"), TwrAccumulatorState.initial())
    carried = advance_daily_twr(
        _period(2, "100", valuation=ValuationStatus.CARRY_FORWARD),
        anchor.next_state,
    )
    measured = advance_daily_twr(
        _period(3, "80", flow_out="20"),
        carried.next_state,
    )

    assert measured.status is DailyCalculationStatus.CALCULATED
    assert measured.return_period_start_date == date(2026, 7, 1)
    assert measured.return_period_day_count == 2
    assert measured.adjusted_beginning_value == Decimal("100")
    assert measured.adjusted_ending_value == Decimal("100")
    assert measured.subperiod_twr_method50 == 0


@pytest.mark.parametrize(
    ("period", "reason"),
    [
        (
            _period(1, None, coverage=CoverageStatus.PARTIAL),
            PortfolioDailyReasonCode.PARTIAL_COVERAGE,
        ),
        (
            _period(1, None, coverage=CoverageStatus.UNAVAILABLE),
            PortfolioDailyReasonCode.UNAVAILABLE_COVERAGE,
        ),
        (
            _period(1, None, fx=FxStatus.UNAVAILABLE),
            PortfolioDailyReasonCode.FX_UNAVAILABLE,
        ),
    ],
)
def test_missing_asset_or_fx_is_blocking_not_carry(
    period: PortfolioDailyInput,
    reason: PortfolioDailyReasonCode,
) -> None:
    outcome = advance_daily_twr(period, TwrAccumulatorState.initial())
    publication = quantize_portfolio_daily_publication(period, outcome)

    assert outcome.status is DailyCalculationStatus.BROKEN
    assert outcome.reason_codes == (reason,)
    assert publication.nav is None
    assert publication.subperiod_twr_published is None


def test_carry_publication_retains_measured_nav_and_prior_cumulative_state() -> None:
    anchor = advance_daily_twr(_period(1, "100"), TwrAccumulatorState.initial())
    gain = advance_daily_twr(_period(2, "110"), anchor.next_state)
    carry_period = _period(3, "110", valuation=ValuationStatus.CARRY_FORWARD)
    carry = advance_daily_twr(carry_period, gain.next_state)
    publication = quantize_portfolio_daily_publication(carry_period, carry)

    assert publication.nav == Decimal("110.00000000")
    assert publication.subperiod_twr_published is None
    assert publication.cumulative_twr_published == Decimal("0.100000000000000000")
    assert (
        carry.next_state.wealth_index_method50
        == gain.next_state.wealth_index_method50
    )


def test_drawdown_and_exact_state_are_not_contaminated_by_publication_rounding() -> (
    None
):
    anchor = advance_daily_twr(_period(1, "1"), TwrAccumulatorState.initial())
    gain_period = _period(2, "1.1234567890123456785")
    gain = advance_daily_twr(gain_period, anchor.next_state)
    publication = quantize_portfolio_daily_publication(gain_period, gain)
    loss = advance_daily_twr(_period(3, "0.8987654312098765428"), gain.next_state)

    assert gain.subperiod_twr_method50 == Decimal("0.1234567890123456785")
    assert gain.next_state.wealth_index_method50 == Decimal("1.1234567890123456785")
    assert publication.nav == Decimal("1.12345679")
    assert publication.subperiod_twr_published == Decimal("0.123456789012345678")
    assert loss.drawdown_method50 == Decimal("-0.2")


def test_twr_boundary_sums_preserve_more_than_fifty_digits_in_hostile_context() -> None:
    anchor_nav = Decimal(
        "12345678901234567890123456789012345678901234567890123456789000"
    )
    exact_adjusted_value = Decimal(
        "12345678901234567890123456789012345678901234567890123456789001"
    )
    anchor = advance_daily_twr(
        _period(1, str(anchor_nav)),
        TwrAccumulatorState.initial(),
    )

    with localcontext() as hostile_context:
        hostile_context.prec = 6
        hostile_context.rounding = ROUND_DOWN
        outcome = advance_daily_twr(
            _period(
                2,
                str(anchor_nav),
                flow_in="1",
                flow_out="1",
            ),
            anchor.next_state,
        )

    assert outcome.adjusted_beginning_value == exact_adjusted_value
    assert outcome.adjusted_ending_value == exact_adjusted_value
    assert outcome.subperiod_twr_method50 == Decimal("0")
    assert outcome.next_state.wealth_index_method50 == Decimal("1")


def test_twr_nonterminating_division_uses_method_precision_not_ambient_context() -> (
    None
):
    anchor = advance_daily_twr(
        _period(1, "3"),
        TwrAccumulatorState.initial(),
    )

    with localcontext() as hostile_context:
        hostile_context.prec = 6
        hostile_context.rounding = ROUND_DOWN
        outcome = advance_daily_twr(_period(2, "1"), anchor.next_state)

    assert outcome.subperiod_twr_method50 == Decimal(
        "-0.66666666666666666666666666666666666666666666666667"
    )
    assert outcome.next_state.wealth_index_method50 == Decimal(
        "0.33333333333333333333333333333333333333333333333333"
    )
    assert outcome.cumulative_twr_method50 == outcome.subperiod_twr_method50
    assert outcome.drawdown_method50 == outcome.subperiod_twr_method50


def test_twr_factor_add_and_wealth_multiply_are_distinct_method50_boundaries() -> (
    None
):
    prior_wealth = Decimal(
        "6.5868344978690736625851781286570704999622830388368"
    )
    subperiod_return = Decimal(
        "0.22914177763170669074391500080636083778353374068124"
    )
    exact_factor = exact_decimal_sum((Decimal("1"), subperiod_return))
    method_factor = method_decimal_add(Decimal("1"), subperiod_return)
    state = TwrAccumulatorState(
        status=TwrWindowStatus.ACTIVE,
        wealth_index_method50=prior_wealth,
        peak_wealth_index_method50=prior_wealth,
        anchor_nav=Decimal("1"),
        anchor_date=date(2026, 7, 1),
        last_as_of_date=date(2026, 7, 1),
    )

    outcome = advance_daily_twr(
        _period(2, str(exact_factor)),
        state,
    )

    expected_wealth = Decimal(
        "8.0961534636766433381005738264705832943975119738807"
    )
    fused_wealth = method_decimal_multiply(prior_wealth, exact_factor)
    assert outcome.subperiod_twr_method50 == subperiod_return
    assert method_factor == Decimal(
        "1.2291417776317066907439150008063608377835337406812"
    )
    assert outcome.wealth_index_method50 == expected_wealth
    assert fused_wealth == Decimal(
        "8.0961534636766433381005738264705832943975119738810"
    )
    assert outcome.wealth_index_method50 != fused_wealth
    assert outcome.wealth_chain_rounding_adjustment_exact == Decimal(
        "-3.670602706288601177205914765946733427035968762816E-50"
    )
    assert exact_decimal_sum(
        (
            exact_decimal_product(prior_wealth, method_factor),
            outcome.wealth_chain_rounding_adjustment_exact,
        )
    ) == expected_wealth


def test_twr_common_numerator_preserves_return_before_factor_rounding() -> None:
    opening = Decimal("1E+50")
    anchor = advance_daily_twr(
        _period(1, str(opening)),
        TwrAccumulatorState.initial(),
    )
    outcome = advance_daily_twr(
        _period(2, str(exact_decimal_sum((opening, Decimal("1"))))),
        anchor.next_state,
    )

    assert outcome.subperiod_twr_method50 == Decimal("1E-50")
    assert outcome.next_state.wealth_index_method50 == Decimal("1")
    # Around 1 the precision-50 ULP is 1E-49, so this factor increment is
    # below half an ULP and add50 rounds back to 1.  The return evidence
    # remains visible while the subsequent multiplication has no residual.
    assert outcome.wealth_chain_rounding_adjustment_exact == Decimal("0")


def test_drawdown_common_numerator_preserves_tiny_loss_from_large_peak() -> None:
    peak_nav = Decimal("1E+50")
    anchor = advance_daily_twr(
        _period(1, str(peak_nav)),
        TwrAccumulatorState.initial(),
    )
    loss = advance_daily_twr(
        _period(2, str(exact_decimal_subtract(peak_nav, Decimal("1")))),
        anchor.next_state,
    )

    assert loss.subperiod_twr_method50 == Decimal("-1E-50")
    assert loss.drawdown_method50 == Decimal("-1E-50")


def test_long_nonterminating_twr_chain_is_bounded_and_records_exact_rounding() -> None:
    start = date(2025, 1, 1)
    state = advance_daily_twr(
        PortfolioDailyInput(as_of_date=start, measured_nav=Decimal("3")),
        TwrAccumulatorState.initial(),
    ).next_state
    serialized_width = 0

    with localcontext() as hostile_context:
        hostile_context.prec = 6
        hostile_context.rounding = ROUND_DOWN
        for offset in range(1, 2_001):
            nav = Decimal("1") if offset % 2 else Decimal("3")
            previous_wealth = state.wealth_index_method50
            assert previous_wealth is not None
            outcome = advance_daily_twr(
                PortfolioDailyInput(
                    as_of_date=start + timedelta(days=offset),
                    measured_nav=nav,
                ),
                state,
            )
            assert outcome.subperiod_twr_method50 is not None
            assert outcome.wealth_index_method50 is not None
            assert outcome.wealth_chain_rounding_adjustment_exact is not None
            method_factor = method_decimal_add(
                Decimal("1"),
                outcome.subperiod_twr_method50,
            )
            exact_unrounded_wealth = exact_decimal_product(
                previous_wealth,
                method_factor,
            )
            expected_wealth = method_decimal_multiply(
                previous_wealth,
                method_factor,
            )
            assert outcome.wealth_index_method50 == expected_wealth
            assert exact_decimal_sum(
                (
                    exact_unrounded_wealth,
                    outcome.wealth_chain_rounding_adjustment_exact,
                )
            ) == expected_wealth
            method_ulp = Decimal(
                (
                    0,
                    (1,),
                    expected_wealth.adjusted() - 49,
                )
            )
            assert (
                outcome.wealth_chain_rounding_adjustment_exact.copy_abs()
                <= method_ulp / Decimal("2")
            )
            serialized_width += len(canonical_decimal(expected_wealth)) + len(
                canonical_decimal(outcome.wealth_chain_rounding_adjustment_exact)
            )
            state = outcome.next_state

    assert state.wealth_index_method50 is not None
    assert len(state.wealth_index_method50.as_tuple().digits) <= 50
    assert serialized_width < 2_000 * 230


def test_exact_total_loss_is_reported_then_forces_a_fresh_reanchor() -> None:
    anchor = advance_daily_twr(_period(1, "100"), TwrAccumulatorState.initial())
    loss = advance_daily_twr(_period(2, "0"), anchor.next_state)
    reanchor = advance_daily_twr(_period(3, "25"), loss.next_state)

    assert loss.status is DailyCalculationStatus.CALCULATED
    assert loss.subperiod_twr_method50 == Decimal("-1")
    assert loss.cumulative_twr_method50 == Decimal("-1")
    assert loss.drawdown_method50 == Decimal("-1")
    assert loss.next_state.status is TwrWindowStatus.BROKEN
    assert reanchor.status is DailyCalculationStatus.REANCHORED
    assert reanchor.subperiod_twr_method50 is None


def test_positive_nav_that_rounds_to_method_total_loss_breaks_next_state() -> None:
    anchor = advance_daily_twr(
        _period(1, "1E+100"),
        TwrAccumulatorState.initial(),
    )
    loss = advance_daily_twr(_period(2, "1"), anchor.next_state)
    reanchor = advance_daily_twr(_period(3, "2"), loss.next_state)

    assert loss.status is DailyCalculationStatus.CALCULATED
    assert loss.subperiod_twr_method50 == Decimal("-1")
    assert loss.wealth_index_method50 == 0
    assert loss.next_state.status is TwrWindowStatus.BROKEN
    assert reanchor.status is DailyCalculationStatus.REANCHORED
    assert reanchor.reason_codes == (
        PortfolioDailyReasonCode.REANCHORED_AFTER_BREAK,
    )


@pytest.mark.parametrize(
    ("anchor_nav", "ending_nav"),
    (
        ("1E-8", "1E+25"),
        (
            "3E+51",
            str(exact_decimal_sum((Decimal("3E+51"), Decimal("1")))),
        ),
    ),
)
def test_method_values_outside_declared_typmod_domain_fail_closed(
    anchor_nav: str,
    ending_nav: str,
) -> None:
    anchor = advance_daily_twr(
        _period(1, anchor_nav),
        TwrAccumulatorState.initial(),
    )
    outcome = advance_daily_twr(_period(2, ending_nav), anchor.next_state)

    assert outcome.status is DailyCalculationStatus.BROKEN
    assert outcome.reason_codes == (PortfolioDailyReasonCode.NUMERIC_FAILURE,)
    assert outcome.next_state.status is TwrWindowStatus.BROKEN


@pytest.mark.parametrize(
    "kwargs",
    [
        {"measured_nav": 100.0},
        {"external_flow_in": 1.0},
        {"external_flow_out": 1.0},
    ],
)
def test_twr_contract_rejects_every_float(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "as_of_date": date(2026, 7, 1),
        "measured_nav": Decimal("100"),
        "external_flow_in": Decimal("0"),
        "external_flow_out": Decimal("0"),
    }
    values.update(kwargs)
    with pytest.raises(PortfolioDailyExactError, match="float"):
        PortfolioDailyInput(**values)  # type: ignore[arg-type]


def test_measurement_contract_fails_closed_instead_of_publishing_partial_nav() -> None:
    with pytest.raises(PortfolioDailyExactError, match="must be null"):
        _period(1, "99", coverage=CoverageStatus.PARTIAL)
    with pytest.raises(PortfolioDailyExactError, match="require measured_nav"):
        _period(1, None)
    with pytest.raises(PortfolioDailyExactError, match="date"):
        PortfolioDailyInput(
            as_of_date=datetime(2026, 7, 1),  # type: ignore[arg-type]
            measured_nav=Decimal("1"),
        )


def test_nonpositive_anchor_and_adjusted_denominator_fail_closed() -> None:
    anchor = advance_daily_twr(_period(1, "0"), TwrAccumulatorState.initial())
    assert anchor.status is DailyCalculationStatus.BROKEN
    assert anchor.reason_codes == (
        PortfolioDailyReasonCode.NON_POSITIVE_REANCHOR_VALUE,
    )

    active = advance_daily_twr(_period(1, "100"), TwrAccumulatorState.initial())
    # A normal portfolio anchor is positive and inflows cannot make the
    # denominator non-positive.  State construction itself prevents a bad anchor.
    with pytest.raises(PortfolioDailyExactError, match="anchor_nav"):
        TwrAccumulatorState(
            status=TwrWindowStatus.ACTIVE,
            wealth_index_method50=Decimal("1"),
            peak_wealth_index_method50=Decimal("1"),
            anchor_nav=Decimal("0"),
            anchor_date=date(2026, 7, 1),
            last_as_of_date=date(2026, 7, 1),
        )
    assert active.next_state.anchor_nav == Decimal("100")


def test_active_state_and_no_new_outcome_shapes_fail_closed() -> None:
    anchor = advance_daily_twr(_period(1, "100"), TwrAccumulatorState.initial())
    active = anchor.next_state
    with pytest.raises(PortfolioDailyExactError, match="wealth/peak"):
        replace(active, wealth_index_method50=Decimal("0"))

    carried = advance_daily_twr(
        _period(2, "100", valuation=ValuationStatus.CARRY_FORWARD),
        active,
    )
    with pytest.raises(PortfolioDailyExactError, match="no-new-valuation invariant"):
        replace(
            carried,
            return_period_start_date=date(2026, 7, 1),
            return_period_day_count=1,
        )
    with pytest.raises(
        PortfolioDailyExactError,
        match="active no-new-valuation method evidence mismatch",
    ):
        replace(carried, cumulative_twr_method50=Decimal("0.01"))
    with pytest.raises(
        PortfolioDailyExactError,
        match="awaiting no-new-valuation cannot retain method evidence",
    ):
        replace(
            carried,
            next_state=TwrAccumulatorState.awaiting(
                last_as_of_date=date(2026, 7, 2)
            ),
        )


def test_dates_are_strictly_increasing() -> None:
    state = advance_daily_twr(_period(2, "100"), TwrAccumulatorState.initial())
    with pytest.raises(PortfolioDailyExactError, match="strictly later"):
        advance_daily_twr(_period(2, "101"), state.next_state)


def test_rollup_checks_exact_and_publication_closure_without_residual_plug() -> None:
    passed = validate_rollup(
        RollupCheckInput(
            kind=RollupKind.PORTFOLIO,
            publication_field="nav",
            parent_value=Decimal("100"),
            component_values=(Decimal("25"), Decimal("75")),
        )
    )
    failed = validate_rollup(
        RollupCheckInput(
            kind=RollupKind.PORTFOLIO,
            publication_field="nav",
            parent_value=Decimal("0.000000024"),
            component_values=(Decimal("0.000000006"),) * 4,
        )
    )

    assert passed.status is RollupStatus.PASSED
    assert passed.exact_residual == 0
    assert failed.status is RollupStatus.FAILED
    assert failed.publication_residual == Decimal("-0.00000002")
    assert failed.reason_codes == (
        PortfolioDailyReasonCode.PUBLICATION_ROLLUP_RESIDUAL_EXCEEDED,
    )


def test_rollup_exact_total_is_not_truncated_at_fifty_digits() -> None:
    component = Decimal(
        "0.123456789012345678901234567890123456789012345678901234567890"
    )
    final_quantum = Decimal(
        "0.000000000000000000000000000000000000000000000000000000000001"
    )
    parent = Decimal("0.123456789012345678901234567890123456789012345678901234567891")

    with localcontext() as hostile_context:
        hostile_context.prec = 6
        hostile_context.rounding = ROUND_DOWN
        result = validate_rollup(
            RollupCheckInput(
                kind=RollupKind.PORTFOLIO,
                publication_field="nav",
                parent_value=parent,
                component_values=(component, final_quantum),
            )
        )

    assert result.status is RollupStatus.PASSED
    assert result.exact_component_total == parent
    assert result.exact_residual == Decimal("0")
    assert result.publication_residual == Decimal("0")
