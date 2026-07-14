"""Release-only durable Portfolio Daily publication drain.

The normal worker derives an economic date from each portfolio's configured
timezone. A database release instead uses the one date explicitly confirmed by
the operator. This gate runs the same lease-fenced worker and succeeds only
when every portfolio's current generation points to an immutable publication
for exactly that release date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from time import monotonic, sleep

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from portfolio_app.calculations.portfolio_daily.constants import (
    CALCULATION_KIND,
    SCOPE_KIND,
)
from portfolio_app.calculations.portfolio_daily.intent_dispatcher import (
    IntentDispatchStatus,
)
from portfolio_app.calculations.portfolio_daily.published_repository import (
    read_current_portfolio_daily_metadata,
)
from portfolio_app.calculations.portfolio_daily.worker import (
    PortfolioDailyWorker,
    WorkerCycleStatus,
    _registration,
)
from portfolio_app.core.settings import Settings
from portfolio_app.db.models import PortfolioRecordModel
from portfolio_ops_calculation_core import (
    CalculationRecomputeIntent,
    CalculationRun,
    CalculationRunStatus,
    RecomputeIntentStatus,
)


class PortfolioDailyReleaseGateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PortfolioDailyReleaseState:
    portfolio_count: int
    published_portfolio_ids: tuple[str, ...]
    incomplete_portfolio_ids: tuple[str, ...]
    active_run_count: int
    pending_intent_count: int

    @property
    def complete(self) -> bool:
        return not self.incomplete_portfolio_ids and not self.active

    @property
    def active(self) -> bool:
        return self.active_run_count > 0 or self.pending_intent_count > 0


def request_portfolio_daily_release_recompute(
    session: Session,
    *,
    requested_as_of: date,
) -> int:
    """Atomically advance every Portfolio Daily generation for this release."""

    portfolio_count = int(
        session.scalar(
            select(func.count()).select_from(PortfolioRecordModel)
        )
        or 0
    )
    requested_count = int(
        session.scalar(
            text(
                """
                SELECT count(*)
                FROM portfolio.pd_invalidate_scopes(
                    ARRAY(
                        SELECT portfolio_id
                        FROM portfolio.portfolio_record
                        ORDER BY portfolio_id
                    )::varchar[],
                    'portfolio_daily_release',
                    jsonb_build_object(
                        'requested_as_of', CAST(:requested_as_of AS text),
                        'source_relation', 'release_gate'
                    )
                )
                """
            ),
            {"requested_as_of": requested_as_of.isoformat()},
        )
        or 0
    )
    if requested_count != portfolio_count:
        raise PortfolioDailyReleaseGateError(
            "Portfolio Daily release invalidation did not cover every portfolio: "
            f"requested={requested_count} portfolios={portfolio_count}"
        )
    return requested_count


def read_portfolio_daily_release_state(
    session: Session,
    *,
    requested_as_of: date,
) -> PortfolioDailyReleaseState:
    portfolio_ids = tuple(
        session.scalars(
            select(PortfolioRecordModel.portfolio_id).order_by(
                PortfolioRecordModel.portfolio_id
            )
        ).all()
    )
    published: list[str] = []
    incomplete: list[str] = []
    for portfolio_id in portfolio_ids:
        metadata = read_current_portfolio_daily_metadata(
            session,
            portfolio_id=portfolio_id,
        )
        if (
            metadata is not None
            and metadata.requested_as_of == requested_as_of
            and metadata.current_generation == metadata.captured_generation
            and not metadata.pending
            and not metadata.stale
        ):
            published.append(portfolio_id)
        else:
            incomplete.append(portfolio_id)

    active_run_count = int(
        session.scalar(
            select(func.count())
            .select_from(CalculationRun)
            .where(
                CalculationRun.calculation_kind == CALCULATION_KIND,
                CalculationRun.scope_kind == SCOPE_KIND,
                CalculationRun.status.in_(
                    (
                        CalculationRunStatus.CAPTURING,
                        CalculationRunStatus.QUEUED,
                        CalculationRunStatus.RUNNING,
                        CalculationRunStatus.SUCCEEDED,
                    )
                ),
            )
        )
        or 0
    )
    pending_intent_count = int(
        session.scalar(
            select(func.count())
            .select_from(CalculationRecomputeIntent)
            .where(
                CalculationRecomputeIntent.calculation_kind
                == CALCULATION_KIND,
                CalculationRecomputeIntent.scope_kind == SCOPE_KIND,
                CalculationRecomputeIntent.status
                == RecomputeIntentStatus.PENDING,
            )
        )
        or 0
    )
    return PortfolioDailyReleaseState(
        portfolio_count=len(portfolio_ids),
        published_portfolio_ids=tuple(published),
        incomplete_portfolio_ids=tuple(incomplete),
        active_run_count=active_run_count,
        pending_intent_count=pending_intent_count,
    )


def drain_portfolio_daily_publications(
    engine: Engine,
    *,
    settings: Settings,
    requested_as_of: date,
    timeout_seconds: float,
) -> PortfolioDailyReleaseState:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    with Session(bind=engine, expire_on_commit=False) as session:
        with session.begin():
            request_portfolio_daily_release_recompute(
                session,
                requested_as_of=requested_as_of,
            )
    deadline = monotonic() + timeout_seconds
    worker = PortfolioDailyWorker(
        engine=engine,
        settings=settings,
        registration=_registration("release-gate"),
        intent_requested_as_of=requested_as_of,
    )
    worker.start()
    try:
        while True:
            outcome = worker.run_once()
            if outcome.dispatch.status is IntentDispatchStatus.FAILED:
                raise PortfolioDailyReleaseGateError(
                    "Portfolio Daily intent capture failed: "
                    f"portfolio={outcome.dispatch.portfolio_id!r} "
                    f"reason={outcome.dispatch.reason_code!r}"
                )
            if outcome.status is WorkerCycleStatus.FAILED:
                raise PortfolioDailyReleaseGateError(
                    "Portfolio Daily calculation failed: "
                    f"run_id={outcome.run_id} reason={outcome.reason_code!r}"
                )

            with Session(bind=engine, expire_on_commit=False) as session:
                state = read_portfolio_daily_release_state(
                    session,
                    requested_as_of=requested_as_of,
                )
            if state.complete:
                return state
            if not state.active:
                raise PortfolioDailyReleaseGateError(
                    "Portfolio Daily drain stopped without a current exact-date "
                    "publication for: "
                    + ", ".join(state.incomplete_portfolio_ids)
                )
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise PortfolioDailyReleaseGateError(
                    "Portfolio Daily publication drain timed out; incomplete="
                    + ",".join(state.incomplete_portfolio_ids)
                )
            if outcome.status is WorkerCycleStatus.IDLE:
                sleep(min(0.25, remaining))
    finally:
        worker.close()


__all__ = [
    "PortfolioDailyReleaseGateError",
    "PortfolioDailyReleaseState",
    "drain_portfolio_daily_publications",
    "read_portfolio_daily_release_state",
    "request_portfolio_daily_release_recompute",
]
