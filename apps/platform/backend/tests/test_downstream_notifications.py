from __future__ import annotations

import json
from datetime import date
from urllib.error import URLError

import pytest

from platform_app.services import downstream_notifications


class _StubSettings:
    portfolio_api_url = "http://portfolio.local"
    watchlist_api_url = "http://watchlist.local"


class _StubResponse:
    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.payload = payload or {}

    def __enter__(self) -> "_StubResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _portfolio_ack(*portfolio_ids: str) -> _StubResponse:
    return _StubResponse(
        {
            "portfolio_ids": list(portfolio_ids),
            "accepted": [
                {
                    "portfolio_id": portfolio_id,
                    "status": "accepted",
                    "daily_snapshot_status": "stale",
                    "refresh_request_id": f"request-{portfolio_id}",
                    "dirty_from": "2026-05-01",
                }
                for portfolio_id in portfolio_ids
            ],
        }
    )


def test_market_data_refresh_notifies_portfolio_and_watchlist(monkeypatch) -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        del timeout
        payload = json.loads(request.data.decode("utf-8"))
        requests.append((request.full_url, payload))
        if request.full_url.endswith("/api/recalc/bulk"):
            return _StubResponse(
                {
                    "accepted_count": len(payload["instrument_ids"]),
                    "missing_instrument_ids": [],
                }
            )
        return _portfolio_ack("portfolio-1")

    monkeypatch.setattr(downstream_notifications, "get_settings", lambda: _StubSettings())
    monkeypatch.setattr(downstream_notifications, "urlopen", fake_urlopen)

    result = downstream_notifications.notify_market_data_downstream_refresh(
        instrument_ids=[" fund-a ", "fund-a", "fx-usdcny", ""],
        dirty_from=date(2026, 5, 1),
    )

    assert requests == [
        (
            "http://portfolio.local/api/portfolios/snapshots/daily/recalculations",
            {
                "instrument_ids": [],
                "dirty_from": "2026-05-01",
                "refresh_all": True,
            },
        ),
        (
            "http://watchlist.local/api/recalc/bulk",
            {
                "instrument_ids": ["fund-a"],
                "job_type": "all",
                "trigger_type": "market_data_refresh",
                "trigger_ref_type": "shared_market_data",
            },
        ),
    ]
    assert result.request_count == 2
    assert result.succeeded is True


def test_fx_refresh_only_notifies_portfolio(monkeypatch) -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        del timeout
        requests.append((request.full_url, json.loads(request.data.decode("utf-8"))))
        return _portfolio_ack("portfolio-1")

    monkeypatch.setattr(downstream_notifications, "get_settings", lambda: _StubSettings())
    monkeypatch.setattr(downstream_notifications, "urlopen", fake_urlopen)

    downstream_notifications.notify_market_data_downstream_refresh(
        dirty_from=date(2026, 5, 1),
        refresh_all_portfolios=True,
        refresh_watchlist=False,
    )

    assert requests == [
        (
            "http://portfolio.local/api/portfolios/snapshots/daily/recalculations",
            {
                "instrument_ids": [],
                "dirty_from": "2026-05-01",
                "refresh_all": True,
            },
        ),
    ]


def test_watchlist_bulk_acknowledgement_must_accept_every_instrument(monkeypatch) -> None:
    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        del timeout
        if request.full_url.endswith("/snapshots/daily/recalculations"):
            return _portfolio_ack("portfolio-1")
        return _StubResponse(
            {
                "accepted_count": 1,
                "missing_instrument_ids": ["fund-b"],
            }
        )

    monkeypatch.setattr(downstream_notifications, "get_settings", lambda: _StubSettings())
    monkeypatch.setattr(downstream_notifications, "urlopen", fake_urlopen)

    result = downstream_notifications.notify_market_data_downstream_refresh(
        instrument_ids=["fund-a", "fund-b"],
        refresh_all_portfolios=False,
    )

    assert result.succeeded is False
    assert "accepted only 1 of 2" in result.failures[0].message
    assert "fund-b" in result.failures[0].message


def test_portfolio_recalculation_acknowledgement_must_be_complete(monkeypatch) -> None:
    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        del timeout
        if request.full_url.endswith("/api/recalc/bulk"):
            return _StubResponse(
                {"accepted_count": 1, "missing_instrument_ids": []}
            )
        return _StubResponse(
            {
                "portfolio_ids": ["portfolio-1"],
                "accepted": [
                    {
                        "portfolio_id": "portfolio-1",
                        "status": "accepted",
                        "refresh_request_id": "",
                    }
                ],
            }
        )

    monkeypatch.setattr(downstream_notifications, "get_settings", lambda: _StubSettings())
    monkeypatch.setattr(downstream_notifications, "urlopen", fake_urlopen)

    result = downstream_notifications.notify_market_data_downstream_refresh(
        instrument_ids=["fund-a"],
    )

    assert result.succeeded is False
    assert "missing a portfolio or request id" in result.failures[0].message


def test_best_effort_notifications_report_failures_without_raising(monkeypatch) -> None:
    requests: list[tuple[str, float]] = []

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        requests.append((request.full_url, timeout))
        raise URLError("unavailable")

    monkeypatch.setattr(downstream_notifications, "get_settings", lambda: _StubSettings())
    monkeypatch.setattr(downstream_notifications, "urlopen", fake_urlopen)

    result = downstream_notifications.notify_market_data_downstream_refresh(
        instrument_ids=["fund-a"],
        request_timeout_seconds=17.0,
        watchlist_request_timeout_seconds=3.0,
    )

    assert requests == [
        (
            "http://portfolio.local/api/portfolios/snapshots/daily/recalculations",
            17.0,
        ),
        ("http://watchlist.local/api/recalc/bulk", 3.0),
    ]
    assert result.request_count == 2
    assert len(result.failures) == 2
    assert result.succeeded is False


def test_strict_notifications_attempt_every_request_then_raise(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        del timeout
        requested_urls.append(request.full_url)
        raise URLError("unavailable")

    monkeypatch.setattr(downstream_notifications, "get_settings", lambda: _StubSettings())
    monkeypatch.setattr(downstream_notifications, "urlopen", fake_urlopen)

    with pytest.raises(downstream_notifications.DownstreamRefreshError) as raised:
        downstream_notifications.notify_market_data_downstream_refresh(
            instrument_ids=["fund-a", "fund-b"],
            raise_on_error=True,
        )

    assert requested_urls == [
        "http://portfolio.local/api/portfolios/snapshots/daily/recalculations",
        "http://watchlist.local/api/recalc/bulk",
    ]
    assert raised.value.result.request_count == 2
    assert len(raised.value.result.failures) == 2


def test_notification_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        downstream_notifications.notify_market_data_downstream_refresh(
            instrument_ids=["fund-a"],
            request_timeout_seconds=0,
        )
    with pytest.raises(ValueError, match="watchlist_request_timeout_seconds"):
        downstream_notifications.notify_market_data_downstream_refresh(
            instrument_ids=["fund-a"],
            watchlist_request_timeout_seconds=0,
        )
