from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from portfolio_app.db.models import PortfolioRecordModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.daily_snapshots import (
    mark_portfolio_daily_snapshots_stale,
    refresh_portfolio_daily_snapshots,
)


EventSink = Callable[[dict[str, object]], None]
RebuildOne = Callable[[str, date], dict[str, object]]


class TargetSelectionError(ValueError):
    def __init__(self, missing_ids: Sequence[str]) -> None:
        self.missing_ids = tuple(missing_ids)
        super().__init__("One or more requested portfolios do not exist.")


class EmptyTargetSelectionError(ValueError):
    pass


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: invalid arguments; use --help\n")


@dataclass(frozen=True)
class RebuildFailure:
    portfolio_id: str
    error_type: str


@dataclass(frozen=True)
class RebuildSummary:
    requested_count: int
    attempted_count: int
    completed_count: int
    failed_count: int
    stopped_early: bool
    failures: tuple[RebuildFailure, ...]

    @property
    def exit_code(self) -> int:
        return 1 if self.failed_count else 0

    def as_event(self) -> dict[str, object]:
        return {
            "event": "rebuild_summary",
            "scope": "portfolio_daily_snapshots",
            **asdict(self),
        }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = SafeArgumentParser(
        description=(
            "Rebuild materialized Portfolio daily snapshots using the configured "
            "Portfolio database. No database URL is accepted by this command."
        )
    )
    parser.add_argument(
        "--as-of-date",
        required=True,
        type=date.fromisoformat,
        help="Required calculation endpoint in YYYY-MM-DD format.",
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--portfolio-id",
        action="append",
        default=[],
        help="Portfolio ID to rebuild; repeat to select more than one.",
    )
    scope.add_argument(
        "--all",
        action="store_true",
        help="Explicitly rebuild every portfolio.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue after a target fails. The command still exits non-zero.",
    )
    return parser.parse_args(argv)


def _normalized_ids(values: Sequence[str]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def select_target_ids(
    *,
    available_ids: Sequence[str],
    requested_ids: Sequence[str],
    rebuild_all: bool,
) -> list[str]:
    available = _normalized_ids(available_ids)
    selected = available if rebuild_all else _normalized_ids(requested_ids)
    if not selected:
        raise EmptyTargetSelectionError("No portfolios were selected for rebuild.")
    missing = sorted(set(selected).difference(available))
    if missing:
        raise TargetSelectionError(missing)
    return selected


def load_target_ids(*, requested_ids: Sequence[str], rebuild_all: bool) -> list[str]:
    session_factory = get_session_factory()
    with session_factory() as session:
        available_ids = list(
            session.scalars(
                select(PortfolioRecordModel.portfolio_id).order_by(
                    PortfolioRecordModel.portfolio_id
                )
            ).all()
        )
    return select_target_ids(
        available_ids=available_ids,
        requested_ids=requested_ids,
        rebuild_all=rebuild_all,
    )


def rebuild_one_portfolio(portfolio_id: str, as_of_date: date) -> dict[str, object]:
    # date.min guarantees that no prior materialized row can be used as an
    # incremental seed. This is a full derived-state rebuild, not a refresh.
    mark_portfolio_daily_snapshots_stale(portfolio_id, dirty_from=date.min)
    result = refresh_portfolio_daily_snapshots(portfolio_id, end_date=as_of_date)
    if result is None:
        raise LookupError(f"Portfolio disappeared during rebuild: {portfolio_id}")

    snapshot_count = int(result.get("snapshot_count") or 0)
    refreshed_to = result.get("refreshed_to")
    if isinstance(refreshed_to, str):
        refreshed_to = date.fromisoformat(refreshed_to)
    if snapshot_count > 0 and refreshed_to != as_of_date:
        raise RuntimeError(
            f"Portfolio snapshot endpoint mismatch for {portfolio_id}: "
            f"expected {as_of_date.isoformat()}"
        )
    return {
        "snapshot_count": snapshot_count,
        "refreshed_from": result.get("refreshed_from"),
        "refreshed_to": refreshed_to,
        "recalculated_from": result.get("recalculated_from"),
    }


def run_rebuild(
    *,
    portfolio_ids: Sequence[str],
    as_of_date: date,
    continue_on_error: bool,
    rebuild_one: RebuildOne,
    emit: EventSink,
) -> RebuildSummary:
    targets = _normalized_ids(portfolio_ids)
    attempted_count = 0
    completed_count = 0
    failures: list[RebuildFailure] = []

    emit(
        {
            "event": "rebuild_started",
            "scope": "portfolio_daily_snapshots",
            "as_of_date": as_of_date.isoformat(),
            "target_count": len(targets),
            "fail_fast": not continue_on_error,
        }
    )
    for index, portfolio_id in enumerate(targets, start=1):
        attempted_count += 1
        emit(
            {
                "event": "target_started",
                "scope": "portfolio_daily_snapshots",
                "portfolio_id": portfolio_id,
                "position": index,
                "target_count": len(targets),
            }
        )
        try:
            result = rebuild_one(portfolio_id, as_of_date)
        except Exception as error:  # noqa: BLE001 - each failure is an operational result
            failure = RebuildFailure(
                portfolio_id=portfolio_id,
                error_type=type(error).__name__,
            )
            failures.append(failure)
            # Deliberately omit exception text: driver errors can contain a
            # credential-bearing database URL. Target and type are sufficient
            # for the auditable summary; service state retains calculation errors.
            emit(
                {
                    "event": "target_failed",
                    "scope": "portfolio_daily_snapshots",
                    **asdict(failure),
                }
            )
            if not continue_on_error:
                break
            continue

        completed_count += 1
        emit(
            {
                "event": "target_completed",
                "scope": "portfolio_daily_snapshots",
                "portfolio_id": portfolio_id,
                **result,
            }
        )

    summary = RebuildSummary(
        requested_count=len(targets),
        attempted_count=attempted_count,
        completed_count=completed_count,
        failed_count=len(failures),
        stopped_early=attempted_count < len(targets),
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
        portfolio_ids = load_target_ids(
            requested_ids=args.portfolio_id,
            rebuild_all=args.all,
        )
    except TargetSelectionError as error:
        emit_json(
            {
                "event": "selection_failed",
                "scope": "portfolio_daily_snapshots",
                "error_type": type(error).__name__,
                "missing_portfolio_ids": list(error.missing_ids),
            }
        )
        return 2
    except Exception as error:  # noqa: BLE001 - never print credential-bearing text
        emit_json(
            {
                "event": "selection_failed",
                "scope": "portfolio_daily_snapshots",
                "error_type": type(error).__name__,
            }
        )
        return 2

    summary = run_rebuild(
        portfolio_ids=portfolio_ids,
        as_of_date=args.as_of_date,
        continue_on_error=args.continue_on_error,
        rebuild_one=rebuild_one_portfolio,
        emit=emit_json,
    )
    return summary.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
