from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN, localcontext
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from portfolio_app.api.routes import performance as performance_routes
from portfolio_app.calculations.portfolio_daily import (
    published_views,
    reporting_engine,
)
from portfolio_app.calculations.numeric import (
    RATIO_SCALE,
    exact_decimal_product,
    exact_decimal_subtract,
    exact_decimal_sum,
    method_decimal_add,
    method_decimal_divide,
    method_decimal_multiply,
    method_decimal_subtract,
    quantize_decimal,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_contribution_output,
    portfolio_daily_snapshot_output,
)
from portfolio_app.calculations.portfolio_daily.published_repository import (
    CurrentPortfolioDailyPublication,
    PortfolioDailyContribution,
    PortfolioDailySnapshot,
    _metadata_from_row,
    _typed_output_row,
)
from portfolio_app.calculations.portfolio_daily.reporting_engine import (
    CalendarFrequency,
    FRONGELLO_METHOD_VERSION,
    STATISTICS_METHOD_VERSION_MONTHLY,
    STATISTICS_METHOD_VERSION_WEEKLY,
    build_linked_attribution,
    build_return_calendar,
    build_selected_range_performance,
)
from portfolio_app.db.session import get_db_session
from portfolio_app.main import app
from tests.test_portfolio_daily_published_repository import (
    _metadata_row,
    _output_row,
)


pytestmark = pytest.mark.no_database


def _publication(
    *,
    snapshots: tuple[dict[str, object], ...],
    contributions: tuple[dict[str, object], ...],
    range_start: date,
    range_end: date,
) -> CurrentPortfolioDailyPublication:
    normalized_snapshots: list[dict[str, object]] = []
    previous_wealth: Decimal | None = None
    previous_peak: Decimal | None = None
    for source in snapshots:
        row = dict(source)
        if row["return_chain_status"] == "active":
            subperiod = row["subperiod_twr_method50"]
            assert isinstance(subperiod, Decimal)
            if previous_wealth is not None:
                assert previous_peak is not None
                factor = method_decimal_add(Decimal("1"), subperiod)
                raw_wealth = exact_decimal_product(previous_wealth, factor)
                wealth = method_decimal_multiply(previous_wealth, factor)
                peak = max(previous_peak, wealth)
                row["wealth_index_method50"] = wealth
                row["peak_wealth_index_method50"] = peak
                row["cumulative_twr_method50"] = method_decimal_subtract(
                    wealth,
                    Decimal("1"),
                )
                row["drawdown_method50"] = method_decimal_divide(
                    method_decimal_subtract(wealth, peak),
                    peak,
                )
                row["wealth_chain_rounding_adjustment_exact"] = exact_decimal_subtract(
                    wealth, raw_wealth
                )
            previous_wealth = row["wealth_index_method50"]  # type: ignore[assignment]
            previous_peak = row["peak_wealth_index_method50"]  # type: ignore[assignment]
        for method_field, published_field in (
            ("subperiod_twr_method50", "subperiod_twr_published"),
            ("cumulative_twr_method50", "cumulative_twr_published"),
            ("wealth_index_method50", "wealth_index_published"),
            ("peak_wealth_index_method50", "peak_wealth_index_published"),
            ("drawdown_method50", "drawdown_published"),
        ):
            value = row[method_field]
            row[published_field] = (
                None
                if value is None
                else quantize_decimal(
                    value,  # type: ignore[arg-type]
                    scale=RATIO_SCALE,
                    field_name=published_field,
                )
            )
        normalized_snapshots.append(row)
    snapshots = tuple(normalized_snapshots)
    first = snapshots[0]
    run_id = first["run_id"]
    token = first["output_fencing_token"]
    metadata = _metadata_from_row(
        _metadata_row(
            publication_id=uuid4(),
            run_id=run_id,  # type: ignore[arg-type]
            manifest_id=uuid4(),
            token=token,  # type: ignore[arg-type]
        )
    )
    metadata = replace(
        metadata,
        output_range_start=range_start,
        output_range_end=range_end,
    )
    return CurrentPortfolioDailyPublication(
        metadata=metadata,
        requested_range_start=range_start,
        requested_range_end=range_end,
        snapshots=tuple(
            _typed_output_row(
                PortfolioDailySnapshot,
                portfolio_daily_snapshot_output,
                row,
                publication=metadata,
            )
            for row in snapshots
        ),
        holdings=(),
        balances=(),
        lots=(),
        lot_dispositions=(),
        contributions=tuple(
            _typed_output_row(
                PortfolioDailyContribution,
                portfolio_daily_contribution_output,
                row,
                publication=metadata,
            )
            for row in contributions
        ),
    )


def _snapshot(
    *,
    run_id,
    token: int,
    as_of: date,
    period_start: date,
    opening: str,
    closing: str,
    subperiod_return: str,
    cumulative_return: str,
    flow_in: str = "0",
    flow_out: str = "0",
) -> dict[str, object]:
    cumulative = Decimal(cumulative_return)
    wealth = method_decimal_add(Decimal("1"), cumulative)
    subperiod = Decimal(subperiod_return)
    return _output_row(
        portfolio_daily_snapshot_output,
        run_id=run_id,
        token=token,
        as_of_date=as_of,
        base_currency="CNY",
        measured_nav=True,
        measured_position_market_value=True,
        measured_book_pnl=True,
        measured_return=True,
        measured_external_flows=True,
        opening_nav=Decimal(opening),
        closing_nav=Decimal(closing),
        external_flow_in=Decimal(flow_in),
        external_flow_out=Decimal(flow_out),
        economic_pnl=(
            Decimal(closing) + Decimal(flow_out) - Decimal(opening) - Decimal(flow_in)
        ),
        subperiod_twr_method50=subperiod,
        subperiod_twr_published=quantize_decimal(
            subperiod,
            scale=RATIO_SCALE,
            field_name="subperiod_twr_published",
        ),
        cumulative_twr_method50=cumulative,
        cumulative_twr_published=quantize_decimal(
            cumulative,
            scale=RATIO_SCALE,
            field_name="cumulative_twr_published",
        ),
        wealth_index_method50=wealth,
        wealth_index_published=quantize_decimal(
            wealth,
            scale=RATIO_SCALE,
            field_name="wealth_index_published",
        ),
        peak_wealth_index_method50=wealth,
        peak_wealth_index_published=quantize_decimal(
            wealth,
            scale=RATIO_SCALE,
            field_name="peak_wealth_index_published",
        ),
        drawdown_method50=Decimal("0"),
        drawdown_published=Decimal("0"),
        wealth_chain_rounding_adjustment_exact=Decimal("0"),
        reliable_anchor_date=as_of,
        reliable_anchor_nav_exact=Decimal(closing),
        reliable_anchor_nav=Decimal(closing),
        return_period_start_date=period_start,
        return_period_end_date=as_of,
        return_period_day_count=(as_of - period_start).days,
        calculation_status="calculated",
        return_chain_status="active",
        nav_coverage_state="complete",
        nav_reason_codes=[],
        book_pnl_coverage_state="complete",
        book_pnl_reason_codes=[],
        return_coverage_state="complete",
        return_reason_codes=[],
        flow_coverage_state="complete",
        flow_reason_codes=[],
        position_attribution_coverage_state="complete",
        position_attribution_reason_codes=[],
        valuation_endpoint_status="fresh",
        valuation_reason_codes=[],
    )


