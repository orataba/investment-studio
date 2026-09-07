#!/usr/bin/env python3
"""Refresh Portfolio snapshots invalidated by a release migration."""

from __future__ import annotations

import argparse

from sqlalchemy import select, update

from portfolio_app.db.models import (
    PortfolioCalculationStateModel,
    PortfolioRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.daily_snapshots import (
    PortfolioCalculationUnavailable,
    _financial_read_generation_in_session,
    _run_portfolio_daily_snapshot_recalculation_synchronously,
    _state_requires_refresh,
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
            _run_portfolio_daily_snapshot_recalculation_synchronously(str(portfolio_id))
            refreshed.append(str(portfolio_id))

    incomplete: list[str] = []
    blocked: list[str] = []
    with session_factory() as session:
        for portfolio_id in portfolio_ids:
            state = session.get(PortfolioCalculationStateModel, portfolio_id)
            status = state.daily_snapshot_status if state is not None else "missing"
            error = state.error_message if state is not None else None
            try:
                generation = _financial_read_generation_in_session(session, portfolio_id)
            except PortfolioCalculationUnavailable:
                generation = None
            if generation is None:
                incomplete.append(f"{portfolio_id}:{status}:{error or ''}")
            elif status == "failed":
                # A freshly published terminal data gap is readable under the
                # same strict cutoff contract as the financial API. It does not
                # make a genuine calculation failure or stale generation ready.
                blocked.append(
                    f"{portfolio_id}: reliable_through={state.refreshed_to or 'none'}, "
                    f"blocked_from={state.dirty_from}; {error}"
                )
    for detail in blocked:
        print("Release snapshot valuation blocked: " + detail)
    if incomplete:
        raise RuntimeError(
            "Release snapshot refresh left unpublished portfolios: " + ", ".join(incomplete)
        )

    print(
        "Release snapshot refresh completed: "
        f"{len(refreshed)} refreshed, {len(portfolio_ids) - len(refreshed)} already published, "
        f"{len(blocked)} valuation blocked, "
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
