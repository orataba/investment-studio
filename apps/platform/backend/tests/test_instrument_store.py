from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest

from platform_app.services import instrument_store
from platform_app.services.instrument_store import (
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
from platform_app.services.market_data_ops import import_nav_file, preview_nav_import

TEST_SHARED_STORE = {
    "registry_name": DEFAULT_REGISTRY_NAME,
    "instruments": [
        {
            "instrument_id": "cash-usd",
            "instrument_name": "USD Cash",
            "instrument_type": "cash",
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
            "instrument_id": "fund-us-agg",
            "instrument_name": "iShares Core U.S. Aggregate Bond ETF",
            "instrument_type": "fund",
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
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
SHARED_ASSET_MIGRATIONS_ROOT = WORKSPACE_ROOT / "infra" / "instrument_registry"


def _run_alembic_upgrade(database_url: str) -> None:
    config = Config(str(SHARED_ASSET_MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SHARED_ASSET_MIGRATIONS_ROOT / "alembic"))
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
        instrument.get("instrument_id") != first_default.get("instrument_id")
        for instrument in normalized["instruments"]
    )


@pytest.fixture()
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database_path = tmp_path / "platform.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("YUNGU_PLATFORM_DATABASE_URL", database_url)
    monkeypatch.setenv("YUNGU_PLATFORM_DATABASE_SCHEMA", "")

    from platform_app.core import settings as settings_module
    from platform_app.db import session as session_module

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
    archived = archive_instrument(instrument_id="fund-us-agg", updated_by="pytest")
    assert archived is not None
    assert archived["lifecycle_state"]["status"] == "archived"
    assert archived["lifecycle_state"]["changed_by"] == "pytest"
    assert archived["lifecycle_state"]["changed_at"] is not None

    active_ids = {item["instrument_id"] for item in list_instruments()}
    assert "fund-us-agg" not in active_ids

    all_records = {
        item["instrument_id"]: item for item in list_instruments(include_inactive=True)
    }
    assert all_records["fund-us-agg"]["lifecycle_state"]["status"] == "archived"

    resolved_active_only = find_instrument_by_identifier(identifier_value="AGG")
    assert resolved_active_only is None

    resolved_including_inactive = find_instrument_by_identifier(
        identifier_value="AGG",
        include_inactive=True,
    )
    assert resolved_including_inactive is not None
    assert resolved_including_inactive["instrument_id"] == "fund-us-agg"

    detail = get_instrument("fund-us-agg")
    assert detail is not None
    assert detail["lifecycle_state"]["status"] == "archived"

    restored = restore_instrument(instrument_id="fund-us-agg", updated_by="pytest")
    assert restored is not None
    assert restored["lifecycle_state"]["status"] == "active"
    assert restored["lifecycle_state"]["changed_by"] == "pytest"

    restored_ids = {item["instrument_id"] for item in list_instruments()}
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
    archived = archive_instrument(instrument_id="fund-us-agg", updated_by="pytest")
    assert archived is not None

    with pytest.raises(ValueError, match='Identifier "ticker:AGG" already belongs'):
        create_instrument(
            instrument_name="Duplicate AGG",
            instrument_type="fund",
            currency="USD",
            identifiers=[
                {
                    "identifier_type": "ticker",
                    "identifier_value": "AGG",
                    "is_primary": True,
                }
            ],
        )


def test_create_allows_same_identifier_value_across_different_types(
    isolated_store: Path,
) -> None:
    created = create_instrument(
        instrument_name="Internal AGG Alias",
        instrument_type="fund",
        currency="USD",
        identifiers=[
            {
                "identifier_type": "internal",
                "identifier_value": "AGG",
                "is_primary": True,
            }
        ],
    )

    assert created["instrument_id"] == "agg"
    ticker_match = find_instrument_by_identifier(identifier_value="AGG", identifier_type="ticker")
    internal_match = find_instrument_by_identifier(identifier_value="AGG", identifier_type="internal")
    assert ticker_match is not None
    assert internal_match is not None
    assert ticker_match["instrument_id"] == "fund-us-agg"
    assert internal_match["instrument_id"] == "agg"


def test_create_rejects_same_identifier_type_value_in_request(
    isolated_store: Path,
) -> None:
    with pytest.raises(ValueError, match='Duplicate identifier "ticker:dup"'):
        create_instrument(
            instrument_name="Duplicate Request Identifier",
            instrument_type="fund",
            currency="USD",
            identifiers=[
                {
                    "identifier_type": "ticker",
                    "identifier_value": "DUP",
                    "is_primary": True,
                },
                {
                    "identifier_type": "ticker",
                    "identifier_value": "dup",
                    "is_primary": False,
                },
            ],
        )


def test_upsert_market_data_updates_shared_store_without_app_callbacks(
    isolated_store: Path,
) -> None:
    record = upsert_market_data(
        instrument_id="fund-us-agg",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 4, 16),
        value="97.0100",
        currency="USD",
        provider="pytest",
        status="complete",
    )

    assert record is not None
    assert any(
        point["as_of_date"] == "2026-04-16" and point["value"] == "97.0100"
        for point in record["latest_market_data"]
    )


def test_replace_nav_history_updates_shared_store_without_app_callbacks(
    isolated_store: Path,
) -> None:
    record = replace_nav_history(
        instrument_id="fund-us-agg",
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
    assert any(
        point["quote_basis"] == "official_nav"
        and point["as_of_date"] == "2026-04-15"
        and point["value"] == "100.2000"
        for point in record["latest_market_data"]
    )


def test_preview_nav_import_filters_rows_to_selected_instrument(
    isolated_store: Path,
) -> None:
    rows = preview_nav_import(
        instrument_id="fund-us-agg",
        raw_text=(
            "date,instrument_code,instrument_name,nav,nav_with_dividend,currency\n"
            "2026-04-15,AGG,iShares Core U.S. Aggregate Bond ETF,100.2,100.7,USD\n"
            "2026-04-15,SPY,SPDR S&P 500 ETF Trust,500.1,500.1,USD\n"
        ),
    )

    assert rows is not None
    assert len(rows) == 1
    assert rows[0]["instrument_code"] == "AGG"
    assert rows[0]["as_of_date"] == "2026-04-15"


def test_import_nav_file_accepts_csv_bytes(
    isolated_store: Path,
) -> None:
    record = import_nav_file(
        instrument_id="fund-us-agg",
        file_name="agg_nav.csv",
        file_bytes=(
            "date,instrument_code,instrument_name,nav,nav_with_dividend,currency\n"
            "2026-04-15,AGG,iShares Core U.S. Aggregate Bond ETF,100.2,100.7,USD\n"
            "2026-04-14,AGG,iShares Core U.S. Aggregate Bond ETF,100.0,100.5,USD\n"
        ).encode("utf-8"),
        provider="pytest_file",
        status="complete",
        updated_by="pytest",
    )

    assert record is not None
    assert any(
        point["quote_basis"] == "official_nav"
        and point["as_of_date"] == "2026-04-15"
        and point["value"] == "100.2"
        for point in record["latest_market_data"]
    )
