"""Application commands for creating sealed Portfolio Daily calculation runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from portfolio_app.calculations.portfolio_daily.capture import (
    persist_seal_and_queue_portfolio_daily,
)
from portfolio_app.calculations.portfolio_daily.capture_common import (
    PortfolioDailyCapturePolicy,
)
from portfolio_app.calculations.portfolio_daily.constants import (
    CALCULATION_KIND,
    INPUT_SCHEMA_VERSION,
    METHODOLOGY_VERSION,
    OUTPUT_SCHEMA_VERSION,
    SCOPE_KIND,
)
from portfolio_app.core.settings import Settings
from portfolio_app.db.models import PortfolioRecordModel
from portfolio_ops_calculation_core import (
    ActiveDedupeConflict,
    CalculationRunStatus,
    CalculationScope,
    LifecycleRepository,
    RunLineage,
    RunRequest,
    ScopeGenerationLock,
)


class PortfolioDailyCommandError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PortfolioDailyRunHandle:
    run_id: UUID
    manifest_id: UUID | None
    portfolio_id: str
    status: CalculationRunStatus
    requested_as_of: date
    effective_as_of: date
    cutoff_at: datetime
    captured_generation: int
    deduplicated: bool
    canonical_manifest_hash: str | None


def _assert_repeatable_read(session: Session) -> None:
    if session.get_bind().dialect.name != "postgresql":
        raise PortfolioDailyCommandError(
            "Portfolio Daily commands require PostgreSQL"
        )
    isolation = str(
        session.execute(text("SHOW transaction_isolation")).scalar_one()
    ).lower()
    if isolation != "repeatable read":
        raise PortfolioDailyCommandError(
            "Portfolio Daily commands require a REPEATABLE READ transaction"
        )


def _portfolio_valuation_context(
    session: Session,
    portfolio_id: str,
) -> tuple[str, date]:
    local_transaction_date = (
        func.transaction_timestamp()
        .op("AT TIME ZONE")(PortfolioRecordModel.valuation_timezone)
        .cast(Date)
        .label("local_transaction_date")
    )
    row = session.execute(
        select(
            PortfolioRecordModel.portfolio_id,
            PortfolioRecordModel.valuation_timezone,
            local_transaction_date,
        )
        .where(PortfolioRecordModel.portfolio_id == portfolio_id)
        .with_for_update(read=True)
    ).one_or_none()
    if row is None:
        raise PortfolioDailyCommandError(f"portfolio not found: {portfolio_id}")
    timezone_name = str(row.valuation_timezone or "").strip()
    if not timezone_name:
        raise PortfolioDailyCommandError(
            f"portfolio valuation timezone is missing: {portfolio_id}"
        )
    if type(row.local_transaction_date) is not date:
        raise PortfolioDailyCommandError(
            f"portfolio valuation date is unavailable: {portfolio_id}"
        )
    return timezone_name, row.local_transaction_date


def enqueue_portfolio_daily(
    session: Session,
    *,
    portfolio_id: str,
    as_of_date: date,
    requested_by: str,
    policy: PortfolioDailyCapturePolicy,
    max_attempts: int,
) -> PortfolioDailyRunHandle:
    """Capture, seal, and queue a run in the caller's one open transaction."""

    _assert_repeatable_read(session)
    if type(as_of_date) is not date:
        raise PortfolioDailyCommandError("as_of_date must be a date")
    scope = CalculationScope(CALCULATION_KIND, SCOPE_KIND, portfolio_id)
    timezone_name, local_transaction_date = _portfolio_valuation_context(
        session,
        portfolio_id,
    )
    if as_of_date > local_transaction_date:
        raise PortfolioDailyCommandError(
            "as_of_date must not be after the portfolio-local transaction date "
            f"({local_transaction_date.isoformat()} in {timezone_name})"
        )
    generation = LifecycleRepository.read_scope_generation(
        session,
        scope,
        lock=ScopeGenerationLock.SHARE,
    )
    request = RunRequest(
        scope=scope,
        requested_as_of=as_of_date,
        effective_as_of=as_of_date,
        timezone_name=timezone_name,
        methodology_version=METHODOLOGY_VERSION,
        input_schema_version=INPUT_SCHEMA_VERSION,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        captured_generation=generation.generation,
        requested_by=requested_by,
    )
    try:
        capturing = LifecycleRepository.create_capturing_run(session, request)
    except ActiveDedupeConflict as exc:
        active = exc.active_run
        if active is None:
            raise
        return PortfolioDailyRunHandle(
            run_id=active.run_id,
            manifest_id=active.manifest_id,
            portfolio_id=active.scope.scope_id,
            status=active.status,
            requested_as_of=active.requested_as_of,
            effective_as_of=active.effective_as_of,
            cutoff_at=active.cutoff_at,
            captured_generation=active.captured_generation,
            deduplicated=True,
            canonical_manifest_hash=None,
        )

    queued = persist_seal_and_queue_portfolio_daily(
        session,
        capturing=capturing,
        policy=policy,
        max_attempts=max_attempts,
    )
    return PortfolioDailyRunHandle(
        run_id=capturing.run_id,
        manifest_id=capturing.manifest_id,
        portfolio_id=portfolio_id,
        status=CalculationRunStatus.QUEUED,
        requested_as_of=as_of_date,
        effective_as_of=as_of_date,
        cutoff_at=capturing.cutoff_at,
        captured_generation=generation.generation,
        deduplicated=False,
        canonical_manifest_hash=queued.seal.canonical_manifest_hash,
    )