def _reanchor_snapshot(
    *,
    run_id,
    token: int,
    as_of: date,
    nav: str,
    reason: str = "initial_anchor",
) -> dict[str, object]:
    row = _snapshot(
        run_id=run_id,
        token=token,
        as_of=as_of,
        period_start=as_of - timedelta(days=1),
        opening=nav,
        closing=nav,
        subperiod_return="0",
        cumulative_return="0",
    )
    row.update(
        measured_return=False,
        subperiod_twr_method50=None,
        subperiod_twr_published=None,
        cumulative_twr_method50=Decimal("0"),
        cumulative_twr_published=Decimal("0"),
        wealth_index_method50=Decimal("1"),
        wealth_index_published=Decimal("1"),
        peak_wealth_index_method50=Decimal("1"),
        peak_wealth_index_published=Decimal("1"),
        drawdown_method50=Decimal("0"),
        drawdown_published=Decimal("0"),
        wealth_chain_rounding_adjustment_exact=None,
        reliable_anchor_date=as_of,
        reliable_anchor_nav_exact=Decimal(nav),
        reliable_anchor_nav=Decimal(nav),
        return_period_start_date=None,
        return_period_end_date=None,
        return_period_day_count=None,
        calculation_status="reanchored",
        return_chain_status="reanchor",
        return_coverage_state="unavailable",
        return_reason_codes=[reason],
    )
    return row


def _no_new_valuation_snapshot(
    *,
    run_id,
    token: int,
    as_of: date,
    anchor_date: date,
    nav: str,
    cumulative_return: str,
) -> dict[str, object]:
    row = _snapshot(
        run_id=run_id,
        token=token,
        as_of=as_of,
        period_start=anchor_date,
        opening=nav,
        closing=nav,
        subperiod_return="0",
        cumulative_return=cumulative_return,
    )
    row.update(
        measured_return=False,
        subperiod_twr_method50=None,
        subperiod_twr_published=None,
        wealth_chain_rounding_adjustment_exact=None,
        reliable_anchor_date=anchor_date,
        reliable_anchor_nav_exact=Decimal(nav),
        reliable_anchor_nav=Decimal(nav),
        return_period_start_date=None,
        return_period_end_date=None,
        return_period_day_count=None,
        calculation_status="no_new_valuation",
        return_chain_status="no_new_valuation",
        return_coverage_state="unavailable",
        return_reason_codes=["carry_forward_valuation"],
        valuation_endpoint_status="carry_forward",
        valuation_reason_codes=["market_data_quote_carried"],
    )
    return row


def _contribution(
    *,
    run_id,
    token: int,
    as_of: date,
    axis: str,
    key: str,
    label: str,
    opening: str,
    closing: str,
    pnl: str,
    contribution: str,
    flow_in: str = "0",
    flow_out: str = "0",
    division_adjustment: str = "0",
) -> dict[str, object]:
    contribution_method50 = Decimal(contribution)
    contribution_adjustment = Decimal(division_adjustment)
    effective_contribution = exact_decimal_sum(
        (contribution_method50, contribution_adjustment)
    )
    contribution_published = quantize_decimal(
        effective_contribution,
        scale=RATIO_SCALE,
        field_name="contribution_published",
    )
    return _output_row(
        portfolio_daily_contribution_output,
        run_id=run_id,
        token=token,
        as_of_date=as_of,
        axis=axis,
        group_key=key,
        group_label=label,
        measured=True,
        opening_nav_exact=Decimal(opening),
        opening_nav=Decimal(opening),
        opening_nav_rounding_adjustment=Decimal("0"),
        closing_nav_exact=Decimal(closing),
        closing_nav=Decimal(closing),
        closing_nav_rounding_adjustment=Decimal("0"),
        external_flow_in_exact=Decimal(flow_in),
        external_flow_in=Decimal(flow_in),
        external_flow_in_rounding_adjustment=Decimal("0"),
        external_flow_out_exact=Decimal(flow_out),
        external_flow_out=Decimal(flow_out),
        external_flow_out_rounding_adjustment=Decimal("0"),
        internal_flow_in_exact=Decimal("0"),
        internal_flow_in=Decimal("0"),
        internal_flow_in_rounding_adjustment=Decimal("0"),
        internal_flow_out_exact=Decimal("0"),
        internal_flow_out=Decimal("0"),
        internal_flow_out_rounding_adjustment=Decimal("0"),
        economic_pnl_exact=Decimal(pnl),
        economic_pnl=Decimal(pnl),
        economic_pnl_rounding_adjustment=Decimal("0"),
        contribution_method50=contribution_method50,
        contribution_published=contribution_published,
        contribution_division_adjustment_exact=contribution_adjustment,
        contribution_rounding_adjustment=exact_decimal_subtract(
            contribution_published,
            effective_contribution,
        ),
        closure_residual_exact=Decimal("0"),
        rounding_adjustment_base=Decimal("0"),
        coverage_state="complete",
        reason_codes=[],
    )


def _two_day_publication() -> CurrentPortfolioDailyPublication:
    run_id = uuid4()
    token = 17
    first = date(2026, 1, 2)
    second = date(2026, 1, 3)
    snapshots = (
        _snapshot(
            run_id=run_id,
            token=token,
            as_of=first,
            period_start=date(2026, 1, 1),
            opening="100",
            closing="110",
            subperiod_return="0.1",
            cumulative_return="0.1",
        ),
        _snapshot(
            run_id=run_id,
            token=token,
            as_of=second,
            period_start=first,
            opening="110",
            closing="132",
            subperiod_return="0.2",
            cumulative_return="0.32",
        ),
    )
    contributions = (
        _contribution(
            run_id=run_id,
            token=token,
            as_of=first,
            axis="portfolio",
            key="__portfolio__",
            label="Portfolio",
            opening="100",
            closing="110",
            pnl="10",
            contribution="0.1",
        ),
        _contribution(
            run_id=run_id,
            token=token,
            as_of=second,
            axis="portfolio",
            key="__portfolio__",
            label="Portfolio",
            opening="110",
            closing="132",
            pnl="22",
            contribution="0.2",
        ),
        _contribution(
            run_id=run_id,
            token=token,
            as_of=first,
            axis="instrument",
            key="A",
            label="A",
            opening="60",
            closing="66",
            pnl="6",
            contribution="0.06",
        ),
        _contribution(
            run_id=run_id,
            token=token,
            as_of=first,
            axis="instrument",
            key="B",
            label="B",
            opening="40",
            closing="44",
            pnl="4",
            contribution="0.04",
        ),
        _contribution(
            run_id=run_id,
            token=token,
            as_of=second,
            axis="instrument",
            key="A",
            label="A",
            opening="66",
            closing="79.2",
            pnl="13.2",
            contribution="0.12",
        ),
        _contribution(
            run_id=run_id,
            token=token,
            as_of=second,
            axis="instrument",
            key="B",
            label="B",
            opening="44",
            closing="52.8",
            pnl="8.8",
            contribution="0.08",
        ),
    )
    return _publication(
        snapshots=snapshots,
        contributions=contributions,
        range_start=first,
        range_end=second,
    )


