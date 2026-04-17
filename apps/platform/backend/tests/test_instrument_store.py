from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest

from app.services import instrument_store
from app.services.instrument_store import (
    DEFAULT_REGISTRY_NAME,
    _normalize_store,
    archive_instrument,
    create_instrument,
    find_instrument_by_identifier,
    get_instrument,
    instrument_registry_name,
    list_instruments,
    restore_instrument,
    replace_nav_history,
    upsert_market_data,
)

TEST_SHARED_STORE = {
    "registry_name": DEFAULT_REGISTRY_NAME,
    "instruments": [
        {
            "asset_id": "cash-usd",
            "asset_name": "USD Cash",
            "asset_type": "cash",
            "currency": "USD",
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "identifier_value": "CASH",
                    "is_primary": True,
                }
            ],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "par",
                    "as_of_date": "2026-04-15",
                    "value": "1.0000",
                    "currency": "USD",
                    "provider": "test_fixture",
                    "status": "complete",
                }
            ],
            "quote_selection_policy": {
                "trading": ["par"],
                "valuation": ["par"],
                "total_return": ["par"],
                "chart": ["par"],
                "reference": ["par"],
            },
        },
        {
            "asset_id": "fund-us-agg",
            "asset_name": "iShares Core U.S. Aggregate Bond ETF",
            "asset_type": "fund",
            "currency": "USD",
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "identifier_value": "AGG",
                    "is_primary": True,
                }
            ],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-04-15",
                    "value": "96.8200",
                    "currency": "USD",
                    "provider": "test_fixture",
                    "status": "complete",
                }
            ],
            "quote_selection_policy": {
                "trading": ["last", "close", "official_nav"],
                "valuation": ["official_nav", "close", "last"],
                "total_return": ["total_return_nav", "adjusted_close", "official_nav", "close"],
                "chart": ["total_return_nav", "adjusted_close", "official_nav", "close"],
                "reference": ["official_nav", "close", "last"],
            },
        },
    ],
}


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _run_alembic_upgrade(database_url: str) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def test_normalize_store_does_not_reinsert_missing_default_instruments() -> None:
    first_default = TEST_SHARED_STORE["instruments"][0]
    second_default = TEST_SHARED_STORE["instruments"][1]

    normalized = _normalize_store(
        {
            "registry_name": "Custom Registry",
            "instruments": [second_default],
        }
    )

    assert normalized["registry_name"] == "Custom Registry"
    assert normalized["instruments"] == [second_default]
    assert all(
        instrument.get("asset_id") != first_default.get("asset_id")
        for instrument in normalized["instruments"]
    )


@pytest.fixture()
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database_path = tmp_path / "platform.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("YUNGU_PLATFORM_DATABASE_URL", database_url)
    monkeypatch.setenv("YUNGU_PLATFORM_DATABASE_SCHEMA", "")

    from app.core import settings as settings_module
    from app.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()

    _run_alembic_upgrade(database_url)
    instrument_store.reset_store(
        {
            "registry_name": TEST_SHARED_STORE["registry_name"],
            "instruments": deepcopy(TEST_SHARED_STORE["instruments"]),
        }
    )

    yield database_path

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()


def test_archive_restore_filters_default_shared_search(
    isolated_store: Path,
) -> None:
    archived = archive_instrument(asset_id="fund-us-agg", updated_by="pytest")
    assert archived is not None
    assert archived["lifecycle_state"]["status"] == "archived"
    assert archived["lifecycle_state"]["changed_by"] == "pytest"
    assert archived["lifecycle_state"]["changed_at"] is not None

    active_ids = {item["asset_id"] for item in list_instruments()}
    assert "fund-us-agg" not in active_ids

    all_records = {
        item["asset_id"]: item for item in list_instruments(include_inactive=True)
    }
    assert all_records["fund-us-agg"]["lifecycle_state"]["status"] == "archived"

    resolved_active_only = find_instrument_by_identifier(identifier_value="AGG")
    assert resolved_active_only is None

    resolved_including_inactive = find_instrument_by_identifier(
        identifier_value="AGG",
        include_inactive=True,
    )
    assert resolved_including_inactive is not None
    assert resolved_including_inactive["asset_id"] == "fund-us-agg"

    detail = get_instrument("fund-us-agg")
    assert detail is not None
    assert detail["lifecycle_state"]["status"] == "archived"

    restored = restore_instrument(asset_id="fund-us-agg", updated_by="pytest")
    assert restored is not None
    assert restored["lifecycle_state"]["status"] == "active"
    assert restored["lifecycle_state"]["changed_by"] == "pytest"

    restored_ids = {item["asset_id"] for item in list_instruments()}
    assert "fund-us-agg" in restored_ids


def test_reset_store_without_payload_initializes_empty_registry(
    isolated_store: Path,
) -> None:
    instrument_store.reset_store()

    assert instrument_registry_name() == DEFAULT_REGISTRY_NAME
    assert list_instruments() == []


def test_create_rejects_identifier_collision_with_archived_instrument(
    isolated_store: Path,
) -> None:
    archived = archive_instrument(asset_id="fund-us-agg", updated_by="pytest")
    assert archived is not None

    with pytest.raises(ValueError, match='Identifier "AGG" already belongs'):
        create_instrument(
            asset_name="Duplicate AGG",
            asset_type="fund",
            currency="USD",
            identifiers=[
                {
                    "identifier_type": "ticker",
                    "identifier_value": "AGG",
                    "is_primary": True,
                }
            ],
        )


def test_upsert_market_data_notifies_watchlist_recalc(
    isolated_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifications: list[dict[str, str | None]] = []

    monkeypatch.setattr(
        instrument_store,
        "schedule_watchlist_recalc",
        lambda **kwargs: notifications.append(kwargs),
    )

    record = upsert_market_data(
        asset_id="fund-us-agg",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 4, 16),
        value="97.0100",
        currency="USD",
        provider="pytest",
        status="complete",
    )

    assert record is not None
    assert notifications == [
        {
            "asset_id": "fund-us-agg",
            "trigger_ref_type": "instrument_market_data_upsert",
            "trigger_ref_id": "price:close:2026-04-16",
        }
    ]


def test_replace_nav_history_notifies_watchlist_recalc_with_latest_date(
    isolated_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notifications: list[dict[str, str | None]] = []

    monkeypatch.setattr(
        instrument_store,
        "schedule_watchlist_recalc",
        lambda **kwargs: notifications.append(kwargs),
    )

    record = replace_nav_history(
        asset_id="fund-us-agg",
        rows=[
            {
                "as_of_date": "2026-04-14",
                "nav": "100.0000",
                "nav_with_dividend": "100.5000",
                "currency": "USD",
            },
            {
                "as_of_date": "2026-04-15",
                "nav": "100.2000",
                "nav_with_dividend": "100.7000",
                "currency": "USD",
            },
        ],
        provider="pytest",
        point_status="complete",
        refresh_status="ready",
        updated_by="pytest",
        message="nav import",
    )

    assert record is not None
    assert notifications == [
        {
            "asset_id": "fund-us-agg",
            "trigger_ref_type": "instrument_nav_history_replace",
            "trigger_ref_id": "2026-04-15",
        }
    ]
