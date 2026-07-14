"""Published-only Portfolio Daily performance APIs."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from portfolio_app.api.contracts import (
    PortfolioDailyPublishedPerformanceReportResponse,
    PortfolioDailyPublishedSnapshotListResponse,
)
from portfolio_app.api.published_portfolio_daily import read_published_range
from portfolio_app.calculations.portfolio_daily.published_views import (
    build_performance_report_response,
    build_snapshots_response,
)
from portfolio_app.calculations.portfolio_daily.reporting_engine import (
    AttributionAxis,
    CalendarFrequency,
    PublishedReportingIntegrityError,
)
from portfolio_app.db.session import get_db_session


router = APIRouter()
_ATTRIBUTION_AXES = {
    "portfolio",
    "account",
    "instrument",
    "currency",
    "taxonomy",
}
_CALENDAR_FREQUENCIES = {"monthly", "weekly"}


def _axis(value: str) -> AttributionAxis:
    if value not in _ATTRIBUTION_AXES:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_attribution_axis", "axis": value},
        )
    return value  # type: ignore[return-value]


def _frequency(value: str) -> CalendarFrequency:
    if value not in _CALENDAR_FREQUENCIES:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_calendar_frequency", "frequency": value},
        )
    return value  # type: ignore[return-value]


def _integrity_error(
    portfolio_id: str,
    error: PublishedReportingIntegrityError,
) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "published_reporting_integrity_error",
            "portfolio_id": portfolio_id,
            "reason": str(error),
        },
    )


@router.get(
    "/{portfolio_id}/snapshots/daily",
    response_model=PortfolioDailyPublishedSnapshotListResponse,
)
def list_daily_snapshots(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    session: Session = Depends(get_db_session),
) -> PortfolioDailyPublishedSnapshotListResponse:
    publication = read_published_range(
        session,
        portfolio_id=portfolio_id,
        start_date=start_date,
        end_date=end_date,
        tables=("snapshots",),
    )
    try:
        return build_snapshots_response(publication)
    except PublishedReportingIntegrityError as error:
        raise _integrity_error(portfolio_id, error) from error


@router.get(
    "/{portfolio_id}/performance/report",
    response_model=PortfolioDailyPublishedPerformanceReportResponse,
)
def get_portfolio_performance_report(
    portfolio_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
    axis: str = "instrument",
    frequency: str = "monthly",
    group_key: str | None = None,
    session: Session = Depends(get_db_session),
) -> PortfolioDailyPublishedPerformanceReportResponse:
    """Return the whole Performance page from one fenced publication read."""

    resolved_axis = _axis(axis)
    resolved_frequency = _frequency(frequency)
    publication = read_published_range(
        session,
        portfolio_id=portfolio_id,
        start_date=start_date,
        end_date=end_date,
        tables=("snapshots", "contributions"),
    )
    try:
        return build_performance_report_response(
            publication,
            frequency=resolved_frequency,
            axis=resolved_axis,
            group_key=group_key,
        )
    except PublishedReportingIntegrityError as error:
        raise _integrity_error(portfolio_id, error) from error
