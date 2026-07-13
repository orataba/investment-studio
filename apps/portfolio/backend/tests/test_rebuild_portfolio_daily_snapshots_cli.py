from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "rebuild_portfolio_daily_snapshots.py"
)
SPEC = importlib.util.spec_from_file_location(
    "portfolio_rebuild_daily_snapshots_cli",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cli
SPEC.loader.exec_module(cli)


def test_parse_args_requires_explicit_date_and_scope(capsys: pytest.CaptureFixture[str]) -> None:
    args = cli.parse_args(
        [
            "--as-of-date",
            "2026-07-13",
            "--portfolio-id",
            "portfolio-b",
            "--portfolio-id",
            "portfolio-a",
        ]
    )

    assert args.as_of_date == date(2026, 7, 13)
    assert args.portfolio_id == ["portfolio-b", "portfolio-a"]
    assert args.all is False
    assert args.continue_on_error is False

    with pytest.raises(SystemExit):
        cli.parse_args(["--portfolio-id", "portfolio-a"])
    with pytest.raises(SystemExit):
        cli.parse_args(["--as-of-date", "2026-07-13"])
    with pytest.raises(SystemExit):
        cli.parse_args(
            [
                "--as-of-date",
                "2026-07-13",
                "--all",
                "--database-url",
                "postgresql://user:secret@example.invalid/db",
            ]
        )
    assert "secret" not in capsys.readouterr().err


def test_select_target_ids_is_deterministic_and_rejects_missing_before_work() -> None:
    assert cli.select_target_ids(
        available_ids=["portfolio-b", "portfolio-a"],
        requested_ids=["portfolio-b", "portfolio-b", " portfolio-a "],
        rebuild_all=False,
    ) == ["portfolio-a", "portfolio-b"]
    assert cli.select_target_ids(
        available_ids=["portfolio-b", "portfolio-a"],
        requested_ids=[],
        rebuild_all=True,
    ) == ["portfolio-a", "portfolio-b"]

    with pytest.raises(cli.TargetSelectionError) as caught:
        cli.select_target_ids(
            available_ids=["portfolio-a"],
            requested_ids=["portfolio-a", "missing"],
            rebuild_all=False,
        )
    assert caught.value.missing_ids == ("missing",)

    with pytest.raises(cli.EmptyTargetSelectionError):
        cli.select_target_ids(
            available_ids=[],
            requested_ids=[],
            rebuild_all=True,
        )


def test_rebuild_one_forces_full_history_and_explicit_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        cli,
        "mark_portfolio_daily_snapshots_stale",
        lambda portfolio_id, dirty_from: calls.append(
            ("mark", portfolio_id, dirty_from)
        ),
    )

    def fake_refresh(portfolio_id: str, end_date: date) -> dict[str, object]:
        calls.append(("refresh", portfolio_id, end_date))
        return {
            "snapshot_count": 12,
            "refreshed_from": date(2026, 7, 2),
            "refreshed_to": end_date,
            "recalculated_from": date(2026, 7, 2),
        }

    monkeypatch.setattr(cli, "refresh_portfolio_daily_snapshots", fake_refresh)

    result = cli.rebuild_one_portfolio("portfolio-a", date(2026, 7, 13))

    assert calls == [
        ("mark", "portfolio-a", date.min),
        ("refresh", "portfolio-a", date(2026, 7, 13)),
    ]
    assert result["snapshot_count"] == 12
    assert result["refreshed_to"] == date(2026, 7, 13)


def test_run_rebuild_is_sorted_and_fail_fast_without_leaking_error_text() -> None:
    calls: list[str] = []
    events: list[dict[str, object]] = []

    def rebuild_one(portfolio_id: str, _as_of_date: date) -> dict[str, object]:
        calls.append(portfolio_id)
        if portfolio_id == "portfolio-b":
            raise RuntimeError("postgresql://user:secret@example.invalid/db")
        return {"snapshot_count": 1, "refreshed_to": date(2026, 7, 13)}

    summary = cli.run_rebuild(
        portfolio_ids=["portfolio-c", "portfolio-a", "portfolio-b"],
        as_of_date=date(2026, 7, 13),
        continue_on_error=False,
        rebuild_one=rebuild_one,
        emit=events.append,
    )

    assert calls == ["portfolio-a", "portfolio-b"]
    assert summary.attempted_count == 2
    assert summary.completed_count == 1
    assert summary.failed_count == 1
    assert summary.stopped_early is True
    assert summary.exit_code == 1
    serialized_events = json.dumps(events, default=str)
    assert "secret" not in serialized_events
    assert "postgresql://" not in serialized_events
    assert events[-1]["event"] == "rebuild_summary"


def test_run_rebuild_can_continue_but_still_exits_nonzero() -> None:
    calls: list[str] = []

    def rebuild_one(portfolio_id: str, _as_of_date: date) -> dict[str, object]:
        calls.append(portfolio_id)
        if portfolio_id == "portfolio-a":
            raise ValueError("bad fact")
        return {"snapshot_count": 0}

    summary = cli.run_rebuild(
        portfolio_ids=["portfolio-b", "portfolio-a"],
        as_of_date=date(2026, 7, 13),
        continue_on_error=True,
        rebuild_one=rebuild_one,
        emit=lambda _event: None,
    )

    assert calls == ["portfolio-a", "portfolio-b"]
    assert summary.attempted_count == 2
    assert summary.completed_count == 1
    assert summary.failed_count == 1
    assert summary.stopped_early is False
    assert summary.exit_code == 1
