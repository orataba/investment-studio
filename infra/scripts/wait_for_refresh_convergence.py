#!/usr/bin/env python3
"""Wait for market-data refresh side effects to become durably converged."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import psycopg
from psycopg.rows import dict_row


DEFAULT_DATABASE_URL = (
    "postgresql://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops"
)
DEFAULT_TIMEOUT_SECONDS = 900.0
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_STABLE_SAMPLES = 2

ConvergencePhase = Literal["targeted", "final"]
ConvergenceStatus = Literal["waiting", "converged", "failed", "timed_out"]


CONVERGENCE_SNAPSHOT_QUERY = """
    WITH portfolio_state AS (
        SELECT
            portfolio.portfolio_id,
            generation.generation,
            run.requested_as_of,
            run.effective_as_of,
            (
                generation.scope_id IS NOT NULL
                AND current.publication_id IS NOT NULL
                AND publication.publication_id IS NOT NULL
                AND run.run_id IS NOT NULL
                AND output.run_id IS NOT NULL
                AND publication.calculation_kind = 'portfolio_daily'
                AND publication.scope_kind = 'portfolio'
                AND publication.scope_id = portfolio.portfolio_id
                AND run.calculation_kind = publication.calculation_kind
                AND run.scope_kind = publication.scope_kind
                AND run.scope_id = publication.scope_id
                AND run.manifest_id = publication.manifest_id
                AND run.status = 'published'
                AND run.captured_generation = generation.generation
                AND run.published_output_hash
                    IS NOT DISTINCT FROM publication.canonical_output_hash
                AND output.canonical_output_hash
                    IS NOT DISTINCT FROM publication.canonical_output_hash
                AND publication.output_schema_version
                    IS NOT DISTINCT FROM run.output_schema_version
                AND output.output_schema_version
                    IS NOT DISTINCT FROM run.output_schema_version
                AND output.output_fencing_token
                    IS NOT DISTINCT FROM publication.published_fencing_token
                AND output.closure_status = 'passed'
                AND output.ledger_balance_residual_exact = 0
                AND output.nav_bridge_residual_exact = 0
                AND output.pnl_residual_exact = 0
                AND output.twr_residual_exact = 0
                AND output.lot_residual_exact = 0
            ) AS publication_valid
        FROM portfolio.portfolio_record AS portfolio
        LEFT JOIN calculation_registry.calculation_scope_generation AS generation
          ON generation.calculation_kind = 'portfolio_daily'
         AND generation.scope_kind = 'portfolio'
         AND generation.scope_id = portfolio.portfolio_id
        LEFT JOIN calculation_registry.calculation_current_publication AS current
          ON current.calculation_kind = 'portfolio_daily'
         AND current.scope_kind = 'portfolio'
         AND current.scope_id = portfolio.portfolio_id
        LEFT JOIN calculation_registry.calculation_publication AS publication
          ON publication.publication_id = current.publication_id
         AND publication.calculation_kind = current.calculation_kind
         AND publication.scope_kind = current.scope_kind
         AND publication.scope_id = current.scope_id
        LEFT JOIN calculation_registry.calculation_run AS run
          ON run.run_id = publication.run_id
        LEFT JOIN portfolio.portfolio_daily_run_output AS output
          ON output.run_id = publication.run_id
         AND output.output_fencing_token = publication.published_fencing_token
         AND output.portfolio_id = portfolio.portfolio_id
    )
    SELECT
        clock_timestamp() AS observed_at,
        (
            SELECT count(*)
            FROM instrument_registry.market_data_outbox_event
            WHERE status = 'pending'
        ) AS outbox_pending_count,
        (
            SELECT count(*)
            FROM instrument_registry.market_data_outbox_event
            WHERE status = 'processing'
        ) AS outbox_processing_count,
        (
            SELECT count(*)
            FROM instrument_registry.market_data_outbox_event
            WHERE status = 'dead'
        ) AS outbox_dead_count,
        (
            SELECT count(*)
            FROM instrument_registry.market_data_outbox_event AS event
            WHERE event.status = 'delivered'
              AND NOT EXISTS (
                  SELECT 1
                  FROM watchlist.recalc_source_event_inbox AS inbox
                  WHERE inbox.trigger_ref_type = 'instrument_registry_outbox'
                    AND inbox.trigger_ref_id = event.event_id::text
                    AND inbox.instrument_id = event.instrument_id
                    AND inbox.job_type = 'all'
              )
        ) AS outbox_delivered_missing_inbox_count,
        (
            SELECT count(*)
            FROM watchlist.recalc_source_event_inbox
            WHERE consumed_at IS NULL
        ) AS watchlist_unconsumed_inbox_count,
        (
            SELECT count(*)
            FROM watchlist.recalc_invalidation_state
            WHERE requested_generation <> completed_generation
        ) AS watchlist_generation_gap_count,
        (
            SELECT count(*)
            FROM watchlist.recalc_job
            WHERE job_status IN ('queued', 'running')
        ) AS watchlist_active_job_count,
        (
            SELECT count(*)
            FROM watchlist.recalc_job AS job
            LEFT JOIN watchlist.recalc_invalidation_state AS invalidation
              ON invalidation.instrument_id = job.instrument_id
             AND invalidation.job_type = job.job_type
            WHERE job.job_status = 'failed'
              AND job.claimed_generation IS NOT NULL
              AND (
                  invalidation.instrument_id IS NULL
                  OR job.claimed_generation > invalidation.completed_generation
              )
        ) AS watchlist_unresolved_failed_job_count,
        (
            SELECT count(*)
            FROM watchlist.recalc_invalidation_state AS invalidation
            WHERE invalidation.requested_generation
                    > invalidation.completed_generation
              AND NOT EXISTS (
                  SELECT 1
                  FROM watchlist.recalc_job AS job
                  WHERE job.instrument_id = invalidation.instrument_id
                    AND job.job_type = invalidation.job_type
                    AND (
                        (
                            job.job_status = 'queued'
                            AND job.attempt_count < job.max_attempts
                        )
                        OR job.job_status = 'running'
                    )
              )
        ) AS watchlist_unserviceable_invalidation_count,
        (SELECT count(*) FROM portfolio_state)
            AS portfolio_count,
        (
            SELECT count(*)
            FROM portfolio_state
            WHERE NOT publication_valid
        ) AS portfolio_publication_inconsistency_count,
        (
            SELECT count(*)
            FROM portfolio_state
            WHERE %(required_portfolio_as_of_date)s::date IS NOT NULL
              AND requested_as_of IS DISTINCT FROM
                    %(required_portfolio_as_of_date)s::date
        ) AS portfolio_requested_as_of_mismatch_count,
        (
            SELECT count(*)
            FROM portfolio_state
            WHERE %(required_portfolio_as_of_date)s::date IS NOT NULL
              AND effective_as_of IS DISTINCT FROM
                    %(required_portfolio_as_of_date)s::date
        ) AS portfolio_effective_as_of_mismatch_count,
        (
            SELECT count(*)
            FROM calculation_registry.calculation_run
            WHERE calculation_kind = 'portfolio_daily'
              AND scope_kind = 'portfolio'
              AND status IN ('capturing', 'queued', 'running', 'succeeded')
        ) AS portfolio_active_run_count,
        (
            SELECT count(*)
            FROM calculation_registry.calculation_recompute_intent AS intent
            JOIN portfolio_state AS state
              ON state.portfolio_id = intent.scope_id
             AND state.generation = intent.requested_generation
            WHERE intent.calculation_kind = 'portfolio_daily'
              AND intent.scope_kind = 'portfolio'
              AND intent.status = 'pending'
        ) AS portfolio_pending_current_intent_count,
        (
            SELECT count(*)
            FROM calculation_registry.calculation_recompute_intent AS intent
            JOIN portfolio_state AS state
              ON state.portfolio_id = intent.scope_id
             AND state.generation = intent.requested_generation
             AND NOT state.publication_valid
            WHERE intent.calculation_kind = 'portfolio_daily'
              AND intent.scope_kind = 'portfolio'
              AND intent.status = 'failed'
        ) AS portfolio_current_failed_intent_count,
        (
            SELECT count(*)
            FROM calculation_registry.calculation_run AS run
            JOIN portfolio_state AS state
              ON state.portfolio_id = run.scope_id
             AND state.generation = run.captured_generation
             AND NOT state.publication_valid
            WHERE run.calculation_kind = 'portfolio_daily'
              AND run.scope_kind = 'portfolio'
              AND run.status = 'failed'
        ) AS portfolio_current_failed_run_count,
        (
            SELECT count(*)
            FROM calculation_registry.calculation_job AS job
            JOIN calculation_registry.calculation_run AS run
              ON run.run_id = job.run_id
            JOIN portfolio_state AS state
              ON state.portfolio_id = run.scope_id
             AND state.generation = run.captured_generation
             AND NOT state.publication_valid
            WHERE run.calculation_kind = 'portfolio_daily'
              AND run.scope_kind = 'portfolio'
              AND job.status = 'failed'
        ) AS portfolio_current_failed_job_count
