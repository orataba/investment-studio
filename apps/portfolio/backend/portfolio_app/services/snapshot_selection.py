from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from portfolio_app.core.settings import get_settings
from portfolio_app.db.models import PortfolioDailySnapshotModel, PortfolioRecordModel


def is_fresh_complete_portfolio_snapshot(snapshot: PortfolioDailySnapshotModel) -> bool:
    payload = snapshot.snapshot_json if isinstance(snapshot.snapshot_json, dict) else {}
    return (
        snapshot.valuation_coverage_state == "complete"
        and snapshot.nav is not None
        and not bool(payload.get("stale_price_flag"))
        and not bool(payload.get("stale_fx_flag"))
    )


def latest_fresh_complete_portfolio_snapshot(session, portfolio_id: str) -> PortfolioDailySnapshotModel | None:
    portfolio = session.get(PortfolioRecordModel, portfolio_id)
    timezone_name = str(
        (portfolio.valuation_timezone if portfolio is not None else None)
        or get_settings().default_trade_timezone
    ).strip()
    try:
        valuation_today = datetime.now(ZoneInfo(timezone_name)).date()
    except (KeyError, ValueError):
        valuation_today = datetime.now(
            ZoneInfo(get_settings().default_trade_timezone)
        ).date()
    snapshots = session.scalars(
        select(PortfolioDailySnapshotModel)
        .where(
            PortfolioDailySnapshotModel.portfolio_id == portfolio_id,
            PortfolioDailySnapshotModel.valuation_coverage_state == "complete",
            PortfolioDailySnapshotModel.nav.is_not(None),
            PortfolioDailySnapshotModel.as_of_date <= valuation_today,
        )
        .order_by(PortfolioDailySnapshotModel.as_of_date.desc())
    ).all()
    return next((snapshot for snapshot in snapshots if is_fresh_complete_portfolio_snapshot(snapshot)), None)


def default_portfolio_snapshot(session, portfolio_id: str) -> PortfolioDailySnapshotModel | None:
    return latest_fresh_complete_portfolio_snapshot(session, portfolio_id)