def test_selected_range_twr_statistics_and_xirr_are_decimal_and_ambient_independent() -> (
    None
):
    publication = _two_day_publication()
    with localcontext() as hostile:
        hostile.prec = 6
        hostile.rounding = ROUND_DOWN
        result = build_selected_range_performance(publication)

    assert result.status == "ready"
    assert result.cumulative_twr_method50 == Decimal("0.32")
    assert result.effective_return_start_date == date(2026, 1, 1)
    assert result.effective_return_end_date == date(2026, 1, 3)
    assert result.current_drawdown_method50 == Decimal("0")
    assert result.max_drawdown_method50 == Decimal("0")
    assert result.annualized_twr is None
    assert result.statistics.status == "unavailable"
    assert result.statistics.method_version == STATISTICS_METHOD_VERSION_MONTHLY
    assert result.statistics.frequency == "monthly"
    assert result.statistics.observation_count == 0
    assert result.statistics.excluded_partial_bucket_count == 1
    assert result.xirr.status == "ready"
    assert result.xirr.annualized_headline_eligible is False
    assert result.xirr.reason_codes == (
        "xirr_annualized_headline_requires_at_least_365_elapsed_days",
    )
    assert result.xirr.rate is not None
    assert isinstance(result.xirr.rate.method50, Decimal)
    assert result.xirr.rate.rounding_adjustment_exact == (
        result.xirr.rate.published - result.xirr.rate.method50
    )


def test_frongello_forward_linking_closes_each_axis_exactly() -> None:
    publication = _two_day_publication()
    performance = build_selected_range_performance(publication)
    attribution = build_linked_attribution(
        publication,
        performance,
        axis="instrument",
    )

    assert attribution.status == "ready"
    assert attribution.method_version == FRONGELLO_METHOD_VERSION
    assert attribution.cumulative_twr_method50 == Decimal("0.32")
    assert attribution.total_linked_contribution_effective == Decimal("0.32")
    assert attribution.closure_residual_exact == Decimal("0")
    assert {
        row.group_key: row.linked_contribution_effective for row in attribution.groups
    } == {"A": Decimal("0.192"), "B": Decimal("0.128")}
    assert all(row.closure_residual_exact == 0 for row in attribution.groups)


def test_annualized_twr_keeps_decimal50_exact_and_explicit_published_rounding() -> None:
    run_id = uuid4()
    token = 19
    as_of = date(2027, 1, 1)
    snapshot = _snapshot(
        run_id=run_id,
        token=token,
        as_of=as_of,
        period_start=date(2025, 1, 1),
        opening="100",
        closing="110",
        subperiod_return="0.1",
        cumulative_return="0.1",
    )
    portfolio_row = _contribution(
        run_id=run_id,
        token=token,
        as_of=as_of,
        axis="portfolio",
        key="__portfolio__",
        label="Portfolio",
        opening="100",
        closing="110",
        pnl="10",
        contribution="0.1",
    )
    publication = _publication(
        snapshots=(snapshot,),
        contributions=(portfolio_row,),
        range_start=as_of,
        range_end=as_of,
    )

    result = build_selected_range_performance(publication)
    assert result.annualized_twr is not None
    assert result.annualized_twr.method50 == Decimal(
        "0.0488088481701515469914535136799375984752718576815"
    )
    assert result.annualized_twr.published == Decimal("0.048808848170151547")
    assert result.annualized_twr.rounding_adjustment_exact == Decimal(
        "8.5464863200624015247281423185E-21"
    )


def test_xirr_known_one_year_ten_percent_has_deterministic_residual() -> None:
    run_id = uuid4()
    token = 23
    as_of = date(2026, 12, 31)
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=as_of,
                period_start=date(2025, 12, 31),
                opening="100",
                closing="110",
                subperiod_return="0.1",
                cumulative_return="0.1",
            ),
        ),
        contributions=(
            _contribution(
                run_id=run_id,
                token=token,
                as_of=as_of,
                axis="portfolio",
                key="__portfolio__",
                label="Portfolio",
                opening="100",
                closing="110",
                pnl="10",
                contribution="0.1",
            ),
        ),
        range_start=as_of,
        range_end=as_of,
    )

    result = build_selected_range_performance(publication)
    assert result.xirr.status == "ready"
    assert result.xirr.annualized_headline_eligible is True
    assert result.xirr.reason_codes == ()
    assert result.xirr.rate is not None
    assert result.xirr.rate.method50 == Decimal(
        "0.1000000000000000000000000000000000000000000000070"
    )
    assert result.xirr.rate.published == Decimal("0.100000000000000000")
    assert result.xirr.xnpv_residual_exact == Decimal("-6.4120E-46")


def test_xirr_irregular_dates_uses_actual_365_decimal_cash_flow_dates() -> None:
    run_id = uuid4()
    token = 29
    origin = date(2026, 1, 1)
    first = origin + timedelta(days=100)
    end = origin + timedelta(days=365)
    subperiod = Decimal("0.090909090909090909090909090909090909090909090909091")
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=first,
                period_start=origin,
                opening="100",
                closing="110",
                subperiod_return="0",
                cumulative_return="0",
                flow_in="10",
            ),
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=end,
                period_start=first,
                opening="110",
                closing="120",
                subperiod_return=str(subperiod),
                cumulative_return=str(subperiod),
            ),
        ),
        contributions=(
            _contribution(
                run_id=run_id,
                token=token,
                as_of=first,
                axis="portfolio",
                key="__portfolio__",
                label="Portfolio",
                opening="100",
                closing="110",
                pnl="0",
                contribution="0",
                flow_in="10",
            ),
            _contribution(
                run_id=run_id,
                token=token,
                as_of=end,
                axis="portfolio",
                key="__portfolio__",
                label="Portfolio",
                opening="110",
                closing="120",
                pnl="10",
                contribution=str(subperiod),
            ),
        ),
        range_start=first,
        range_end=end,
    )

    result = build_selected_range_performance(publication)
    assert result.xirr.status == "ready"
    assert result.xirr.annualized_headline_eligible is True
    assert result.xirr.rate is not None
    assert result.xirr.rate.method50 == Decimal(
        "0.0933088536465658337349077286887179823930152362324"
    )
    assert result.xirr.rate.published == Decimal("0.093308853646565834")
    assert result.xirr.xnpv_residual_exact == Decimal("-7.9480E-46")


