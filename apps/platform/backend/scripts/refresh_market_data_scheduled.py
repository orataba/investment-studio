from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import IO, Iterable, Iterator


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from platform_app.services.downstream_notifications import (  # noqa: E402
    DownstreamRequestFailure,
    DownstreamRefreshError,
    DownstreamRefreshResult,
    notify_market_data_downstream_refresh,
)
from platform_app.services.market_data_ops import (  # noqa: E402
    refresh_market_data_batch,
    refresh_market_data_with_timeout,
)


LOGGER = logging.getLogger("portfolio_ops.market_data_refresh")
UPDATED_STATUSES = {"imported", "refreshed"}
FAILED_STATUSES = {"failed", "blocked"}
RETRYABLE_STATUSES = {"failed"}
ALREADY_RUNNING_EXIT_CODE = 75


class RefreshAlreadyRunningError(RuntimeError):
    pass


@contextmanager
def _exclusive_refresh_lock(lock_file: Path) -> Iterator[IO[str]]:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_file.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RefreshAlreadyRunningError(
                f"Another market data refresh owns lock {lock_file}."
            ) from error
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "acquired_at": datetime.now().astimezone().isoformat(),
                },
                sort_keys=True,
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
        yield handle
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _write_json_atomic(target: Path, payload: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, target)
        directory_descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary_path.unlink(missing_ok=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Portfolio Operations market data without a browser session.")
    parser.add_argument(
        "--channel",
        choices=("all", "email", "tushare"),
        default="all",
        help="Data channel to refresh. all runs tushare first, then email.",
    )
    parser.add_argument("--updated-by", default="scheduler", help="Audit label for refresh_status.")
    parser.add_argument("--full-history", action="store_true", help="Request full history instead of incremental refresh.")
    parser.add_argument("--include-inactive", action="store_true", help="Refresh inactive instruments as well.")
    parser.add_argument(
        "--no-downstream-refresh",
        action="store_true",
        help="Do not notify Watchlist/Portfolio after market data changes.",
    )
    parser.add_argument(
        "--require-downstream-success",
        action="store_true",
        help=(
            "Require the Portfolio refresh response and Watchlist recalc enqueue "
            "acknowledgement; this does not wait for Watchlist workers to finish."
        ),
    )
    parser.add_argument(
        "--downstream-timeout-seconds",
        type=float,
        default=900.0,
        help="Timeout used for the synchronous downstream Portfolio refresh.",
    )
    parser.add_argument(
        "--watchlist-downstream-timeout-seconds",
        type=float,
        default=15.0,
        help="Per-request timeout used while enqueueing downstream Watchlist recalculations.",
    )
    parser.add_argument(
        "--fail-on-item-failure",
        action="store_true",
        help="Exit non-zero when any item ends in failed or blocked status.",
    )
    parser.add_argument(
        "--retry-failed-attempts",
        type=int,
        default=2,
        help="Retry failed item refreshes this many times before the final summary.",
    )
    parser.add_argument(
        "--instrument-id",
        dest="instrument_ids",
        action="append",
        default=[],
        help="Refresh only these instrument ids. Repeat or use comma-separated values.",
    )
    parser.add_argument("--json", action="store_true", help="Emit a JSON summary in addition to logs.")
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=PROJECT_ROOT / "var" / "market-data-refresh.lock",
        help="fcntl lock file used to reject overlapping scheduled runs.",
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=PROJECT_ROOT / "var" / "market-data-refresh-summary.json",
        help="Latest run summary, atomically replaced after each acquired run.",
    )
    return parser.parse_args()


def _channels(selected_channel: str) -> list[str]:
    if selected_channel == "all":
        return ["tushare", "email"]
    return [selected_channel]


def _status_counts(results: Iterable[dict[str, object]]) -> dict[str, int]:
    return dict(Counter(str(item.get("status") or "unknown") for item in results))


