from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.session import get_session_factory
from watchlist_app.services.canonical_recalc import CanonicalRecalcService


EventSink = Callable[[dict[str, object]], None]
RebuildOne = Callable[[str, date, int], dict[str, object]]


class TargetSelectionError(ValueError):
    def __init__(self, missing_ids: Sequence[str]) -> None:
        self.missing_ids = tuple(missing_ids)
        super().__init__("One or more requested instruments do not exist.")


class EmptyTargetSelectionError(ValueError):
    pass


class SourceChangedDuringRebuildError(RuntimeError):
    pass


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: invalid arguments; use --help\n")


@dataclass(frozen=True)
class RebuildFailure:
    instrument_id: str
    round_number: int
    error_type: str


@dataclass(frozen=True)
class RebuildSummary:
    target_count: int
    rounds_requested: int
    attempted_count: int
    completed_attempt_count: int
    fully_processed_target_count: int
    cohort_convergence_scope: str
    cohort_convergence_pass_completed: bool
    failed_count: int
    stopped_early: bool
    failures: tuple[RebuildFailure, ...]

    @property
    def exit_code(self) -> int:
        return 1 if self.failed_count else 0

    def as_event(self) -> dict[str, object]:
        return {
            "event": "rebuild_summary",
            "scope": "watchlist_derived_state",
            **asdict(self),
        }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = SafeArgumentParser(
        description=(
            "Rebuild Watchlist snapshots and read models using the configured "
            "Watchlist database. No database URL is accepted by this command."
        )
    )
    parser.add_argument(
        "--valuation-date",
        required=True,
        type=date.fromisoformat,
        help="Required canonical quote cutoff in YYYY-MM-DD format.",
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--instrument-id",
        action="append",
        default=[],
        help="Instrument ID to rebuild; repeat to select more than one.",
    )
    scope.add_argument(
        "--all-active",
        action="store_true",
        help="Explicitly rebuild every active Watchlist instrument.",
    )
    parser.add_argument(
        "--rounds",
        choices=(1, 2),
        default=2,
        type=int,
        help=(
            "Number of fixed-order passes. The default of 2 is required for "
            "peer-cohort fingerprint convergence after a broad rebuild."
        ),
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue after an attempt fails. The command still exits non-zero.",
    )
    return parser.parse_args(argv)


