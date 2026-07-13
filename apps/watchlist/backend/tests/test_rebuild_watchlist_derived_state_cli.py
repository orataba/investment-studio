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
    / "rebuild_watchlist_derived_state.py"
)
SPEC = importlib.util.spec_from_file_location(
    "watchlist_rebuild_derived_state_cli",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cli
SPEC.loader.exec_module(cli)


def test_parse_args_defaults_to_two_convergence_rounds_and_requires_scope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = cli.parse_args(
        [
            "--valuation-date",
            "2026-07-13",
            "--instrument-id",
            "fund-b",
            "--instrument-id",
            "fund-a",
        ]
    )

    assert args.valuation_date == date(2026, 7, 13)
    assert args.instrument_id == ["fund-b", "fund-a"]
    assert args.rounds == 2
    assert args.continue_on_error is False

    with pytest.raises(SystemExit):
        cli.parse_args(["--instrument-id", "fund-a"])
    with pytest.raises(SystemExit):
        cli.parse_args(["--valuation-date", "2026-07-13"])
    with pytest.raises(SystemExit):
        cli.parse_args(
            [
                "--valuation-date",
                "2026-07-13",
                "--all-active",
                "--database-url",
                "postgresql://user:secret@example.invalid/db",
            ]
        )
    assert "secret" not in capsys.readouterr().err


def test_select_target_ids_uses_only_active_for_bulk_but_allows_explicit_inactive() -> None:
    assert cli.select_target_ids(
        available_ids=["inactive", "fund-b", "fund-a"],
        active_ids=["fund-b", "fund-a"],
        requested_ids=[],
        rebuild_all_active=True,
    ) == ["fund-a", "fund-b"]
    assert cli.select_target_ids(
        available_ids=["inactive", "fund-a"],
        active_ids=["fund-a"],
        requested_ids=["inactive"],
        rebuild_all_active=False,
    ) == ["inactive"]

    with pytest.raises(cli.TargetSelectionError) as caught:
        cli.select_target_ids(
            available_ids=["fund-a"],
            active_ids=["fund-a"],
            requested_ids=["missing"],
            rebuild_all_active=False,
        )
    assert caught.value.missing_ids == ("missing",)

    with pytest.raises(cli.EmptyTargetSelectionError):
        cli.select_target_ids(
            available_ids=["inactive"],
            active_ids=[],
            requested_ids=[],
            rebuild_all_active=True,
        )


def test_rebuild_one_passes_explicit_date_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    session = object()

    class SessionContext:
        def __enter__(self) -> object:
            return session

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeService:
        def execute_recalc(self, received_session: object, **kwargs: object) -> dict[str, object]:
            calls.append({"session": received_session, **kwargs})
            return {
                "recalc_job_id": "recalc-1",
                "job_status": "completed",
                "result": {"source_changed_during_recalc": False},
            }

    monkeypatch.setattr(cli, "get_session_factory", lambda: SessionContext)
    monkeypatch.setattr(cli, "CanonicalRecalcService", FakeService)

    result = cli.rebuild_one_instrument("fund-a", date(2026, 7, 13), 2)

    assert calls == [
        {
            "session": session,
            "instrument_id": "fund-a",
            "job_type": "all",
            "trigger_type": "maintenance_cli",
            "trigger_ref_type": "derived_state_rebuild_valuation_date",
            "trigger_ref_id": "2026-07-13",
            "valuation_date": date(2026, 7, 13),
            "commit": True,
        }
    ]
    assert result == {
        "round_number": 2,
        "recalc_job_id": "recalc-1",
        "job_status": "completed",
        "source_changed_during_recalc": False,
    }


def test_two_round_rebuild_uses_same_stable_order_and_converges() -> None:
    calls: list[tuple[str, int]] = []
    events: list[dict[str, object]] = []

    def rebuild_one(
        instrument_id: str,
        _valuation_date: date,
        round_number: int,
    ) -> dict[str, object]:
        calls.append((instrument_id, round_number))
        return {"round_number": round_number, "job_status": "completed"}

    summary = cli.run_rebuild(
        instrument_ids=["fund-b", "fund-a", "fund-b"],
        valuation_date=date(2026, 7, 13),
        rounds=2,
        full_active_universe=True,
        continue_on_error=False,
        rebuild_one=rebuild_one,
        emit=events.append,
    )

    assert calls == [
        ("fund-a", 1),
        ("fund-b", 1),
        ("fund-a", 2),
        ("fund-b", 2),
    ]
    assert summary.target_count == 2
    assert summary.attempted_count == 4
    assert summary.completed_attempt_count == 4
    assert summary.fully_processed_target_count == 2
    assert summary.cohort_convergence_scope == "full_active_universe"
    assert summary.cohort_convergence_pass_completed is True
    assert summary.failed_count == 0
    assert summary.exit_code == 0
    assert [
        event["round_number"]
        for event in events
        if event["event"] == "round_started"
    ] == [1, 2]


def test_rebuild_is_fail_fast_and_never_emits_exception_text() -> None:
    calls: list[tuple[str, int]] = []
    events: list[dict[str, object]] = []

    def rebuild_one(
        instrument_id: str,
        _valuation_date: date,
        round_number: int,
    ) -> dict[str, object]:
        calls.append((instrument_id, round_number))
        if instrument_id == "fund-b":
            raise RuntimeError("postgresql://user:secret@example.invalid/db")
        return {"round_number": round_number}

    summary = cli.run_rebuild(
        instrument_ids=["fund-c", "fund-a", "fund-b"],
        valuation_date=date(2026, 7, 13),
        rounds=2,
        full_active_universe=True,
        continue_on_error=False,
        rebuild_one=rebuild_one,
        emit=events.append,
    )

    assert calls == [("fund-a", 1), ("fund-b", 1)]
    assert summary.attempted_count == 2
    assert summary.completed_attempt_count == 1
    assert summary.fully_processed_target_count == 0
    assert summary.cohort_convergence_pass_completed is False
    assert summary.failed_count == 1
    assert summary.stopped_early is True
    assert summary.exit_code == 1
    serialized_events = json.dumps(events)
    assert "secret" not in serialized_events
    assert "postgresql://" not in serialized_events


def test_continue_on_error_cannot_report_failed_target_as_converged() -> None:
    calls: list[tuple[str, int]] = []

    def rebuild_one(
        instrument_id: str,
        _valuation_date: date,
        round_number: int,
    ) -> dict[str, object]:
        calls.append((instrument_id, round_number))
        if instrument_id == "fund-a" and round_number == 1:
            raise ValueError("bad source fact")
        return {"round_number": round_number}

    summary = cli.run_rebuild(
        instrument_ids=["fund-b", "fund-a"],
        valuation_date=date(2026, 7, 13),
        rounds=2,
        full_active_universe=False,
        continue_on_error=True,
        rebuild_one=rebuild_one,
        emit=lambda _event: None,
    )

    assert calls == [
        ("fund-a", 1),
        ("fund-b", 1),
        ("fund-a", 2),
        ("fund-b", 2),
    ]
    assert summary.attempted_count == 4
    assert summary.completed_attempt_count == 3
    assert summary.fully_processed_target_count == 1
    assert summary.cohort_convergence_scope == "selected_instruments_only"
    assert summary.cohort_convergence_pass_completed is False
    assert summary.failed_count == 1
    assert summary.exit_code == 1
