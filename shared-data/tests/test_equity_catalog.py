from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import func, select

from studio_data.db.equity_models import FmpEquityCatalog
from studio_data.db.session import get_session_factory
from studio_data.services.equities.catalog import (
    search_equity_catalog,
    sync_equity_catalog,
)
from studio_data.services.fmp.client import FmpClient
from studio_data.services.fmp.exchanges import FMP_EQUITY_CATALOG_EXCHANGES
from studio_data.services.equities.service import materialize_equity, search_equities
from studio_data.services.instrument_store import get_price_bar_coverage, list_instruments


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parent
REGISTRY_MIGRATIONS_ROOT = WORKSPACE_ROOT / "shared-data" / "instruments"


def _upgrade(database_url: str, *, root: Path, revision: str = "head") -> None:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)


@pytest.fixture()
def isolated_equity_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'equities.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA", "")
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA", "")

    from studio_data.core import settings as settings_module
    from studio_data.db import session as session_module

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
    "NASDAQ": {"symbol": "AAPL", "companyName": "Apple Inc."},
    "NYSE": {"symbol": "IBM", "companyName": "International Business Machines"},
    "AMEX": {"symbol": "AEMD", "companyName": "Aethlon Medical"},
    "HKSE": {"symbol": "0700.HK", "companyName": "Tencent Holdings"},
    "SHH": {"symbol": "600519.SS", "companyName": "Kweichow Moutai"},
    "SHZ": {"symbol": "000001.SZ", "companyName": "Ping An Bank"},
    "LSE": {"symbol": "SHEL.L", "companyName": "Shell plc"},
    "XETRA": {"symbol": "SAP.DE", "companyName": "SAP SE"},
    "PAR": {"symbol": "MC.PA", "companyName": "LVMH"},
    "AMS": {"symbol": "ASML.AS", "companyName": "ASML Holding"},
    "MIL": {"symbol": "ENI.MI", "companyName": "Eni S.p.A."},
    "SIX": {"symbol": "NESN.SW", "companyName": "Nestlé S.A."},
}


