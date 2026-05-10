from __future__ import annotations

import json
from datetime import date

from platform_app.services import downstream_notifications


class _StubSettings:
    portfolio_api_url = "http://portfolio.local"
    watchlist_api_url = "http://watchlist.local"


class _StubResponse:
    def __enter__(self) -> "_StubResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None

    def read(self) -> bytes:
        return b"{}"


def test_market_data_refresh_notifies_portfolio_and_watchlist(monkeypatch) -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        del timeout
        requests.append((request.full_url, json.loads(request.data.decode("utf-8"))))
        return _StubResponse()

    monkeypatch.setattr(downstream_notifications, "get_settings", lambda: _StubSettings())
    monkeypatch.setattr(downstream_notifications, "urlopen", fake_urlopen)

    downstream_notifications.notify_market_data_downstream_refresh(
        instrument_ids=[" fund-a ", "fund-a", "fx-usdcny", ""],
        dirty_from=date(2026, 5, 1),
    )

    assert requests == [
        (
            "http://portfolio.local/api/portfolios/snapshots/daily/refresh",
            {
                "instrument_ids": ["fund-a", "fx-usdcny"],
                "dirty_from": "2026-05-01",
                "refresh_all": False,
            },
        ),
        ("http://watchlist.local/api/recalc/instruments/fund-a/all", {}),
    ]


def test_fx_refresh_only_notifies_portfolio(monkeypatch) -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        del timeout
        requests.append((request.full_url, json.loads(request.data.decode("utf-8"))))
        return _StubResponse()

    monkeypatch.setattr(downstream_notifications, "get_settings", lambda: _StubSettings())
    monkeypatch.setattr(downstream_notifications, "urlopen", fake_urlopen)

    downstream_notifications.notify_market_data_downstream_refresh(
        dirty_from=date(2026, 5, 1),
        refresh_all_portfolios=True,
        refresh_watchlist=False,
    )

    assert requests == [
        (
            "http://portfolio.local/api/portfolios/snapshots/daily/refresh",
            {
                "instrument_ids": [],
                "dirty_from": "2026-05-01",
                "refresh_all": True,
            },
        ),
    ]
