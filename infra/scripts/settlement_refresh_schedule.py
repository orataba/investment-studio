"""Skip the 23:00 retry only after a success started in today's primary slot."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


SETTLEMENT_TIMEZONE = ZoneInfo("Asia/Shanghai")
MAX_SUMMARY_BYTES = 1024 * 1024


def should_run(summary_file: Path, now: datetime) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("The schedule clock must include a timezone")
    local_now = now.astimezone(SETTLEMENT_TIMEZONE)
    # The timer has two daily slots. Only the late slot is conditional; manual
    # starts outside it and the primary slot retain their normal behavior.
    if local_now.hour != 23:
        return True

    try:
        with summary_file.open("rb") as stream:
            raw = stream.read(MAX_SUMMARY_BYTES + 1)
    except FileNotFoundError:
        return True
    if len(raw) > MAX_SUMMARY_BYTES:
        return True
    try:
        summary = json.loads(raw)
        if not isinstance(summary, dict):
            return True
        if (
            summary.get("channel") != "settlement"
            or summary.get("status") != "succeeded"
            or type(summary.get("exit_code")) is not int
            or summary["exit_code"] != 0
        ):
            return True
        started = datetime.fromisoformat(summary["started_at"])
        finished = datetime.fromisoformat(summary["finished_at"])
    except (ValueError, TypeError, KeyError):
        return True
    if started.utcoffset() is None or finished.utcoffset() is None:
        return True
    primary_at = local_now.replace(hour=21, minute=0, second=0, microsecond=0)
    return not (
        primary_at <= started <= finished <= now
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-file", type=Path, required=True)
    parser.add_argument("--now", type=datetime.fromisoformat)
    args = parser.parse_args()
    due = should_run(args.summary_file, args.now or datetime.now(timezone.utc))
    if not due:
        print("Skipping settlement retry: today's settlement already succeeded.")
    return 0 if due else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # ExecCondition exit 1 skips work; 255 reports a condition failure.
        print(f"Settlement schedule failed: {error}", file=sys.stderr)
        raise SystemExit(255) from error
