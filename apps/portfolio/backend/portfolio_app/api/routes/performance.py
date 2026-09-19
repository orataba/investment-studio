from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from portfolio_app.api.financial_read import FinancialReadRoute
from portfolio_app.api.contracts import (
    BoundaryGroupRecord,
    BoundaryGroupsResponse,
    BoundaryGroupsSummary,
    ContributionEntryCalendarBucketRecord,
    ContributionEntryCalendarResponse,
    ContributionEntryCalendarSummary,
    ContributionEntriesResponse,
    ContributionEntriesSummary,
    ContributionEntryRecord,
    ContributionBucketCalendarBucketRecord,
    ContributionBucketCalendarResponse,
    ContributionBucketCalendarSummary,
    ContributionBucketGroupRecord,
    ContributionBucketResponse,
    ContributionBucketSummary,
    PeriodCalculationEntriesResponse,
    PeriodCalculationEntriesSummary,
    PeriodCalculationEntryRecord,
    PeriodCalculationEntryCalendarBucketRecord,
    PeriodCalculationEntryCalendarResponse,
    PeriodCalculationEntryCalendarSummary,
    PeriodCalculationBucketGroupRecord,
    PeriodCalculationBucketResponse,
    PeriodCalculationBucketSummary,
    ContributionCalendarBucketRecord,
    ContributionCalendarResponse,
    ContributionCalendarSummary,
    ContributionLineRecord,
    ContributionReportResponse,
    ContributionReportSummary,
    DailySnapshotRecalculationAccepted,
    DailySnapshotRecalculationRequest,
    DailySnapshotRecalculationResponse,
    DailySnapshotListResponse,
    DailySnapshotListSummary,
    DailySnapshotRecord,
    DailyPerformancePoint,
    DailyContributionSliceRecord,
    PeriodBoundaryHoldingRecord,
    PeriodBoundaryHoldingsResponse,
    PeriodBoundaryHoldingsSummary,
    PeriodCalculationLine,
    PeriodCalculationGroupRecord,
    PeriodCalculationGroupCalendarBucketRecord,
    PeriodCalculationGroupsCalendarResponse,
    PeriodCalculationGroupsCalendarSummary,
    PeriodCalculationGroupsResponse,
    PeriodCalculationGroupsSummary,
    PeriodCalculationResponse,
    PeriodCalculationSummary,
    PerformanceResponse,
    PerformanceSummary,
    ReturnCalendarBucket,
    ReturnCalendarResponse,
    ReturnCalendarSummary,
)
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.period_calculation_state import load_period_calculation_inputs
from portfolio_app.services.attribution import CONTRIBUTION_AXES, CONTRIBUTION_AXIS_ERROR
from portfolio_app.services.daily_snapshots import (
    enqueue_portfolio_daily_snapshot_recalculations_for_instrument_change,
    enqueue_selected_portfolio_daily_snapshot_recalculations,
    list_materialized_daily_snapshots,
)
from portfolio_app.services.instrument_event_tasks import (
    reconcile_instrument_event_tasks,
)
from portfolio_app.services.workspace_cache import (
    get_cached_materialized_contribution_report,
    get_cached_materialized_performance_report,
)
from portfolio_app.services.performance import (
    build_period_boundary_groups_report,
    build_contribution_bucket_calendar_report,
    build_contribution_bucket_report,
    build_contribution_calendar_report,
    build_contribution_entries_calendar_report,
    build_contribution_entries_report,
    build_contribution_report,
    build_period_boundary_holdings_report,
    build_period_calculation_report,
    build_period_calculation_entries_report,
    build_period_calculation_entries_calendar_report,
    build_period_calculation_bucket_report,
    build_period_calculation_groups_calendar_report,
    build_period_calculation_groups_report,
    build_taxonomy_calculation_detail_report_from_base_report,
    build_taxonomy_contribution_report_from_base_report,
    build_return_calendar_report_from_performance_report,
)
from portfolio_app.services.return_chain import (
    resolve_reliable_snapshot_window,
    summarize_daily_snapshots,
)
from portfolio_app.services.portfolio_store import get_portfolio, list_accounts, list_transactions
from portfolio_app.services.portfolio_store import (
    list_taxonomies,
    list_taxonomy_assignments,
    list_taxonomy_nodes,
)


