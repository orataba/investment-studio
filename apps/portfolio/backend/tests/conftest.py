from __future__ import annotations

import importlib
import json
import sys
from copy import deepcopy
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT_STR = str(BACKEND_ROOT)
if BACKEND_ROOT_STR in sys.path:
    sys.path.remove(BACKEND_ROOT_STR)
sys.path.insert(0, BACKEND_ROOT_STR)

from tests.store_fixture import TEST_PORTFOLIO_STORE

from portfolio_app.api.routes import transactions as transaction_routes
from portfolio_app.services import portfolio_store
from portfolio_ops_instrument_core import instrument_store as shared_store
from portfolio_ops_instrument_core.db_models import InstrumentRegistryBase


def _market_point(
    metric_family: str,
    quote_basis: str,
    as_of_date: str,
    value: str,
    currency: str,
) -> dict[str, object]:
    return {
        "metric_family": metric_family,
        "quote_basis": quote_basis,
        "as_of_date": as_of_date,
        "value": value,
        "currency": currency,
        "status": "complete",
    }


REGISTRY_INSTRUMENT_DETAILS = [
    {
        "instrument_id": "equity-us-abbv",
        "instrument_name": "AbbVie Inc",
        "instrument_type": "equity",
        "currency": "USD",
        "identifiers": [
            {"identifier_type": "ticker", "identifier_value": "ABBV", "is_primary": True},
            {"identifier_type": "isin", "identifier_value": "US00287Y1091", "is_primary": False},
        ],
        "quote_selection_policy": {
            "valuation": ["close"],
            "reference": ["close"],
            "total_return": ["adjusted_close"],
        },
        "market_data": [
            _market_point("price", "close", "2026-02-10", "206.47", "USD"),
            _market_point("price", "close", "2026-03-15", "210.20", "USD"),
            _market_point("price", "close", "2026-04-02", "208.00", "USD"),
            _market_point("price", "close", "2026-04-08", "207.18", "USD"),
            _market_point("price", "close", "2026-04-15", "206.47", "USD"),
            _market_point("price", "adjusted_close", "2026-02-10", "206.47", "USD"),
            _market_point("price", "adjusted_close", "2026-03-15", "210.20", "USD"),
            _market_point("price", "adjusted_close", "2026-04-02", "208.00", "USD"),
            _market_point("price", "adjusted_close", "2026-04-08", "207.18", "USD"),
            _market_point("price", "adjusted_close", "2026-04-15", "206.47", "USD"),
        ],
    },
    {
        "instrument_id": "fund-us-agg",
        "instrument_name": "iShares Core U.S. Aggregate Bond ETF",
        "instrument_type": "fund",
        "currency": "USD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "AGG", "is_primary": True}],
        "quote_selection_policy": {
            "valuation": ["close"],
            "reference": ["close"],
            "total_return": ["total_return_nav"],
        },
        "market_data": [
            _market_point("price", "close", "2026-03-05", "96.82", "USD"),
            _market_point("price", "close", "2026-03-28", "97.62", "USD"),
            _market_point("price", "close", "2026-04-14", "96.97", "USD"),
            _market_point("price", "close", "2026-04-15", "91.62", "USD"),
            _market_point("nav", "total_return_nav", "2026-03-05", "96.82", "USD"),
            _market_point("nav", "total_return_nav", "2026-03-28", "97.62", "USD"),
            _market_point("nav", "total_return_nav", "2026-04-14", "96.97", "USD"),
            _market_point("nav", "total_return_nav", "2026-04-15", "91.62", "USD"),
        ],
    },
    {
        "instrument_id": "fund-hk-2800",
        "instrument_name": "Tracker Fund of Hong Kong",
        "instrument_type": "fund",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "2800.HK", "is_primary": True}],
        "quote_selection_policy": {
            "valuation": ["close"],
            "reference": ["close"],
            "total_return": ["total_return_nav"],
        },
        "market_data": [
            _market_point("price", "close", "2026-03-04", "21.30", "HKD"),
            _market_point("price", "close", "2026-04-02", "21.05", "HKD"),
            _market_point("price", "close", "2026-04-15", "21.34", "HKD"),
            _market_point("nav", "total_return_nav", "2026-03-04", "21.30", "HKD"),
            _market_point("nav", "total_return_nav", "2026-04-02", "21.05", "HKD"),
            _market_point("nav", "total_return_nav", "2026-04-15", "21.34", "HKD"),
        ],
    },
    {
        "instrument_id": "fund-us-watch",
        "instrument_name": "Watchlist Fund",
        "instrument_type": "fund",
        "currency": "USD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "WATCH", "is_primary": True}],
        "quote_selection_policy": {
            "valuation": ["close"],
            "reference": ["close"],
            "total_return": ["total_return_nav"],
        },
        "market_data": [
            _market_point("price", "close", "2026-04-15", "100.00", "USD"),
            _market_point("nav", "total_return_nav", "2026-04-15", "100.00", "USD"),
        ],
    },
    {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD Spot",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            _market_point("fx", "spot", "2026-02-20", "7.80", "HKD"),
            _market_point("fx", "spot", "2026-02-25", "7.80", "HKD"),
            _market_point("fx", "spot", "2026-03-02", "7.80", "HKD"),
            _market_point("fx", "spot", "2026-03-04", "7.79", "HKD"),
            _market_point("fx", "spot", "2026-03-09", "7.79", "HKD"),
            _market_point("fx", "spot", "2026-03-14", "7.79", "HKD"),
            _market_point("fx", "spot", "2026-03-19", "7.79", "HKD"),
            _market_point("fx", "spot", "2026-03-24", "7.79", "HKD"),
            _market_point("fx", "spot", "2026-03-29", "7.79", "HKD"),
            _market_point("fx", "spot", "2026-04-02", "7.82", "HKD"),
            _market_point("fx", "spot", "2026-04-07", "7.82", "HKD"),
            _market_point("fx", "spot", "2026-04-12", "7.82", "HKD"),
            _market_point("fx", "spot", "2026-04-15", "7.80", "HKD"),
        ],
    },
    {
        "instrument_id": "fx-usd-cny",
        "instrument_name": "USD/CNY Spot",
        "instrument_type": "fx",
        "currency": "CNY",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDCNY", "is_primary": True}],
        "quote_selection_policy": {"valuation": ["spot"], "reference": ["spot"]},
        "market_data": [
            _market_point("fx", "spot", "2026-04-02", "7.29", "CNY"),
            _market_point("fx", "spot", "2026-04-07", "7.29", "CNY"),
            _market_point("fx", "spot", "2026-04-12", "7.29", "CNY"),
            _market_point("fx", "spot", "2026-04-15", "7.20", "CNY"),
        ],
    },
]


