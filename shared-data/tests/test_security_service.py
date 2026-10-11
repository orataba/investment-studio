from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from studio_data.services.securities import service


def test_sync_security_catalogs_uses_one_client_for_both_catalogs(monkeypatch) -> None:
    client = object()
    seen = []

    def sync(*, client):
        seen.append(client)
        return {"active_count": 2}

    monkeypatch.setattr(service, "FmpClient", lambda: client)
    monkeypatch.setattr(service, "sync_equity_catalog", sync)
    monkeypatch.setattr(service, "sync_etf_catalog", sync)
    preferences = []
    monkeypatch.setattr(service, "_sync_futu_catalog_if_authorized", lambda **kwargs: (
        preferences.append(kwargs) or {"status": "not_authorized"}
    ))

    result = service.sync_security_catalogs()

    assert result["active_count"] == 4
    assert seen == [client, client]
    assert preferences == [{"prefer_current": False}]


@pytest.mark.parametrize("prefer_current,has_snapshot", [(True, True), (True, False), (False, True)])
def test_only_release_preference_reuses_a_verified_current_futu_catalog(monkeypatch, prefer_current, has_snapshot):
    from studio_market.config import MarketSettings
    from studio_market.futu import catalog, oauth

    settings = object()
    monkeypatch.setattr(MarketSettings, "from_environment", lambda: settings)
    monkeypatch.setattr(oauth, "FutuOAuth", lambda *_: nullcontext(SimpleNamespace(status=lambda: {"authenticated": True})))
    calls = []
    retained = {"status": "ready", "active_count": 42, "reused": True, "batch_id": "verified-today"}

    def snapshot(actual_settings):
        assert actual_settings is settings
        calls.append("snapshot")
        return retained if has_snapshot else None

    def sync(actual_settings):
        assert actual_settings is settings
        calls.append("sync")
        return {"status": "ready", "active_count": 43}

    monkeypatch.setattr(catalog, "current_catalog_snapshot", snapshot)
    monkeypatch.setattr(catalog, "sync_catalog", sync)
    result = service._sync_futu_catalog_if_authorized(prefer_current=prefer_current)
    if prefer_current and has_snapshot:
        assert result == retained
        assert calls == ["snapshot"]
    else:
        assert result == {"status": "ready", "active_count": 43}
        assert calls == (["snapshot", "sync"] if prefer_current else ["sync"])


def test_release_catalog_sync_failure_is_not_hidden_by_retained_data(monkeypatch):
    from studio_market.config import MarketSettings
    from studio_market.futu import catalog, oauth
    from studio_market.futu.quotes import FutuQuoteError

    monkeypatch.setattr(MarketSettings, "from_environment", lambda: object())
    monkeypatch.setattr(oauth, "FutuOAuth", lambda *_: nullcontext(SimpleNamespace(status=lambda: {"authenticated": True})))
    monkeypatch.setattr(catalog, "current_catalog_snapshot", lambda *_: None)

    def fail_sync(*_):
        raise FutuQuoteError("Rate limited")

    monkeypatch.setattr(catalog, "sync_catalog", fail_sync)
    with pytest.raises(FutuQuoteError, match="Rate limited"):
        service._sync_futu_catalog_if_authorized(prefer_current=True)


def test_release_installer_requests_and_discloses_current_futu_catalog_reuse(monkeypatch, capsys):
    from scripts import refresh_release_catalogs

    calls = []
    monkeypatch.setattr("sys.argv", ["refresh_release_catalogs.py"])
    monkeypatch.setattr(refresh_release_catalogs, "sync_security_catalogs", lambda **kwargs: (
        calls.append(kwargs) or {"catalogs": {
            "equity": {"active_count": 3, "exchange_counts": {"XNYS": 3}},
            "etf": {"active_count": 2, "exchange_counts": {"XHKG": 2}},
            "futu": {"status": "ready", "reused": True},
        }}
    ))
    assert refresh_release_catalogs.main() == 0
    assert calls == [{"prefer_current_futu": True}]
    assert "reused today's complete catalog" in capsys.readouterr().out


def test_fmp_index_refresh_uses_the_index_contract(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        service,
        "get_instrument",
        lambda _instrument_id: {
            "instrument_id": "fmp-index",
            "instrument_type": "index",
            "source_settings": {"source_api_profile": "fmp"},
        },
    )

    def fake_refresh_fmp_eod(
        instrument_id: str,
        *,
        instrument_type: str,
        full_history: bool,
        store: object,
    ) -> dict[str, object]:
        captured.update(
            {
                "instrument_id": instrument_id,
                "instrument_type": instrument_type,
                "full_history": full_history,
                "store": store,
            }
        )
        return {"instrument_id": instrument_id}

    from studio_data.services.fmp import eod

    monkeypatch.setattr(eod, "refresh_fmp_eod", fake_refresh_fmp_eod)
    store = object()

    result = service.refresh_security_eod(
        "fmp-index",
        full_history=True,
        store=store,  # type: ignore[arg-type]
    )

    assert result == {"instrument_id": "fmp-index"}
    assert captured == {
        "instrument_id": "fmp-index",
        "instrument_type": "index",
        "full_history": True,
        "store": store,
    }


def test_tushare_index_refresh_uses_only_the_configured_primary_source(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        service,
        "get_instrument",
        lambda _instrument_id: {
            "instrument_id": "tushare-index",
            "instrument_type": "index",
            "source_settings": {"source_api_profile": "tushare"},
        },
    )

    from studio_data.services import market_data_ops

    def fake_refresh_market_data(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"instrument_id": "tushare-index"}

    monkeypatch.setattr(market_data_ops, "refresh_market_data", fake_refresh_market_data)

    result = service.refresh_security_eod("tushare-index", full_history=True)

    assert result == {"instrument_id": "tushare-index"}
    assert captured == {
        "instrument_id": "tushare-index",
        "updated_by": "tushare_index_sync",
        "full_history": True,
        "source": "tushare",
    }
