from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

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
    build_daily_portfolio_snapshots,
    build_portfolio_performance_report,
    build_return_calendar_report,
    summarize_daily_snapshots,
)
from portfolio_app.services.portfolio_store import get_portfolio, list_accounts, list_transactions
from portfolio_app.services.portfolio_store import (
    list_taxonomies,
    list_taxonomy_assignments,
    list_taxonomy_nodes,
)


router = APIRouter()


@router.get("/{portfolio_id}/snapshots/daily", response_model=DailySnapshotListResponse)
def list_daily_snapshots(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> DailySnapshotListResponse:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        snapshots = build_daily_portfolio_snapshots(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            start_date=start_date,
            end_date=end_date,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return DailySnapshotListResponse(
        portfolio_id=portfolio_id,
        base_currency=str(portfolio.get("base_currency") or "USD"),
        valuation_timezone=str(portfolio.get("valuation_timezone") or ""),
        valuation_cutoff_policy=str(portfolio.get("valuation_cutoff_policy") or "latest_complete_eod"),
        summary=DailySnapshotListSummary.model_validate(summarize_daily_snapshots(snapshots)),
        snapshots=[DailySnapshotRecord.model_validate(item) for item in snapshots],
    )


@router.get("/{portfolio_id}/performance", response_model=PerformanceResponse)
def get_portfolio_performance(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> PerformanceResponse:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        report = build_portfolio_performance_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            start_date=start_date,
            end_date=end_date,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

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
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    try:
        report = build_period_calculation_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            start_date=start_date,
            end_date=end_date,
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
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in {"instrument", "account", "taxonomy"}:
        raise HTTPException(status_code=422, detail="axis must be instrument, account, or taxonomy")

    try:
        report = build_period_calculation_groups_report(
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
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in {"instrument", "account", "taxonomy"}:
        raise HTTPException(status_code=422, detail="axis must be instrument, account, or taxonomy")

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
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in {"instrument", "account", "taxonomy"}:
        raise HTTPException(status_code=422, detail="axis must be instrument, account, or taxonomy")

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


@router.get("/{portfolio_id}/performance/calculation/entries", response_model=PeriodCalculationEntriesResponse)
def get_portfolio_period_calculation_entries(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    bucket: str = "earnings",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> PeriodCalculationEntriesResponse:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in {"instrument", "account", "taxonomy"}:
        raise HTTPException(status_code=422, detail="axis must be instrument, account, or taxonomy")

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


@router.get(
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
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in {"instrument", "account", "taxonomy"}:
        raise HTTPException(status_code=422, detail="axis must be instrument, account, or taxonomy")

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
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis is not None and axis not in {"instrument", "account", "taxonomy"}:
        raise HTTPException(status_code=422, detail="axis must be instrument, account, or taxonomy")

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
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if frequency not in {"monthly", "weekly"}:
        raise HTTPException(status_code=422, detail="frequency must be monthly or weekly")

    try:
        report = build_return_calendar_report(
            portfolio,
            list_accounts(portfolio_id),
            list_transactions(portfolio_id),
            start_date=start_date,
            end_date=end_date,
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
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in {"instrument", "account", "taxonomy"}:
        raise HTTPException(status_code=422, detail="axis must be instrument, account, or taxonomy")

    try:
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
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in {"instrument", "account", "taxonomy"}:
        raise HTTPException(status_code=422, detail="axis must be instrument, account, or taxonomy")
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
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in {"instrument", "account", "taxonomy"}:
        raise HTTPException(status_code=422, detail="axis must be instrument, account, or taxonomy")

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
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in {"instrument", "account", "taxonomy"}:
        raise HTTPException(status_code=422, detail="axis must be instrument, account, or taxonomy")
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


@router.get("/{portfolio_id}/performance/contribution/entries", response_model=ContributionEntriesResponse)
def get_portfolio_contribution_entries(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    bucket: str = "income_cash_amount",
    taxonomy_id: str | None = None,
    group_key: str | None = None,
) -> ContributionEntriesResponse:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in {"instrument", "account", "taxonomy"}:
        raise HTTPException(status_code=422, detail="axis must be instrument, account, or taxonomy")

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


@router.get(
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
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if axis not in {"instrument", "account", "taxonomy"}:
        raise HTTPException(status_code=422, detail="axis must be instrument, account, or taxonomy")
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
