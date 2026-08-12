#!/usr/bin/env python3
"""Refresh Portfolio snapshots invalidated by a release migration."""

from __future__ import annotations

from sqlalchemy import or_, select

from portfolio_app.db.models import (
    PortfolioCalculationStateModel,
    PortfolioRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.daily_snapshots import (
    _state_requires_refresh,
    ensure_portfolio_daily_snapshots,
)


def main() -> int:
    session_factory = get_session_factory()
    with session_factory() as session:
        portfolio_ids = list(
            session.scalars(
                select(PortfolioRecordModel.portfolio_id).order_by(
                    PortfolioRecordModel.portfolio_id
                )
            ).all()
        )

    refreshed: list[str] = []
    for portfolio_id in portfolio_ids:
        with session_factory() as session:
            should_refresh = _state_requires_refresh(session, str(portfolio_id))
        if should_refresh:
            ensure_portfolio_daily_snapshots(str(portfolio_id))
            refreshed.append(str(portfolio_id))

    with session_factory() as session:
        incomplete = list(
            session.execute(
                select(
                    PortfolioRecordModel.portfolio_id,
                    PortfolioCalculationStateModel.daily_snapshot_status,
                    PortfolioCalculationStateModel.error_message,
                )
                .outerjoin(
                    PortfolioCalculationStateModel,
                    PortfolioCalculationStateModel.portfolio_id
                    == PortfolioRecordModel.portfolio_id,
                )
                .where(
                    or_(
                        PortfolioCalculationStateModel.daily_snapshot_status.is_(
                            None
                        ),
                        PortfolioCalculationStateModel.daily_snapshot_status
                        != "current",
                    )
                )
                .order_by(PortfolioRecordModel.portfolio_id)
            ).all()
        )
    if incomplete:
        details = ", ".join(
            f"{portfolio_id}:{status or 'missing'}:{error or ''}"
            for portfolio_id, status, error in incomplete
        )
        raise RuntimeError(
            "Release snapshot refresh left non-current portfolios: " + details
        )

    print(
        "Release snapshot refresh completed: "
        f"{len(refreshed)} refreshed, {len(portfolio_ids) - len(refreshed)} current."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