def test_xirr_multiple_sign_changes_fail_closed_without_running_a_float_solver() -> (
    None
):
    run_id = uuid4()
    token = 31
    origin = date(2026, 1, 1)
    first = origin + timedelta(days=100)
    second = origin + timedelta(days=200)
    end = origin + timedelta(days=365)
    final_return = Decimal("-0.090909090909090909090909090909090909090909090909091")
    snapshots = (
        _snapshot(
            run_id=run_id,
            token=token,
            as_of=first,
            period_start=origin,
            opening="100",
            closing="10",
            subperiod_return="0.6",
            cumulative_return="0.6",
            flow_out="150",
        ),
        _snapshot(
            run_id=run_id,
            token=token,
            as_of=second,
            period_start=first,
            opening="10",
            closing="110",
            subperiod_return="0",
            cumulative_return="0.6",
            flow_in="100",
        ),
        _snapshot(
            run_id=run_id,
            token=token,
            as_of=end,
            period_start=second,
            opening="110",
            closing="100",
            subperiod_return=str(final_return),
            cumulative_return="0.45454545454545454545454545454545454545454545454545",
        ),
    )
    contributions = (
        _contribution(
            run_id=run_id,
            token=token,
            as_of=first,
            axis="portfolio",
            key="__portfolio__",
            label="Portfolio",
            opening="100",
            closing="10",
            pnl="60",
            contribution="0.6",
            flow_out="150",
        ),
        _contribution(
            run_id=run_id,
            token=token,
            as_of=second,
            axis="portfolio",
            key="__portfolio__",
            label="Portfolio",
            opening="10",
            closing="110",
            pnl="0",
            contribution="0",
            flow_in="100",
        ),
        _contribution(
            run_id=run_id,
            token=token,
            as_of=end,
            axis="portfolio",
            key="__portfolio__",
            label="Portfolio",
            opening="110",
            closing="100",
            pnl="-10",
            contribution=str(final_return),
        ),
    )
    publication = _publication(
        snapshots=snapshots,
        contributions=contributions,
        range_start=first,
        range_end=end,
    )

    result = build_selected_range_performance(publication)
    assert result.status == "ready"
    assert result.xirr.status == "unavailable"
    assert result.xirr.reason_codes == ("xirr_unique_root_not_provable",)
    assert result.xirr.rate is None
    assert result.xirr.xnpv_residual_exact is None


def test_xirr_root_outside_method_domain_is_typed_unavailable() -> None:
    run_id = uuid4()
    token = 35
    as_of = date(2026, 6, 2)
    factor = Decimal("1E-37")
    subperiod = method_decimal_subtract(factor, Decimal("1"))
    opening = Decimal("1E29")
    closing = Decimal("1E-8")
    pnl = exact_decimal_subtract(closing, opening)
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=as_of,
                period_start=date(2026, 6, 1),
                opening=str(opening),
                closing=str(closing),
                subperiod_return=str(subperiod),
                cumulative_return=str(subperiod),
            ),
        ),
        contributions=(
            _contribution(
                run_id=run_id,
                token=token,
                as_of=as_of,
                axis="portfolio",
                key="__portfolio__",
                label="Portfolio",
                opening=str(opening),
                closing=str(closing),
                pnl=str(pnl),
                contribution=str(subperiod),
            ),
        ),
        range_start=as_of,
        range_end=as_of,
    )

    result = build_selected_range_performance(publication)

    assert result.status == "ready"
    assert result.xirr.status == "unavailable"
    assert result.xirr.reason_codes == ("xirr_root_outside_method_domain",)
    assert result.xirr.rate is None
    assert result.xirr.xnpv_residual_exact is None


def test_xirr_rate_outside_method_domain_is_typed_unavailable() -> None:
    run_id = uuid4()
    token = 36
    as_of = date(2026, 6, 4)
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=as_of,
                period_start=date(2026, 6, 3),
                opening="10",
                closing="100",
                subperiod_return="9",
                cumulative_return="9",
            ),
        ),
        contributions=(
            _contribution(
                run_id=run_id,
                token=token,
                as_of=as_of,
                axis="portfolio",
                key="__portfolio__",
                label="Portfolio",
                opening="10",
                closing="100",
                pnl="90",
                contribution="9",
            ),
        ),
        range_start=as_of,
        range_end=as_of,
    )

    result = build_selected_range_performance(publication)

    assert result.status == "ready"
    assert result.cumulative_twr_method50 == Decimal("9")
    assert result.xirr.status == "unavailable"
    assert result.xirr.reason_codes == ("xirr_rate_outside_method_domain",)
    assert result.xirr.rate is None
    assert result.xirr.xnpv_residual_exact is None


def test_negative_drawdown_and_target_zero_downside_use_exact_decimal_policy() -> None:
    run_id = uuid4()
    token = 37
    first = date(2026, 1, 11)
    second = date(2026, 1, 18)
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=first,
                period_start=date(2026, 1, 4),
                opening="100",
                closing="110",
                subperiod_return="0.1",
                cumulative_return="0.1",
            ),
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=second,
                period_start=first,
                opening="110",
                closing="88",
                subperiod_return="-0.2",
                cumulative_return="-0.12",
            ),
        ),
        contributions=(
            _contribution(
                run_id=run_id,
                token=token,
                as_of=first,
                axis="portfolio",
                key="__portfolio__",
                label="Portfolio",
                opening="100",
                closing="110",
                pnl="10",
                contribution="0.1",
            ),
            _contribution(
                run_id=run_id,
                token=token,
                as_of=second,
                axis="portfolio",
                key="__portfolio__",
                label="Portfolio",
                opening="110",
                closing="88",
                pnl="-22",
                contribution="-0.2",
            ),
        ),
        range_start=date(2026, 1, 5),
        range_end=second,
    )

    result = build_selected_range_performance(
        publication,
        statistics_frequency="weekly",
    )
    assert result.current_drawdown_method50 == Decimal("-0.2")
    assert result.max_drawdown_method50 == Decimal("-0.2")
    downside = result.statistics.annualized_downside_deviation
    assert downside is not None
    assert result.statistics.method_version == STATISTICS_METHOD_VERSION_WEEKLY
    assert result.statistics.periods_per_year is not None
    assert result.statistics.periods_per_year.method50 == Decimal("52")
    assert downside.method50 == Decimal(
        "1.0198039027185569660056448218045563979127541892199"
    )
    assert downside.published == Decimal("1.019803902718556966")


