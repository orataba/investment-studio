from __future__ import annotations

import importlib
import sys
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

TEST_SHARED_INSTRUMENTS = {
    "fund-us-agg": {
        "asset_id": "fund-us-agg",
        "asset_name": "iShares Core U.S. Aggregate Bond ETF",
        "asset_type": "fund",
        "currency": "USD",
        "identifiers": [
            {"identifier_type": "ticker", "identifier_value": "AGG", "is_primary": True},
        ],
        "market_data": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-04-15",
                "value": "96.8200",
                "currency": "USD",
                "status": "complete",
            }
        ],
        "lifecycle_state": {"status": "active"},
    },
    "sxv264": {
        "asset_id": "sxv264",
        "asset_name": "SXV264 Total Return Fund",
        "asset_type": "fund",
        "currency": "USD",
        "identifiers": [
            {"identifier_type": "ticker", "identifier_value": "SXV264", "is_primary": True},
        ],
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2025-12-31",
                "value": "97.500000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-03-14",
                "value": "99.000000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-04-07",
                "value": "100.000000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-04-14",
                "value": "101.236476",
                "currency": "USD",
                "status": "complete",
            },
        ],
        "lifecycle_state": {"status": "active"},
    },
    "savf63": {
        "asset_id": "savf63",
        "asset_name": "SAVF63 Short Duration Income Fund",
        "asset_type": "fund",
        "currency": "USD",
        "identifiers": [
            {"identifier_type": "ticker", "identifier_value": "SAVF63", "is_primary": True},
        ],
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-04-14",
                "value": "99.870000",
                "currency": "USD",
                "status": "complete",
            },
        ],
        "lifecycle_state": {"status": "active"},
    },
}


def _run_alembic_upgrade(database_url: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _shared_registry_fetch(path: str) -> dict[str, object]:
    from app.services.shared_instrument_registry import SharedInstrumentRegistryNotFoundError

    if path == "/api/instruments":
        return {"instruments": list(TEST_SHARED_INSTRUMENTS.values())}
    if path.startswith("/api/instruments/resolve?"):
        _, query = path.split("?", 1)
        parts = dict(item.split("=", 1) for item in query.split("&") if "=" in item)
        identifier_value = parts.get("identifier_value", "").upper()
        for item in TEST_SHARED_INSTRUMENTS.values():
            identifiers = item.get("identifiers", [])
            for identifier in identifiers:
                if str(identifier.get("identifier_value") or "").upper() == identifier_value:
                    return item
        raise SharedInstrumentRegistryNotFoundError()
    prefix = "/api/instruments/"
    if path.startswith(prefix):
        asset_id = path[len(prefix) :]
        record = TEST_SHARED_INSTRUMENTS.get(asset_id)
        if record is None:
            raise SharedInstrumentRegistryNotFoundError()
        return record
    raise RuntimeError(f"unsupported shared registry path: {path}")


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    database_path = tmp_path / "test.db"
    monkeypatch.setenv("FTV2_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setenv("FTV2_DATABASE_SCHEMA", "")
    monkeypatch.setenv("FTV2_EMAIL_SYNC_ENABLED", "false")

    from app.core import settings as settings_module
    from app.db import session as session_module
    from app.services import shared_instrument_registry as shared_registry_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()

    _run_alembic_upgrade(f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setattr(shared_registry_module, "_fetch_json", _shared_registry_fetch)

    import app.main as main_module

    main_module = importlib.reload(main_module)

    with TestClient(main_module.app) as test_client:
        yield test_client

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()