def _retryable_database_error(error: DBAPIError) -> bool:
    sqlstate = getattr(error.orig, "sqlstate", None)
    return sqlstate in {"40001", "40P01"}


def enqueue_portfolio_daily_unit_of_work(
    engine: Engine,
    *,
    portfolio_id: str,
    as_of_date: date,
    requested_by: str,
    settings: Settings,
    transaction_attempts: int = 3,
) -> PortfolioDailyRunHandle:
    """Own and retry the PostgreSQL snapshot transaction used by the API."""

    if not 1 <= transaction_attempts <= 10:
        raise PortfolioDailyCommandError(
            "transaction_attempts must be between 1 and 10"
        )
    policy = PortfolioDailyCapturePolicy(
        daily_market_max_age_days=(
            settings.daily_market_valuation_quote_max_age_days
        ),
        fund_max_age_days=settings.fund_valuation_quote_max_age_days,
        fx_max_age_days=settings.fx_valuation_quote_max_age_days,
    )
    last_error: Exception | None = None
    for attempt in range(transaction_attempts):
        try:
            with engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection:
                with Session(bind=connection, expire_on_commit=False) as session:
                    with session.begin():
                        return enqueue_portfolio_daily(
                            session,
                            portfolio_id=portfolio_id,
                            as_of_date=as_of_date,
                            requested_by=requested_by,
                            policy=policy,
                            max_attempts=settings.calculation_job_max_attempts,
                        )
        except ActiveDedupeConflict as exc:
            if exc.active_run is not None:
                raise PortfolioDailyCommandError(
                    "visible active run conflict escaped command boundary"
                ) from exc
            last_error = exc
        except DBAPIError as exc:
            if not _retryable_database_error(exc):
                raise
            last_error = exc
        if attempt + 1 == transaction_attempts:
            break
    raise PortfolioDailyCommandError(
        "Portfolio Daily command could not obtain a stable database snapshot"
    ) from last_error


def read_portfolio_daily_run(
    session: Session,
    *,
    run_id: UUID,
) -> RunLineage | None:
    lineage = LifecycleRepository.read_run_lineage(session, run_id)
    if lineage is None:
        return None
    if (
        lineage.scope.calculation_kind != CALCULATION_KIND
        or lineage.scope.scope_kind != SCOPE_KIND
    ):
        return None
    return lineage


__all__ = [
    "PortfolioDailyCommandError",
    "PortfolioDailyRunHandle",
    "enqueue_portfolio_daily",
    "enqueue_portfolio_daily_unit_of_work",
    "read_portfolio_daily_run",
]
