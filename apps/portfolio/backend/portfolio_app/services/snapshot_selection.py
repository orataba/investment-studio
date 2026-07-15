from __future__ import annotations

from sqlalchemy import select

from portfolio_app.db.models import PortfolioDailySnapshotModel


def is_fresh_complete_portfolio_snapshot(snapshot: PortfolioDailySnapshotModel) -> bool:
    payload = snapshot.snapshot_json if isinstance(snapshot.snapshot_json, dict) else {}
    return (
        snapshot.valuation_coverage_state == "complete"
        and snapshot.nav is not None
        and not bool(payload.get("stale_price_flag"))
    )


def latest_fresh_complete_portfolio_snapshot(session, portfolio_id: str) -> PortfolioDailySnapshotModel | None:
    snapshots = session.scalars(
        select(PortfolioDailySnapshotModel)
        .where(
            PortfolioDailySnapshotModel.portfolio_id == portfolio_id,
            PortfolioDailySnapshotModel.valuation_coverage_state == "complete",
            PortfolioDailySnapshotModel.nav.is_not(None),
        )
        .order_by(PortfolioDailySnapshotModel.as_of_date.desc())
    ).all()
    return next((snapshot for snapshot in snapshots if is_fresh_complete_portfolio_snapshot(snapshot)), None)


def default_portfolio_snapshot(session, portfolio_id: str) -> PortfolioDailySnapshotModel | None:
    return latest_fresh_complete_portfolio_snapshot(session, portfolio_id)