def _updated_instrument_ids(results: Iterable[dict[str, object]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for item in results:
        status = str(item.get("status") or "")
        instrument_id = str(item.get("instrument_id") or "").strip()
        if status in UPDATED_STATUSES and instrument_id and instrument_id not in seen:
            seen.add(instrument_id)
            ids.append(instrument_id)
    return ids


def _requested_instrument_ids(raw_values: Iterable[str]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        for item in str(raw_value or "").split(","):
            instrument_id = item.strip()
            if instrument_id and instrument_id not in seen:
                seen.add(instrument_id)
                ids.append(instrument_id)
    return ids


def _failed_results(results: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    return [
        item
        for item in results
        if str(item.get("status") or "") in FAILED_STATUSES
    ]


def _result_from_record(record: dict[str, object]) -> dict[str, object]:
    source_settings = dict(record.get("source_settings", {}))
    refresh_status = dict(record.get("refresh_status", {}))
    return {
        "instrument_id": record["instrument_id"],
        "instrument_name": record["instrument_name"],
        "instrument_type": record["instrument_type"],
        "source_mode": source_settings.get("source_mode") or "manual",
        "source_api_profile": source_settings.get("source_api_profile") or "",
        "status": refresh_status.get("status") or "idle",
        "message": refresh_status.get("message") or "",
    }


def _refresh_selected_instruments(
    *,
    channel: str,
    instrument_ids: list[str],
    updated_by: str | None,
    full_history: bool,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for index, instrument_id in enumerate(instrument_ids, start=1):
        LOGGER.info(
            "refreshing selected item channel=%s index=%s/%s instrument_id=%s",
            channel,
            index,
            len(instrument_ids),
            instrument_id,
        )
        record = refresh_market_data_with_timeout(
            instrument_id=instrument_id,
            updated_by=updated_by,
            full_history=full_history,
            source=channel,
        )
        if record is None:
            result = {
                "instrument_id": instrument_id,
                "instrument_name": "",
                "instrument_type": "",
                "source_mode": "",
                "source_api_profile": "",
                "status": "failed",
                "message": "Instrument not found.",
            }
        else:
            result = _result_from_record(record)
        results.append(result)
        LOGGER.info(
            "selected item result channel=%s index=%s/%s instrument_id=%s status=%s",
            channel,
            index,
            len(instrument_ids),
            instrument_id,
            result["status"],
        )
    return results


def _retry_failed_results(
    *,
    channel: str,
    results: list[dict[str, object]],
    updated_by: str | None,
    full_history: bool,
    retry_attempts: int,
) -> list[dict[str, object]]:
    if retry_attempts <= 0:
        return results

    by_instrument_id = {
        str(item.get("instrument_id") or ""): item
        for item in results
        if str(item.get("instrument_id") or "")
    }
    for attempt in range(1, retry_attempts + 1):
        retry_ids = [
            instrument_id
            for instrument_id, item in by_instrument_id.items()
            if str(item.get("status") or "") in RETRYABLE_STATUSES
        ]
        if not retry_ids:
            break
        LOGGER.info("retrying failed items channel=%s attempt=%s count=%s", channel, attempt, len(retry_ids))
        for instrument_id in retry_ids:
            record = refresh_market_data_with_timeout(
                instrument_id=instrument_id,
                updated_by=updated_by,
                full_history=full_history,
                source=channel,
            )
            if record is None:
                continue
            result = _result_from_record(record)
            by_instrument_id[instrument_id] = result
            LOGGER.info(
                "retry result channel=%s attempt=%s instrument_id=%s status=%s",
                channel,
                attempt,
                instrument_id,
                result["status"],
            )
    return list(by_instrument_id.values())


def _run_refresh(
    args: argparse.Namespace,
    *,
    started_at: datetime | None = None,
) -> tuple[int, dict[str, object]]:
    started_at = started_at or datetime.now().astimezone()
    requested_instrument_ids = _requested_instrument_ids(args.instrument_ids)
    LOGGER.info(
        "scheduled market data refresh started channel=%s selected_count=%s",
        args.channel,
        len(requested_instrument_ids),
    )

    channel_summaries: list[dict[str, object]] = []
    all_results: list[dict[str, object]] = []
    all_updated_ids: list[str] = []
    seen_updated_ids: set[str] = set()
    channels = ["configured"] if requested_instrument_ids and args.channel == "all" else _channels(args.channel)
    for channel in channels:
        LOGGER.info("refreshing channel=%s", channel)
        if requested_instrument_ids:
            results = _refresh_selected_instruments(
                channel=channel,
                instrument_ids=requested_instrument_ids,
                updated_by=args.updated_by,
                full_history=args.full_history,
            )
            response = {
                "source": channel,
                "refreshed_count": sum(1 for item in results if item["status"] in UPDATED_STATUSES),
                "skipped_count": 0,
                "results": results,
            }
        else:
            response = refresh_market_data_batch(
                source=channel,
                updated_by=args.updated_by,
                full_history=args.full_history,
                include_inactive=args.include_inactive,
            )
        results = list(response.get("results", []))
        results = _retry_failed_results(
            channel=channel,
            results=results,
            updated_by=args.updated_by,
            full_history=args.full_history,
            retry_attempts=args.retry_failed_attempts,
        )
        all_results.extend(results)
        updated_ids = _updated_instrument_ids(results)
        for instrument_id in updated_ids:
            if instrument_id not in seen_updated_ids:
                seen_updated_ids.add(instrument_id)
                all_updated_ids.append(instrument_id)

        counts = _status_counts(results)
        final_refreshed_count = sum(
            1
            for item in results
            if str(item.get("status") or "") in UPDATED_STATUSES
        )
        channel_summaries.append(
            {
                "channel": channel,
                "refreshed_count": final_refreshed_count,
                "skipped_count": response.get("skipped_count", 0),
                "result_count": len(results),
                "status_counts": counts,
            }
        )
        LOGGER.info(
            "channel=%s refreshed=%s checked=%s skipped=%s statuses=%s",
            channel,
            final_refreshed_count,
            len(results),
            response.get("skipped_count", 0),
            counts,
        )

    failures = _failed_results(all_results)
    for item in failures:
        LOGGER.warning(
            "refresh item did not complete instrument_id=%s status=%s message=%s",
            item.get("instrument_id"),
            item.get("status"),
            item.get("message"),
        )

    downstream_result = DownstreamRefreshResult()
    should_notify_downstream = bool(all_updated_ids and not args.no_downstream_refresh)
    refresh_all_portfolios = any(
        str(item.get("status") or "") in UPDATED_STATUSES
        and (
            str(item.get("instrument_type") or "").strip().lower() == "fx"
            or str(item.get("instrument_id") or "").strip().lower().startswith("fx-")
        )
        for item in all_results
    )
    if should_notify_downstream:
        LOGGER.info(
            "requesting Portfolio refresh and Watchlist recalc enqueue instrument_count=%s refresh_all_portfolios=%s",
            len(all_updated_ids),
            refresh_all_portfolios,
        )
        try:
            downstream_result = notify_market_data_downstream_refresh(
                instrument_ids=all_updated_ids,
                refresh_all_portfolios=refresh_all_portfolios,
                request_timeout_seconds=args.downstream_timeout_seconds,
                watchlist_request_timeout_seconds=args.watchlist_downstream_timeout_seconds,
                raise_on_error=args.require_downstream_success,
            )
        except DownstreamRefreshError as error:
            downstream_result = error.result
            LOGGER.error("Scheduled downstream refresh failed: %s", error)

    exit_code = 0
    if args.fail_on_item_failure and failures:
        exit_code = 1
    if args.require_downstream_success and downstream_result.failures:
        exit_code = 1

    summary = {
        "status": "succeeded" if exit_code == 0 else "failed",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(),
        "channel": args.channel,
        "channels": channel_summaries,
        "selected_instrument_count": len(requested_instrument_ids),
        "updated_instrument_count": len(all_updated_ids),
        "updated_instrument_ids": all_updated_ids,
        "failed_item_count": len(failures),
        "failed_items": [
            {
                "instrument_id": str(item.get("instrument_id") or ""),
                "status": str(item.get("status") or "unknown"),
                "message": str(item.get("message") or ""),
            }
            for item in failures
        ],
        "downstream_refresh": should_notify_downstream,
        "downstream_delivery_semantics": (
            "portfolio_refresh_response_and_watchlist_recalc_enqueue_acknowledgement"
            if should_notify_downstream
            else "not_requested"
        ),
        "watchlist_recalc_completed": False,
        "downstream_request_count": downstream_result.request_count,
        "downstream_failure_count": len(downstream_result.failures),
        "downstream_failures": [
            {"url": item.url, "message": item.message}
            for item in downstream_result.failures
        ],
        "refresh_all_portfolios": refresh_all_portfolios,
    }
    return exit_code, summary


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    args = _parse_args()
    if args.downstream_timeout_seconds <= 0:
        LOGGER.error("--downstream-timeout-seconds must be positive.")
        return 2
    if args.watchlist_downstream_timeout_seconds <= 0:
        LOGGER.error("--watchlist-downstream-timeout-seconds must be positive.")
        return 2
    if args.retry_failed_attempts < 0:
        LOGGER.error("--retry-failed-attempts must not be negative.")
        return 2

    try:
        with _exclusive_refresh_lock(args.lock_file):
            started_at = datetime.now().astimezone()
            try:
                exit_code, summary = _run_refresh(args, started_at=started_at)
            except Exception as error:
                finished_at = datetime.now().astimezone().isoformat()
                summary = {
                    "status": "failed",
                    "exit_code": 1,
                    "started_at": started_at.isoformat(),
                    "finished_at": finished_at,
                    "channel": args.channel,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                }
                exit_code = 1
                LOGGER.exception("Scheduled market data refresh failed unexpectedly.")

            summary["exit_code"] = exit_code
            try:
                _write_json_atomic(args.summary_file, summary)
            except Exception:
                LOGGER.exception("Failed to write market data refresh summary: %s", args.summary_file)
                exit_code = 1
                summary["status"] = "failed"
                summary["exit_code"] = exit_code
                summary["summary_write_failed"] = True

            LOGGER.info("scheduled market data refresh finished summary=%s", summary)
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return exit_code
    except RefreshAlreadyRunningError as error:
        LOGGER.warning("%s", error)
        return ALREADY_RUNNING_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
