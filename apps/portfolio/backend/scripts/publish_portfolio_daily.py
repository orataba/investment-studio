#!/usr/bin/env python3
"""Drain durable Portfolio Daily work and verify exact release publications."""

from __future__ import annotations

import argparse
from datetime import date
import json

from portfolio_app.calculations.portfolio_daily.release_gate import (
    drain_portfolio_daily_publications,
)
from portfolio_app.core.settings import get_settings
from portfolio_app.db.session import get_engine


def _canonical_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected canonical YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("expected canonical YYYY-MM-DD")
    return parsed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the durable Portfolio Daily worker until every portfolio's "
            "current generation is published for the requested release date."
        )
    )
    parser.add_argument("--as-of-date", required=True, type=_canonical_date)
    parser.add_argument("--timeout-seconds", type=float, default=3600.0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    state = drain_portfolio_daily_publications(
        get_engine(),
        settings=get_settings(),
        requested_as_of=args.as_of_date,
        timeout_seconds=args.timeout_seconds,
    )
    print(
        json.dumps(
            {
                "status": "published",
                "requested_as_of": args.as_of_date.isoformat(),
                "portfolio_count": state.portfolio_count,
                "published_portfolio_ids": list(state.published_portfolio_ids),
                "active_run_count": state.active_run_count,
                "pending_intent_count": state.pending_intent_count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
