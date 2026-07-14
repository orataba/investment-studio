"""Pure API projections over one fenced Portfolio Daily publication.

No function in this module reads mutable facts or writes state.  The input is
already bound to one immutable publication attempt by ``published_repository``.
All arithmetic is Decimal-only and ambient-context independent.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import TypeVar

from pydantic import BaseModel

from portfolio_app.api.contracts import (
    PortfolioDailyPublicationMetadataResponse,
    PortfolioDailyPublishedAttributionCalendarBucket,
    PortfolioDailyPublishedAttributionGroup,
    PortfolioDailyPublishedAttributionSummary,
    PortfolioDailyPublishedDecimalMetric,
    PortfolioDailyPublishedLotListResponse,
    PortfolioDailyPublishedLotRecord,
    PortfolioDailyPublishedLotSummary,
    PortfolioDailyPublishedPerformanceReportResponse,
    PortfolioDailyPublishedPerformanceStatistics,
    PortfolioDailyPublishedPerformanceSummary,
    PortfolioDailyPublishedPositionListResponse,
    PortfolioDailyPublishedPositionRecord,
    PortfolioDailyPublishedPositionSummary,
    PortfolioDailyPublishedRebasedWealthPoint,
    PortfolioDailyPublishedReturnCalendarBucket,
    PortfolioDailyPublishedSnapshotListResponse,
    PortfolioDailyPublishedSnapshotRecord,
    PortfolioDailyPublishedSnapshotSummary,
    PortfolioDailyPublishedXirrResult,
)
from portfolio_app.calculations.numeric import (
    RATIO_SCALE,
    exact_decimal_subtract,
    exact_decimal_sum,
    quantize_decimal,
)
from portfolio_app.calculations.portfolio_daily.published_repository import (
    CurrentPortfolioDailyPublication,
    PortfolioDailyRunMetadata,
    PortfolioDailySnapshot,
)
from portfolio_app.calculations.portfolio_daily.reporting_engine import (
    AttributionAxis,
    AttributionCalendarBucketResult,
    CalendarPeriodStatistics,
    CalendarFrequency,
    DecimalReportingMetric,
    LinkedAttribution,
    PublishedReportingIntegrityError,
    ReturnCalendarBucketResult,
    SelectedRangePerformance,
    XirrResult,
    build_attribution_calendar,
    build_linked_attribution,
    build_return_calendar,
    build_selected_range_performance,
    validate_published_snapshot_chain,
)


_ModelT = TypeVar("_ModelT", bound=BaseModel)
_COVERAGE_RANK = {"complete": 0, "partial": 1, "unavailable": 2}


def _model_from_attributes(model_type: type[_ModelT], source: object) -> _ModelT:
    return model_type.model_validate(
        {name: getattr(source, name) for name in model_type.model_fields}
    )


def publication_metadata_response(
    metadata: PortfolioDailyRunMetadata,
) -> PortfolioDailyPublicationMetadataResponse:
    return PortfolioDailyPublicationMetadataResponse(
        publication_id=str(metadata.publication_id),
        run_id=str(metadata.run_id),
        manifest_id=str(metadata.manifest_id),
        published_fencing_token=metadata.published_fencing_token,
        published_at=metadata.published_at,
        calculated_at=metadata.calculated_at,
        requested_as_of=metadata.requested_as_of,
        effective_as_of=metadata.effective_as_of,
        output_range_start=metadata.output_range_start,
        output_range_end=metadata.output_range_end,
        methodology_version=metadata.methodology_version,
        output_schema_version=metadata.output_schema_version,
        canonical_output_hash=metadata.canonical_output_hash,
        captured_generation=metadata.captured_generation,
        current_generation=metadata.current_generation,
        stale=metadata.stale,
        pending=metadata.pending,
        pending_generation=metadata.pending_generation,
        pending_run_id=(
            str(metadata.pending_run_id)
            if metadata.pending_run_id is not None
            else None
        ),
        pending_run_status=(
            metadata.pending_run_status.value
            if metadata.pending_run_status is not None
            else None
        ),
        pending_intent_id=(
            str(metadata.pending_intent_id)
            if metadata.pending_intent_id is not None
            else None
        ),
        pending_intent_status=metadata.pending_intent_status,  # type: ignore[arg-type]
        coverage_state=metadata.coverage_state,
        reason_codes=list(metadata.reason_codes),
    )


def _sum_present(values: Iterable[Decimal | None]) -> Decimal | None:
    resolved = tuple(values)
    if any(value is None for value in resolved):
        return None
    return exact_decimal_sum(tuple(value for value in resolved if value is not None))


def build_positions_response(
    publication: CurrentPortfolioDailyPublication,
) -> PortfolioDailyPublishedPositionListResponse:
    holdings = publication.holdings
    measured_market_value = all(row.measured_market_value for row in holdings)
    market_value = (
        _sum_present(row.market_value_base_exact for row in holdings)
        if measured_market_value
        else None
    )
    return PortfolioDailyPublishedPositionListResponse(
        portfolio_id=publication.metadata.portfolio_id,
        publication=publication_metadata_response(publication.metadata),
        summary=PortfolioDailyPublishedPositionSummary(
            as_of_date=publication.requested_range_end,
            position_count=len(holdings),
            priced_position_count=sum(
                1 for row in holdings if row.measured_market_value
            ),
            account_count=len({row.account_id for row in holdings}),
            measured_market_value=measured_market_value,
            market_value_base_exact=market_value,
        ),
        positions=[
            _model_from_attributes(PortfolioDailyPublishedPositionRecord, row)
            for row in holdings
        ],
    )


def build_lots_response(
    publication: CurrentPortfolioDailyPublication,
) -> PortfolioDailyPublishedLotListResponse:
    lots = publication.lots
    measured_base_cost = all(row.measured_base_cost for row in lots)
    return PortfolioDailyPublishedLotListResponse(
        portfolio_id=publication.metadata.portfolio_id,
        publication=publication_metadata_response(publication.metadata),
        summary=PortfolioDailyPublishedLotSummary(
            as_of_date=publication.requested_range_end,
            lot_count=len(lots),
            account_count=len({row.account_id for row in lots}),
            instrument_count=len({row.instrument_id for row in lots}),
            measured_base_cost=measured_base_cost,
            open_quantity_exact=exact_decimal_sum(
                tuple(row.open_quantity_exact for row in lots)
            ),
            cost_basis_base_exact=(
                _sum_present(row.cost_basis_base_exact for row in lots)
                if measured_base_cost
                else None
            ),
        ),
        position_lots=[
            _model_from_attributes(PortfolioDailyPublishedLotRecord, row)
            for row in lots
        ],
    )


def _snapshot_contract(
    snapshot: PortfolioDailySnapshot,
) -> PortfolioDailyPublishedSnapshotRecord:
    return _model_from_attributes(PortfolioDailyPublishedSnapshotRecord, snapshot)


def _snapshot_coverage(snapshot: PortfolioDailySnapshot) -> str:
    return max(
        (
            snapshot.nav_coverage_state,
            snapshot.book_pnl_coverage_state,
            snapshot.return_coverage_state,
            snapshot.flow_coverage_state,
        ),
        key=_COVERAGE_RANK.__getitem__,
    )


def _coverage_counts(
    snapshots: tuple[PortfolioDailySnapshot, ...],
) -> dict[str, int]:
    counts = {"complete": 0, "partial": 0, "unavailable": 0}
    for snapshot in snapshots:
        counts[_snapshot_coverage(snapshot)] += 1
    return counts


def build_snapshots_response(
    publication: CurrentPortfolioDailyPublication,
) -> PortfolioDailyPublishedSnapshotListResponse:
    validate_published_snapshot_chain(publication)
    snapshots = publication.snapshots
    counts = _coverage_counts(snapshots)
    base_currency = snapshots[-1].base_currency if snapshots else ""
    return PortfolioDailyPublishedSnapshotListResponse(
        portfolio_id=publication.metadata.portfolio_id,
        publication=publication_metadata_response(publication.metadata),
        base_currency=base_currency,
        valuation_timezone=publication.metadata.timezone_name,
        summary=PortfolioDailyPublishedSnapshotSummary(
            snapshot_count=len(snapshots),
            measured_nav_count=sum(1 for row in snapshots if row.measured_nav),
            measured_return_count=sum(1 for row in snapshots if row.measured_return),
            complete_count=counts["complete"],
            partial_count=counts["partial"],
            unavailable_count=counts["unavailable"],
            latest_as_of_date=snapshots[-1].as_of_date if snapshots else None,
        ),
        snapshots=[_snapshot_contract(row) for row in snapshots],
    )


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _metric_contract(
    metric: DecimalReportingMetric | None,
) -> PortfolioDailyPublishedDecimalMetric | None:
    if metric is None:
        return None
    return _model_from_attributes(PortfolioDailyPublishedDecimalMetric, metric)


def _method_value_contract(
    value: Decimal | None,
    *,
    field_name: str,
) -> PortfolioDailyPublishedDecimalMetric | None:
    if value is None:
        return None
    published = quantize_decimal(
        value,
        scale=RATIO_SCALE,
        field_name=field_name,
    )
    return PortfolioDailyPublishedDecimalMetric(
        method50=value,
        published=published,
        rounding_adjustment_exact=exact_decimal_subtract(published, value),
    )


def _statistics_contract(
    statistics: CalendarPeriodStatistics,
) -> PortfolioDailyPublishedPerformanceStatistics:
    return PortfolioDailyPublishedPerformanceStatistics(
        status=statistics.status,
        method_version=statistics.method_version,  # type: ignore[arg-type]
        reason_codes=list(statistics.reason_codes),
        frequency=statistics.frequency,
        observation_count=statistics.observation_count,
        excluded_partial_bucket_count=statistics.excluded_partial_bucket_count,
        excluded_unavailable_bucket_count=(
            statistics.excluded_unavailable_bucket_count
        ),
        periods_per_year=_metric_contract(statistics.periods_per_year),
        mean_period_return=_metric_contract(statistics.mean_period_return),
        annualized_arithmetic_mean=_metric_contract(
            statistics.annualized_arithmetic_mean
        ),
        annualized_volatility=_metric_contract(statistics.annualized_volatility),
        annualized_downside_deviation=_metric_contract(
            statistics.annualized_downside_deviation
        ),
    )


def _xirr_contract(result: XirrResult) -> PortfolioDailyPublishedXirrResult:
    return PortfolioDailyPublishedXirrResult(
        status=result.status,
        method_version=result.method_version,  # type: ignore[arg-type]
        reason_codes=list(result.reason_codes),
        cash_flow_count=result.cash_flow_count,
        annualized_headline_eligible=result.annualized_headline_eligible,
        rate=_metric_contract(result.rate),
        xnpv_residual_exact=result.xnpv_residual_exact,
    )


def _performance_summary_contract(
    publication: CurrentPortfolioDailyPublication,
    *,
    selected: SelectedRangePerformance,
    portfolio_attribution: LinkedAttribution,
) -> PortfolioDailyPublishedPerformanceSummary:
    snapshots = publication.snapshots
    if snapshots:
        coverage_state = max(
            (_snapshot_coverage(row) for row in snapshots),
            key=_COVERAGE_RANK.__getitem__,
        )
        reason_codes = _ordered_unique(
            reason
            for row in snapshots
            for reason in (
                *row.nav_reason_codes,
                *row.book_pnl_reason_codes,
                *row.return_reason_codes,
                *row.flow_reason_codes,
            )
        )
    else:
        coverage_state = "unavailable"
        reason_codes = ["requested_range_not_in_current_publication"]

    first = snapshots[0] if snapshots else None
    reason_codes = _ordered_unique(
        (
            *reason_codes,
            *selected.reason_codes,
            *portfolio_attribution.reason_codes,
        )
    )

    return PortfolioDailyPublishedPerformanceSummary(
        status=selected.status,
        start_date=first.as_of_date if first is not None else None,
        end_date=snapshots[-1].as_of_date if snapshots else None,
        effective_return_start_date=selected.effective_return_start_date,
        effective_return_end_date=selected.effective_return_end_date,
        elapsed_days=selected.elapsed_days,
        snapshot_count=len(snapshots),
        measured_nav_count=sum(1 for row in snapshots if row.measured_nav),
        measured_return_count=sum(1 for row in snapshots if row.measured_return),
        cumulative_twr=_method_value_contract(
            selected.cumulative_twr_method50,
            field_name="performance.cumulative_twr",
        ),
        annualized_twr=_metric_contract(selected.annualized_twr),
        current_drawdown=_method_value_contract(
            selected.current_drawdown_method50,
            field_name="performance.current_drawdown",
        ),
        max_drawdown=_method_value_contract(
            selected.max_drawdown_method50,
            field_name="performance.max_drawdown",
        ),
        return_chain_status=("linked" if selected.status == "ready" else "unavailable"),
        coverage_state=coverage_state,  # type: ignore[arg-type]
        reason_codes=reason_codes,
    )


def _attribution_summary_contract(
    attribution: LinkedAttribution,
) -> PortfolioDailyPublishedAttributionSummary:
    return PortfolioDailyPublishedAttributionSummary(
        status=attribution.status,
        method_version=attribution.method_version,  # type: ignore[arg-type]
        axis=attribution.axis,
        effective_start_date=attribution.effective_start_date,
        effective_end_date=attribution.effective_end_date,
        observation_count=attribution.observation_count,
        cumulative_twr_method50=attribution.cumulative_twr_method50,
        total_linked_contribution_effective=(
            attribution.total_linked_contribution_effective
        ),
        total_linking_adjustment_exact=attribution.total_linking_adjustment_exact,
        closure_residual_exact=attribution.closure_residual_exact,
        reason_codes=list(attribution.reason_codes),
    )


def _attribution_group_contract(
    group: object,
) -> PortfolioDailyPublishedAttributionGroup:
    return _model_from_attributes(PortfolioDailyPublishedAttributionGroup, group)


def _portfolio_bridge_contract(
    attribution: LinkedAttribution,
) -> PortfolioDailyPublishedAttributionGroup | None:
    if attribution.status != "ready":
        return None
    if len(attribution.groups) != 1:
        raise PublishedReportingIntegrityError(
            "ready portfolio attribution must contain exactly one bridge row"
        )
    return _attribution_group_contract(attribution.groups[0])


def _return_calendar_bucket_contract(
    bucket: ReturnCalendarBucketResult,
) -> PortfolioDailyPublishedReturnCalendarBucket:
    return PortfolioDailyPublishedReturnCalendarBucket(
        bucket_key=bucket.bucket_key,
        frequency=bucket.frequency,
        calendar_start_date=bucket.calendar_start_date,
        calendar_end_date=bucket.calendar_end_date,
        coverage_state=bucket.coverage_state,
        coverage_reason_codes=list(bucket.coverage_reason_codes),
        status=bucket.performance.status,
        effective_return_start_date=bucket.performance.effective_return_start_date,
        effective_return_end_date=bucket.performance.effective_return_end_date,
        observation_count=bucket.performance.observation_count,
        cumulative_twr=_method_value_contract(
            bucket.performance.cumulative_twr_method50,
            field_name=f"return_calendar.{bucket.bucket_key}.cumulative_twr",
        ),
        current_drawdown=_method_value_contract(
            bucket.performance.current_drawdown_method50,
            field_name=f"return_calendar.{bucket.bucket_key}.current_drawdown",
        ),
        max_drawdown=_method_value_contract(
            bucket.performance.max_drawdown_method50,
            field_name=f"return_calendar.{bucket.bucket_key}.max_drawdown",
        ),
        reason_codes=list(bucket.performance.reason_codes),
    )


def _attribution_calendar_bucket_contract(
    bucket: AttributionCalendarBucketResult,
) -> PortfolioDailyPublishedAttributionCalendarBucket:
    return PortfolioDailyPublishedAttributionCalendarBucket(
        bucket_key=bucket.bucket_key,
        frequency=bucket.frequency,
        calendar_start_date=bucket.calendar_start_date,
        calendar_end_date=bucket.calendar_end_date,
        coverage_state=bucket.coverage_state,
        coverage_reason_codes=list(bucket.coverage_reason_codes),
        summary=_attribution_summary_contract(bucket.attribution),
        groups=[_attribution_group_contract(row) for row in bucket.attribution.groups],
    )


def build_performance_report_response(
    publication: CurrentPortfolioDailyPublication,
    *,
    frequency: CalendarFrequency,
    axis: AttributionAxis,
    group_key: str | None = None,
) -> PortfolioDailyPublishedPerformanceReportResponse:
    """Build the entire Performance page from one already-fenced publication."""

    return_buckets = build_return_calendar(publication, frequency=frequency)
    selected = build_selected_range_performance(
        publication,
        statistics_frequency=frequency,
        return_buckets=return_buckets,
    )
    portfolio_attribution = build_linked_attribution(
        publication,
        selected,
        axis="portfolio",
    )
    attribution = build_linked_attribution(
        publication,
        selected,
        axis=axis,
        group_key=group_key,
    )
    attribution_calendar = build_attribution_calendar(
        publication,
        frequency=frequency,
        axis=axis,
        group_key=group_key,
        return_buckets=return_buckets,
    )
    return PortfolioDailyPublishedPerformanceReportResponse(
        portfolio_id=publication.metadata.portfolio_id,
        publication=publication_metadata_response(publication.metadata),
        base_currency=(
            publication.snapshots[-1].base_currency if publication.snapshots else ""
        ),
        valuation_timezone=publication.metadata.timezone_name,
        axis=axis,
        selected_group_key=group_key,
        frequency=frequency,
        performance=_performance_summary_contract(
            publication,
            selected=selected,
            portfolio_attribution=portfolio_attribution,
        ),
        statistics=_statistics_contract(selected.statistics),
        xirr=_xirr_contract(selected.xirr),
        portfolio_bridge=_portfolio_bridge_contract(portfolio_attribution),
        rebased_wealth_series=[
            _model_from_attributes(PortfolioDailyPublishedRebasedWealthPoint, row)
            for row in selected.wealth_points
        ],
        daily_series=[_snapshot_contract(row) for row in publication.snapshots],
        attribution=_attribution_summary_contract(attribution),
        attribution_groups=[
            _attribution_group_contract(row) for row in attribution.groups
        ],
        return_calendar=[
            _return_calendar_bucket_contract(bucket) for bucket in return_buckets
        ],
        attribution_calendar=[
            _attribution_calendar_bucket_contract(bucket)
            for bucket in attribution_calendar
        ],
    )


__all__ = [
    "build_lots_response",
    "build_performance_report_response",
    "build_positions_response",
    "build_snapshots_response",
    "publication_metadata_response",
]