router = APIRouter(route_class=FinancialReadRoute)
# Entry reports read transaction facts and their opening boundary. Requiring a
# portfolio-wide refresh here would change their date window at valuation gaps.
entry_router = APIRouter()

_MATERIALIZED_CALCULATION_GROUP_AXES = {"instrument", "account", "instrument_type", "currency"}


def _validate_date_window(
    start_date: date | None,
    end_date: date | None,
) -> None:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail="start_date must be on or before end_date",
        )


def _calculation_group_detail_axis(axis: str) -> str:
    return "cash_detail" if axis == "instrument" else f"{axis}_detail"


def _taxonomy_base_axes(
    taxonomy_id: str | None,
    taxonomies: list[dict[str, object]],
) -> tuple[str, str] | None:
    resolved_taxonomy_id = str(taxonomy_id or "").strip()
    taxonomy = next(
        (
            item
            for item in taxonomies
            if str(item.get("taxonomy_id") or "") == resolved_taxonomy_id
        ),
        None,
    )
    if taxonomy is None:
        return None
    return ("instrument", "instrument_detail")


@router.post(
    "/snapshots/daily/recalculations",
    response_model=DailySnapshotRecalculationResponse,
    status_code=202,
)
def enqueue_daily_snapshot_recalculations(
    payload: DailySnapshotRecalculationRequest,
) -> DailySnapshotRecalculationResponse:
    if payload.refresh_all:
        accepted = (
            enqueue_portfolio_daily_snapshot_recalculations_for_instrument_change(
                instrument_ids=[],
                dirty_from=payload.dirty_from,
                refresh_all=True,
            )
        )
    elif payload.portfolio_ids:
        accepted = enqueue_selected_portfolio_daily_snapshot_recalculations(
            payload.portfolio_ids,
            dirty_from=payload.dirty_from,
        )
    else:
        accepted = (
            enqueue_portfolio_daily_snapshot_recalculations_for_instrument_change(
                instrument_ids=payload.instrument_ids,
                dirty_from=payload.dirty_from,
            )
        )

    accepted_portfolio_ids = [
        str(item.get("portfolio_id") or "") for item in accepted
    ]
    try:
        reconcile_instrument_event_tasks(
            portfolio_ids=accepted_portfolio_ids,
            instrument_ids=payload.instrument_ids,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return DailySnapshotRecalculationResponse(
        portfolio_ids=accepted_portfolio_ids,
        accepted=[
            DailySnapshotRecalculationAccepted.model_validate(item)
            for item in accepted
        ],
    )


@router.get("/{portfolio_id}/snapshots/daily", response_model=DailySnapshotListResponse)
def list_daily_snapshots(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> DailySnapshotListResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        loaded_snapshots = list_materialized_daily_snapshots(
            portfolio_id,
            start_date=None,
            end_date=end_date,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    snapshot_window = resolve_reliable_snapshot_window(
        loaded_snapshots,
        requested_start_date=start_date,
        requested_end_date=end_date,
        default_end_date=(
            date.fromisoformat(str(portfolio.get("as_of_date"))[:10])
            if portfolio.get("as_of_date")
            else None
        ),
    )
    snapshots = list(snapshot_window["snapshots"])
    return DailySnapshotListResponse(
        portfolio_id=portfolio_id,
        base_currency=str(portfolio.get("base_currency") or "USD"),
        valuation_timezone=str(portfolio.get("valuation_timezone") or ""),
        valuation_cutoff_policy=str(portfolio.get("valuation_cutoff_policy") or "latest_complete_eod"),
        summary=DailySnapshotListSummary.model_validate(
            summarize_daily_snapshots(
                snapshots,
                requested_start_date=snapshot_window["requested_start_date"],
                requested_end_date=snapshot_window["requested_end_date"],
                effective_start_date=snapshot_window["effective_start_date"],
                effective_end_date=snapshot_window["effective_end_date"],
                as_of_clamp_reason=snapshot_window["as_of_clamp_reason"],
            )
        ),
        snapshots=[DailySnapshotRecord.model_validate(item) for item in snapshots],
    )


@router.get("/{portfolio_id}/performance", response_model=PerformanceResponse)
def get_portfolio_performance(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> PerformanceResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        report = get_cached_materialized_performance_report(
            portfolio_id,
            start_date=start_date,
            end_date=end_date,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if report is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    return PerformanceResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=PerformanceSummary.model_validate(report["summary"]),
        daily_series=[DailyPerformancePoint.model_validate(item) for item in report["daily_series"]],
    )


@router.get("/{portfolio_id}/performance/calculation", response_model=PeriodCalculationResponse)
def get_portfolio_period_calculation(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> PeriodCalculationResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        materialized_snapshots = list_materialized_daily_snapshots(
            portfolio_id,
            start_date=None,
            end_date=end_date,
        )
        instrument_contribution_report = (
            get_cached_materialized_contribution_report(
                portfolio_id,
                start_date=start_date,
                end_date=end_date,
                axis="instrument",
            )
        )
        if instrument_contribution_report is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        calculation_inputs = load_period_calculation_inputs(
            portfolio_id,
            start_date=start_date,
            end_date=end_date,
            prebuilt_snapshots=materialized_snapshots,
        )
        report = build_period_calculation_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            start_date=start_date,
            end_date=end_date,
            prebuilt_snapshots=materialized_snapshots,
            prebuilt_instrument_contribution_report=(
                instrument_contribution_report
            ),
            calculation_inputs=calculation_inputs,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return PeriodCalculationResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=PeriodCalculationSummary.model_validate(report["summary"]),
        lines=[PeriodCalculationLine.model_validate(item) for item in report["lines"]],
    )


@router.get("/{portfolio_id}/performance/calculation/groups", response_model=PeriodCalculationGroupsResponse)
def get_portfolio_period_calculation_groups(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> PeriodCalculationGroupsResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in CONTRIBUTION_AXES:
        raise HTTPException(status_code=422, detail=CONTRIBUTION_AXIS_ERROR)

    accounts = list_accounts(portfolio_id)
    transactions = list_transactions(portfolio_id)
    taxonomies = list_taxonomies(portfolio_id)
    taxonomy_nodes = list_taxonomy_nodes(portfolio_id)
    taxonomy_assignments = list_taxonomy_assignments(portfolio_id)

    try:
        contribution_report = (
            get_cached_materialized_contribution_report(
                portfolio_id,
                start_date=start_date,
                end_date=end_date,
                axis=axis,
                group_key=group_key,
            )
            if axis in _MATERIALIZED_CALCULATION_GROUP_AXES
            else None
        )
        detail_contribution_report = (
            get_cached_materialized_contribution_report(
                portfolio_id,
                start_date=start_date,
                end_date=end_date,
                axis=_calculation_group_detail_axis(axis),
            )
            if axis in _MATERIALIZED_CALCULATION_GROUP_AXES
            else None
        )
        if axis == "taxonomy":
            taxonomy_axes = _taxonomy_base_axes(taxonomy_id, taxonomies)
            if taxonomy_axes is not None:
                base_axis, base_detail_axis = taxonomy_axes
                base_contribution_report = get_cached_materialized_contribution_report(
                    portfolio_id,
                    start_date=start_date,
                    end_date=end_date,
                    axis=base_axis,
                )
                base_detail_report = get_cached_materialized_contribution_report(
                    portfolio_id,
                    start_date=start_date,
                    end_date=end_date,
                    axis=base_detail_axis,
                )
                if base_contribution_report is not None:
                    contribution_report = build_taxonomy_contribution_report_from_base_report(
                        portfolio,
                        accounts,
                        transactions,
                        taxonomies=taxonomies,
                        taxonomy_nodes=taxonomy_nodes,
                        taxonomy_assignments=taxonomy_assignments,
                        start_date=start_date,
                        end_date=end_date,
                        taxonomy_id=taxonomy_id,
                        group_key=group_key,
                        base_report=base_contribution_report,
                        apply_boundary_values=False,
                    )
                else:
                    base_contribution_report = build_contribution_report(
                        portfolio,
                        accounts,
                        transactions,
                        taxonomies=taxonomies,
                        taxonomy_nodes=taxonomy_nodes,
                        taxonomy_assignments=taxonomy_assignments,
                        start_date=start_date,
                        end_date=end_date,
                        axis=base_axis,
                    )
                    contribution_report = build_taxonomy_contribution_report_from_base_report(
                        portfolio,
                        accounts,
                        transactions,
                        taxonomies=taxonomies,
                        taxonomy_nodes=taxonomy_nodes,
                        taxonomy_assignments=taxonomy_assignments,
                        start_date=start_date,
                        end_date=end_date,
                        taxonomy_id=taxonomy_id,
                        group_key=group_key,
                        base_report=base_contribution_report,
                        apply_boundary_values=False,
                    )
                if base_detail_report is not None:
                    detail_contribution_report = build_taxonomy_calculation_detail_report_from_base_report(
                        portfolio,
                        accounts,
                        transactions,
                        taxonomies=taxonomies,
                        taxonomy_nodes=taxonomy_nodes,
                        taxonomy_assignments=taxonomy_assignments,
                        start_date=start_date,
                        end_date=end_date,
                        taxonomy_id=taxonomy_id,
                        base_report=base_detail_report,
                    )
                else:
                    base_detail_report = build_contribution_report(
                        portfolio,
                        accounts,
                        transactions,
                        taxonomies=taxonomies,
                        taxonomy_nodes=taxonomy_nodes,
                        taxonomy_assignments=taxonomy_assignments,
                        start_date=start_date,
                        end_date=end_date,
                        axis=base_detail_axis,
                        allow_internal_detail_axis=True,
                    )
                    detail_contribution_report = build_taxonomy_calculation_detail_report_from_base_report(
                        portfolio,
                        accounts,
                        transactions,
                        taxonomies=taxonomies,
                        taxonomy_nodes=taxonomy_nodes,
                        taxonomy_assignments=taxonomy_assignments,
                        start_date=start_date,
                        end_date=end_date,
                        taxonomy_id=taxonomy_id,
                        base_report=base_detail_report,
                    )
        calculation_inputs = load_period_calculation_inputs(
            portfolio_id, start_date=start_date, end_date=end_date,
        )
        report = build_period_calculation_groups_report(
            portfolio,
            accounts,
            transactions,
            taxonomies=taxonomies,
            taxonomy_nodes=taxonomy_nodes,
            taxonomy_assignments=taxonomy_assignments,
            start_date=start_date,
            end_date=end_date,
            axis=axis,
            taxonomy_id=taxonomy_id,
            group_key=group_key,
            contribution_report=contribution_report,
            detail_contribution_report=detail_contribution_report,
            calculation_inputs=calculation_inputs,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return PeriodCalculationGroupsResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=PeriodCalculationGroupsSummary.model_validate(report["summary"]),
        groups=[PeriodCalculationGroupRecord.model_validate(item) for item in report["groups"]],
    )


@router.get(
    "/{portfolio_id}/performance/calculation/groups/calendar",
    response_model=PeriodCalculationGroupsCalendarResponse,
)
def get_portfolio_period_calculation_groups_calendar(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    frequency: str = "monthly",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> PeriodCalculationGroupsCalendarResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in CONTRIBUTION_AXES:
        raise HTTPException(status_code=422, detail=CONTRIBUTION_AXIS_ERROR)

    try:
        report = build_period_calculation_groups_calendar_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            taxonomies=list_taxonomies(portfolio_id),
            taxonomy_nodes=list_taxonomy_nodes(portfolio_id),
            taxonomy_assignments=list_taxonomy_assignments(portfolio_id),
            start_date=start_date,
            end_date=end_date,
            axis=axis,
            frequency=frequency,
            taxonomy_id=taxonomy_id,
            group_key=group_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return PeriodCalculationGroupsCalendarResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=PeriodCalculationGroupsCalendarSummary.model_validate(report["summary"]),
        buckets=[PeriodCalculationGroupCalendarBucketRecord.model_validate(item) for item in report["buckets"]],
    )


@router.get("/{portfolio_id}/performance/calculation/drilldown", response_model=PeriodCalculationBucketResponse)
def get_portfolio_period_calculation_drilldown(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    bucket: str = "total_pnl",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> PeriodCalculationBucketResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in CONTRIBUTION_AXES:
        raise HTTPException(status_code=422, detail=CONTRIBUTION_AXIS_ERROR)

    try:
        report = build_period_calculation_bucket_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            taxonomies=list_taxonomies(portfolio_id),
            taxonomy_nodes=list_taxonomy_nodes(portfolio_id),
            taxonomy_assignments=list_taxonomy_assignments(portfolio_id),
            start_date=start_date,
            end_date=end_date,
            axis=axis,
            bucket=bucket,
            taxonomy_id=taxonomy_id,
            group_key=group_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return PeriodCalculationBucketResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=PeriodCalculationBucketSummary.model_validate(report["summary"]),
        groups=[PeriodCalculationBucketGroupRecord.model_validate(item) for item in report["groups"]],
    )


@entry_router.get("/{portfolio_id}/performance/calculation/entries", response_model=PeriodCalculationEntriesResponse)
def get_portfolio_period_calculation_entries(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    bucket: str = "earnings",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> PeriodCalculationEntriesResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in CONTRIBUTION_AXES:
        raise HTTPException(status_code=422, detail=CONTRIBUTION_AXIS_ERROR)

    try:
        report = build_period_calculation_entries_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            taxonomies=list_taxonomies(portfolio_id),
            taxonomy_nodes=list_taxonomy_nodes(portfolio_id),
            taxonomy_assignments=list_taxonomy_assignments(portfolio_id),
            start_date=start_date,
            end_date=end_date,
            axis=axis,
            bucket=bucket,
            taxonomy_id=taxonomy_id,
            group_key=group_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return PeriodCalculationEntriesResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=PeriodCalculationEntriesSummary.model_validate(report["summary"]),
        entries=[PeriodCalculationEntryRecord.model_validate(item) for item in report["entries"]],
    )


@entry_router.get(
    "/{portfolio_id}/performance/calculation/entries/calendar",
    response_model=PeriodCalculationEntryCalendarResponse,
)
def get_portfolio_period_calculation_entries_calendar(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    bucket: str = "earnings",
    frequency: str = "monthly",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> PeriodCalculationEntryCalendarResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in CONTRIBUTION_AXES:
        raise HTTPException(status_code=422, detail=CONTRIBUTION_AXIS_ERROR)

    try:
        report = build_period_calculation_entries_calendar_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            taxonomies=list_taxonomies(portfolio_id),
            taxonomy_nodes=list_taxonomy_nodes(portfolio_id),
            taxonomy_assignments=list_taxonomy_assignments(portfolio_id),
            start_date=start_date,
            end_date=end_date,
            axis=axis,
            bucket=bucket,
            frequency=frequency,
            taxonomy_id=taxonomy_id,
            group_key=group_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return PeriodCalculationEntryCalendarResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=PeriodCalculationEntryCalendarSummary.model_validate(report["summary"]),
        buckets=[PeriodCalculationEntryCalendarBucketRecord.model_validate(item) for item in report["buckets"]],
    )


@router.get("/{portfolio_id}/performance/boundary-holdings", response_model=PeriodBoundaryHoldingsResponse)
def get_portfolio_period_boundary_holdings(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str | None = None,
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> PeriodBoundaryHoldingsResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis is not None and axis not in CONTRIBUTION_AXES:
        raise HTTPException(status_code=422, detail=CONTRIBUTION_AXIS_ERROR)

    try:
        report = build_period_boundary_holdings_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            taxonomies=list_taxonomies(portfolio_id),
            taxonomy_nodes=list_taxonomy_nodes(portfolio_id),
            taxonomy_assignments=list_taxonomy_assignments(portfolio_id),
            start_date=start_date,
            end_date=end_date,
            axis=axis,
            taxonomy_id=taxonomy_id,
            group_key=group_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return PeriodBoundaryHoldingsResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=PeriodBoundaryHoldingsSummary.model_validate(report["summary"]),
        start_positions=[PeriodBoundaryHoldingRecord.model_validate(item) for item in report["start_positions"]],
        end_positions=[PeriodBoundaryHoldingRecord.model_validate(item) for item in report["end_positions"]],
    )


@router.get("/{portfolio_id}/performance/boundary-groups", response_model=BoundaryGroupsResponse)
def get_portfolio_period_boundary_groups(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    taxonomy_id: str | None = None,
) -> BoundaryGroupsResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        report = build_period_boundary_groups_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            taxonomies=list_taxonomies(portfolio_id),
            taxonomy_nodes=list_taxonomy_nodes(portfolio_id),
            taxonomy_assignments=list_taxonomy_assignments(portfolio_id),
            start_date=start_date,
            end_date=end_date,
            taxonomy_id=taxonomy_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return BoundaryGroupsResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=BoundaryGroupsSummary.model_validate(report["summary"]),
        start_groups=[BoundaryGroupRecord.model_validate(item) for item in report["start_groups"]],
        end_groups=[BoundaryGroupRecord.model_validate(item) for item in report["end_groups"]],
    )


@router.get("/{portfolio_id}/performance/calendar", response_model=ReturnCalendarResponse)
def get_portfolio_return_calendar(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    frequency: str = "monthly",
) -> ReturnCalendarResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if frequency not in {"monthly", "weekly"}:
        raise HTTPException(status_code=422, detail="frequency must be monthly or weekly")

    try:
        # Calendar buckets are a presentation rollup over the same authoritative,
        # snapshot-fenced window as Performance. Reuse its bounded cache instead of
        # rebuilding valuation for each calendar request or frequency.
        performance_report = get_cached_materialized_performance_report(
            portfolio_id,
            start_date=start_date,
            end_date=end_date,
        )
        if performance_report is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        report = build_return_calendar_report_from_performance_report(
            performance_report,
            frequency=frequency,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return ReturnCalendarResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=ReturnCalendarSummary.model_validate(report["summary"]),
        buckets=[ReturnCalendarBucket.model_validate(item) for item in report["buckets"]],
    )


@router.get("/{portfolio_id}/performance/contribution", response_model=ContributionReportResponse)
def get_portfolio_contribution_report(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> ContributionReportResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in CONTRIBUTION_AXES:
        raise HTTPException(status_code=422, detail=CONTRIBUTION_AXIS_ERROR)

    try:
        report = (
            get_cached_materialized_contribution_report(
                portfolio_id,
                start_date=start_date,
                end_date=end_date,
                axis=axis,
                group_key=group_key,
            )
            if axis in _MATERIALIZED_CALCULATION_GROUP_AXES
            else None
        )
        if report is None:
            report = build_contribution_report(
                portfolio,
                list_accounts(portfolio_id),
                list_transactions(portfolio_id),
                taxonomies=list_taxonomies(portfolio_id),
                taxonomy_nodes=list_taxonomy_nodes(portfolio_id),
                taxonomy_assignments=list_taxonomy_assignments(portfolio_id),
                start_date=start_date,
                end_date=end_date,
                axis=axis,
                taxonomy_id=taxonomy_id,
                group_key=group_key,
            )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return ContributionReportResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=ContributionReportSummary.model_validate(report["summary"]),
        lines=[ContributionLineRecord.model_validate(item) for item in report["lines"]],
        daily_slices=[DailyContributionSliceRecord.model_validate(item) for item in report["daily_slices"]],
    )


@router.get("/{portfolio_id}/performance/contribution/calendar", response_model=ContributionCalendarResponse)
def get_portfolio_contribution_calendar(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    taxonomy_id: str | None = None,
    frequency: str = "monthly",
    group_key: str | None = None,
) -> ContributionCalendarResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in CONTRIBUTION_AXES:
        raise HTTPException(status_code=422, detail=CONTRIBUTION_AXIS_ERROR)
    if frequency not in {"monthly", "weekly"}:
        raise HTTPException(status_code=422, detail="frequency must be monthly or weekly")

    try:
        report = build_contribution_calendar_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            taxonomies=list_taxonomies(portfolio_id),
            taxonomy_nodes=list_taxonomy_nodes(portfolio_id),
            taxonomy_assignments=list_taxonomy_assignments(portfolio_id),
            start_date=start_date,
            end_date=end_date,
            axis=axis,
            taxonomy_id=taxonomy_id,
            frequency=frequency,
            group_key=group_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return ContributionCalendarResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=ContributionCalendarSummary.model_validate(report["summary"]),
        buckets=[ContributionCalendarBucketRecord.model_validate(item) for item in report["buckets"]],
    )


@router.get("/{portfolio_id}/performance/contribution/drilldown", response_model=ContributionBucketResponse)
def get_portfolio_contribution_drilldown(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    bucket: str = "total_pnl",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> ContributionBucketResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in CONTRIBUTION_AXES:
        raise HTTPException(status_code=422, detail=CONTRIBUTION_AXIS_ERROR)

    try:
        report = build_contribution_bucket_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            taxonomies=list_taxonomies(portfolio_id),
            taxonomy_nodes=list_taxonomy_nodes(portfolio_id),
            taxonomy_assignments=list_taxonomy_assignments(portfolio_id),
            start_date=start_date,
            end_date=end_date,
            axis=axis,
            bucket=bucket,
            taxonomy_id=taxonomy_id,
            group_key=group_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return ContributionBucketResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=ContributionBucketSummary.model_validate(report["summary"]),
        groups=[ContributionBucketGroupRecord.model_validate(item) for item in report["groups"]],
    )


@router.get(
    "/{portfolio_id}/performance/contribution/calendar/drilldown",
    response_model=ContributionBucketCalendarResponse,
)
def get_portfolio_contribution_calendar_drilldown(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    bucket: str = "total_pnl",
    taxonomy_id: str | None = None,
    frequency: str = "monthly",
    group_key: str | None = None,
) -> ContributionBucketCalendarResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in CONTRIBUTION_AXES:
        raise HTTPException(status_code=422, detail=CONTRIBUTION_AXIS_ERROR)
    if frequency not in {"monthly", "weekly"}:
        raise HTTPException(status_code=422, detail="frequency must be monthly or weekly")

    try:
        report = build_contribution_bucket_calendar_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            taxonomies=list_taxonomies(portfolio_id),
            taxonomy_nodes=list_taxonomy_nodes(portfolio_id),
            taxonomy_assignments=list_taxonomy_assignments(portfolio_id),
            start_date=start_date,
            end_date=end_date,
            axis=axis,
            bucket=bucket,
            taxonomy_id=taxonomy_id,
            frequency=frequency,
            group_key=group_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return ContributionBucketCalendarResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=ContributionBucketCalendarSummary.model_validate(report["summary"]),
        buckets=[ContributionBucketCalendarBucketRecord.model_validate(item) for item in report["buckets"]],
    )


@entry_router.get("/{portfolio_id}/performance/contribution/entries", response_model=ContributionEntriesResponse)
def get_portfolio_contribution_entries(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    bucket: str = "income_cash_amount",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> ContributionEntriesResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in CONTRIBUTION_AXES:
        raise HTTPException(status_code=422, detail=CONTRIBUTION_AXIS_ERROR)

    try:
        report = build_contribution_entries_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            taxonomies=list_taxonomies(portfolio_id),
            taxonomy_nodes=list_taxonomy_nodes(portfolio_id),
            taxonomy_assignments=list_taxonomy_assignments(portfolio_id),
            start_date=start_date,
            end_date=end_date,
            axis=axis,
            bucket=bucket,
            taxonomy_id=taxonomy_id,
            group_key=group_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return ContributionEntriesResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=ContributionEntriesSummary.model_validate(report["summary"]),
        entries=[ContributionEntryRecord.model_validate(item) for item in report["entries"]],
    )


@entry_router.get(
    "/{portfolio_id}/performance/contribution/entries/calendar",
    response_model=ContributionEntryCalendarResponse,
)
def get_portfolio_contribution_entries_calendar(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    bucket: str = "income_cash_amount",
    frequency: str = "monthly",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> ContributionEntryCalendarResponse:
    _validate_date_window(start_date, end_date)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in CONTRIBUTION_AXES:
        raise HTTPException(status_code=422, detail=CONTRIBUTION_AXIS_ERROR)
    if frequency not in {"monthly", "weekly"}:
        raise HTTPException(status_code=422, detail="frequency must be monthly or weekly")

    try:
        report = build_contribution_entries_calendar_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            taxonomies=list_taxonomies(portfolio_id),
            taxonomy_nodes=list_taxonomy_nodes(portfolio_id),
            taxonomy_assignments=list_taxonomy_assignments(portfolio_id),
            start_date=start_date,
            end_date=end_date,
            axis=axis,
            bucket=bucket,
            frequency=frequency,
            taxonomy_id=taxonomy_id,
            group_key=group_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return ContributionEntryCalendarResponse(
        portfolio_id=portfolio_id,
        base_currency=report["base_currency"],
        valuation_timezone=report["valuation_timezone"],
        valuation_cutoff_policy=report["valuation_cutoff_policy"],
        summary=ContributionEntryCalendarSummary.model_validate(report["summary"]),
        buckets=[ContributionEntryCalendarBucketRecord.model_validate(item) for item in report["buckets"]],
    )


router.include_router(entry_router)
