from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import psycopg

from studio_data.services.downstream_notifications import DownstreamRequestFailure


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "refresh_market_data_scheduled.py"
SPEC = importlib.util.spec_from_file_location("refresh_market_data_scheduled", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
scheduled_refresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scheduled_refresh)


@pytest.fixture(autouse=True)
def _empty_projection_reconciliation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduled_refresh, "refresh_reference_data_batch",
                        lambda **kwargs: {"results": [], "refreshed_count": 0})
    monkeypatch.setattr(
        scheduled_refresh,
        "sync_security_catalogs",
        lambda: {
            "active_count": 5,
            "catalogs": {
                "equity": {"active_count": 3},
                "etf": {"active_count": 2},
            },
        },
    )
    monkeypatch.setattr(
        scheduled_refresh,
        "rebuild_stale_fund_nav_projections",
        lambda **kwargs: {
            "source": "fund_nav_projection",
            "refreshed_count": 0,
            "skipped_count": 0,
            "results": [],
        },
    )


def _args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "channel": "all",
        "updated_by": "test-scheduler",
        "full_history": False,
        "include_inactive": False,
        "no_downstream_refresh": False,
        "require_downstream_success": True,
        "downstream_timeout_seconds": 30.0,
        "watchlist_downstream_timeout_seconds": 7.0,
        "fail_on_item_failure": True,
        "retry_failed_attempts": 0,
        "database_wait_seconds": 0.0,
        "database_retry_interval_seconds": 1.0,
        "instrument_ids": [],
        "json": False,
        "lock_file": tmp_path / "refresh.lock",
        "lock_wait_seconds": 0.0,
        "summary_file": tmp_path / "summary.json",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _updated_result(instrument_id: str, instrument_type: str) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "instrument_name": instrument_id,
        "instrument_type": instrument_type,
        "source_mode": "api",
        "source_api_profile": "tushare",
        "status": "refreshed",
        "message": "updated",
    }


def test_database_readiness_retries_without_exposing_connection_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    clock = [100.0]

    class ReadyConnection:
        def __enter__(self) -> "ReadyConnection":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, statement: str) -> None:
            assert statement == "SELECT 1"

    def connect(*args: object, **kwargs: object) -> ReadyConnection:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        if attempts < 3:
            raise psycopg.OperationalError("database unavailable at secret-host")
        return ReadyConnection()

    monkeypatch.setenv(
        "INVESTMENT_STUDIO_DATA_DATABASE_URL",
        "postgresql+psycopg://secret:secret@secret-host/database",
    )
    monkeypatch.setattr(scheduled_refresh.psycopg, "connect", connect)
    monkeypatch.setattr(scheduled_refresh.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        scheduled_refresh.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    assert scheduled_refresh._wait_for_database(
        timeout_seconds=10,
        retry_interval_seconds=2,
    ) == 3
    assert attempts == 3


def test_database_readiness_timeout_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "INVESTMENT_STUDIO_DATA_DATABASE_URL",
        "postgresql://secret:secret@secret-host/database",
    )
    monkeypatch.setattr(
        scheduled_refresh.psycopg,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            psycopg.OperationalError("secret-host refused secret")
        ),
    )

    with pytest.raises(scheduled_refresh.DatabaseUnavailableError) as captured:
        scheduled_refresh._wait_for_database(
            timeout_seconds=0,
            retry_interval_seconds=1,
        )

    assert "secret" not in str(captured.value)
    assert "secret-host" not in str(captured.value)