def test_calendar_owns_return_period_by_open_left_closed_right_boundary() -> None:
    run_id = uuid4()
    token = 41
    february_end = date(2026, 2, 28)
    march_end = date(2026, 3, 31)
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=february_end,
                period_start=date(2026, 1, 31),
                opening="100",
                closing="110",
                subperiod_return="0.1",
                cumulative_return="0.1",
            ),
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=march_end,
                period_start=february_end,
                opening="110",
                closing="121",
                subperiod_return="0.1",
                cumulative_return="0.21",
            ),
        ),
        contributions=(
            _contribution(
                run_id=run_id,
                token=token,
                as_of=february_end,
                axis="portfolio",
                key="__portfolio__",
                label="Portfolio",
                opening="100",
                closing="110",
                pnl="10",
                contribution="0.1",
            ),
            _contribution(
                run_id=run_id,
                token=token,
                as_of=march_end,
                axis="portfolio",
                key="__portfolio__",
                label="Portfolio",
                opening="110",
                closing="121",
                pnl="11",
                contribution="0.1",
            ),
        ),
        range_start=date(2026, 2, 1),
        range_end=march_end,
    )

    buckets = build_return_calendar(publication, frequency="monthly")
    assert [bucket.bucket_key for bucket in buckets] == ["2026-02", "2026-03"]
    assert all(bucket.performance.status == "ready" for bucket in buckets)
    assert all(bucket.coverage_state == "complete" for bucket in buckets)
    assert buckets[0].performance.effective_return_start_date == date(2026, 1, 31)
    assert buckets[0].performance.effective_return_end_date == february_end
    performance = build_selected_range_performance(
        publication,
        statistics_frequency="monthly",
    )
    assert performance.statistics.status == "ready"
    assert performance.statistics.observation_count == 2
    assert performance.statistics.mean_period_return is not None
    assert performance.statistics.mean_period_return.method50 == Decimal("0.1")
    assert performance.statistics.periods_per_year is not None
    assert performance.statistics.periods_per_year.method50 == Decimal("12")


def test_calendar_assigns_a_sparse_return_to_its_endpoint_bucket() -> None:
    run_id = uuid4()
    token = 43
    as_of = date(2026, 3, 1)
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=as_of,
                period_start=date(2026, 1, 31),
                opening="100",
                closing="110",
                subperiod_return="0.1",
                cumulative_return="0.1",
            ),
        ),
        contributions=(
            _contribution(
                run_id=run_id,
                token=token,
                as_of=as_of,
                axis="portfolio",
                key="__portfolio__",
                label="Portfolio",
                opening="100",
                closing="110",
                pnl="10",
                contribution="0.1",
            ),
        ),
        range_start=as_of,
        range_end=as_of,
    )

    bucket = build_return_calendar(publication, frequency="monthly")[0]
    assert bucket.performance.status == "ready"
    assert bucket.performance.cumulative_twr_method50 == Decimal("0.1")
    assert bucket.performance.effective_return_start_date == date(2026, 1, 31)
    assert bucket.coverage_state == "partial"
    assert bucket.coverage_reason_codes == (
        "calendar_bucket_requested_end_before_calendar_end",
    )


@pytest.mark.parametrize(
    (
        "frequency",
        "range_start",
        "range_end",
        "anchor_date",
        "period_ends",
        "anchor_reason",
    ),
    (
        (
            "monthly",
            date(2026, 1, 1),
            date(2026, 3, 31),
            date(2026, 1, 15),
            (date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)),
            "initial_anchor",
        ),
        (
            "monthly",
            date(2026, 1, 1),
            date(2026, 3, 31),
            date(2026, 1, 15),
            (date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)),
            "reanchored_after_break",
        ),
        (
            "weekly",
            date(2026, 3, 2),
            date(2026, 3, 22),
            date(2026, 3, 4),
            (date(2026, 3, 8), date(2026, 3, 15), date(2026, 3, 22)),
            "initial_anchor",
        ),
        (
            "weekly",
            date(2026, 3, 2),
            date(2026, 3, 22),
            date(2026, 3, 4),
            (date(2026, 3, 8), date(2026, 3, 15), date(2026, 3, 22)),
            "reanchored_after_break",
        ),
    ),
)
def test_mid_bucket_anchor_or_reanchor_is_partial_and_excluded_from_statistics(
    frequency: CalendarFrequency,
    range_start: date,
    range_end: date,
    anchor_date: date,
    period_ends: tuple[date, date, date],
    anchor_reason: str,
) -> None:
    run_id = uuid4()
    token = 47
    first_end, second_end, third_end = period_ends
    publication = _publication(
        snapshots=(
            _reanchor_snapshot(
                run_id=run_id,
                token=token,
                as_of=anchor_date,
                nav="100",
                reason=anchor_reason,
            ),
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=first_end,
                period_start=anchor_date,
                opening="100",
                closing="110",
                subperiod_return="0.1",
                cumulative_return="0.1",
            ),
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=second_end,
                period_start=first_end,
                opening="110",
                closing="132",
                subperiod_return="0.2",
                cumulative_return="0.32",
            ),
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=third_end,
                period_start=second_end,
                opening="132",
                closing="171.6",
                subperiod_return="0.3",
                cumulative_return="0.716",
            ),
        ),
        contributions=(),
        range_start=range_start,
        range_end=range_end,
    )

    buckets = build_return_calendar(publication, frequency=frequency)
    assert [bucket.coverage_state for bucket in buckets] == [
        "partial",
        "complete",
        "complete",
    ]
    assert buckets[0].coverage_reason_codes == (
        "calendar_bucket_return_start_after_calendar_opening_boundary",
    )

    performance = build_selected_range_performance(
        publication,
        statistics_frequency=frequency,
    )
    assert performance.statistics.status == "ready"
    assert performance.statistics.observation_count == 2
    assert performance.statistics.excluded_partial_bucket_count == 1
    assert performance.statistics.excluded_unavailable_bucket_count == 0
    assert performance.statistics.mean_period_return is not None
    assert performance.statistics.mean_period_return.method50 == Decimal("0.25")


@pytest.mark.parametrize(
    ("frequency", "range_start", "range_end", "period_start"),
    (
        (
            "monthly",
            date(2026, 3, 1),
            date(2026, 3, 31),
            date(2026, 1, 31),
        ),
        (
            "weekly",
            date(2026, 3, 2),
            date(2026, 3, 8),
            date(2026, 2, 20),
        ),
    ),
)
def test_sparse_return_that_covers_bucket_boundaries_remains_complete(
    frequency: CalendarFrequency,
    range_start: date,
    range_end: date,
    period_start: date,
) -> None:
    run_id = uuid4()
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=49,
                as_of=range_end,
                period_start=period_start,
                opening="100",
                closing="110",
                subperiod_return="0.1",
                cumulative_return="0.1",
            ),
        ),
        contributions=(),
        range_start=range_start,
        range_end=range_end,
    )

    bucket = build_return_calendar(
        publication,
        frequency=frequency,
    )[0]
    assert bucket.performance.effective_return_start_date == period_start
    assert bucket.performance.effective_return_end_date == range_end
    assert bucket.coverage_state == "complete"
    assert bucket.coverage_reason_codes == ()


