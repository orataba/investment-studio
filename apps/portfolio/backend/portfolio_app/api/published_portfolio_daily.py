"""HTTP boundary for immutable Portfolio Daily publications."""

from __future__ import annotations

from collections.abc import Collection
from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from portfolio_app.calculations.portfolio_daily.published_repository import (
    CurrentPortfolioDailyPublication,
    PortfolioDailyCurrentPublicationChanged,
    PortfolioDailyPublishedReadIntegrityError,
    PortfolioDailyPublishedTable,
    read_current_portfolio_daily_metadata,
    read_current_portfolio_daily_range,
    read_latest_current_portfolio_daily_publication,
)


def calculation_not_ready(
    portfolio_id: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    reason: str = "current_publication_missing",
) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "calculation_not_ready",
            "portfolio_id": portfolio_id,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "reason": reason,
        },
    )


def _translate_read_error(
    portfolio_id: str,
    error: PortfolioDailyCurrentPublicationChanged
    | PortfolioDailyPublishedReadIntegrityError,
) -> HTTPException:
    if isinstance(error, PortfolioDailyCurrentPublicationChanged):
        return HTTPException(
            status_code=409,
            detail={
                "code": "publication_changed_retry",
                "portfolio_id": portfolio_id,
                "expected_publication_id": str(error.expected_publication_id),
                "actual_publication_id": (
                    str(error.actual_publication_id)
                    if error.actual_publication_id is not None
                    else None
                ),
            },
        )
    return HTTPException(
        status_code=503,
        detail={
            "code": "published_calculation_integrity_error",
            "portfolio_id": portfolio_id,
        },
    )


def _require_requested_dates_in_publication(
    publication: CurrentPortfolioDailyPublication,
    *,
    start_date: date | None,
    end_date: date | None,
) -> None:
    output_start = publication.metadata.output_range_start
    output_end = publication.metadata.output_range_end
    if (
        start_date is not None
        and not output_start <= start_date <= output_end
    ) or (
        end_date is not None
        and not output_start <= end_date <= output_end
    ):
        raise calculation_not_ready(
            publication.metadata.portfolio_id,
            start_date=start_date,
            end_date=end_date,
            reason="requested_range_not_in_current_publication",
        )


def read_published_latest(
    session: Session,
    *,
    portfolio_id: str,
    as_of_date: date | None,
    tables: Collection[PortfolioDailyPublishedTable],
) -> CurrentPortfolioDailyPublication:
    try:
        publication = read_latest_current_portfolio_daily_publication(
            session,
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            tables=tables,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_portfolio_id",
                "portfolio_id": portfolio_id,
            },
        ) from error
    except (
        PortfolioDailyCurrentPublicationChanged,
        PortfolioDailyPublishedReadIntegrityError,
    ) as error:
        raise _translate_read_error(portfolio_id, error) from error
    if publication is None:
        raise calculation_not_ready(
            portfolio_id,
            end_date=as_of_date,
        )
    _require_requested_dates_in_publication(
        publication,
        start_date=as_of_date,
        end_date=as_of_date,
    )
    return publication


def confirm_published_read_context(
    session: Session,
    publication: CurrentPortfolioDailyPublication,
) -> None:
    """Reject a response assembled across a concurrent fact-generation change.

    Published output rows are immutable, but some workspaces also project
    current editable facts.  Re-reading the pointer metadata after assembly
    prevents those facts from being presented under stale publication
    freshness metadata.
    """

    expected = publication.metadata
    try:
        current = read_current_portfolio_daily_metadata(
            session,
            portfolio_id=expected.portfolio_id,
        )
    except (
        PortfolioDailyCurrentPublicationChanged,
        PortfolioDailyPublishedReadIntegrityError,
    ) as error:
        raise _translate_read_error(expected.portfolio_id, error) from error
    unchanged = current is not None and (
        current.publication_id,
        current.run_id,
        current.manifest_id,
        current.published_fencing_token,
        current.current_generation,
    ) == (
        expected.publication_id,
        expected.run_id,
        expected.manifest_id,
        expected.published_fencing_token,
        expected.current_generation,
    )
    if unchanged:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "publication_context_changed_retry",
            "portfolio_id": expected.portfolio_id,
            "expected_publication_id": str(expected.publication_id),
            "actual_publication_id": (
                str(current.publication_id) if current is not None else None
            ),
            "expected_generation": expected.current_generation,
            "actual_generation": (
                current.current_generation if current is not None else None
            ),
        },
    )


def read_published_range(
    session: Session,
    *,
    portfolio_id: str,
    start_date: date | None,
    end_date: date | None,
    tables: Collection[PortfolioDailyPublishedTable],
) -> CurrentPortfolioDailyPublication:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_date_range",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
    try:
        publication = read_current_portfolio_daily_range(
            session,
            portfolio_id=portfolio_id,
            range_start=start_date,
            range_end=end_date,
            tables=tables,
        )
    except ValueError as error:
        if "portfolio_id" in str(error):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_portfolio_id",
                    "portfolio_id": portfolio_id,
                },
            ) from error
        raise calculation_not_ready(
            portfolio_id,
            start_date=start_date,
            end_date=end_date,
            reason="requested_range_not_in_current_publication",
        ) from error
    except (
        PortfolioDailyCurrentPublicationChanged,
        PortfolioDailyPublishedReadIntegrityError,
    ) as error:
        raise _translate_read_error(portfolio_id, error) from error
    if publication is None:
        raise calculation_not_ready(
            portfolio_id,
            start_date=start_date,
            end_date=end_date,
        )
    _require_requested_dates_in_publication(
        publication,
        start_date=start_date,
        end_date=end_date,
    )
    return publication


__all__ = [
    "calculation_not_ready",
    "read_published_latest",
    "read_published_range",
]