class FakeFmpClient:
    def __init__(self) -> None:
        self.catalog_calls: list[str] = []
        self.eod_calls: list[dict[str, object]] = []

    def active_equities(self, exchange: str) -> list[dict[str, object]]:
        self.catalog_calls.append(exchange)
        return [
            {
                **deepcopy(_CATALOG_ROWS[exchange]),
                "exchangeShortName": exchange,
                "isEtf": False,
                "isFund": False,
                "isActivelyTrading": True,
                "country": (
                    "CN"
                    if exchange in {"SHH", "SHZ"}
                    else "HK"
                    if exchange == "HKSE"
                    else "US"
                ),
                "sector": "Technology",
                "industry": "Software",
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
            return [{"date": "2026-08-15", "adjClose": 202.5}]
        return [
            {
                "date": "2026-08-15",
                "open": 200,
                "high": 204,
                "low": 199,
                "close": 203,
                "volume": 123456,
            }
        ]

    def profile(self, symbol: str) -> dict[str, object]:
        exchange = next(
            exchange
            for exchange, row in _CATALOG_ROWS.items()
            if row["symbol"] == symbol
        )
        return {
            "symbol": symbol,
            "exchangeShortName": exchange,
            "isEtf": False,
            "currency": "GBp" if exchange == "LSE" else "EUR" if exchange != "SIX" else "CHF",
        }


def test_catalog_sync_supports_local_search_without_fmp_round_trip(
    isolated_equity_store: None,
) -> None:
    client = FakeFmpClient()
    summary = sync_equity_catalog(client=client)

    assert client.catalog_calls == list(FMP_EQUITY_CATALOG_EXCHANGES)
    assert summary["active_count"] == 12
    assert search_equity_catalog("600519", limit=10)[0]["exchange_ticker"] == "600519.SH"

    client.active_equities = lambda exchange: pytest.fail(  # type: ignore[method-assign]
        f"local search unexpectedly called FMP for {exchange}"
    )
    result = search_equities("Tencent", limit=10)
    assert result == [
        {
            "instrument_type": "equity",
            "symbol": "0700.HK",
            "catalog_provider": "fmp",
            "catalog_symbol": "0700.HK",
            "name": "Tencent Holdings",
            "exchange_code": "XHKG",
            "exchange_label": "Hong Kong Exchange",
            "market": "HK",
            "currency": "HKD",
            "currency_verified": True,
            "country": "HK",
            "sector": "Technology",
            "industry": "Software",
            "existing_instrument_id": None,
        }
    ]


def test_catalog_sync_fetch_failure_preserves_previous_snapshot(
    isolated_equity_store: None,
) -> None:
    sync_equity_catalog(client=FakeFmpClient())

    class FailingClient(FakeFmpClient):
        def active_equities(self, exchange: str) -> list[dict[str, object]]:
            if exchange == "AMEX":
                raise RuntimeError("FMP unavailable")
            return super().active_equities(exchange)

    with pytest.raises(RuntimeError, match="FMP unavailable"):
        sync_equity_catalog(client=FailingClient())

    with get_session_factory()() as session:
        count = session.scalar(select(func.count()).select_from(FmpEquityCatalog))
    assert count == 12


def test_catalog_sync_empty_exchange_preserves_previous_snapshot(
    isolated_equity_store: None,
) -> None:
    sync_equity_catalog(client=FakeFmpClient())

    class EmptyExchangeClient(FakeFmpClient):
        def active_equities(self, exchange: str) -> list[dict[str, object]]:
            if exchange == "HKSE":
                return []
            return super().active_equities(exchange)

    with pytest.raises(RuntimeError, match="HKSE"):
        sync_equity_catalog(client=EmptyExchangeClient())

    with get_session_factory()() as session:
        count = session.scalar(select(func.count()).select_from(FmpEquityCatalog))
    assert count == 12


def test_catalog_sync_excludes_china_b_shares(
    isolated_equity_store: None,
) -> None:
    class MixedChinaShareClient(FakeFmpClient):
        def active_equities(self, exchange: str) -> list[dict[str, object]]:
            rows = super().active_equities(exchange)
            if exchange == "SHH":
                rows.append(
                    {
                        **rows[0],
                        "symbol": "900938.SS",
                        "companyName": "Shanghai B Share",
                    }
                )
            if exchange == "SHZ":
                rows.append(
                    {
                        **rows[0],
                        "symbol": "200553.SZ",
                        "companyName": "Shenzhen B Share",
                    }
                )
            return rows

    summary = sync_equity_catalog(client=MixedChinaShareClient())

    assert summary["active_count"] == 12
    assert search_equity_catalog("900938", limit=10) == []
    assert search_equity_catalog("200553", limit=10) == []


def test_first_materialization_loads_history_then_refreshes_incrementally(
    isolated_equity_store: None,
) -> None:
    client = FakeFmpClient()
    sync_equity_catalog(client=client)
    client.eod_calls.clear()

    first = materialize_equity("AAPL", client=client)
    second = materialize_equity("AAPL", client=client)

    assert second["instrument_id"] == first["instrument_id"]
    assert first["instrument_type"] == "equity"
    assert first["exchange_code"] == "XNAS"
    assert len(list_instruments(instrument_type="equity", limit=None)) == 1
    assert [call["adjusted"] for call in client.eod_calls] == [False, True, False, True]
    assert client.eod_calls[0]["start_date"] == "1900-01-01"
    expected_incremental_start = (date(2026, 8, 15) - timedelta(days=7)).isoformat()
    assert client.eod_calls[2]["start_date"] == expected_incremental_start
    coverage = get_price_bar_coverage(instrument_id=str(first["instrument_id"]))
    assert coverage["latest_date"] == "2026-08-15"
    assert coverage["adjustment_factor_count"] == 1


def test_mainland_equity_materialization_uses_fmp_source_contract(
    isolated_equity_store: None,
) -> None:
    client = FakeFmpClient()
    sync_equity_catalog(client=client)

    materialized = materialize_equity(
        "600519.SS",
        refresh_eod=False,
        client=client,
    )

    assert materialized["exchange_code"] == "XSHG"
    assert materialized["source_settings"]["source_location"] == "FMP API"
    assert materialized["source_settings"]["source_api_profile"] == "fmp"
    assert materialized["source_settings"]["return_semantics"] == "price_return"
    assert not any(
        str(identifier["identifier_value"]).startswith("tushare:")
        for identifier in materialized["identifiers"]
    )
    assert client.eod_calls == []


def test_london_equity_verifies_gbp_and_normalizes_pence_prices(
    isolated_equity_store: None,
) -> None:
    client = FakeFmpClient()
    sync_equity_catalog(client=client)

    search_result = search_equities("SHEL", limit=10)[0]
    assert search_result["currency"] == "GBP"
    assert search_result["currency_verified"] is False

    materialized = materialize_equity("SHEL.L", client=client)

    assert materialized["exchange_code"] == "XLON"
    assert materialized["currency"] == "GBP"
    assert materialized["source_settings"]["source_provider_currency"] == "GBp"
    assert materialized["source_settings"]["source_price_multiplier"] == "0.01"
    close = next(
        point
        for point in materialized["market_data"]
        if point["quote_basis"] == "close"
    )
    assert close["value"] == "2.03"


def test_fmp_eod_fetch_continues_before_a_full_5000_row_response() -> None:
    recent_start = date(2000, 1, 1)
    recent_rows = [
        {"date": (recent_start + timedelta(days=offset)).isoformat(), "close": 1}
        for offset in range(5000)
    ]
    older_rows = [
        {"date": "1999-12-30", "close": 1},
        {"date": "1999-12-31", "close": 1},
    ]

    class Response:
        def __init__(self, payload: list[dict[str, object]]) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, object]]:
            return self.payload

    class Session:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def get(self, _url: str, *, params: dict[str, object], timeout: int) -> Response:
            self.calls.append({"params": dict(params), "timeout": timeout})
            return Response(
                older_rows if params["to"] == "1999-12-31" else recent_rows
            )

    class Settings:
        fmp_api_url = "https://example.test/stable"
        fmp_timeout_seconds = 30

        @staticmethod
        def resolved_fmp_api_key() -> str:
            return "test-key"

    session = Session()
    client = FmpClient(settings=Settings(), session=session)  # type: ignore[arg-type]
    rows = client.historical_eod(
        "AAPL",
        adjusted=False,
        start_date="1900-01-01",
        end_date="2026-08-18",
    )

    assert len(rows) == 5002
    assert rows[0]["date"] == "1999-12-30"
    assert rows[-1]["date"] == (recent_start + timedelta(days=4999)).isoformat()
    assert [call["params"]["to"] for call in session.calls] == [
        "2026-08-18",
        "1999-12-31",
    ]
