"""Durable, lease-fenced Portfolio Daily worker process."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
import logging
import os
import signal
import socket
from threading import Event, Lock, Thread
from time import monotonic
from types import FrameType
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from portfolio_app.calculations.portfolio_daily.constants import (
    CALCULATION_KIND,
    SCOPE_KIND,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    portfolio_daily_run_output,
)
from portfolio_app.calculations.portfolio_daily.intent_dispatcher import (
    PortfolioDailyIntentDispatchOutcome,
    dispatch_next_portfolio_daily_intent,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer import (
    build_ledger_events,
)
from portfolio_app.calculations.portfolio_daily.ledger_event_producer_contracts import (
    LedgerEventBuildError,
)
from portfolio_app.calculations.portfolio_daily.ledger_replay import (
    replay_ledger_series,
)
from portfolio_app.calculations.portfolio_daily.ledger_state import LedgerStatus
from portfolio_app.calculations.portfolio_daily.output_builder import (
    PortfolioDailyOutputBuildError,
    build_portfolio_daily_financial_outputs,
)
from portfolio_app.calculations.portfolio_daily.output_repository import (
    PortfolioDailyFinancialOutputs,
    PortfolioDailyOutputError,
    insert_portfolio_daily_output_attempt,
    prepare_portfolio_daily_output_attempt,
)
from portfolio_app.calculations.portfolio_daily.sealed_manifest import (
    SealedManifestError,
    SealedPortfolioDailyManifest,
    load_sealed_portfolio_daily_manifest,
)
from portfolio_app.calculations.portfolio_daily.valuation_contracts import (
    ValuationContractError,
)
from portfolio_app.calculations.portfolio_daily.valuation_engine import (
    ValuationClosureError,
    calculate_exact_portfolio_valuation,
)
from portfolio_app.calculations.portfolio_daily.valuation_input_builder import (
    ValuationInputBuildError,
    build_daily_valuation_book,
)
from portfolio_app.calculations.portfolio_daily.worker_repository import (
    PublicationRecoveryOutcome,
    lease_has_retry_remaining,
    recover_next_portfolio_daily_publication,
    supersede_next_stale_generation_run,
)
from portfolio_app.core.settings import Settings, get_settings
from portfolio_app.db.session import get_engine
from portfolio_ops_calculation_core import (
    ActiveJobLease,
    CalculationRun,
    CalculationRunStatus,
    FailureReason,
    LifecycleErrorCode,
    LifecycleRepository,
    LifecycleRepositoryError,
    StaleLease,
    WorkerRegistration,
)


LOGGER = logging.getLogger("portfolio_daily.worker")
WORKER_VERSION = "portfolio-daily-worker.v1"


class WorkerCycleStatus(StrEnum):
    IDLE = "idle"
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    STALE = "stale"


class PortfolioDailyWorkerError(RuntimeError):
    pass


class PortfolioDailyCalculationFailure(PortfolioDailyWorkerError):
    def __init__(self, code: str, diagnostic: str) -> None:
        super().__init__(diagnostic)
        self.code = code
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class WorkerCycleOutcome:
    status: WorkerCycleStatus
    dispatch: PortfolioDailyIntentDispatchOutcome
    publication: PublicationRecoveryOutcome
    run_id: UUID | None = None
    canonical_output_hash: str | None = None
    reason_code: str | None = None


_DETERMINISTIC_CALCULATION_ERRORS = (
    LedgerEventBuildError,
    PortfolioDailyCalculationFailure,
    PortfolioDailyOutputBuildError,
    PortfolioDailyOutputError,
    SealedManifestError,
    ValuationContractError,
    ValuationInputBuildError,
)


@dataclass(frozen=True, slots=True)
class _AttemptFailure:
    deterministic: bool
    code: str


_TRANSIENT_SQLSTATES = frozenset(
    {
        "40001",  # serialization_failure
        "40P01",  # deadlock_detected
        "55P03",  # lock_not_available
        "57P01",  # admin_shutdown
        "57P02",  # crash_shutdown
        "57P03",  # cannot_connect_now
        "57014",  # query_canceled / statement timeout
    }
)


def _database_sqlstate(error: DBAPIError) -> str | None:
    value = getattr(error.orig, "sqlstate", None) or getattr(
        error.orig,
        "pgcode",
        None,
    )
    return value if isinstance(value, str) and len(value) == 5 else None


def _retryable_database_error(error: DBAPIError) -> bool:
    sqlstate = _database_sqlstate(error)
    return bool(
        sqlstate is not None
        and (sqlstate in _TRANSIENT_SQLSTATES or sqlstate.startswith("08"))
    )


def _classify_attempt_failure(error: BaseException) -> _AttemptFailure:
    """Classify a failed attempt without retrying deterministic bad data.

    PostgreSQL class 22 (data exception) and class 23 (integrity constraint
    violation) describe a reproducible disagreement between the exact output
    and its storage contract. Retrying the same sealed manifest cannot repair
    either class. Infrastructure/transient failures remain retryable.
    """

    if isinstance(error, PortfolioDailyCalculationFailure):
        return _AttemptFailure(True, error.code)
    if isinstance(error, ValuationClosureError):
        return _AttemptFailure(True, error.reason_code.value)
    if isinstance(error, LedgerEventBuildError):
        return _AttemptFailure(True, error.code.value)
    if isinstance(error, PortfolioDailyOutputBuildError):
        return _AttemptFailure(True, "output_build_contract_failed")
    if isinstance(error, PortfolioDailyOutputError):
        return _AttemptFailure(True, "output_persistence_contract_failed")
    if isinstance(error, SealedManifestError):
        return _AttemptFailure(True, "sealed_manifest_verification_failed")
    if isinstance(error, ValuationInputBuildError):
        return _AttemptFailure(True, "valuation_input_contract_failed")
    if isinstance(error, ValuationContractError):
        return _AttemptFailure(True, "valuation_contract_failed")
    if isinstance(error, DBAPIError):
        sqlstate = _database_sqlstate(error)
        if sqlstate is not None and sqlstate.startswith("22"):
            return _AttemptFailure(True, "database_data_contract_failed")
        if sqlstate is not None and sqlstate.startswith("23"):
            return _AttemptFailure(True, "database_integrity_contract_failed")
        if sqlstate is not None and not _retryable_database_error(error):
            return _AttemptFailure(True, "database_nontransient_failure")
        return _AttemptFailure(False, "database_transient_failure")
    if isinstance(error, _DETERMINISTIC_CALCULATION_ERRORS):
        return _AttemptFailure(True, "calculation_contract_failed")
    return _AttemptFailure(False, "worker_attempt_failed")


def _diagnostic(error: BaseException) -> str:
    source = error.orig if isinstance(error, DBAPIError) else error
    value = str(source).strip() or type(error).__name__
    return value[:2000].strip()


def _output_portfolio_id(
    financial_outputs: PortfolioDailyFinancialOutputs,
) -> str:
    rows = financial_outputs.rows_by_table.get(portfolio_daily_run_output.name)
    if (
        not isinstance(rows, Sequence)
        or isinstance(rows, (str, bytes))
        or len(rows) != 1
        or not isinstance(rows[0], Mapping)
    ):
        raise PortfolioDailyOutputError(
            "exactly one mapped Portfolio Daily run output row is required"
        )
    value = rows[0].get("portfolio_id")
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 255
    ):
        raise PortfolioDailyOutputError(
            "Portfolio Daily run output portfolio_id is invalid"
        )
    return value


def _manifest_range(manifest: SealedPortfolioDailyManifest) -> tuple[date, date]:
    rows = manifest.dependencies.rows_by_table["portfolio_daily_config_input"]
    if len(rows) != 1:
        raise PortfolioDailyCalculationFailure(
            "input_config_cardinality",
            "sealed manifest must contain exactly one Portfolio Daily config",
        )
    start = rows[0].get("range_start")
    end = rows[0].get("effective_as_of")
    if type(start) is not date or type(end) is not date or end < start:
        raise PortfolioDailyCalculationFailure(
            "input_date_range_invalid",
            "sealed manifest Portfolio Daily range is invalid",
        )
    if end != manifest.effective_as_of:
        raise PortfolioDailyCalculationFailure(
            "input_effective_as_of_mismatch",
            "manifest config and calculation run effective as-of differ",
        )
    return start, end


def calculate_portfolio_daily_attempt(
    engine: Engine,
    *,
    lease: ActiveJobLease,
) -> PortfolioDailyFinancialOutputs:
    """Consume only a verified sealed manifest, then calculate outside a write UoW."""

    with Session(bind=engine, expire_on_commit=False) as session:
        with session.begin():
            manifest = load_sealed_portfolio_daily_manifest(
                session,
                run_id=lease.run_id,
            )
    if manifest.captured_generation != lease.captured_generation:
        raise PortfolioDailyCalculationFailure(
            "manifest_lease_generation_mismatch",
            "sealed manifest generation differs from the claimed lease",
        )
    range_start, range_end = _manifest_range(manifest)
    built_events = build_ledger_events(manifest.dependencies)
    ledger = replay_ledger_series(
        built_events.events,
        start_date=range_start,
        end_date=range_end,
    )
    if ledger.status is not LedgerStatus.SUCCEEDED:
        reasons = ",".join(reason.value for reason in ledger.reason_codes)
        raise PortfolioDailyCalculationFailure(
            f"ledger_{ledger.status.value}",
            ledger.diagnostic or reasons or "ledger replay did not succeed",
        )
    valuation_book = build_daily_valuation_book(manifest)
    valuation = calculate_exact_portfolio_valuation(
        ledger,
        valuation_book,
        base_currency=str(
            manifest.dependencies.rows_by_table[
                "portfolio_daily_config_input"
            ][0]["base_currency"]
        ),
    )
    return build_portfolio_daily_financial_outputs(
        manifest,
        ledger,
        valuation,
    )


class _HeartbeatSupervisor:
    """Heartbeat worker liveness and the currently attached job lease."""

    def __init__(
        self,
        *,
        engine: Engine,
        registration: WorkerRegistration,
        interval: timedelta,
        lease_duration: timedelta,
    ) -> None:
        self._engine = engine
        self._registration = registration
        self._interval_seconds = interval.total_seconds()
        self._lease_duration = lease_duration
        self._stop = Event()
        self._lock = Lock()
        self._lease: ActiveJobLease | None = None
        self._lost_fences: set[tuple[UUID, int]] = set()
        self._thread = Thread(
            target=self._run,
            name="portfolio-daily-heartbeat",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def attach(self, lease: ActiveJobLease) -> None:
        with self._lock:
            self._lease = lease

    def detach(self, lease: ActiveJobLease) -> bool:
        with self._lock:
            if self._lease is not None and (
                self._lease.job_id,
                self._lease.fencing_token,
            ) == (lease.job_id, lease.fencing_token):
                self._lease = None
            return (lease.job_id, lease.fencing_token) in self._lost_fences

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._interval_seconds + 1.0))

    def _heartbeat(self) -> None:
        with self._lock:
            lease = self._lease
            try:
                with Session(bind=self._engine, expire_on_commit=False) as session:
                    with session.begin():
                        LifecycleRepository.heartbeat_worker(
                            session,
                            self._registration,
                        )
                        if lease is not None:
                            renewed = LifecycleRepository.heartbeat_job(
                                session,
                                lease,
                                lease_duration=self._lease_duration,
                            )
                            self._lease = renewed
            except StaleLease:
                if lease is not None:
                    self._lost_fences.add(
                        (lease.job_id, lease.fencing_token)
                    )
                    self._lease = None
            except Exception:
                LOGGER.exception("calculation worker heartbeat failed")

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self._heartbeat()


class PortfolioDailyWorker:
    def __init__(
        self,
        *,
        engine: Engine,
        settings: Settings,
        registration: WorkerRegistration,
        intent_requested_as_of: date | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.registration = registration
        self.intent_requested_as_of = intent_requested_as_of
        self.lease_duration = timedelta(
            seconds=settings.calculation_worker_lease_seconds
        )
        self.heartbeat_interval = timedelta(
            seconds=settings.calculation_worker_heartbeat_seconds
        )
        self.retry_delay = timedelta(
            seconds=settings.calculation_job_retry_delay_seconds
        )
        self._supervisor = _HeartbeatSupervisor(
            engine=engine,
            registration=registration,
            interval=self.heartbeat_interval,
            lease_duration=self.lease_duration,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        with Session(bind=self.engine, expire_on_commit=False) as session:
            with session.begin():
                LifecycleRepository.register_worker(session, self.registration)
        self._supervisor.start()
        self._started = True

    def close(self) -> None:
        if self._started:
            self._supervisor.stop()
            self._started = False

    def _recover_publication(self) -> PublicationRecoveryOutcome:
        last_error: Exception | None = None
        for _ in range(3):
            try:
                with self.engine.connect().execution_options(
                    isolation_level="SERIALIZABLE"
                ) as connection:
                    with Session(bind=connection, expire_on_commit=False) as session:
                        with session.begin():
                            return recover_next_portfolio_daily_publication(
                                session
                            )
            except LifecycleRepositoryError as exc:
                if exc.code is not LifecycleErrorCode.PUBLICATION_CAS_CONFLICT:
                    raise
                last_error = exc
            except DBAPIError as exc:
                if not _retryable_database_error(exc):
                    raise
                last_error = exc
        raise PortfolioDailyWorkerError(
            "publication recovery exhausted its serialization retries"
        ) from last_error

    def _claim(self) -> ActiveJobLease | None:
        with Session(bind=self.engine, expire_on_commit=False) as session:
            with session.begin():
                return LifecycleRepository.claim_next_job(
                    session,
                    lease_owner=self.registration.worker_id,
                    calculation_kinds=(CALCULATION_KIND,),
                    lease_duration=self.lease_duration,
                )

    def _complete(
        self,
        *,
        lease: ActiveJobLease,
        financial_outputs: PortfolioDailyFinancialOutputs,
    ) -> str:
        with Session(bind=self.engine, expire_on_commit=False) as session:
            with session.begin():
                renewed = LifecycleRepository.heartbeat_job(
                    session,
                    lease,
                    lease_duration=self.lease_duration,
                )
                portfolio_id = _output_portfolio_id(financial_outputs)
                run_portfolio_id = session.scalar(
                    select(CalculationRun.scope_id).where(
                        CalculationRun.run_id == renewed.run_id,
                        CalculationRun.calculation_kind == CALCULATION_KIND,
                        CalculationRun.scope_kind == SCOPE_KIND,
                        CalculationRun.status == CalculationRunStatus.RUNNING,
                        CalculationRun.captured_generation
                        == renewed.captured_generation,
                    )
                )
                if not isinstance(run_portfolio_id, str):
                    raise PortfolioDailyCalculationFailure(
                        "active_run_scope_unavailable",
                        "the fenced running Portfolio Daily scope is unavailable",
                    )
                if portfolio_id != run_portfolio_id:
                    raise PortfolioDailyCalculationFailure(
                        "output_portfolio_scope_mismatch",
                        "Portfolio Daily output portfolio differs from its run scope",
                    )
                calculated_at = session.scalar(select(func.clock_timestamp()))
                if (
                    not isinstance(calculated_at, datetime)
                    or calculated_at.tzinfo is None
                    or calculated_at.utcoffset() is None
                ):
                    raise PortfolioDailyCalculationFailure(
                        "database_clock_invalid",
                        "PostgreSQL clock_timestamp() did not return an aware datetime",
                    )
                prepared = prepare_portfolio_daily_output_attempt(
                    lease=renewed,
                    portfolio_id=portfolio_id,
                    calculated_at=calculated_at,
                    financial_outputs=financial_outputs,
                )
                prior_hashes = set(
                    session.scalars(
                        select(
                            portfolio_daily_run_output.c.canonical_output_hash
                        ).where(
                            portfolio_daily_run_output.c.run_id == lease.run_id,
                            portfolio_daily_run_output.c.output_fencing_token
                            != lease.fencing_token,
                        )
                    )
                )
                prior_hashes.discard(None)
                if prior_hashes and prior_hashes != {
                    prepared.canonical_output_hash
                }:
                    raise PortfolioDailyCalculationFailure(
                        "nondeterministic_output_hash",
                        "a prior attempt produced a different financial output hash",
                    )
                insert_portfolio_daily_output_attempt(
                    session,
                    prepared=prepared,
                )
                LifecycleRepository.succeed_job(session, renewed)
                return prepared.canonical_output_hash

    def _resolve_failure(
        self,
        *,
        lease: ActiveJobLease,
        error: BaseException,
        failure: _AttemptFailure,
    ) -> WorkerCycleStatus:
        context: dict[str, object] = {
            "exception_type": type(error).__name__,
        }
        if isinstance(error, DBAPIError):
            sqlstate = _database_sqlstate(error)
            if sqlstate is not None:
                context["sqlstate"] = sqlstate
            constraint_name = getattr(
                getattr(error.orig, "diag", None),
                "constraint_name",
                None,
            )
            if isinstance(constraint_name, str) and constraint_name:
                context["constraint_name"] = constraint_name[:255]
        reason = FailureReason(
            failure.code[:64],
            diagnostic=_diagnostic(error),
            context=context,
        )
        try:
            with Session(bind=self.engine, expire_on_commit=False) as session:
                with session.begin():
                    if not failure.deterministic and lease_has_retry_remaining(
                        session,
                        lease,
                    ):
                        LifecycleRepository.retry_job(
                            session,
                            lease,
                            reason,
                            retry_delay=self.retry_delay,
                        )
                        return WorkerCycleStatus.RETRY_SCHEDULED
                    LifecycleRepository.fail_active_job(session, lease, reason)
                    return WorkerCycleStatus.FAILED
        except StaleLease:
            return WorkerCycleStatus.STALE

    def run_once(self) -> WorkerCycleOutcome:
        if not self._started:
            raise PortfolioDailyWorkerError("worker must be started before polling")
        publication = self._recover_publication()
        dispatch = dispatch_next_portfolio_daily_intent(
            self.engine,
            settings=self.settings,
            requested_by=self.registration.worker_id,
            requested_as_of=self.intent_requested_as_of,
        )
        with Session(bind=self.engine, expire_on_commit=False) as session:
            with session.begin():
                supersede_next_stale_generation_run(
                    session,
                    worker_id=self.registration.worker_id,
                )
                exhausted = LifecycleRepository.lock_expired_job_for_failure(
                    session,
                    calculation_kinds=(CALCULATION_KIND,),
                )
                if exhausted is not None:
                    LifecycleRepository.fail_expired_job(
                        session,
                        exhausted,
                        FailureReason(
                            "attempts_exhausted",
                            diagnostic="job lease expired after its final attempt",
                        ),
                    )
        lease = self._claim()
        if lease is None:
            return WorkerCycleOutcome(
                WorkerCycleStatus.IDLE,
                dispatch=dispatch,
                publication=publication,
            )

        self._supervisor.attach(lease)
        try:
            financial_outputs = calculate_portfolio_daily_attempt(
                self.engine,
                lease=lease,
            )
        except Exception as exc:
            failure = _classify_attempt_failure(exc)
            lost = self._supervisor.detach(lease)
            status = (
                WorkerCycleStatus.STALE
                if lost
                else self._resolve_failure(
                    lease=lease,
                    error=exc,
                    failure=failure,
                )
            )
            return WorkerCycleOutcome(
                status,
                dispatch=dispatch,
                publication=publication,
                run_id=lease.run_id,
                reason_code=failure.code,
            )

        if self._supervisor.detach(lease):
            return WorkerCycleOutcome(
                WorkerCycleStatus.STALE,
                dispatch=dispatch,
                publication=publication,
                run_id=lease.run_id,
                reason_code="lease_lost",
            )
        try:
            output_hash = self._complete(
                lease=lease,
                financial_outputs=financial_outputs,
            )
        except StaleLease:
            return WorkerCycleOutcome(
                WorkerCycleStatus.STALE,
                dispatch=dispatch,
                publication=publication,
                run_id=lease.run_id,
                reason_code="lease_lost",
            )
        except Exception as exc:
            failure = _classify_attempt_failure(exc)
            status = self._resolve_failure(
                lease=lease,
                error=exc,
                failure=failure,
            )
            return WorkerCycleOutcome(
                status,
                dispatch=dispatch,
                publication=publication,
                run_id=lease.run_id,
                reason_code=failure.code,
            )

        # Publication is deliberately a second transaction.  If the process
        # dies here, the next cycle recovers this succeeded immutable output.
        published = self._recover_publication()
        return WorkerCycleOutcome(
            WorkerCycleStatus.SUCCEEDED,
            dispatch=dispatch,
            publication=published,
            run_id=lease.run_id,
            canonical_output_hash=output_hash,
        )

    def run_forever(self, *, stop: Event) -> None:
        poll_seconds = self.settings.calculation_worker_poll_milliseconds / 1000
        next_log_at = monotonic()
        while not stop.is_set():
            try:
                outcome = self.run_once()
                if outcome.status is not WorkerCycleStatus.IDLE:
                    LOGGER.info(
                        "portfolio daily worker cycle status=%s run_id=%s",
                        outcome.status.value,
                        outcome.run_id,
                    )
            except Exception:
                LOGGER.exception("portfolio daily worker cycle failed")
                if monotonic() >= next_log_at:
                    next_log_at = monotonic() + 60
            stop.wait(poll_seconds)


def _registration(worker_id_prefix: str | None = None) -> WorkerRegistration:
    instance_id = uuid4()
    host = socket.gethostname().strip() or "unknown-host"
    prefix = (worker_id_prefix or f"{host}:{os.getpid()}").strip()
    if not prefix:
        prefix = f"{host}:{os.getpid()}"
    suffix = f":{instance_id.hex[:12]}"
    prefix_limit = 255 - len(suffix)
    resolved_worker_id = f"{prefix[:prefix_limit]}{suffix}"
    return WorkerRegistration(
        worker_id=resolved_worker_id,
        instance_id=instance_id,
        worker_version=WORKER_VERSION,
        supported_calculation_kinds=(CALCULATION_KIND,),
        metadata={"host": host, "pid": os.getpid()},
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Portfolio Daily worker")
    parser.add_argument(
        "--worker-id-prefix",
        help="stable operator label; a unique process-instance suffix is always added",
    )
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _parse_args()
    stop = Event()

    def request_stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    worker = PortfolioDailyWorker(
        engine=get_engine(),
        settings=get_settings(),
        registration=_registration(args.worker_id_prefix),
    )
    worker.start()
    try:
        if args.once:
            worker.run_once()
        else:
            worker.run_forever(stop=stop)
    finally:
        worker.close()
    return 0


__all__ = [
    "PortfolioDailyCalculationFailure",
    "PortfolioDailyWorker",
    "PortfolioDailyWorkerError",
    "WorkerCycleOutcome",
    "WorkerCycleStatus",
    "calculate_portfolio_daily_attempt",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