def test_all_channels_merge_into_one_downstream_notification(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_batch(*, source, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        result = (
            _updated_result("fund-a", "public_fund")
            if source == "tushare"
            else _updated_result("fx-usdcny", "fx")
        )
        return {"refreshed_count": 1, "skipped_count": 0, "results": [result]}

    def fake_notify(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return scheduled_refresh.DownstreamRefreshResult(request_count=2)

    monkeypatch.setattr(scheduled_refresh, "refresh_market_data_batch", fake_batch)
    monkeypatch.setattr(scheduled_refresh, "notify_market_data_downstream_refresh", fake_notify)

    exit_code, summary = scheduled_refresh._run_refresh(_args(tmp_path))

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["instrument_ids"] == ["fund-a", "fx-usdcny"]
    assert calls[0]["refresh_all_portfolios"] is True
    assert calls[0]["raise_on_error"] is True
    assert calls[0]["request_timeout_seconds"] == 30.0
    assert calls[0]["watchlist_request_timeout_seconds"] == 7.0
    assert summary["updated_instrument_count"] == 2
    assert summary["downstream_request_count"] == 2
    assert summary["refresh_all_portfolios"] is True
    assert [item["channel"] for item in summary["channels"]] == [
        "fmp_catalog",
        "tushare",
        "email",
        "fmp",
        "fund_nav_projection",
        "reference",
    ]


def test_all_channel_notifies_for_projection_only_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    notified: list[dict[str, object]] = []
    monkeypatch.setattr(
        scheduled_refresh,
        "refresh_market_data_batch",
        lambda **kwargs: {
            "source": kwargs["source"],
            "refreshed_count": 0,
            "skipped_count": 0,
            "results": [],
        },
    )
    monkeypatch.setattr(
        scheduled_refresh,
        "rebuild_stale_fund_nav_projections",
        lambda **kwargs: {
            "source": "fund_nav_projection",
            "refreshed_count": 1,
            "skipped_count": 4,
            "results": [_updated_result("fund-manual", "public_fund")],
        },
    )
    monkeypatch.setattr(
        scheduled_refresh,
        "notify_market_data_downstream_refresh",
        lambda **kwargs: (
            notified.append(kwargs)
            or scheduled_refresh.DownstreamRefreshResult(request_count=2)
        ),
    )

    exit_code, summary = scheduled_refresh._run_refresh(_args(tmp_path))

    assert exit_code == 0
    assert summary["updated_instrument_ids"] == ["fund-manual"]
    assert notified[0]["instrument_ids"] == ["fund-manual"]


def test_strict_downstream_failure_sets_failed_summary(monkeypatch, tmp_path: Path) -> None:
    result = scheduled_refresh.DownstreamRefreshResult(
        request_count=2,
        failures=(
            DownstreamRequestFailure(
                url="http://portfolio.local/refresh",
                message="timed out",
            ),
        ),
    )

    monkeypatch.setattr(
        scheduled_refresh,
        "refresh_market_data_batch",
        lambda **kwargs: {
            "refreshed_count": 1 if kwargs["source"] == "tushare" else 0,
            "skipped_count": 0,
            "results": (
                [_updated_result("fund-a", "public_fund")]
                if kwargs["source"] == "tushare"
                else []
            ),
        },
    )

    def fail_notify(**kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise scheduled_refresh.DownstreamRefreshError(result)

    monkeypatch.setattr(scheduled_refresh, "notify_market_data_downstream_refresh", fail_notify)

    exit_code, summary = scheduled_refresh._run_refresh(_args(tmp_path))

    assert exit_code == 1
    assert summary["status"] == "failed"
    assert summary["downstream_failure_count"] == 1


def test_channel_summary_uses_final_retry_result(monkeypatch, tmp_path: Path) -> None:
    failed = _updated_result("fund-a", "public_fund")
    failed["status"] = "failed"

    monkeypatch.setattr(
        scheduled_refresh,
        "refresh_market_data_batch",
        lambda **kwargs: {
            "refreshed_count": 0,
            "skipped_count": 0,
            "results": [failed] if kwargs["source"] == "tushare" else [],
        },
    )
    monkeypatch.setattr(
        scheduled_refresh,
        "refresh_market_data_with_timeout",
        lambda **kwargs: {
            "instrument_id": kwargs["instrument_id"],
            "instrument_name": "fund-a",
            "instrument_type": "public_fund",
            "source_settings": {"source_mode": "api", "source_api_profile": "tushare"},
            "refresh_status": {"status": "refreshed", "message": "updated on retry"},
        },
    )
    monkeypatch.setattr(
        scheduled_refresh,
        "notify_market_data_downstream_refresh",
        lambda **kwargs: scheduled_refresh.DownstreamRefreshResult(request_count=2),
    )

    exit_code, summary = scheduled_refresh._run_refresh(
        _args(tmp_path, retry_failed_attempts=1)
    )

    assert exit_code == 0
    tushare_summary = summary["channels"][1]
    assert tushare_summary["refreshed_count"] == 1
    assert tushare_summary["status_counts"] == {"refreshed": 1}
    assert summary["updated_instrument_count"] == 1


def test_retry_timeout_keeps_failed_result_and_continues_to_next_item(monkeypatch) -> None:
    calls: list[str] = []

    def fake_refresh_with_timeout(**kwargs):  # type: ignore[no-untyped-def]
        instrument_id = kwargs["instrument_id"]
        calls.append(instrument_id)
        status = "failed" if instrument_id == "fund-timeout" else "refreshed"
        return {
            "instrument_id": instrument_id,
            "instrument_name": instrument_id,
            "instrument_type": "public_fund",
            "source_settings": {"source_mode": "api", "source_api_profile": "tushare"},
            "refresh_status": {
                "status": status,
                "message": "timed out" if status == "failed" else "updated",
            },
        }

    monkeypatch.setattr(
        scheduled_refresh,
        "refresh_market_data_with_timeout",
        fake_refresh_with_timeout,
    )
    results = [
        {**_updated_result("fund-timeout", "public_fund"), "status": "failed"},
        {**_updated_result("fund-next", "public_fund"), "status": "failed"},
    ]

    retried = scheduled_refresh._retry_failed_results(
        channel="tushare",
        results=results,
        updated_by="pytest",
        full_history=False,
        retry_attempts=1,
    )

    assert calls == ["fund-timeout", "fund-next"]
    assert {item["instrument_id"]: item["status"] for item in retried} == {
        "fund-timeout": "failed",
        "fund-next": "refreshed",
    }


def test_retry_exception_keeps_failed_result_and_continues_to_next_item(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_refresh_with_timeout(**kwargs):  # type: ignore[no-untyped-def]
        instrument_id = kwargs["instrument_id"]
        calls.append(instrument_id)
        if instrument_id == "fund-conflict":
            raise ValueError("conflicting provider observations")
        return {
            "instrument_id": instrument_id,
            "instrument_name": instrument_id,
            "instrument_type": "public_fund",
            "source_settings": {
                "source_mode": "api",
                "source_api_profile": "tushare",
            },
            "refresh_status": {
                "status": "refreshed",
                "message": "updated",
            },
        }

    monkeypatch.setattr(
        scheduled_refresh,
        "refresh_market_data_with_timeout",
        fake_refresh_with_timeout,
    )
    results = [
        {**_updated_result("fund-conflict", "public_fund"), "status": "failed"},
        {**_updated_result("fund-next", "public_fund"), "status": "failed"},
    ]

    retried = scheduled_refresh._retry_failed_results(
        channel="tushare",
        results=results,
        updated_by="pytest",
        full_history=False,
        retry_attempts=1,
    )

    assert calls == ["fund-conflict", "fund-next"]
    assert {item["instrument_id"]: item["status"] for item in retried} == {
        "fund-conflict": "failed",
        "fund-next": "refreshed",
    }
    assert "ValueError: conflicting provider observations" in next(
        item["message"]
        for item in retried
        if item["instrument_id"] == "fund-conflict"
    )


def test_email_failures_retry_one_mailbox_batch_not_once_per_failed_item(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_batch(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs["source"])
        return {
            "source": "email",
            "skipped_count": 0,
            "results": [_updated_result("fund-a", "public_fund")],
        }

    monkeypatch.setattr(scheduled_refresh, "refresh_market_data_batch", fake_batch)
    monkeypatch.setattr(
        scheduled_refresh,
        "refresh_market_data_with_timeout",
        lambda **kwargs: pytest.fail(
            f"email retry must not refresh one item: {kwargs['instrument_id']}"
        ),
    )

    results, response = scheduled_refresh._retry_failed_email_batch(
        results=[
            {**_updated_result("fund-a", "public_fund"), "status": "failed"},
            {**_updated_result("fund-b", "public_fund"), "status": "failed"},
            {
                **_updated_result("email-folder:INBOX", "other"),
                "status": "failed",
            },
        ],
        updated_by="pytest",
        full_history=False,
        include_inactive=False,
        retry_attempts=1,
    )

    assert calls == ["email"]
    assert response is not None
    assert [item["instrument_id"] for item in results] == ["fund-a"]


def test_email_retry_preserves_updates_from_first_attempt_and_drops_resolved_folder_failure(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        scheduled_refresh,
        "refresh_market_data_batch",
        lambda **kwargs: {
            "source": kwargs["source"],
            "skipped_count": 0,
            "results": [
                {
                    **_updated_result("fund-a", "public_fund"),
                    "status": "no_new_data",
                },
                _updated_result("fund-b", "public_fund"),
            ],
        },
    )

    results, _ = scheduled_refresh._retry_failed_email_batch(
        results=[
            _updated_result("fund-a", "public_fund"),
            {
                **_updated_result("email-folder:INBOX", "other"),
                "status": "failed",
            },
        ],
        updated_by="pytest",
        full_history=False,
        include_inactive=False,
        retry_attempts=1,
    )

    assert {
        item["instrument_id"]: item["status"] for item in results
    } == {"fund-a": "refreshed", "fund-b": "refreshed"}


def test_projection_retry_targets_only_failed_reconciliations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[set[str]] = []

    def fake_rebuild(**kwargs: object) -> dict[str, object]:
        instrument_ids = set(kwargs["instrument_ids"])
        calls.append(instrument_ids)
        return {
            "source": "fund_nav_projection",
            "results": [_updated_result(instrument_id, "public_fund") for instrument_id in instrument_ids],
        }

    monkeypatch.setattr(
        scheduled_refresh,
        "rebuild_stale_fund_nav_projections",
        fake_rebuild,
    )
    results = scheduled_refresh._retry_failed_projection_batch(
        results=[
            {**_updated_result("fund-failed", "public_fund"), "status": "failed"},
            _updated_result("fund-ready", "public_fund"),
        ],
        updated_by="pytest",
        include_inactive=False,
        retry_attempts=1,
    )

    assert calls == [{"fund-failed"}]
    assert {item["instrument_id"]: item["status"] for item in results} == {
        "fund-failed": "refreshed",
        "fund-ready": "refreshed",
    }


def test_selected_email_ids_still_scan_mailbox_only_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_batch(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return {
            "source": "email",
            "refreshed_count": 2,
            "skipped_count": 0,
            "results": [
                _updated_result("fund-a", "public_fund"),
                _updated_result("fund-b", "public_fund"),
            ],
        }

    monkeypatch.setattr(scheduled_refresh, "refresh_market_data_batch", fake_batch)
    monkeypatch.setattr(
        scheduled_refresh,
        "refresh_market_data_with_timeout",
        lambda **kwargs: pytest.fail(
            f"selected email path must not rescan per item: {kwargs['instrument_id']}"
        ),
    )
    monkeypatch.setattr(
        scheduled_refresh,
        "notify_market_data_downstream_refresh",
        lambda **kwargs: scheduled_refresh.DownstreamRefreshResult(request_count=2),
    )

    exit_code, summary = scheduled_refresh._run_refresh(
        _args(
            tmp_path,
            channel="email",
            instrument_ids=["fund-a", "fund-b"],
            retry_failed_attempts=0,
        )
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["source"] == "email"
    assert summary["updated_instrument_ids"] == ["fund-a", "fund-b"]


def test_exclusive_lock_rejects_overlap_from_another_process(tmp_path: Path) -> None:
    lock_file = tmp_path / "refresh.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl, pathlib, sys; "
                "handle = pathlib.Path(sys.argv[1]).open('a+'); "
                "fcntl.flock(handle.fileno(), fcntl.LOCK_EX); "
                "print('ready', flush=True); sys.stdin.read(1)"
            ),
            str(lock_file),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ready"
    try:
        with pytest.raises(scheduled_refresh.RefreshAlreadyRunningError):
            with scheduled_refresh._exclusive_refresh_lock(lock_file):
                pytest.fail("overlapping lock unexpectedly acquired")
    finally:
        assert holder.stdin is not None
        holder.stdin.write("x")
        holder.stdin.flush()
        holder.wait(timeout=5)
    assert holder.returncode == 0


def test_waiting_projection_acquires_lock_after_the_owner_finishes(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    waiting = Event()
    real_sleep = scheduled_refresh.time.sleep
    def observe_wait(seconds):
        waiting.set()
        real_sleep(seconds)
    monkeypatch.setattr(scheduled_refresh.time, "sleep", observe_wait)
    lock_file = tmp_path / "refresh.lock"
    def acquire_after_owner():
        with scheduled_refresh._exclusive_refresh_lock(lock_file, wait_seconds=2):
            with pytest.raises(scheduled_refresh.RefreshAlreadyRunningError):
                with scheduled_refresh._exclusive_refresh_lock(lock_file):
                    pytest.fail("waiter did not hold the exclusive lock")
            return True
    with ThreadPoolExecutor(max_workers=1) as workers:
        with scheduled_refresh._exclusive_refresh_lock(lock_file):
            future = workers.submit(acquire_after_owner)
            assert waiting.wait(timeout=1)
            assert not future.done()
        assert future.result(timeout=3)


def test_lock_wait_timeout_returns_tempfail_without_refresh_or_summary(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(scheduled_refresh.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(scheduled_refresh.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    args = _args(tmp_path, lock_wait_seconds=0.6)
    monkeypatch.setattr(scheduled_refresh, "_parse_args", lambda: args)
    monkeypatch.setattr(scheduled_refresh, "_wait_for_database", lambda **kwargs: pytest.fail("timeout attempted data work"))
    with scheduled_refresh._exclusive_refresh_lock(args.lock_file):
        assert scheduled_refresh.main() == scheduled_refresh.ALREADY_RUNNING_EXIT_CODE
        assert clock[0] == 0.6
        assert not args.summary_file.exists()


def test_atomic_summary_replaces_file_without_temp_residue(tmp_path: Path) -> None:
    target = tmp_path / "state" / "summary.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"status":"old"}\n', encoding="utf-8")

    scheduled_refresh._write_json_atomic(target, {"status": "succeeded", "count": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "count": 2,
        "status": "succeeded",
    }
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_market_scope_uses_exchange_dates_and_does_not_treat_private_nav_as_listed_prices(monkeypatch):
    from datetime import UTC, datetime
    rows = [{"instrument_id": iid, "instrument_type": kind, "source_settings": {"market_calendar": calendar}}
            for iid, kind, calendar in [("xlk", "etf", "XASE"), ("cn-stock", "equity", "XSHE"),
                                       ("hk-stock", "equity", "XHKG"), ("nav", "private_fund", "XSHG")]]
    monkeypatch.setattr(scheduled_refresh, "list_instruments", lambda **kwargs: rows)
    # US Labor Day; both Asian markets are open. US aliases share the NYSE session.
    now = datetime(2026, 9, 7, 13, tzinfo=UTC)
    assert scheduled_refresh._market_instrument_ids("us", now, channel="market") == []
    assert scheduled_refresh._market_instrument_ids("cn-hk", now, channel="market") == ["cn-stock", "hk-stock"]
    assert scheduled_refresh._market_instrument_ids("us", datetime(2026, 9, 8, 13, tzinfo=UTC), channel="reference") == ["xlk"]


def test_empty_market_scope_never_falls_back_to_global_refresh(monkeypatch, tmp_path):
    from datetime import UTC, datetime
    monkeypatch.setattr(scheduled_refresh, "_market_instrument_ids", lambda *args, **kwargs: [])
    def unexpected(**kwargs):
        raise AssertionError("An empty market scope must not refresh every instrument")
    monkeypatch.setattr(scheduled_refresh, "refresh_reference_data_batch", unexpected)
    monkeypatch.setattr(scheduled_refresh, "refresh_market_data_batch", unexpected)
    code, summary = scheduled_refresh._run_refresh(
        _args(tmp_path, channel="reference", market_scope="us"),
        started_at=datetime(2026, 9, 7, 13, tzinfo=UTC),
    )
    assert code == 0


def test_settlement_retains_nav_projection_and_fx_without_repeating_research():
    assert scheduled_refresh._channels("settlement") == ["fmp_catalog", "tushare", "email", "fx", "fund_nav_projection"]
    assert scheduled_refresh._channels("market") == ["configured"]