@pytest.mark.parametrize(
    ("frequency", "range_start", "range_end"),
    (
        ("monthly", date(2026, 1, 1), date(2026, 1, 31)),
        ("weekly", date(2026, 3, 2), date(2026, 3, 8)),
    ),
)
def test_calendar_opening_requires_the_preceding_eod_boundary(
    frequency: CalendarFrequency,
    range_start: date,
    range_end: date,
) -> None:
    run_id = uuid4()
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=51,
                as_of=range_end,
                period_start=range_start,
                opening="100",
                closing="110",
                subperiod_return="0.1",
                cumulative_return="0.1",
            ),
        ),
        contributions=(),
        range_start=range_start,
        range_end=range_end,
    )

    bucket = build_return_calendar(
        publication,
        frequency=frequency,
    )[0]
    assert bucket.coverage_state == "partial"
    assert bucket.coverage_reason_codes == (
        "calendar_bucket_return_start_after_calendar_opening_boundary",
    )


@pytest.mark.parametrize(
    ("frequency", "range_start", "range_end", "endpoint"),
    (
        (
            "monthly",
            date(2026, 3, 1),
            date(2026, 3, 31),
            date(2026, 3, 20),
        ),
        (
            "weekly",
            date(2026, 3, 2),
            date(2026, 3, 8),
            date(2026, 3, 6),
        ),
    ),
)
def test_calendar_requires_a_return_through_the_closing_boundary(
    frequency: CalendarFrequency,
    range_start: date,
    range_end: date,
    endpoint: date,
) -> None:
    run_id = uuid4()
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=53,
                as_of=endpoint,
                period_start=range_start - timedelta(days=1),
                opening="100",
                closing="110",
                subperiod_return="0.1",
                cumulative_return="0.1",
            ),
        ),
        contributions=(),
        range_start=range_start,
        range_end=range_end,
    )

    bucket = build_return_calendar(
        publication,
        frequency=frequency,
    )[0]
    assert bucket.coverage_state == "partial"
    assert bucket.coverage_reason_codes == (
        "calendar_bucket_return_end_before_calendar_closing_boundary",
    )


@pytest.mark.parametrize(
    ("frequency", "range_start", "fresh_endpoint", "range_end", "carry_dates"),
    (
        (
            "monthly",
            date(2026, 5, 1),
            date(2026, 5, 29),
            date(2026, 5, 31),
            (date(2026, 5, 30), date(2026, 5, 31)),
        ),
        (
            "weekly",
            date(2026, 3, 2),
            date(2026, 3, 6),
            date(2026, 3, 8),
            (date(2026, 3, 7), date(2026, 3, 8)),
        ),
    ),
)
def test_qualified_calendar_carry_closes_a_bucket_after_its_fresh_endpoint(
    frequency: CalendarFrequency,
    range_start: date,
    fresh_endpoint: date,
    range_end: date,
    carry_dates: tuple[date, date],
) -> None:
    run_id = uuid4()
    token = 55
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=fresh_endpoint,
                period_start=range_start - timedelta(days=1),
                opening="100",
                closing="110",
                subperiod_return="0.1",
                cumulative_return="0.1",
            ),
            *tuple(
                _no_new_valuation_snapshot(
                    run_id=run_id,
                    token=token,
                    as_of=carry_date,
                    anchor_date=fresh_endpoint,
                    nav="110",
                    cumulative_return="0.1",
                )
                for carry_date in carry_dates
            ),
        ),
        contributions=(),
        range_start=range_start,
        range_end=range_end,
    )

    bucket = build_return_calendar(publication, frequency=frequency)[0]
    assert bucket.performance.effective_return_end_date == fresh_endpoint
    assert bucket.performance.selected_end_date == range_end
    assert bucket.coverage_state == "complete"
    assert bucket.coverage_reason_codes == ()


def test_reporting_rejects_an_unsupported_calendar_frequency() -> None:
    with pytest.raises(ValueError, match="unsupported reporting calendar frequency"):
        build_return_calendar(
            _two_day_publication(),
            frequency="daily",  # type: ignore[arg-type]
        )


def _assert_no_json_float(value: object) -> None:
    if isinstance(value, float):
        raise AssertionError(f"published report emitted float {value!r}")
    if isinstance(value, dict):
        for item in value.values():
            _assert_no_json_float(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_json_float(item)


def test_consolidated_performance_report_uses_one_publication_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = _two_day_publication()
    calls: list[dict[str, object]] = []

    def fake_read(
        _session: object, **kwargs: object
    ) -> CurrentPortfolioDailyPublication:
        calls.append(kwargs)
        return publication

    monkeypatch.setattr(performance_routes, "read_published_range", fake_read)
    app.dependency_overrides[get_db_session] = lambda: object()
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/portfolios/portfolio-exact/performance/report",
                params={"axis": "instrument", "frequency": "monthly"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["tables"] == ("snapshots", "contributions")
    payload = response.json()
    _assert_no_json_float(payload)
    assert payload["publication"]["publication_id"] == str(
        publication.metadata.publication_id
    )
    assert payload["performance"]["cumulative_twr"]["method50"] == "0.32"
    assert payload["portfolio_bridge"]["closure_residual_exact"] == "0"
    assert payload["attribution"]["closure_residual_exact"] == "0"
    assert payload["return_calendar"][0]["cumulative_twr"]["method50"] == "0.32"


def test_attribution_rejects_offsetting_group_boundary_breaks() -> None:
    run_id = uuid4()
    token = 47
    first = date(2026, 1, 2)
    second = date(2026, 1, 3)
    third = date(2026, 1, 4)
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=first,
                period_start=date(2026, 1, 1),
                opening="100",
                closing="110",
                subperiod_return="0.1",
                cumulative_return="0.1",
            ),
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=second,
                period_start=first,
                opening="110",
                closing="121",
                subperiod_return="0.1",
                cumulative_return="0.21",
            ),
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=third,
                period_start=second,
                opening="121",
                closing="133.1",
                subperiod_return="0.1",
                cumulative_return="0.331",
            ),
        ),
        contributions=(
            _contribution(
                run_id=run_id,
                token=token,
                as_of=first,
                axis="instrument",
                key="A",
                label="A",
                opening="100",
                closing="110",
                pnl="10",
                contribution="0.1",
            ),
            _contribution(
                run_id=run_id,
                token=token,
                as_of=second,
                axis="instrument",
                key="A",
                label="A",
                opening="109",
                closing="120",
                pnl="11",
                contribution="0.1",
            ),
            _contribution(
                run_id=run_id,
                token=token,
                as_of=third,
                axis="instrument",
                key="A",
                label="A",
                opening="121",
                closing="133.1",
                pnl="12.1",
                contribution="0.1",
            ),
        ),
        range_start=first,
        range_end=third,
    )
    performance = build_selected_range_performance(
        publication,
        include_secondary_metrics=False,
    )

    with pytest.raises(ValueError, match="opening NAV does not equal"):
        build_linked_attribution(publication, performance, axis="instrument")