def _latest_market_data(detail: dict[str, object]) -> list[dict[str, object]]:
    latest_by_basis: dict[str, dict[str, object]] = {}
    for point in detail.get("market_data", []):
        if not isinstance(point, dict):
            continue
        basis = str(point.get("quote_basis") or "")
        if not basis:
            continue
        current = latest_by_basis.get(basis)
        if current is None or str(point.get("as_of_date") or "") >= str(current.get("as_of_date") or ""):
            latest_by_basis[basis] = point
    return [deepcopy(item) for item in latest_by_basis.values()]


REGISTRY_INSTRUMENTS = [
    {
        **{
            key: deepcopy(value)
            for key, value in detail.items()
            if key != "market_data"
        },
        "latest_market_data": _latest_market_data(detail),
    }
    for detail in REGISTRY_INSTRUMENT_DETAILS
]


def _run_alembic_upgrade(database_url: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _get_registry_instrument(instrument_id: str):
    return deepcopy(next((item for item in REGISTRY_INSTRUMENTS if item["instrument_id"] == instrument_id), None))


def _get_registry_instrument_detail(instrument_id: str):
    return deepcopy(next((item for item in REGISTRY_INSTRUMENT_DETAILS if item["instrument_id"] == instrument_id), None))


def _get_registry_instrument_details(instrument_ids):
    return {
        instrument_id: _get_registry_instrument_detail(instrument_id)
        for instrument_id in instrument_ids
    }


@pytest.fixture(autouse=True)
def isolated_portfolio_store(request, tmp_path, monkeypatch):
    if request.node.get_closest_marker("postgresql_integration") is not None:
        yield
        return

    database_path = tmp_path / "portfolio.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    research_outputs_root = tmp_path / "research_outputs"
    monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA", "")
    monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_RESEARCH_OUTPUTS_ROOT", str(research_outputs_root))

    from portfolio_app.core import settings as settings_module
    from portfolio_app.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()

    _run_alembic_upgrade(database_url)
    InstrumentRegistryBase.metadata.create_all(bind=session_module.get_engine())
    # Performance-kernel scenarios own their complete portfolio fixture.  They
    # must start from an empty ledger so their loader remains a fresh-database
    # bootstrap, rather than attempting to replace an initialized append-only
    # audit history.
    portfolio_fixture_mode = getattr(
        request.module,
        "PORTFOLIO_LEDGER_FIXTURE_MODE",
        "seeded",
    )
    if portfolio_fixture_mode == "seeded":
        portfolio_store.reset_store(deepcopy(TEST_PORTFOLIO_STORE))
    elif portfolio_fixture_mode != "empty":
        raise RuntimeError(
            "Unsupported PORTFOLIO_LEDGER_FIXTURE_MODE: "
            f"{portfolio_fixture_mode!r}."
        )
    shared_store.reset_store(
        session_module.get_session_factory(),
        {
            "registry_name": "Test Shared Instruments",
            "instruments": deepcopy(REGISTRY_INSTRUMENT_DETAILS),
        },
    )

    monkeypatch.setattr(transaction_routes, "get_registry_instrument", _get_registry_instrument)
    monkeypatch.setattr(transaction_routes, "list_registry_instruments", lambda: deepcopy(REGISTRY_INSTRUMENTS))
    yield

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()


@pytest.fixture
def client():
    import portfolio_app.main as main_module

    main_module = importlib.reload(main_module)
    with TestClient(main_module.app) as test_client:
        yield test_client
