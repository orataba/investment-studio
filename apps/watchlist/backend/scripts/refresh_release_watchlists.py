#!/usr/bin/env python3
"""Reconcile Watchlist directories and invalidated read models before release.

Run with managed database writers stopped, after migrations and catalog refresh.
This reuses normal directory membership and canonical recalculation against
stored source data. It does not fetch new quotes, run AI research, or alter user
list membership, names, views or research judgments.
"""

import argparse

from sqlalchemy import or_, select

from watchlist_app.api.routes.watchlists import _sync_system_watchlist
from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.read_models import InstrumentChartReadModel, WatchlistRowReadModel
from watchlist_app.db.session import get_session_factory
from watchlist_app.repositories.sqlalchemy.watchlists import SYSTEM_WATCHLIST_SPECS
from watchlist_app.repositories.sqlalchemy.recalc_jobs import SQLAlchemyRecalcJobRepository
from watchlist_app.services.canonical_recalc import CanonicalRecalcService
from watchlist_app.services.materialization_policy import WATCHLIST_MATERIALIZATION_VERSION


def _invalidated_instrument_ids(session) -> list[str]:
    stale_row = select(WatchlistRowReadModel.instrument_id).where(
        WatchlistRowReadModel.instrument_id == InstrumentDetail.instrument_id,
        WatchlistRowReadModel.materialization_version != WATCHLIST_MATERIALIZATION_VERSION,
    ).exists()
    return list(session.scalars(
        select(InstrumentDetail.instrument_id)
        .outerjoin(InstrumentChartReadModel)
        .where(
            InstrumentDetail.is_active.is_(True),
            or_(
                InstrumentChartReadModel.instrument_id.is_(None),
                InstrumentChartReadModel.materialization_version != WATCHLIST_MATERIALIZATION_VERSION,
                stale_row,
            ),
        )
        .order_by(InstrumentDetail.instrument_id)
    ))


def main(*, recover_interrupted: bool = False) -> int:
    with get_session_factory()() as session, session.begin():
        recovered_count = (
            SQLAlchemyRecalcJobRepository().requeue_stale_running_jobs(
                session, timeout_seconds=None, recover_interrupted=True,
            ) if recover_interrupted else 0
        )
        for spec in SYSTEM_WATCHLIST_SPECS:
            # A release cannot use the UI's temporarily retained directory when
            # registry access or materialization fails. Roll back all four lists.
            _sync_system_watchlist(session, spec=spec, raise_on_registry_error=True)
        # The worker also repairs these versions eventually, but the release
        # gate must not succeed while the first request still serves old math.
        instrument_ids = _invalidated_instrument_ids(session)
        recalc = CanonicalRecalcService()
        for instrument_id in instrument_ids:
            recalc.execute_recalc(
                session,
                instrument_id=instrument_id,
                job_type="all",
                trigger_type="release_materialization",
                trigger_ref_type="materialization_version",
                trigger_ref_id=WATCHLIST_MATERIALIZATION_VERSION,
            )
        remaining = _invalidated_instrument_ids(session)
        if remaining:
            raise RuntimeError(f"Watchlist release left invalidated read models: {remaining}")
    print(
        f"Release Watchlist reconciliation completed: {len(SYSTEM_WATCHLIST_SPECS)} system lists, "
        f"{len(instrument_ids)} read models rebuilt to {WATCHLIST_MATERIALIZATION_VERSION}, "
        f"{recovered_count} interrupted jobs recovered."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recover-interrupted", action="store_true",
        help="Reclaim running recalc jobs only after managed database writers have stopped.",
    )
    arguments = parser.parse_args()
    raise SystemExit(main(recover_interrupted=arguments.recover_interrupted))