def _normalized_ids(values: Sequence[str]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def select_target_ids(
    *,
    available_ids: Sequence[str],
    active_ids: Sequence[str],
    requested_ids: Sequence[str],
    rebuild_all_active: bool,
) -> list[str]:
    available = _normalized_ids(available_ids)
    selected = _normalized_ids(active_ids) if rebuild_all_active else _normalized_ids(requested_ids)
    if not selected:
        raise EmptyTargetSelectionError("No instruments were selected for rebuild.")
    missing = sorted(set(selected).difference(available))
    if missing:
        raise TargetSelectionError(missing)
    return selected


def load_target_ids(
    *,
    requested_ids: Sequence[str],
    rebuild_all_active: bool,
) -> list[str]:
    session_factory = get_session_factory()
    with session_factory() as session:
        rows = list(
            session.execute(
                select(InstrumentDetail.instrument_id, InstrumentDetail.is_active).order_by(
                    InstrumentDetail.instrument_id
                )
            ).all()
        )
    return select_target_ids(
        available_ids=[str(row.instrument_id) for row in rows],
        active_ids=[str(row.instrument_id) for row in rows if bool(row.is_active)],
        requested_ids=requested_ids,
        rebuild_all_active=rebuild_all_active,
    )


def rebuild_one_instrument(
    instrument_id: str,
    valuation_date: date,
    round_number: int,
) -> dict[str, object]:
    session_factory = get_session_factory()
    service = CanonicalRecalcService()
    with session_factory() as session:
        execution = service.execute_recalc(
            session,
            instrument_id=instrument_id,
            job_type="all",
            trigger_type="maintenance_cli",
            trigger_ref_type="derived_state_rebuild_valuation_date",
            trigger_ref_id=valuation_date.isoformat(),
            valuation_date=valuation_date,
            commit=True,
        )
    result = execution.get("result")
    result_payload = result if isinstance(result, dict) else {}
    job_status = str(execution.get("job_status") or "")
    if job_status != "completed":
        raise RuntimeError("Watchlist recalc did not complete.")
    if bool(result_payload.get("source_changed_during_recalc")):
        raise SourceChangedDuringRebuildError(
            "Canonical source changed during the rebuild attempt."
        )
    return {
        "round_number": round_number,
        "recalc_job_id": str(execution.get("recalc_job_id") or ""),
        "job_status": job_status,
        "source_changed_during_recalc": False,
    }


def run_rebuild(
    *,
    instrument_ids: Sequence[str],
    valuation_date: date,
    rounds: int,
    full_active_universe: bool,
    continue_on_error: bool,
    rebuild_one: RebuildOne,
    emit: EventSink,
) -> RebuildSummary:
    if rounds not in {1, 2}:
        raise ValueError("rounds must be 1 or 2")
    targets = _normalized_ids(instrument_ids)
    attempted_count = 0
    completed_attempt_count = 0
    failures: list[RebuildFailure] = []
    completed_rounds: dict[str, set[int]] = defaultdict(set)
    stopped_early = False

    emit(
        {
            "event": "rebuild_started",
            "scope": "watchlist_derived_state",
            "valuation_date": valuation_date.isoformat(),
            "target_count": len(targets),
            "rounds": rounds,
            "fail_fast": not continue_on_error,
            "cohort_convergence_scope": (
                "full_active_universe"
                if full_active_universe
                else "selected_instruments_only"
            ),
        }
    )
    for round_number in range(1, rounds + 1):
        emit(
            {
                "event": "round_started",
                "scope": "watchlist_derived_state",
                "round_number": round_number,
                "rounds": rounds,
                "target_count": len(targets),
            }
        )
        for index, instrument_id in enumerate(targets, start=1):
            attempted_count += 1
            emit(
                {
                    "event": "target_started",
                    "scope": "watchlist_derived_state",
                    "instrument_id": instrument_id,
                    "round_number": round_number,
                    "rounds": rounds,
                    "position": index,
                    "target_count": len(targets),
                }
            )
            try:
                result = rebuild_one(instrument_id, valuation_date, round_number)
            except Exception as error:  # noqa: BLE001 - each failure is an operational result
                failure = RebuildFailure(
                    instrument_id=instrument_id,
                    round_number=round_number,
                    error_type=type(error).__name__,
                )
                failures.append(failure)
                # Never print exception text: database driver failures can embed
                # a credential-bearing URL. Persisted recalc jobs retain details.
                emit(
                    {
                        "event": "target_failed",
                        "scope": "watchlist_derived_state",
                        **asdict(failure),
                    }
                )
                if not continue_on_error:
                    stopped_early = True
                    break
                continue

            completed_attempt_count += 1
            completed_rounds[instrument_id].add(round_number)
            emit(
                {
                    "event": "target_completed",
                    "scope": "watchlist_derived_state",
                    "instrument_id": instrument_id,
                    **result,
                }
            )
        if stopped_early:
            break
        emit(
            {
                "event": "round_completed",
                "scope": "watchlist_derived_state",
                "round_number": round_number,
                "rounds": rounds,
            }
        )

    expected_rounds = set(range(1, rounds + 1))
    fully_processed_target_count = sum(
        completed_rounds[instrument_id] == expected_rounds for instrument_id in targets
    )
    cohort_convergence_scope = (
        "full_active_universe"
        if full_active_universe
        else "selected_instruments_only"
    )
    summary = RebuildSummary(
        target_count=len(targets),
        rounds_requested=rounds,
        attempted_count=attempted_count,
        completed_attempt_count=completed_attempt_count,
        fully_processed_target_count=fully_processed_target_count,
        cohort_convergence_scope=cohort_convergence_scope,
        cohort_convergence_pass_completed=(
            full_active_universe
            and rounds == 2
            and bool(targets)
            and fully_processed_target_count == len(targets)
            and not failures
        ),
        failed_count=len(failures),
        stopped_early=stopped_early,
        failures=tuple(failures),
    )
    emit(summary.as_event())
    return summary


def _json_default(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def emit_json(event: dict[str, object]) -> None:
    print(json.dumps(event, default=_json_default, sort_keys=True), flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        instrument_ids = load_target_ids(
            requested_ids=args.instrument_id,
            rebuild_all_active=args.all_active,
        )
    except TargetSelectionError as error:
        emit_json(
            {
                "event": "selection_failed",
                "scope": "watchlist_derived_state",
                "error_type": type(error).__name__,
                "missing_instrument_ids": list(error.missing_ids),
            }
        )
        return 2
    except Exception as error:  # noqa: BLE001 - never print credential-bearing text
        emit_json(
            {
                "event": "selection_failed",
                "scope": "watchlist_derived_state",
                "error_type": type(error).__name__,
            }
        )
        return 2

    summary = run_rebuild(
        instrument_ids=instrument_ids,
        valuation_date=args.valuation_date,
        rounds=args.rounds,
        full_active_universe=args.all_active,
        continue_on_error=args.continue_on_error,
        rebuild_one=rebuild_one_instrument,
        emit=emit_json,
    )
    return summary.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