def test_attribution_fails_closed_without_using_an_epsilon() -> None:
    publication = _two_day_publication()
    broken = replace(
        publication,
        contributions=tuple(
            replace(
                row,
                contribution_method50=Decimal("0.0800000000000000001"),
            )
            if row.axis == "instrument"
            and row.as_of_date == date(2026, 1, 3)
            and row.group_key == "B"
            else row
            for row in publication.contributions
        ),
    )
    performance = build_selected_range_performance(broken)
    with pytest.raises(
        ValueError,
        match="effective daily contributions do not close",
    ):
        build_linked_attribution(broken, performance, axis="instrument")


def test_attribution_uses_division_adjustment_as_part_of_effective_contribution() -> (
    None
):
    run_id = uuid4()
    token = 53
    as_of = date(2026, 2, 2)
    raw_b = Decimal("0.03999999999999999999999999999999999999999999999999")
    division_adjustment = exact_decimal_subtract(Decimal("0.04"), raw_b)
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=as_of,
                period_start=date(2026, 2, 1),
                opening="100",
                closing="110",
                subperiod_return="0.1",
                cumulative_return="0.1",
            ),
        ),
        contributions=(
            _contribution(
                run_id=run_id,
                token=token,
                as_of=as_of,
                axis="instrument",
                key="A",
                label="A",
                opening="0",
                closing="0",
                pnl="0",
                contribution="0.06",
            ),
            _contribution(
                run_id=run_id,
                token=token,
                as_of=as_of,
                axis="instrument",
                key="B",
                label="B",
                opening="0",
                closing="0",
                pnl="0",
                contribution=str(raw_b),
                division_adjustment=str(division_adjustment),
            ),
        ),
        range_start=as_of,
        range_end=as_of,
    )

    performance = build_selected_range_performance(
        publication,
        include_secondary_metrics=False,
    )
    attribution = build_linked_attribution(
        publication,
        performance,
        axis="instrument",
    )

    assert division_adjustment == Decimal("1E-50")
    assert attribution.status == "ready"
    assert attribution.total_linked_contribution_effective == Decimal("0.1")
    assert {
        row.group_key: row.linked_contribution_method50 for row in attribution.groups
    } == {
        "A": Decimal("0.06"),
        "B": Decimal("0.04"),
    }


def test_frongello_balance_is_nonzero_and_deterministic_for_equal_raw_groups() -> None:
    run_id = uuid4()
    token = 59
    first = date(2026, 3, 2)
    second = date(2026, 3, 3)
    subperiod = Decimal("0.33333333333333333333333333333333333333333333333330")
    contribution = method_decimal_divide(subperiod, Decimal("2"))
    factor = method_decimal_add(Decimal("1"), subperiod)
    first_wealth = method_decimal_multiply(Decimal("1"), factor)
    first_cumulative = method_decimal_subtract(first_wealth, Decimal("1"))
    second_wealth = method_decimal_multiply(first_wealth, factor)
    second_cumulative = method_decimal_subtract(second_wealth, Decimal("1"))
    snapshots = (
        _snapshot(
            run_id=run_id,
            token=token,
            as_of=first,
            period_start=date(2026, 3, 1),
            opening="100",
            closing="100",
            subperiod_return=str(subperiod),
            cumulative_return=str(first_cumulative),
        ),
        _snapshot(
            run_id=run_id,
            token=token,
            as_of=second,
            period_start=first,
            opening="100",
            closing="100",
            subperiod_return=str(subperiod),
            cumulative_return=str(second_cumulative),
        ),
    )
    contributions = tuple(
        _contribution(
            run_id=run_id,
            token=token,
            as_of=as_of,
            axis="instrument",
            key=key,
            label=key,
            opening="0",
            closing="0",
            pnl="0",
            contribution=str(contribution),
        )
        for as_of in (first, second)
        for key in ("A", "B")
    )
    publication = _publication(
        snapshots=snapshots,
        contributions=contributions,
        range_start=first,
        range_end=second,
    )

    performance = build_selected_range_performance(
        publication,
        include_secondary_metrics=False,
    )
    attribution = build_linked_attribution(
        publication,
        performance,
        axis="instrument",
    )

    assert attribution.total_linking_adjustment_exact == Decimal("2E-50")
    assert [row.group_key for row in attribution.groups] == ["A", "B"]
    assert attribution.groups[0].linked_contribution_method50 == (
        attribution.groups[1].linked_contribution_method50
    )
    assert attribution.groups[0].linking_adjustment_exact == Decimal("2E-50")
    assert attribution.groups[1].linking_adjustment_exact == Decimal("0")
    assert attribution.total_linked_contribution_effective == (
        performance.cumulative_twr_method50
    )


def test_consolidated_report_builds_return_calendar_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication = _two_day_publication()
    original = published_views.build_return_calendar
    call_count = 0

    def counted_return_calendar(*args: object, **kwargs: object):
        nonlocal call_count
        call_count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        published_views,
        "build_return_calendar",
        counted_return_calendar,
    )

    report = published_views.build_performance_report_response(
        publication,
        frequency="monthly",
        axis="instrument",
    )

    assert report.performance.status == "ready"
    assert call_count == 1


def test_long_return_chain_is_linear_and_all_published_numbers_stay_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid4()
    token = 61
    observation_count = 1_000
    first = date(2023, 1, 2)
    subperiod = Decimal("0.00012345678901234567890123456789012345678901234567890")
    first_factor = method_decimal_add(Decimal("1"), subperiod)
    first_wealth = method_decimal_multiply(Decimal("1"), first_factor)
    first_cumulative = method_decimal_subtract(first_wealth, Decimal("1"))
    snapshots = tuple(
        _snapshot(
            run_id=run_id,
            token=token,
            as_of=first + timedelta(days=index),
            period_start=first + timedelta(days=index - 1),
            opening="100",
            closing="100",
            subperiod_return=str(subperiod),
            cumulative_return=str(first_cumulative if index == 0 else Decimal("0")),
        )
        for index in range(observation_count)
    )
    publication = _publication(
        snapshots=snapshots,
        contributions=(),
        range_start=first,
        range_end=first + timedelta(days=observation_count - 1),
    )
    original_multiply = reporting_engine.method_decimal_multiply
    multiply_count = 0

    def counted_multiply(left: Decimal, right: Decimal) -> Decimal:
        nonlocal multiply_count
        multiply_count += 1
        return original_multiply(left, right)

    monkeypatch.setattr(
        reporting_engine,
        "method_decimal_multiply",
        counted_multiply,
    )
    result = reporting_engine.build_selected_range_performance(
        publication,
        include_secondary_metrics=False,
    )

    assert result.status == "ready"
    assert len(result.wealth_points) == observation_count
    assert multiply_count <= observation_count * 2
    for point in result.wealth_points:
        assert len(format(point.wealth_index_method50, "f")) <= 134
        assert len(format(point.peak_wealth_index_method50, "f")) <= 134
        assert len(format(point.drawdown_method50, "f")) <= 134
        if point.wealth_chain_rounding_adjustment_exact is not None:
            assert len(format(point.wealth_chain_rounding_adjustment_exact, "f")) <= 234


