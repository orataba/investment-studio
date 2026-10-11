"""Isolated catalog -> owner registration -> canonical quote flow regressions."""
from datetime import UTC, date, datetime
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from investment_studio_instrument_core import instrument_store as core
from investment_studio_instrument_core import security_catalog
from investment_studio_instrument_core.db_models import InstrumentRegistryBase
from studio_data.commands import securities as security_commands
from studio_data.db import session as data_session
from studio_data.db.equity_models import FmpEquityCatalog
from studio_data.db.etf_models import FmpEtfCatalog
from studio_data.services import instrument_store, securities
from studio_data.services.etfs import catalog as etf_catalog
from studio_data.services.equities import catalog as equity_catalog
from studio_data.services.futu import eod
from studio_data.services.securities.contracts import SecurityMaterializeRequest as OwnerRequest
from studio_market.config import MarketSettings
from studio_market.futu import collect
from studio_market.numeric import NumericStore


AS_OF = date(2026, 9, 8)
OBSERVED = datetime(2026, 9, 9, 10, tzinfo=UTC)


@pytest.fixture
def integration(tmp_path, monkeypatch):
    # All three owners share one disposable database, matching the local search
    # projection boundary without opening the developer's configured database.
    url = f"sqlite:///{tmp_path / 'futu-catalog-registration.db'}"
    settings = MarketSettings(url, tmp_path / "market")
    engine = create_engine(url)
    InstrumentRegistryBase.metadata.create_all(engine)
    FmpEquityCatalog.__table__.create(engine)
    FmpEtfCatalog.__table__.create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    market = NumericStore(settings)
    market.create_schema_for_testing()
    for module in (data_session, instrument_store, etf_catalog, equity_catalog, eod):
        monkeypatch.setattr(module, "get_session_factory", lambda: sessions)
    monkeypatch.setattr(MarketSettings, "from_environment", lambda *args, **kwargs: settings)
    monkeypatch.setattr(eod, "_expected_session", lambda instrument, now: AS_OF)
    network = Mock(side_effect=AssertionError("This regression must not make live HTTP requests."))
    monkeypatch.setattr(requests.Session, "request", network)
    notifications = Mock()
    monkeypatch.setattr(security_commands, "notify_market_data_downstream_refresh", notifications)

    def source_capture(settings_arg, symbol, start, end, *, store, now):
        assert settings_arg.database_url == url
        assert end == AS_OF and start <= AS_OF
        assert now.tzinfo is not None
        candidate = store.latest("futu_security_catalog", symbols=[symbol], limit=1)["rows"][0]
        store.ingest("futu_security_directory", [[{
            "symbol": symbol, "stock_id": candidate["stock_id"],
            "stock_type": candidate["stock_type"], "listing_date": candidate["listing_date"],
        }]], source="futu:stock-basicinfo", observed_at=OBSERVED)
        events = [{"ex_div_date": "2026-09-02", "action_types": ["DIVIDEND"],
                   "per_cash_div": .1, "desc_sc": f"1股派股息0.1{candidate['currency']}"}]
        action_result = store.ingest("futu_corporate_actions", [[{
            "symbol": symbol, "stock_id": candidate["stock_id"], "stock_type": candidate["stock_type"],
            "listing_date": candidate["listing_date"], "coverage_start": candidate["listing_date"],
            "coverage_end": AS_OF.isoformat(), "coverage_status": "provider_reported",
            "coverage_basis": "provider_reported_since_listing", "events_json": json.dumps(events), "event_count": 1,
        }]], source=eod.ACTION_SOURCE, observed_at=OBSERVED)
        store.ingest("futu_raw_eod_daily", [[{
            "symbol": symbol, "stock_id": candidate["stock_id"], "date": AS_OF.isoformat(),
            "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0,
            "volume": 1000, "volume_unit": "shares", "ohlc_quality": "valid",
            "source": eod.PROVIDER, "ohlc_adjustment": "unadjusted",
            "adjusted_close_adjustment": "unavailable",
        }]], source=eod.PROVIDER, observed_at=OBSERVED,
              details={"corporate_actions_batch_id": action_result["batch_id"]})
        return {"status": "ready"}

    capture = Mock(side_effect=source_capture)
    monkeypatch.setattr(collect, "collect_history", capture)
    bridge_calls = []

    def owner_command(arguments, *, payload):
        # Replace only the process boundary: keep the real data-owner command,
        # local catalog lookup, materialization and canonical publication helpers.
        bridge_calls.append((arguments, payload))
        assert arguments == ["add", "--input", "-", "--apply"]
        return security_commands.materialize_security_record(OwnerRequest.model_validate(payload)).model_dump(mode="json")

    monkeypatch.setattr(security_catalog, "_run_command", owner_command)
    yield SimpleNamespace(sessions=sessions, market=market, settings=settings,
                          capture=capture, network=network, notifications=notifications,
                          bridge_calls=bridge_calls)
    market.close()
    engine.dispose()


