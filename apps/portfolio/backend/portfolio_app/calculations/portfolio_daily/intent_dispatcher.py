"""Durable recompute-intent to sealed Portfolio Daily job dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from sqlalchemy import Date, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from portfolio_app.calculations.portfolio_daily.capture_common import (
    ManifestCaptureError,
    PortfolioDailyCapturePolicy,
)
from portfolio_app.calculations.portfolio_daily.commands import (
    PortfolioDailyCommandError,
    PortfolioDailyRunHandle,
    enqueue_portfolio_daily,
)
from portfolio_app.calculations.portfolio_daily.constants import (
    CALCULATION_KIND,
    SCOPE_KIND,
)
from portfolio_app.core.settings import Settings
from portfolio_app.db.models import PortfolioRecordModel
from portfolio_ops_calculation_core import (
    ActiveDedupeConflict,
    CalculationRunStatus,
    LifecycleRepository,
    LifecycleRepositoryError,
    PendingRecomputeIntent,
)


class IntentDispatchStatus(StrEnum):
    IDLE = "idle"
    MATERIALIZED = "materialized"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class PortfolioDailyIntentDispatchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PortfolioDailyIntentDispatchOutcome:
    status: IntentDispatchStatus
    intent_id: str | None = None
    portfolio_id: str | None = None
    requested_generation: int | None = None
    run: PortfolioDailyRunHandle | None = None
    reason_code: str | None = None


_DETERMINISTIC_CAPTURE_ERRORS = (
    ManifestCaptureError,
    PortfolioDailyCommandError,
)


def _capture_policy(settings: Settings) -> PortfolioDailyCapturePolicy:
    return PortfolioDailyCapturePolicy(
        daily_market_max_age_days=(
            settings.daily_market_valuation_quote_max_age_days
        ),
        fund_max_age_days=settings.fund_valuation_quote_max_age_days,
        fx_max_age_days=settings.fx_valuation_quote_max_age_days,
    )


def _retryable_database_error(error: DBAPIError) -> bool:
    return getattr(error.orig, "sqlstate", None) in {"40001", "40P01"}


def _database_local_date(session: Session, *, portfolio_id: str) -> date:
    table = PortfolioRecordModel.__table__
    value = session.scalar(
        select(
            (
                # PostgreSQL applies the IANA zone stored with the portfolio;
                # the worker clock never chooses the economic date.
                # SQLAlchemy's ``op('AT TIME ZONE')`` keeps this expression
                # parameterized rather than interpolating the zone name.
                func.transaction_timestamp().op("AT TIME ZONE")(
                    table.c.valuation_timezone
                )
            ).cast(Date)
        ).where(table.c.portfolio_id == portfolio_id)
    )
    if type(value) is not date:
        # A scope may outlive its portfolio record (for example after an
        # administrative deletion).  This is a deterministic capture failure,
        # not a dispatcher-infrastructure failure: terminalize this intent so
        # it cannot poison the oldest-first queue forever.
        raise PortfolioDailyCommandError(
            f"portfolio valuation date is unavailable: {portfolio_id}"
        )
    return value


def _dispatch_locked_intent(
    session: Session,
    *,
    intent: PendingRecomputeIntent,
    requested_by: str,
    policy: PortfolioDailyCapturePolicy,
    max_attempts: int,
    requested_as_of: date | None,
) -> PortfolioDailyIntentDispatchOutcome:
    if (
        intent.scope.calculation_kind != CALCULATION_KIND
        or intent.scope.scope_kind != SCOPE_KIND
    ):
        raise PortfolioDailyIntentDispatchError(
            "Portfolio Daily dispatcher received a foreign calculation scope"
        )
    if intent.current_generation < intent.requested_generation:
        raise PortfolioDailyIntentDispatchError(
            "calculation scope generation regressed below its pending intent"
        )
    if intent.current_generation > intent.requested_generation:
        LifecycleRepository.supersede_recompute_intent(
            session,
            intent.handle,
            reason_code="newer_generation_pending",
        )
        return PortfolioDailyIntentDispatchOutcome(
            status=IntentDispatchStatus.SUPERSEDED,
            intent_id=str(intent.intent_id),
            portfolio_id=intent.scope.scope_id,
            requested_generation=intent.requested_generation,
            reason_code="newer_generation_pending",
        )

    as_of_date = requested_as_of or _database_local_date(
        session,
        portfolio_id=intent.scope.scope_id,
    )
    run = enqueue_portfolio_daily(
        session,
        portfolio_id=intent.scope.scope_id,
        as_of_date=as_of_date,
        requested_by=requested_by,
        policy=policy,
        max_attempts=max_attempts,
    )
    if run.captured_generation != intent.requested_generation:
        raise PortfolioDailyIntentDispatchError(
            "captured run generation does not match its recompute intent"
        )
    if run.deduplicated and run.status is CalculationRunStatus.CAPTURING:
        raise PortfolioDailyIntentDispatchError(
            "a committed capturing run cannot satisfy a recompute intent"
        )
    if run.deduplicated and run.status is not CalculationRunStatus.QUEUED:
        LifecycleRepository.supersede_recompute_intent(
            session,
            intent.handle,
            reason_code="generation_run_already_active",
        )
        return PortfolioDailyIntentDispatchOutcome(
            status=IntentDispatchStatus.SUPERSEDED,
            intent_id=str(intent.intent_id),
            portfolio_id=intent.scope.scope_id,
            requested_generation=intent.requested_generation,
            run=run,
            reason_code="generation_run_already_active",
        )
    LifecycleRepository.materialize_recompute_intent(
        session,
        intent.handle,
        run_id=run.run_id,
    )
    return PortfolioDailyIntentDispatchOutcome(
        status=IntentDispatchStatus.MATERIALIZED,
        intent_id=str(intent.intent_id),
        portfolio_id=intent.scope.scope_id,
        requested_generation=intent.requested_generation,
        run=run,
    )


def _fail_pending_intent(
    engine: Engine,
    *,
    intent: PendingRecomputeIntent,
    reason_code: str,
) -> None:
    try:
        with Session(bind=engine, expire_on_commit=False) as session:
            with session.begin():
                LifecycleRepository.fail_recompute_intent(
                    session,
                    intent.handle,
                    reason_code=reason_code,
                )
    except LifecycleRepositoryError:
        # The original transaction rolled back before this terminalization.
        # Another dispatcher may have acquired and resolved the same row in
        # that gap; its terminal state is authoritative.
        return


def dispatch_next_portfolio_daily_intent(
    engine: Engine,
    *,
    settings: Settings,
    requested_by: str,
    transaction_attempts: int = 3,
    requested_as_of: date | None = None,
) -> PortfolioDailyIntentDispatchOutcome:
    """Materialize at most one intent in one exact capture transaction."""

    if not 1 <= transaction_attempts <= 10:
        raise PortfolioDailyIntentDispatchError(
            "transaction_attempts must be between 1 and 10"
        )
    policy = _capture_policy(settings)
    last_error: Exception | None = None
    for attempt in range(transaction_attempts):
        locked_intent: PendingRecomputeIntent | None = None
        try:
            with engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection:
                with Session(bind=connection, expire_on_commit=False) as session:
                    with session.begin():
                        locked_intent = (
                            LifecycleRepository.lock_next_pending_recompute_intent(
                                session,
                                calculation_kinds=(CALCULATION_KIND,),
                            )
                        )
                        if locked_intent is None:
                            return PortfolioDailyIntentDispatchOutcome(
                                status=IntentDispatchStatus.IDLE
                            )
                        return _dispatch_locked_intent(
                            session,
                            intent=locked_intent,
                            requested_by=requested_by,
                            policy=policy,
                            max_attempts=settings.calculation_job_max_attempts,
                            requested_as_of=requested_as_of,
                        )
        except ActiveDedupeConflict as exc:
            # A unique row not visible to this REPEATABLE READ snapshot requires
            # the entire capture transaction to restart.
            if exc.active_run is not None:
                raise
            last_error = exc
        except DBAPIError as exc:
            if not _retryable_database_error(exc):
                raise
            last_error = exc
        except _DETERMINISTIC_CAPTURE_ERRORS:
            if locked_intent is not None:
                _fail_pending_intent(
                    engine,
                    intent=locked_intent,
                    reason_code="manifest_capture_failed",
                )
                return PortfolioDailyIntentDispatchOutcome(
                    status=IntentDispatchStatus.FAILED,
                    intent_id=str(locked_intent.intent_id),
                    portfolio_id=locked_intent.scope.scope_id,
                    requested_generation=locked_intent.requested_generation,
                    reason_code="manifest_capture_failed",
                )
            raise
        if attempt + 1 == transaction_attempts:
            break
    raise PortfolioDailyIntentDispatchError(
        "intent dispatch could not obtain a stable database snapshot"
    ) from last_error


__all__ = [
    "IntentDispatchStatus",
    "PortfolioDailyIntentDispatchError",
    "PortfolioDailyIntentDispatchOutcome",
    "dispatch_next_portfolio_daily_intent",
]
