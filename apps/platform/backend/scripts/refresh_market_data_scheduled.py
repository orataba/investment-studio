from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from platform_app.services.downstream_notifications import notify_market_data_downstream_refresh  # noqa: E402
from platform_app.services.market_data_ops import refresh_market_data, refresh_market_data_batch  # noqa: E402


LOGGER = logging.getLogger("yungu.market_data_refresh")
UPDATED_STATUSES = {"imported", "refreshed"}
FAILED_STATUSES = {"failed", "blocked"}
RETRYABLE_STATUSES = {"failed"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Yungu market data without a browser session.")
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
        record = refresh_market_data(
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
            record = refresh_market_data(
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


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    args = _parse_args()
    started_at = datetime.now().astimezone()
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
        if updated_ids and not args.no_downstream_refresh:
            LOGGER.info(
                "notifying downstream refresh channel=%s instrument_count=%s",
                channel,
                len(updated_ids),
            )
            notify_market_data_downstream_refresh(instrument_ids=updated_ids)

        counts = _status_counts(results)
        channel_summaries.append(
            {
                "channel": channel,
                "refreshed_count": response.get("refreshed_count", 0),
                "skipped_count": response.get("skipped_count", 0),
                "result_count": len(results),
                "status_counts": counts,
            }
        )
        LOGGER.info(
            "channel=%s refreshed=%s checked=%s skipped=%s statuses=%s",
            channel,
            response.get("refreshed_count", 0),
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

    summary = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(),
        "channel": args.channel,
        "channels": channel_summaries,
        "selected_instrument_count": len(requested_instrument_ids),
        "updated_instrument_count": len(all_updated_ids),
        "failed_item_count": len(failures),
        "downstream_refresh": bool(all_updated_ids and not args.no_downstream_refresh),
    }
    LOGGER.info("scheduled market data refresh finished summary=%s", summary)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))

    if args.fail_on_item_failure and failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