def test_annualization_consumes_positive_method_wealth_without_reconstructing_it() -> (
    None
):
    run_id = uuid4()
    token = 67
    origin = date(2025, 1, 1)
    first = origin + timedelta(days=315)
    observation_count = 51
    snapshots = tuple(
        _snapshot(
            run_id=run_id,
            token=token,
            as_of=first + timedelta(days=index),
            period_start=(origin if index == 0 else first + timedelta(days=index - 1)),
            opening="100",
            closing="10",
            subperiod_return="-0.9",
            cumulative_return="-0.9",
        )
        for index in range(observation_count)
    )
    publication = _publication(
        snapshots=snapshots,
        contributions=(),
        range_start=first,
        range_end=first + timedelta(days=observation_count - 1),
    )

    result = build_selected_range_performance(publication)

    assert result.elapsed_days == 365
    assert result.wealth_points[-1].wealth_index_method50 == Decimal("1E-51")
    assert result.cumulative_twr_method50 == Decimal("-1")
    assert result.annualized_twr is not None
    assert result.annualized_twr.method50 == Decimal("-1")


def test_snapshot_method50_precision_and_zero_wealth_chain_fail_closed() -> None:
    run_id = uuid4()
    token = 71
    first = date(2026, 8, 2)
    too_precise = "0." + ("1" * 90)
    overprecise = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=first,
                period_start=date(2026, 8, 1),
                opening="100",
                closing="110",
                subperiod_return=too_precise,
                cumulative_return="0.1",
            ),
        ),
        contributions=(),
        range_start=first,
        range_end=first,
    )
    with pytest.raises(ValueError, match="bounded method50 domain"):
        build_selected_range_performance(overprecise)

    second = first + timedelta(days=1)
    total_loss = _snapshot(
        run_id=run_id,
        token=token,
        as_of=first,
        period_start=date(2026, 8, 1),
        opening="100",
        closing="0",
        subperiod_return="-1",
        cumulative_return="-1",
    )
    total_loss["peak_wealth_index_method50"] = Decimal("1")
    total_loss["drawdown_method50"] = Decimal("-1")
    zero_then_active = _publication(
        snapshots=(
            total_loss,
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=second,
                period_start=first,
                opening="0",
                closing="0",
                subperiod_return="0",
                cumulative_return="-1",
            ),
        ),
        contributions=(),
        range_start=first,
        range_end=second,
    )
    with pytest.raises(ValueError, match="zero-wealth return chain"):
        build_selected_range_performance(zero_then_active)


def test_selected_range_method_domain_overflow_is_typed_unavailable() -> None:
    run_id = uuid4()
    token = 73
    first = date(2026, 9, 2)
    second = first + timedelta(days=1)
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=first,
                period_start=date(2026, 9, 1),
                opening="100",
                closing="100",
                subperiod_return="9999999999999999999999999999999",
                cumulative_return="-0.9999999999",
            ),
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=second,
                period_start=first,
                opening="100",
                closing="100",
                subperiod_return="99",
                cumulative_return="-0.99999999",
            ),
        ),
        contributions=(),
        range_start=first,
        range_end=second,
    )

    result = build_selected_range_performance(
        publication,
        include_secondary_metrics=False,
    )

    assert result.status == "unavailable"
    assert result.reason_codes == ("selected_range_method_domain_unavailable",)
    assert result.cumulative_twr_method50 is None
    assert result.wealth_points == ()


def test_risk_statistics_method_domain_overflow_is_typed_unavailable() -> None:
    run_id = uuid4()
    token = 77
    february_end = date(2026, 2, 28)
    march_end = date(2026, 3, 31)
    publication = _publication(
        snapshots=(
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=february_end,
                period_start=date(2026, 1, 31),
                opening="1",
                closing="1",
                subperiod_return="1E31",
                cumulative_return="1E31",
            ),
            _snapshot(
                run_id=run_id,
                token=token,
                as_of=march_end,
                period_start=february_end,
                opening="1",
                closing="1",
                subperiod_return="0",
                cumulative_return="1E31",
            ),
        ),
        contributions=(),
        range_start=date(2026, 2, 1),
        range_end=march_end,
    )

    result = build_selected_range_performance(
        publication,
        statistics_frequency="monthly",
    )

    assert result.status == "ready"
    assert result.cumulative_twr_method50 == Decimal("1E31")
    assert result.statistics.status == "unavailable"
    assert result.statistics.reason_codes == (
        "risk_statistics_numeric_domain_unavailable",
    )
    assert result.statistics.observation_count == 2
    assert result.statistics.mean_period_return is None


def test_attribution_method_domain_overflow_is_typed_unavailable() -> None:
    run_id = uuid4()
    token = 79
    first = date(2026, 10, 2)
    second = first + timedelta(days=1)
    snapshots = (
        _snapshot(
            run_id=run_id,
            token=token,
            as_of=first,
            period_start=date(2026, 10, 1),
            opening="100",
            closing="100",
            subperiod_return="0",
            cumulative_return="0",
        ),
        _snapshot(
            run_id=run_id,
            token=token,
            as_of=second,
            period_start=first,
            opening="100",
            closing="10000",
            subperiod_return="99",
            cumulative_return="99",
        ),
    )
    contributions = (
        _contribution(
            run_id=run_id,
            token=token,
            as_of=first,
            axis="instrument",
            key="A",
            label="A",
            opening="0",
            closing="0",
            pnl="0",
            contribution="1E31",
        ),
        _contribution(
            run_id=run_id,
            token=token,
            as_of=first,
            axis="instrument",
            key="B",
            label="B",
            opening="0",
            closing="0",
            pnl="0",
            contribution="-1E31",
        ),
        _contribution(
            run_id=run_id,
            token=token,
            as_of=second,
            axis="instrument",
            key="A",
            label="A",
            opening="0",
            closing="0",
            pnl="0",
            contribution="99",
        ),
        _contribution(
            run_id=run_id,
            token=token,
            as_of=second,
            axis="instrument",
            key="B",
            label="B",
            opening="0",
            closing="0",
            pnl="0",
            contribution="0",
        ),
    )
    publication = _publication(
        snapshots=snapshots,
        contributions=contributions,
        range_start=first,
        range_end=second,
    )
    performance = build_selected_range_performance(
        publication,
        include_secondary_metrics=False,
    )

    attribution = build_linked_attribution(
        publication,
        performance,
        axis="instrument",
    )

    assert performance.status == "ready"
    assert performance.cumulative_twr_method50 == Decimal("99")
    assert attribution.status == "unavailable"
    assert attribution.reason_codes == ("attribution_method_domain_unavailable",)
    assert attribution.total_linked_contribution_effective is None
    assert attribution.groups == ()
