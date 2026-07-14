from __future__ import annotations

import argparse
from datetime import date
import importlib.util
from pathlib import Path

import pytest

from portfolio_app.calculations.portfolio_daily.release_gate import (
    PortfolioDailyReleaseState,
)


pytestmark = pytest.mark.no_database


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "publish_portfolio_daily.py"
)
SPEC = importlib.util.spec_from_file_location("publish_portfolio_daily", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
publish_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publish_cli)


def test_release_date_parser_requires_canonical_iso_date() -> None:
    assert publish_cli._canonical_date("2026-07-14") == date(2026, 7, 14)
    for value in ("2026-7-14", "14-07-2026", "2026-02-30"):
        with pytest.raises(argparse.ArgumentTypeError):
            publish_cli._canonical_date(value)


def test_release_state_is_complete_only_after_queue_and_publications_are_exact() -> None:
    complete = PortfolioDailyReleaseState(
        portfolio_count=2,
        published_portfolio_ids=("p1", "p2"),
        incomplete_portfolio_ids=(),
        active_run_count=0,
        pending_intent_count=0,
    )
    active = PortfolioDailyReleaseState(
        portfolio_count=2,
        published_portfolio_ids=("p1", "p2"),
        incomplete_portfolio_ids=(),
        active_run_count=1,
        pending_intent_count=0,
    )
    incomplete = PortfolioDailyReleaseState(
        portfolio_count=2,
        published_portfolio_ids=("p1",),
        incomplete_portfolio_ids=("p2",),
        active_run_count=0,
        pending_intent_count=0,
    )

    assert complete.complete
    assert not active.complete
    assert not incomplete.complete
