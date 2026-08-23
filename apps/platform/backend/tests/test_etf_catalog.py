from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest

from platform_app.services.etfs.catalog import search_etf_catalog, sync_etf_catalog
from platform_app.services.etfs.service import materialize_etf, search_etfs
from platform_app.services.fmp.exchanges import FMP_ETF_CATALOG_EXCHANGES
from platform_app.services.instrument_store import (
    create_instrument,
    get_price_bar_coverage,
    list_instruments,
)
from platform_app.services.securities import search_securities


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
REGISTRY_MIGRATIONS_ROOT = WORKSPACE_ROOT / "infra" / "instrument_registry"


def _upgrade(database_url: str, *, root: Path, revision: str = "head") -> None:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)


@pytest.fixture()
def isolated_etf_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'etfs.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA", "")
    monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_OPERATIONS_DATABASE_SCHEMA", "")

    from platform_app.core import settings as settings_module
    from platform_app.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()
    _upgrade(database_url, root=REGISTRY_MIGRATIONS_ROOT, revision="20260715_0011")
    _upgrade(database_url, root=BACKEND_ROOT)
    _upgrade(database_url, root=REGISTRY_MIGRATIONS_ROOT)

    yield

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()


_CATALOG_ROWS = {
    "NASDAQ": {"symbol": "QQQ", "companyName": "Invesco QQQ Trust"},
    "NYSE": {"symbol": "TESTN", "companyName": "NYSE Test ETF"},
    "AMEX": {"symbol": "GLDM", "companyName": "SPDR Gold MiniShares Trust"},
    "CBOE": {"symbol": "MAGS", "companyName": "Roundhill Magnificent Seven ETF"},
    "HKSE": {"symbol": "2800.HK", "companyName": "Tracker Fund of Hong Kong"},
    "SHH": {"symbol": "510300.SS", "companyName": "CSI 300 ETF"},
    "SHZ": {"symbol": "159919.SZ", "companyName": "CSI 300 ETF Shenzhen"},
    "LSE": {"symbol": "CSPX.L", "companyName": "iShares Core S&P 500 UCITS ETF"},
    "XETRA": {"symbol": "SXR8.DE", "companyName": "iShares Core S&P 500 UCITS ETF"},
    "PAR": {"symbol": "CW8.PA", "companyName": "Amundi MSCI World UCITS ETF"},
    "AMS": {"symbol": "VUSA.AS", "companyName": "Vanguard S&P 500 UCITS ETF"},
    "MIL": {"symbol": "SWDA.MI", "companyName": "iShares Core MSCI World UCITS ETF"},
    "SIX": {"symbol": "VWRL.SW", "companyName": "Vanguard FTSE All-World UCITS ETF"},
}