"""


COUNT_FIELDS = (
    "outbox_pending_count",
    "outbox_processing_count",
    "outbox_dead_count",
    "outbox_delivered_missing_inbox_count",
    "watchlist_unconsumed_inbox_count",
    "watchlist_generation_gap_count",
    "watchlist_active_job_count",
    "watchlist_unresolved_failed_job_count",
    "watchlist_unserviceable_invalidation_count",
    "portfolio_publication_inconsistency_count",
    "portfolio_requested_as_of_mismatch_count",
    "portfolio_effective_as_of_mismatch_count",
    "portfolio_active_run_count",
    "portfolio_pending_current_intent_count",
    "portfolio_current_failed_intent_count",
    "portfolio_current_failed_run_count",
    "portfolio_current_failed_job_count",
)

TERMINAL_FAILURE_FIELDS = (
    "outbox_dead_count",
    "watchlist_unresolved_failed_job_count",
    "watchlist_unserviceable_invalidation_count",
    "portfolio_current_failed_intent_count",
    "portfolio_current_failed_run_count",
    "portfolio_current_failed_job_count",
)


@dataclass(frozen=True, slots=True)
class ConvergenceSnapshot:
    observed_at: str
    outbox_pending_count: int = 0
    outbox_processing_count: int = 0
    outbox_dead_count: int = 0
    outbox_delivered_missing_inbox_count: int = 0
    watchlist_unconsumed_inbox_count: int = 0
    watchlist_generation_gap_count: int = 0
    watchlist_active_job_count: int = 0
    watchlist_unresolved_failed_job_count: int = 0
    watchlist_unserviceable_invalidation_count: int = 0
    portfolio_count: int = 0
    portfolio_publication_inconsistency_count: int = 0
    portfolio_requested_as_of_mismatch_count: int = 0
    portfolio_effective_as_of_mismatch_count: int = 0
    portfolio_active_run_count: int = 0
    portfolio_pending_current_intent_count: int = 0
    portfolio_current_failed_intent_count: int = 0
    portfolio_current_failed_run_count: int = 0
    portfolio_current_failed_job_count: int = 0

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> ConvergenceSnapshot:
        observed = row.get("observed_at")
        if isinstance(observed, datetime):
            observed_at = observed.astimezone(timezone.utc).isoformat()
        elif observed is None:
            raise RuntimeError("convergence snapshot did not include observed_at")
        else:
            observed_at = str(observed)
        values: dict[str, object] = {"observed_at": observed_at}
        for field_name in (*COUNT_FIELDS, "portfolio_count"):
            raw_value = row.get(field_name)
            if isinstance(raw_value, bool) or not isinstance(raw_value, int):
                raise RuntimeError(
                    f"convergence snapshot field {field_name!r} is not an integer"
                )
            if raw_value < 0:
                raise RuntimeError(
                    f"convergence snapshot field {field_name!r} is negative"
                )
            values[field_name] = raw_value
        return cls(**values)  # type: ignore[arg-type]

    @property
    def converged(self) -> bool:
        return all(getattr(self, field_name) == 0 for field_name in COUNT_FIELDS)

    @property
    def terminal_failure(self) -> bool:
        return any(
            getattr(self, field_name) > 0
            for field_name in TERMINAL_FAILURE_FIELDS
        )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ConvergenceResult:
    status: ConvergenceStatus
    phase: ConvergencePhase
    elapsed_seconds: float
    stable_samples: int
    required_stable_samples: int
    snapshot: ConvergenceSnapshot | None
    reason_code: str | None = None
    reason_context: Mapping[str, object] | None = None
    required_portfolio_as_of_date: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "converged"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "phase": self.phase,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "stable_samples": self.stable_samples,
            "required_stable_samples": self.required_stable_samples,
            "snapshot": None if self.snapshot is None else self.snapshot.as_dict(),
            "reason_code": self.reason_code,
            "reason_context": (
                None if self.reason_context is None else dict(self.reason_context)
            ),
            "required_portfolio_as_of_date": self.required_portfolio_as_of_date,
        }


def database_url_from_environment() -> str:
    raw_url = (
        os.getenv("PORTFOLIO_OPS_LOCAL_DATABASE_URL")
        or os.getenv("PORTFOLIO_OPS_PLATFORM_DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )
    return raw_url.replace("postgresql+psycopg://", "postgresql://", 1)


def read_convergence_snapshot(
    database_url: str,
    *,
    required_portfolio_as_of_date: date | None = None,
) -> ConvergenceSnapshot:
    """Read all convergence predicates from one short MVCC snapshot."""

    with psycopg.connect(
        database_url,
        autocommit=False,
        row_factory=dict_row,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute("SET LOCAL lock_timeout = '3s'")
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
            cursor.execute(
                CONVERGENCE_SNAPSHOT_QUERY,
                {
                    "required_portfolio_as_of_date": (
                        required_portfolio_as_of_date
                    )
                },
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("database returned no convergence snapshot")
            return ConvergenceSnapshot.from_row(row)


def wait_for_convergence(
    fetch_snapshot: Callable[[], ConvergenceSnapshot],
    *,
    phase: ConvergencePhase,
    timeout_seconds: float,
    poll_interval_seconds: float,
    stable_samples: int,
    required_portfolio_as_of_date: date | None = None,
    progress_callback: Callable[[ConvergenceResult], None] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> ConvergenceResult:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")
    if stable_samples < 1:
        raise ValueError("stable_samples must be positive")

    started_at = monotonic()
    deadline = started_at + timeout_seconds
    consecutive_successes = 0

    while True:
        snapshot = fetch_snapshot()
        elapsed = max(0.0, monotonic() - started_at)
        if snapshot.terminal_failure:
            result = ConvergenceResult(
                status="failed",
                phase=phase,
                elapsed_seconds=elapsed,
                stable_samples=0,
                required_stable_samples=stable_samples,
                snapshot=snapshot,
                reason_code="terminal_downstream_failure",
                reason_context={
                    field_name: getattr(snapshot, field_name)
                    for field_name in TERMINAL_FAILURE_FIELDS
                    if getattr(snapshot, field_name) > 0
                },
                required_portfolio_as_of_date=(
                    None
                    if required_portfolio_as_of_date is None
                    else required_portfolio_as_of_date.isoformat()
                ),
            )
            if progress_callback is not None:
                progress_callback(result)
            return result

        consecutive_successes = (
            consecutive_successes + 1 if snapshot.converged else 0
        )
        if consecutive_successes >= stable_samples:
            result = ConvergenceResult(
                status="converged",
                phase=phase,
                elapsed_seconds=elapsed,
                stable_samples=consecutive_successes,
                required_stable_samples=stable_samples,
                snapshot=snapshot,
                required_portfolio_as_of_date=(
                    None
                    if required_portfolio_as_of_date is None
                    else required_portfolio_as_of_date.isoformat()
                ),
            )
            if progress_callback is not None:
                progress_callback(result)
            return result

        now = monotonic()
        if now >= deadline:
            result = ConvergenceResult(
                status="timed_out",
                phase=phase,
                elapsed_seconds=max(0.0, now - started_at),
                stable_samples=consecutive_successes,
                required_stable_samples=stable_samples,
                snapshot=snapshot,
                reason_code="convergence_timeout",
                required_portfolio_as_of_date=(
                    None
                    if required_portfolio_as_of_date is None
                    else required_portfolio_as_of_date.isoformat()
                ),
            )
            if progress_callback is not None:
                progress_callback(result)
            return result

        waiting = ConvergenceResult(
            status="waiting",
            phase=phase,
            elapsed_seconds=elapsed,
            stable_samples=consecutive_successes,
            required_stable_samples=stable_samples,
            snapshot=snapshot,
            required_portfolio_as_of_date=(
                None
                if required_portfolio_as_of_date is None
                else required_portfolio_as_of_date.isoformat()
            ),
        )
        if progress_callback is not None:
            progress_callback(waiting)
        sleeper(min(poll_interval_seconds, max(0.0, deadline - now)))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_refresh_summary(
    summary_file: Path,
    result: ConvergenceResult,
) -> None:
    """Merge convergence state into the refresh summary using atomic replace."""

    try:
        raw_payload = json.loads(summary_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw_payload = {}
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "market-data refresh summary is not readable valid JSON"
        ) from error
    if not isinstance(raw_payload, dict):
        raise RuntimeError("market-data refresh summary must be a JSON object")

    payload: dict[str, object] = dict(raw_payload)
    if "ingestion_status" not in payload:
        current_status = payload.get("status")
        payload["ingestion_status"] = (
            current_status if isinstance(current_status, str) else "unknown"
        )
    convergence_payload = result.as_dict()
    convergence_payload["updated_at"] = _utc_now()
    payload["downstream_convergence"] = convergence_payload
    if result.status in {"failed", "timed_out"}:
        payload["status"] = "failed"

    summary_file.parent.mkdir(parents=True, exist_ok=True)
    existing_mode: int | None
    try:
        existing_mode = summary_file.stat().st_mode & 0o777
    except FileNotFoundError:
        existing_mode = None

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=summary_file.parent,
            prefix=f".{summary_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(payload, temporary, sort_keys=True, separators=(",", ":"))
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        if existing_mode is not None:
            os.chmod(temporary_name, existing_mode)
        os.replace(temporary_name, summary_file)
        temporary_name = None
        directory_fd = os.open(summary_file.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def _execution_failure_result(
    *,
    phase: ConvergencePhase,
    stable_samples: int,
    error: BaseException,
    required_portfolio_as_of_date: date | None = None,
) -> ConvergenceResult:
    context: dict[str, object] = {"error_type": type(error).__name__}
    sqlstate = getattr(error, "sqlstate", None)
    if isinstance(sqlstate, str) and sqlstate:
        context["sqlstate"] = sqlstate
    return ConvergenceResult(
        status="failed",
        phase=phase,
        elapsed_seconds=0.0,
        stable_samples=0,
        required_stable_samples=stable_samples,
        snapshot=None,
        reason_code="convergence_query_failed",
        reason_context=context,
        required_portfolio_as_of_date=(
            None
            if required_portfolio_as_of_date is None
            else required_portfolio_as_of_date.isoformat()
        ),
    )


def _canonical_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected canonical YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("expected canonical YYYY-MM-DD")
    return parsed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
    )
    parser.add_argument(
        "--stable-samples",
        type=int,
        default=DEFAULT_STABLE_SAMPLES,
    )
    parser.add_argument("--summary-file", required=True, type=Path)
    parser.add_argument(
        "--phase",
        required=True,
        choices=("targeted", "final"),
    )
    parser.add_argument(
        "--required-portfolio-as-of-date",
        type=_canonical_date,
        default=None,
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.poll_interval_seconds <= 0:
        parser.error("--poll-interval-seconds must be positive")
    if args.stable_samples < 1:
        parser.error("--stable-samples must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    database_url = database_url_from_environment()
    progress = lambda result: update_refresh_summary(args.summary_file, result)
    try:
        result = wait_for_convergence(
            lambda: read_convergence_snapshot(
                database_url,
                required_portfolio_as_of_date=(
                    args.required_portfolio_as_of_date
                ),
            ),
            phase=args.phase,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            stable_samples=args.stable_samples,
            required_portfolio_as_of_date=args.required_portfolio_as_of_date,
            progress_callback=progress,
        )
    except (psycopg.Error, RuntimeError, OSError) as error:
        result = _execution_failure_result(
            phase=args.phase,
            stable_samples=args.stable_samples,
            error=error,
            required_portfolio_as_of_date=args.required_portfolio_as_of_date,
        )
        try:
            update_refresh_summary(args.summary_file, result)
        except (RuntimeError, OSError):
            pass

    payload = result.as_dict()
    if args.as_json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        print(
            f"refresh convergence {result.status}: phase={result.phase} "
            f"elapsed={result.elapsed_seconds:.3f}s "
            f"stable={result.stable_samples}/{result.required_stable_samples}"
        )
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