def seed_futu(integration, *, symbol="HK.02822", instrument_type="etf"):
    is_hk = symbol.startswith("HK.")
    row = {
        "symbol": symbol,
        "exchange_ticker": f"{int(symbol[3:]):04d}.HK" if is_hk else symbol[3:],
        "instrument_type": instrument_type, "stock_type": "ETF" if instrument_type == "etf" else "STOCK",
        "stock_id": 822 if is_hk else 1001, "name": "Verified local listing",
        "listing_date": "2026-09-01", "active": True,
        "exchange_code": "XHKG" if is_hk else "XNAS", "country": "HK" if is_hk else "US",
        "currency": "HKD" if is_hk else "USD", "currency_verified": True,
    }
    integration.market.ingest("futu_security_catalog", [[row]], source="fixture:verified-exchange+futu",
                              observed_at=OBSERVED)
    return row


def seed_fmp(integration, *, ticker="2822.HK", instrument_type="etf"):
    model = FmpEtfCatalog if instrument_type == "etf" else FmpEquityCatalog
    with integration.sessions() as session:
        session.add(model(fmp_symbol=ticker, exchange_ticker=ticker, company_name="Existing FMP listing",
                          exchange_code="XHKG", market="HK", currency="HKD", country="HK"))
        session.commit()


@pytest.mark.parametrize("symbol,instrument_type", [("HK.02822", "etf"), ("US.AAPL", "equity")])
def test_local_catalog_selection_automatically_registers_and_refreshes_quote_only(
    integration, symbol, instrument_type,
):
    catalog = seed_futu(integration, symbol=symbol, instrument_type=instrument_type)
    response = security_catalog.search_catalog(symbol, 10, session_factory=integration.sessions)
    selected = response.results[0]
    assert selected.catalog_provider == "futu"
    assert selected.catalog_symbol == symbol and selected.existing_instrument_id is None
    assert selected.currency_verified is True
    assert core.list_instruments(integration.sessions, limit=None) == []
    integration.capture.assert_not_called()
    integration.network.assert_not_called()

    result = security_catalog.materialize_catalog_security(security_catalog.SecurityMaterializeRequest(
        instrument_type=selected.instrument_type, catalog_provider=selected.catalog_provider,
        catalog_symbol=selected.catalog_symbol,
    ))
    assert len(integration.bridge_calls) == 1
    assert integration.bridge_calls[0][1]["refresh_eod"] is True
    iid = result["instrument_id"]
    registered = core.get_instrument(integration.sessions, iid)
    assert registered["source_settings"]["source_api_profile"] == "futu"
    assert registered["source_settings"]["source_provider_currency"] == catalog["currency"]
    assert registered["source_settings"]["market_calendar"] == catalog["exchange_code"]
    assert registered["source_settings"]["return_semantics"] == "price_return"
    assert {row["identifier_value"] for row in registered["identifiers"]} >= {
        catalog["exchange_ticker"], f"futu:{symbol}",
    }
    assert registered["quote_selection_policy"] == eod.CLOSE_POLICY
    assert registered["refresh_status"]["status"] == "partial"  # fixture retains only the latest price
    assert "Requested history starts" in registered["refresh_status"]["message"]
    assert {(point["quote_basis"], point["provider"]) for point in registered["market_data"]} == {
        ("close", eod.PROVIDER), ("adjusted_close", eod.RETURN_PROVIDER),
    }
    assert core.get_price_bars(integration.sessions, instrument_id=iid)[0]["adjustment_factor"] == "1"
    assert len(core.list_instruments(integration.sessions, limit=None)) == 1
    integration.capture.assert_called_once()
    integration.notifications.assert_called_once_with(instrument_ids=[iid], raise_on_error=True)
    integration.network.assert_not_called()

    existing = security_catalog.search_catalog(symbol, 10, session_factory=integration.sessions).results[0]
    assert existing.catalog_provider == "futu" and existing.existing_instrument_id == iid
    integration.capture.assert_called_once()  # searching again never fetches data