class FakeFmpClient:
    def __init__(self) -> None:
        self.catalog_calls: list[str] = []
        self.eod_calls: list[dict[str, object]] = []

    def active_etfs(self, exchange: str) -> list[dict[str, object]]:
        self.catalog_calls.append(exchange)
        return [
            {
                **deepcopy(_CATALOG_ROWS[exchange]),
                "exchangeShortName": "BATS" if exchange == "CBOE" else exchange,
                "exchange": (
                    "New York Stock Exchange Arca"
                    if exchange == "AMEX"
                    else None
                ),
                "isEtf": True,
                "isFund": False,
                "isActivelyTrading": True,
                "country": (
                    "CN"
                    if exchange in {"SHH", "SHZ"}
                    else "HK"
                    if exchange == "HKSE"
                    else "US"
                ),
            }
        ]

    def historical_eod(
        self,
        symbol: str,
        *,
        adjusted: bool,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, object]]:
        self.eod_calls.append(
            {
                "symbol": symbol,
                "adjusted": adjusted,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
        if adjusted:
            return [{"date": "2026-08-15", "adjClose": 67.1}]
        return [
            {
                "date": "2026-08-15",
                "open": 66,
                "high": 68,
                "low": 65,
                "close": 67,
                "volume": 123456,
            }
        ]

    def profile(self, symbol: str) -> dict[str, object]:
        exchange = next(
            exchange
            for exchange, row in _CATALOG_ROWS.items()
            if row["symbol"] == symbol
        )
        currency = "USD" if exchange == "LSE" else "CHF" if exchange == "SIX" else "EUR"
        return {
            "symbol": symbol,
            "exchangeShortName": exchange,
            "isEtf": True,
            "currency": currency,
        }


def test_etf_catalog_search_is_local_and_includes_cboe(
    isolated_etf_store: None,
) -> None:
    client = FakeFmpClient()
    summary = sync_etf_catalog(client=client)  # type: ignore[arg-type]

    assert client.catalog_calls == list(FMP_ETF_CATALOG_EXCHANGES)
    assert summary["active_count"] == 13
    assert search_etf_catalog("MAGS", limit=10)[0]["exchange_code"] == "BATS"
    assert search_etf_catalog("GLDM", limit=10)[0]["exchange_code"] == "ARCX"

    client.active_etfs = lambda exchange: pytest.fail(  # type: ignore[method-assign]
        f"local search unexpectedly called FMP for {exchange}"
    )
    assert search_etfs("MAGS", limit=10)[0] == {
        "instrument_type": "etf",
        "symbol": "MAGS",
        "catalog_provider": "fmp",
        "catalog_symbol": "MAGS",
        "name": "Roundhill Magnificent Seven ETF",
        "exchange_code": "BATS",
        "exchange_label": "Cboe BZX",
        "market": "US",
        "currency": "USD",
        "currency_verified": True,
        "country": "US",
        "sector": None,
        "industry": None,
        "existing_instrument_id": None,
    }


def test_etf_materialization_loads_history_then_refreshes_incrementally(
    isolated_etf_store: None,
) -> None:
    client = FakeFmpClient()
    sync_etf_catalog(client=client)  # type: ignore[arg-type]
    client.eod_calls.clear()

    first = materialize_etf("MAGS", client=client)  # type: ignore[arg-type]
    second = materialize_etf("MAGS", client=client)  # type: ignore[arg-type]

    assert second["instrument_id"] == first["instrument_id"]
    assert first["instrument_type"] == "etf"
    assert first["exchange_code"] == "BATS"
    assert first["source_settings"]["source_api_profile"] == "fmp"
    assert first["source_settings"]["market_calendar"] == "BATS"
    assert len(list_instruments(instrument_type="etf", limit=None)) == 1
    assert [call["adjusted"] for call in client.eod_calls] == [False, True, False, True]
    assert client.eod_calls[0]["start_date"] == "1900-01-01"
    expected_incremental_start = (date(2026, 8, 15) - timedelta(days=7)).isoformat()
    assert client.eod_calls[2]["start_date"] == expected_incremental_start
    coverage = get_price_bar_coverage(instrument_id=str(first["instrument_id"]))
    assert coverage["latest_date"] == "2026-08-15"
    assert coverage["adjustment_factor_count"] == 1


def test_nyse_arca_etf_keeps_its_canonical_listing_identity(
    isolated_etf_store: None,
) -> None:
    client = FakeFmpClient()
    sync_etf_catalog(client=client)  # type: ignore[arg-type]

    materialized = materialize_etf("GLDM", refresh_eod=False, client=client)  # type: ignore[arg-type]

    assert materialized["exchange_code"] == "ARCX"
    assert materialized["source_settings"]["market_calendar"] == "ARCX"
    assert search_etfs("GLDM", limit=10)[0]["exchange_label"] == "NYSE Arca"


def test_mainland_etf_materialization_uses_tushare_source_contract(
    isolated_etf_store: None,
) -> None:
    client = FakeFmpClient()
    sync_etf_catalog(client=client)  # type: ignore[arg-type]

    materialized = materialize_etf(
        "510300.SS",
        refresh_eod=False,
        client=client,  # type: ignore[arg-type]
    )

    assert materialized["exchange_code"] == "XSHG"
    assert materialized["source_settings"]["source_location"] == "DataHub Tushare"
    assert materialized["source_settings"]["source_api_profile"] == "tushare"
    assert any(
        identifier["identifier_value"] == "tushare:510300.SH"
        for identifier in materialized["identifiers"]
    )
    assert client.eod_calls == []


def test_existing_registry_etf_is_reused_and_gains_fmp_identity(
    isolated_etf_store: None,
) -> None:
    client = FakeFmpClient()
    sync_etf_catalog(client=client)  # type: ignore[arg-type]
    existing = create_instrument(
        instrument_name="Legacy MAGS",
        instrument_type="etf",
        currency="USD",
        exchange_code="BATS",
        identifiers=[
            {
                "identifier_type": "exchange_ticker",
                "identifier_value": "MAGS",
                "is_primary": True,
            },
        ],
    )

    materialized = materialize_etf("MAGS", client=client)  # type: ignore[arg-type]

    assert materialized["instrument_id"] == existing["instrument_id"]
    assert any(
        item["identifier_type"] == "provider_symbol"
        and item["identifier_value"] == "fmp:MAGS"
        for item in materialized["identifiers"]
    )
    assert materialized["source_settings"]["source_api_profile"] == "fmp"
    assert {str(call["symbol"]) for call in client.eod_calls} == {"MAGS"}


def test_xetra_etf_verifies_currency_and_keeps_listing_exchange(
    isolated_etf_store: None,
) -> None:
    client = FakeFmpClient()
    sync_etf_catalog(client=client)  # type: ignore[arg-type]

    search_result = search_etfs("SXR8", limit=10)[0]
    assert search_result["currency_verified"] is False

    materialized = materialize_etf("SXR8.DE", client=client)  # type: ignore[arg-type]

    assert materialized["instrument_type"] == "etf"
    assert materialized["exchange_code"] == "XETR"
    assert materialized["currency"] == "EUR"
    assert materialized["source_settings"]["source_provider_currency"] == "EUR"


def test_combined_search_keeps_etfs_available_when_stock_catalog_is_empty(
    isolated_etf_store: None,
) -> None:
    sync_etf_catalog(client=FakeFmpClient())  # type: ignore[arg-type]

    results, errors = search_securities("MAGS", limit=10)

    assert [item["instrument_type"] for item in results] == ["etf"]
    assert set(errors) == {"equity"}
