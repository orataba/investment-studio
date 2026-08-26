#!/usr/bin/env python3
"""Refresh Portfolio snapshots invalidated by a release migration."""

from __future__ import annotations

import argparse

from sqlalchemy import or_, select, update

from portfolio_app.db.models import (
    PortfolioCalculationStateModel,
    PortfolioRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.daily_snapshots import (
    _state_requires_refresh,
    ensure_portfolio_daily_snapshots,
)


def _recover_interrupted_refreshes() -> int:
    session_factory = get_session_factory()
    with session_factory() as session:
        result = session.execute(
            update(PortfolioCalculationStateModel)
            .where(
                PortfolioCalculationStateModel.daily_snapshot_status
                == "running"
            )
            .values(
                daily_snapshot_status="stale",
                refresh_request_id=None,
                refresh_started_at=None,
                refresh_completed_at=None,
                error_message=None,
            )
        )
        session.commit()
        return int(result.rowcount or 0)


def main(*, recover_interrupted: bool = False) -> int:
    recovered_count = (
        _recover_interrupted_refreshes() if recover_interrupted else 0
    )
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
        f"{len(refreshed)} refreshed, {len(portfolio_ids) - len(refreshed)} current, "
        f"{recovered_count} interrupted recovered."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recover-interrupted",
        action="store_true",
        help="Reclaim running snapshot jobs after managed database writers have stopped.",
    )
    arguments = parser.parse_args()
    raise SystemExit(
        main(recover_interrupted=arguments.recover_interrupted)
    )