def test_fmp_remains_preferred_when_both_local_catalogs_cover_an_unregistered_listing(integration):
    seed_futu(integration)
    seed_fmp(integration)
    results = security_catalog.search_catalog("2822.HK", 10, session_factory=integration.sessions).results
    assert len(results) == 1
    assert results[0].catalog_provider == "fmp" and results[0].catalog_symbol == "2822.HK"
    assert results[0].existing_instrument_id is None
    integration.capture.assert_not_called()
    integration.network.assert_not_called()


def test_existing_futu_source_survives_later_fmp_directory_coverage_and_refresh(integration):
    seed_futu(integration)
    first = security_catalog.materialize_catalog_security(security_catalog.SecurityMaterializeRequest(
        instrument_type="etf", catalog_provider="futu", catalog_symbol="HK.02822",
    ))
    iid = first["instrument_id"]
    seed_fmp(integration)
    selected = security_catalog.search_catalog("2822.HK", 10, session_factory=integration.sessions).results[0]
    assert selected.catalog_provider == "futu" and selected.existing_instrument_id == iid
    again = security_catalog.materialize_catalog_security(security_catalog.SecurityMaterializeRequest(
        instrument_type=selected.instrument_type, catalog_provider=selected.catalog_provider,
        catalog_symbol=selected.catalog_symbol,
    ))
    assert again["instrument_id"] == iid
    assert again["source_settings"]["source_api_profile"] == "futu"
    assert len(core.list_instruments(integration.sessions, limit=None)) == 1
    # Exercise the generic business refresh path with retained numeric evidence.
    refreshed = securities.refresh_security_eod(iid, store=integration.market)
    assert refreshed["source_settings"]["source_api_profile"] == "futu"
    assert refreshed["refresh_status"]["status"] == "partial"
    assert all(point["provider"] in {eod.PROVIDER, eod.RETURN_PROVIDER}
               for point in core.get_instrument(integration.sessions, iid)["market_data"])
    assert integration.capture.call_count == 2
    integration.network.assert_not_called()


def test_existing_fmp_source_stays_selected_when_futu_catalog_is_added(integration):
    seed_fmp(integration)
    existing = securities.materialize_security("etf", "fmp", "2822.HK", refresh_eod=False, client=object())
    seed_futu(integration)
    selected = security_catalog.search_catalog("HK.02822", 10, session_factory=integration.sessions).results[0]
    assert selected.catalog_provider == "fmp"
    assert selected.existing_instrument_id == existing["instrument_id"]
    registered = core.get_instrument(integration.sessions, existing["instrument_id"])
    assert registered["source_settings"]["source_api_profile"] == "fmp"
    integration.capture.assert_not_called()
    integration.network.assert_not_called()
