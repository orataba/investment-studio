#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "infra/scripts/wait_for_refresh_convergence.py"
SPEC = importlib.util.spec_from_file_location(
    "wait_for_refresh_convergence",
    MODULE_PATH,
)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import contract
    raise RuntimeError(f"Could not import {MODULE_PATH}")
waiter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = waiter
SPEC.loader.exec_module(waiter)


def snapshot(observed_at: str, **overrides: int):
    values = {field_name: 0 for field_name in waiter.COUNT_FIELDS}
    values["portfolio_count"] = 3
    values.update(overrides)
    return waiter.ConvergenceSnapshot(observed_at=observed_at, **values)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class ConvergenceWaiterTests(unittest.TestCase):
    def test_requires_consecutive_stable_samples_and_resets_on_new_work(self) -> None:
        samples = iter(
            (
                snapshot("one"),
                snapshot("two", outbox_pending_count=1),
                snapshot("three"),
                snapshot("four"),
            )
        )
        clock = FakeClock()
        progress = []

        result = waiter.wait_for_convergence(
            lambda: next(samples),
            phase="targeted",
            timeout_seconds=10,
            poll_interval_seconds=1,
            stable_samples=2,
            progress_callback=progress.append,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )

        self.assertEqual(result.status, "converged")
        self.assertEqual(result.stable_samples, 2)
        self.assertEqual(result.snapshot.observed_at, "four")
        self.assertEqual(
            [item.status for item in progress],
            ["waiting", "waiting", "waiting", "converged"],
        )
        self.assertEqual(
            [item.stable_samples for item in progress],
            [1, 0, 1, 2],
        )

    def test_terminal_failure_returns_immediately(self) -> None:
        clock = FakeClock()
        result = waiter.wait_for_convergence(
            lambda: snapshot("dead", outbox_dead_count=1),
            phase="final",
            timeout_seconds=10,
            poll_interval_seconds=1,
            stable_samples=2,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason_code, "terminal_downstream_failure")
        self.assertEqual(result.reason_context, {"outbox_dead_count": 1})
        self.assertEqual(clock.now, 0)

    def test_timeout_keeps_last_snapshot_and_never_oversleeps(self) -> None:
        clock = FakeClock()
        result = waiter.wait_for_convergence(
            lambda: snapshot("busy", watchlist_active_job_count=1),
            phase="final",
            timeout_seconds=2.5,
            poll_interval_seconds=2,
            stable_samples=2,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )

        self.assertEqual(result.status, "timed_out")
        self.assertEqual(result.reason_code, "convergence_timeout")
        self.assertEqual(clock.now, 2.5)
        self.assertEqual(result.snapshot.watchlist_active_job_count, 1)

    def test_snapshot_reader_uses_one_short_read_only_transaction_per_call(self) -> None:
        rows = []
        for observed_at in (
            datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 14, 14, 0, 1, tzinfo=timezone.utc),
        ):
            row = {field_name: 0 for field_name in waiter.COUNT_FIELDS}
            row.update({"observed_at": observed_at, "portfolio_count": 3})
            rows.append(row)
        connections = []

        class Cursor:
            def __init__(self, row):
                self.row = row
                self.statements = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, statement, parameters=None):
                self.statements.append((statement, parameters))

            def fetchone(self):
                return self.row

        class Connection:
            def __init__(self, row):
                self.cursor_instance = Cursor(row)
                self.exited = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.exited = True
                return False

            def cursor(self):
                return self.cursor_instance

        def connect(*args, **kwargs):
            self.assertNotIn("database_url", kwargs)
            self.assertFalse(kwargs["autocommit"])
            connection = Connection(rows[len(connections)])
            connections.append(connection)
            return connection

        with patch.object(waiter.psycopg, "connect", side_effect=connect):
            first = waiter.read_convergence_snapshot("postgresql://secret")
            second = waiter.read_convergence_snapshot(
                "postgresql://secret",
                required_portfolio_as_of_date=date(2026, 7, 14),
            )

        self.assertEqual(first.portfolio_count, 3)
        self.assertNotEqual(first.observed_at, second.observed_at)
        self.assertEqual(len(connections), 2)
        self.assertTrue(all(connection.exited for connection in connections))
        for connection in connections:
            statements = connection.cursor_instance.statements
            self.assertEqual(
                statements[0][0],
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
            )
            self.assertEqual(statements[-1][0], waiter.CONVERGENCE_SNAPSHOT_QUERY)
        self.assertEqual(
            connections[0].cursor_instance.statements[-1][1],
            {"required_portfolio_as_of_date": None},
        )
        self.assertEqual(
            connections[1].cursor_instance.statements[-1][1],
            {"required_portfolio_as_of_date": date(2026, 7, 14)},
        )

    def test_required_portfolio_date_must_converge_before_success(self) -> None:
        samples = iter(
            (
                snapshot("old", portfolio_requested_as_of_mismatch_count=3),
                snapshot("requested", portfolio_effective_as_of_mismatch_count=1),
                snapshot("current"),
                snapshot("stable"),
            )
        )
        clock = FakeClock()

        result = waiter.wait_for_convergence(
            lambda: next(samples),
            phase="final",
            timeout_seconds=10,
            poll_interval_seconds=1,
            stable_samples=2,
            required_portfolio_as_of_date=date(2026, 7, 14),
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )

        self.assertEqual(result.status, "converged")
        self.assertEqual(result.snapshot.observed_at, "stable")
        self.assertEqual(result.required_portfolio_as_of_date, "2026-07-14")
        self.assertEqual(
            result.as_dict()["required_portfolio_as_of_date"],
            "2026-07-14",
        )

    def test_summary_merge_preserves_ingestion_and_only_failures_override_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary_file = Path(directory) / "refresh.json"
            summary_file.write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "updated_instrument_count": 126,
                    }
                ),
                encoding="utf-8",
            )
            converged = waiter.ConvergenceResult(
                status="converged",
                phase="targeted",
                elapsed_seconds=1.25,
                stable_samples=2,
                required_stable_samples=2,
                snapshot=snapshot("done"),
            )
            waiter.update_refresh_summary(summary_file, converged)
            first = json.loads(summary_file.read_text(encoding="utf-8"))

            self.assertEqual(first["status"], "succeeded")
            self.assertEqual(first["ingestion_status"], "succeeded")
            self.assertEqual(first["updated_instrument_count"], 126)
            self.assertEqual(
                first["downstream_convergence"]["status"],
                "converged",
            )

            failed = waiter.ConvergenceResult(
                status="timed_out",
                phase="final",
                elapsed_seconds=900,
                stable_samples=0,
                required_stable_samples=2,
                snapshot=snapshot("busy", outbox_pending_count=1),
                reason_code="convergence_timeout",
            )
            waiter.update_refresh_summary(summary_file, failed)
            second = json.loads(summary_file.read_text(encoding="utf-8"))

            self.assertEqual(second["status"], "failed")
            self.assertEqual(second["ingestion_status"], "succeeded")
            self.assertEqual(second["downstream_convergence"]["phase"], "final")
            self.assertFalse(list(Path(directory).glob("*.tmp")))

    def test_database_url_precedence_and_sqlalchemy_scheme_normalization(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PORTFOLIO_OPS_LOCAL_DATABASE_URL": (
                    "postgresql+psycopg://local:password@db/local"
                ),
                "PORTFOLIO_OPS_PLATFORM_DATABASE_URL": (
                    "postgresql://platform:password@db/platform"
                ),
            },
            clear=False,
        ):
            self.assertEqual(
                waiter.database_url_from_environment(),
                "postgresql://local:password@db/local",
            )

    def test_failure_payload_never_serializes_exception_message_or_database_url(self) -> None:
        error = RuntimeError(
            "postgresql://portfolio_ops:top-secret@127.0.0.1/portfolio_ops"
        )
        result = waiter._execution_failure_result(
            phase="targeted",
            stable_samples=2,
            error=error,
        )
        serialized = json.dumps(result.as_dict())

        self.assertNotIn("top-secret", serialized)
        self.assertNotIn("postgresql://", serialized)
        self.assertEqual(
            result.reason_context,
            {"error_type": "RuntimeError"},
        )

    def test_sql_contract_covers_all_durable_pipeline_boundaries(self) -> None:
        sql = waiter.CONVERGENCE_SNAPSHOT_QUERY
        for relation in (
            "instrument_registry.market_data_outbox_event",
            "watchlist.recalc_source_event_inbox",
            "watchlist.recalc_invalidation_state",
            "watchlist.recalc_job",
            "calculation_registry.calculation_scope_generation",
            "calculation_registry.calculation_current_publication",
            "calculation_registry.calculation_publication",
            "calculation_registry.calculation_run",
            "calculation_registry.calculation_job",
            "calculation_registry.calculation_recompute_intent",
            "portfolio.portfolio_daily_run_output",
        ):
            self.assertIn(relation, sql)
        for closure_field in (
            "closure_status",
            "ledger_balance_residual_exact",
            "nav_bridge_residual_exact",
            "pnl_residual_exact",
            "twr_residual_exact",
            "lot_residual_exact",
        ):
            self.assertIn(closure_field, sql)


if __name__ == "__main__":
    unittest.main()
