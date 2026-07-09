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
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
INSTRUMENT_CORE_PYTHON = WORKSPACE_ROOT / "packages" / "instrument-core" / "python"
INSTRUMENT_CORE_PYTHON_STR = str(INSTRUMENT_CORE_PYTHON)
if INSTRUMENT_CORE_PYTHON_STR in sys.path:
    sys.path.remove(INSTRUMENT_CORE_PYTHON_STR)
sys.path.insert(0, INSTRUMENT_CORE_PYTHON_STR)

from portfolio_ops_instrument_core.db_models import InstrumentRegistryBase
from portfolio_ops_instrument_core import instrument_store as shared_store

TEST_SHARED_INSTRUMENTS = {
    "fund-us-agg": {
        "instrument_id": "fund-us-agg",
        "instrument_name": "iShares Core U.S. Aggregate Bond ETF",
        "instrument_type": "fund",
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
        "instrument_id": "sxv264",
        "instrument_name": "SXV264 Total Return Fund",
        "instrument_type": "fund",
        "currency": "USD",
        "identifiers": [
            {"identifier_type": "ticker", "identifier_value": "SXV264", "is_primary": True},
        ],
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "cumulative_nav",
                "as_of_date": "2025-12-31",
                "value": "97.500000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "cumulative_nav",
                "as_of_date": "2026-03-14",
                "value": "99.000000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "cumulative_nav",
                "as_of_date": "2026-04-07",
                "value": "100.000000",
                "currency": "USD",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "cumulative_nav",
                "as_of_date": "2026-04-14",
                "value": "101.236476",
                "currency": "USD",
                "status": "complete",
            },
        ],
        "lifecycle_state": {"status": "active"},
    },
    "savf63": {
        "instrument_id": "savf63",
        "instrument_name": "SAVF63 Short Duration Income Fund",
        "instrument_type": "fund",
        "currency": "USD",
        "identifiers": [
            {"identifier_type": "ticker", "identifier_value": "SAVF63", "is_primary": True},
        ],
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "cumulative_nav",
                "as_of_date": "2026-04-14",
                "value": "99.870000",
                "currency": "USD",
                "status": "complete",
            },
        ],
        "lifecycle_state": {"status": "active"},
    },
}


def seed_shared_instrument(instrument: dict[str, object]) -> None:
    from watchlist_app.db import session as session_module

    target_instrument_id = str(instrument["instrument_id"])
    existing_ids = [
        item["instrument_id"]
        for item in shared_store.list_instruments(
            session_module.get_session_factory(),
            include_inactive=True,
        )
    ]
    existing_instruments = [
        detail
        for instrument_id in existing_ids
        if str(instrument_id) != target_instrument_id
        if (detail := shared_store.get_instrument(session_module.get_session_factory(), instrument_id)) is not None
    ]
    shared_store.reset_store(
        session_module.get_session_factory(),
        {
            "registry_name": shared_store.instrument_registry_name(
                session_module.get_session_factory()
            ),
            "instruments": [*existing_instruments, instrument],
        },
    )


def _run_alembic_upgrade(database_url: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    database_path = tmp_path / "test.db"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", f"sqlite+pysqlite:///{database_path}")
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_EMAIL_SYNC_ENABLED", "false")
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_RECALC_WORKER_ENABLED", "false")
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DOCUMENT_STORAGE_ROOT", str(tmp_path / "documents"))

    from watchlist_app.core import settings as settings_module
    from watchlist_app.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()

    _run_alembic_upgrade(f"sqlite+pysqlite:///{database_path}")
    InstrumentRegistryBase.metadata.create_all(bind=session_module.get_engine())
    shared_store.reset_store(
        session_module.get_session_factory(),
        {
            "registry_name": shared_store.DEFAULT_REGISTRY_NAME,
            "instruments": list(TEST_SHARED_INSTRUMENTS.values()),
        },
    )

    import watchlist_app.main as main_module

    main_module = importlib.reload(main_module)

    with TestClient(main_module.app) as test_client:
        yield test_client

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()
