from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select

from portfolio_ops_instrument_core.db_models import RegistryMetadata

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
    list_quote_observation_revisions,
    restore_instrument,
    replace_nav_history,
    update_refresh_status,
    upsert_corporate_action_event,
    upsert_market_data,
    upsert_market_data_points,
    upsert_quote_selection_policy,
    upsert_source_settings,
)
from platform_app.services.market_data_ops import import_nav_file, preview_nav_import
from scripts.import_coverage_nav_from_folder import (
    _ensure_instrument as ensure_coverage_nav_instrument,
)

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
                    "source_ref": "test_fixture",
                    "status": "complete",
                }
            ],
            "quote_selection_policy": {
                "trading": ["par"],
                "valuation": ["par"],
                "total_return": [],
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
                    "source_ref": "test_fixture",
                    "status": "complete",
                }
            ],
            "quote_selection_policy": {
                "trading": ["last", "close", "official_nav"],
                "valuation": ["official_nav", "close", "last"],
                "total_return": ["total_return_nav", "adjusted_close"],
                "chart": [
                    "total_return_nav",
                    "adjusted_close",
                    "official_nav",
                    "close",
                ],
                "reference": ["official_nav", "close", "last"],
            },
        },
    ],
}


def _reset_test_store(data: dict[str, object] | None = None) -> None:
    instrument_store.shared_store.reset_store(
        instrument_store.get_session_factory(),
        data,
    )


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
SHARED_ASSET_MIGRATIONS_ROOT = WORKSPACE_ROOT / "infra" / "instrument_registry"


def _run_alembic_upgrade(database_url: str) -> None:
    config = Config(str(SHARED_ASSET_MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(SHARED_ASSET_MIGRATIONS_ROOT / "alembic")
    )
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
    monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_PLATFORM_DATABASE_SCHEMA", "")

    from platform_app.core import settings as settings_module
    from platform_app.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()

    from portfolio_ops_instrument_core.db_models import InstrumentRegistryBase

    InstrumentRegistryBase.metadata.create_all(bind=session_module.get_engine())
    _reset_test_store(
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


def test_corporate_action_upsert_deduplicates_provider_detection_and_issuer_confirmation(
    isolated_store: Path,
) -> None:
    detected = upsert_corporate_action_event(
        instrument_id="fund-us-agg",
        action_type="share_split",
        effective_date=date(2026, 7, 10),
        new_units="2",
        old_units="1",
        source="tushare:fund_adj",
        status="detected",
        provenance={"observed_factor_ratio": "2"},
    )
    assert detected is not None
    assert detected["status"] == "detected"

    confirmed = upsert_corporate_action_event(
        instrument_id="fund-us-agg",
        action_type="share_split",
        announcement_date=date(2026, 7, 6),
        record_date=date(2026, 7, 9),
        effective_date=date(2026, 7, 10),
        payable_date=date(2026, 7, 10),
        new_units="2",
        old_units="1",
        source="fund_manager_announcement",
        external_event_id="issuer-123",
        status="confirmed",
        quantity_rounding="truncate",
        provenance={"announcement_url": "https://issuer.example/split.pdf"},
    )
    assert confirmed is not None
    assert confirmed["status"] == "confirmed"
    assert confirmed["source"] == "fund_manager_announcement"
    assert confirmed["record_date"] == "2026-07-09"
    assert confirmed["quantity_rounding"] == "truncate"

    detail = get_instrument("fund-us-agg")
    assert detail is not None
    assert len(detail["corporate_actions"]) == 1
    observations = detail["corporate_actions"][0]["provenance"]["observations"]
    assert any(item["source"] == "fund_manager_announcement" for item in observations)


def test_reset_store_without_payload_initializes_empty_registry(
    isolated_store: Path,
) -> None:
    _reset_test_store()

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
    ticker_match = find_instrument_by_identifier(
        identifier_value="AGG", identifier_type="ticker"
    )
    internal_match = find_instrument_by_identifier(
        identifier_value="AGG", identifier_type="internal"
    )
    assert ticker_match is not None
    assert internal_match is not None
    assert ticker_match["instrument_id"] == "fund-us-agg"
    assert internal_match["instrument_id"] == "agg"


@pytest.mark.parametrize("instrument_type", ["equity", "etf", "index"])
def test_listed_security_with_only_adjusted_close_has_no_valuation_quote(
    isolated_store: Path,
    instrument_type: str,
) -> None:
    created = create_instrument(
        instrument_name=f"Adjusted-only {instrument_type}",
        instrument_type=instrument_type,
        currency="CNY",
        identifiers=[
            {
                "identifier_type": "ticker",
                "identifier_value": "600000.SH",
                "is_primary": True,
            }
        ],
    )
    upsert_market_data(
        instrument_id=created["instrument_id"],
        metric_family="price",
        quote_basis="adjusted_close",
        as_of_date=date(2026, 7, 10),
        value="12.34",
        currency="CNY",
        source_ref="pytest",
        status="complete",
    )

    detail = get_instrument(created["instrument_id"])
    assert detail is not None
    assert detail["quote_selection_policy"]["valuation"] == ["close", "last"]
    available_bases = {point["quote_basis"] for point in detail["market_data"]}
    assert not available_bases.intersection(
        detail["quote_selection_policy"]["valuation"]
    )


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


def test_create_rejects_blank_master_data_and_multiple_primary_identifiers(
    isolated_store: Path,
) -> None:
    with pytest.raises(ValueError, match="Instrument name must not be blank"):
        create_instrument(
            instrument_name="   ",
            instrument_type="fund",
            currency="USD",
            identifiers=[
                {
                    "identifier_type": "ticker",
                    "identifier_value": "NEW",
                    "is_primary": True,
                }
            ],
        )

    with pytest.raises(
        ValueError, match="Exactly one instrument identifier must be primary"
    ):
        create_instrument(
            instrument_name="Two Primary Identifiers",
            instrument_type="fund",
            currency="USD",
            identifiers=[
                {
                    "identifier_type": "ticker",
                    "identifier_value": "NEW",
                    "is_primary": True,
                },
                {
                    "identifier_type": "isin",
                    "identifier_value": "US0000000001",
                    "is_primary": True,
                },
            ],
        )


def test_list_instruments_filters_in_sql_semantics_and_returns_latest_points(
    isolated_store: Path,
) -> None:
    upsert_market_data(
        instrument_id="fund-us-agg",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 4, 16),
        value="97.0100",
        currency="USD",
        source_ref="pytest",
        status="complete",
    )

    records = list_instruments(search="agg", instrument_type="fund", limit=1)

    assert [record["instrument_id"] for record in records] == ["fund-us-agg"]
    assert len(records[0]["latest_market_data"]) == 1
    point = records[0]["latest_market_data"][0]
    assert {
        "metric_family": point["metric_family"],
        "quote_basis": point["quote_basis"],
        "as_of_date": point["as_of_date"],
        "value": point["value"],
        "currency": point["currency"],
        "source_ref": point["source_ref"],
        "status": point["status"],
    } == {
        "metric_family": "price",
        "quote_basis": "close",
        "as_of_date": "2026-04-16",
        "value": "97.01",
        "currency": "USD",
        "source_ref": "pytest",
        "status": "complete",
    }
    assert point["quote_series_id"]
    assert point["observation_id"]
    assert point["revision_id"]
    assert point["revision_number"] == 1
    assert point["ingested_at"] is not None
    assert point["payload_hash"].startswith("sha256:")
    assert "provider" not in point


def test_coverage_nav_import_ensure_instrument_upserts_shared_record(
    isolated_store: Path,
) -> None:
    ensure_coverage_nav_instrument(
        {
            "instrument_id": "fund-sbmm07",
            "instrument_name": "CTA Factor Composite 3",
            "instrument_type": "fund",
            "identifier_value": "SBMM07",
            "currency": "CNY",
        }
    )

    created = get_instrument("fund-sbmm07")
    assert created is not None
    assert created["instrument_name"] == "CTA Factor Composite 3"
    assert created["instrument_type"] == "fund"
    assert created["currency"] == "CNY"
    assert created["quote_selection_policy"]["valuation"][0] == "official_nav"
    assert (
        find_instrument_by_identifier(
            identifier_value="SBMM07", identifier_type="ticker"
        )["instrument_id"]
        == "fund-sbmm07"
    )

    ensure_coverage_nav_instrument(
        {
            "instrument_id": "fund-sbmm07",
            "instrument_name": "CTA Factor Composite 3 Updated",
            "instrument_type": "fund",
            "identifier_value": "SBMM07",
            "currency": "CNY",
        }
    )

    updated = get_instrument("fund-sbmm07")
    assert updated is not None
    assert updated["instrument_name"] == "CTA Factor Composite 3 Updated"


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
        source_ref="pytest",
        status="complete",
    )

    assert record is not None
    assert record["market_data_updated_at"] is not None
    assert any(
        point["as_of_date"] == "2026-04-16" and point["value"] == "97.01"
        for point in record["latest_market_data"]
    )


def test_market_data_batch_rejects_removed_provider_alias(
    isolated_store: Path,
) -> None:
    with pytest.raises(ValueError, match="provider was removed"):
        upsert_market_data_points(
            instrument_id="fund-us-agg",
            rows=[
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-04-16",
                    "value": "97.01",
                    "currency": "USD",
                    "provider": "removed-field",
                    "status": "complete",
                }
            ],
        )


def test_flat_store_rejects_removed_provider_alias(
    isolated_store: Path,
) -> None:
    payload = deepcopy(TEST_SHARED_STORE)
    point = payload["instruments"][0]["market_data"][0]
    point["provider"] = point.pop("source_ref")

    with pytest.raises(ValueError, match="provider was removed"):
        _reset_test_store(payload)


def test_flat_store_never_defaults_missing_instrument_currency_to_usd(
    isolated_store: Path,
) -> None:
    payload = deepcopy(TEST_SHARED_STORE)
    payload["instruments"][0].pop("currency")

    with pytest.raises(ValueError, match="explicit three-letter currency code"):
        _reset_test_store(payload)


def test_instrument_currency_requires_a_three_letter_code(
    isolated_store: Path,
) -> None:
    with pytest.raises(ValueError, match="explicit three-letter currency code"):
        create_instrument(
            instrument_name="Invalid Currency Instrument",
            instrument_type="fund",
            currency="US",
            identifiers=[
                {
                    "identifier_type": "ticker",
                    "identifier_value": "INVALIDCCY",
                    "is_primary": True,
                }
            ],
        )


def test_market_data_watermark_is_globally_monotonic_within_same_clock_tick(
    isolated_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        instrument_store.shared_store,
        "_utcnow_iso",
        lambda: "2099-01-01T00:00:00.000000Z",
    )

    first = upsert_market_data(
        instrument_id="fund-us-agg",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 4, 16),
        value="97.0100",
        currency="USD",
        source_ref="pytest",
        status="complete",
    )
    second = upsert_market_data(
        instrument_id="cash-usd",
        metric_family="price",
        quote_basis="par",
        as_of_date=date(2026, 4, 16),
        value="5300.0000",
        currency="USD",
        source_ref="pytest",
        status="complete",
    )

    assert first is not None
    assert second is not None
    assert first["market_data_updated_at"] == "2099-01-01T00:00:00.000000Z"
    assert second["market_data_updated_at"] == "2099-01-01T00:00:00.000001Z"


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
        source_ref="pytest",
        point_status="complete",
        refresh_status="ready",
        updated_by="pytest",
        message="nav import",
    )

    assert record is not None
    assert record["market_data_updated_at"] is not None
    assert any(
        point["quote_basis"] == "official_nav"
        and point["as_of_date"] == "2026-04-15"
        and point["value"] == "100.2"
        for point in record["latest_market_data"]
    )


def test_email_refresh_success_cursor_survives_failed_and_blocked_statuses(
    isolated_store: Path,
) -> None:
    upsert_source_settings(
        instrument_id="fund-us-agg",
        source_mode="email",
        source_email="nav@example.test",
        source_location="INBOX",
        source_api_profile=None,
        source_email_rules=[{}],
    )

    succeeded = update_refresh_status(
        instrument_id="fund-us-agg",
        status="no_match",
        message="mailbox scanned",
        updated_by="pytest",
        mode="email",
    )
    assert succeeded is not None
    successful_at = succeeded["refresh_status"]["requested_at"]
    assert succeeded["refresh_status"]["last_successful_requested_at"] == successful_at

    no_new_data = update_refresh_status(
        instrument_id="fund-us-agg",
        status="no_new_data",
        message="no newer NAV rows",
        updated_by="pytest",
        mode="email",
    )
    assert no_new_data is not None
    successful_at = no_new_data["refresh_status"]["requested_at"]
    assert (
        no_new_data["refresh_status"]["last_successful_requested_at"] == successful_at
    )

    failed = update_refresh_status(
        instrument_id="fund-us-agg",
        status="failed",
        message="timeout",
        updated_by="pytest",
        mode="email",
    )
    assert failed is not None
    assert failed["refresh_status"]["status"] == "failed"
    assert failed["refresh_status"]["last_successful_requested_at"] == successful_at

    blocked = update_refresh_status(
        instrument_id="fund-us-agg",
        status="blocked",
        message="configuration unavailable",
        updated_by="pytest",
        mode="email",
    )
    assert blocked is not None
    assert blocked["refresh_status"]["last_successful_requested_at"] == successful_at


def test_email_failure_promotes_legacy_current_success_to_persistent_cursor(
    isolated_store: Path,
) -> None:
    legacy_store = deepcopy(TEST_SHARED_STORE)
    target = legacy_store["instruments"][1]
    target["source_settings"] = {
        "source_mode": "email",
        "source_email": "nav@example.test",
        "source_location": "INBOX",
        "source_api_profile": "",
        "source_email_rules": [{}],
    }
    target["refresh_status"] = {
        "status": "imported",
        "message": "legacy success",
        "requested_at": "2026-07-09T13:00:00Z",
        "requested_by": "legacy",
        "mode": "email",
    }
    _reset_test_store(legacy_store)

    failed = update_refresh_status(
        instrument_id="fund-us-agg",
        status="failed",
        message="timeout",
        updated_by="pytest",
        mode="email",
    )

    assert failed is not None
    assert (
        failed["refresh_status"]["last_successful_requested_at"]
        == "2026-07-09T13:00:00Z"
    )


def test_replace_nav_history_advances_only_email_success_cursor(
    isolated_store: Path,
) -> None:
    manual = replace_nav_history(
        instrument_id="fund-us-agg",
        rows=[
            {
                "as_of_date": "2026-04-16",
                "nav": "100.3000",
                "nav_with_dividend": "100.8000",
                "currency": "USD",
            }
        ],
        source_ref="pytest",
        point_status="complete",
        refresh_status="imported",
        updated_by="pytest",
        message="manual import",
        mode="manual",
    )
    assert manual is not None
    assert manual["refresh_status"]["last_successful_requested_at"] is None
    manual_status = update_refresh_status(
        instrument_id="fund-us-agg",
        status="no_match",
        message="manual operation",
        updated_by="pytest",
        mode="manual",
    )
    assert manual_status is not None
    assert manual_status["refresh_status"]["last_successful_requested_at"] is None

    upsert_source_settings(
        instrument_id="fund-us-agg",
        source_mode="email",
        source_email="nav@example.test",
        source_location="INBOX",
        source_api_profile=None,
        source_email_rules=[{}],
    )
    email = replace_nav_history(
        instrument_id="fund-us-agg",
        rows=[
            {
                "as_of_date": "2026-04-17",
                "nav": "100.4000",
                "nav_with_dividend": "100.9000",
                "currency": "USD",
            }
        ],
        source_ref="pytest",
        point_status="complete",
        refresh_status="imported",
        updated_by="pytest",
        message="email import",
        mode="email",
    )
    assert email is not None
    assert (
        email["refresh_status"]["last_successful_requested_at"]
        == email["refresh_status"]["requested_at"]
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
        source_ref="pytest_file",
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


def test_quote_input_scale_is_revisioned_and_each_representation_is_idempotent(
    isolated_store: Path,
) -> None:
    before = get_instrument("fund-us-agg")
    assert before is not None
    before_watermark = before["market_data_updated_at"]
    before_revisions = list_quote_observation_revisions(
        instrument_id="fund-us-agg",
        as_of_date=date(2026, 4, 15),
    )
    assert len(before_revisions) == 1
    assert before_revisions[0]["value"] == Decimal("96.82")
    assert before_revisions[0]["ingested_at"] is not None
    assert before_revisions[0]["ingestion_time_state"] == "observed"

    scale_changed = upsert_market_data(
        instrument_id="fund-us-agg",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 4, 15),
        value="96.820000",
        currency="USD",
        source_ref="test_fixture",
        status="complete",
    )
    assert scale_changed is not None
    assert scale_changed["market_data_updated_at"] > before_watermark
    scale_revisions = list_quote_observation_revisions(
        instrument_id="fund-us-agg",
        as_of_date=date(2026, 4, 15),
    )
    assert [item["revision_number"] for item in scale_revisions] == [2, 1]
    assert [item["value_input_scale"] for item in scale_revisions] == [6, 4]
    assert {item["numeric_scale_state"] for item in scale_revisions} == {"declared"}
    assert scale_revisions[0]["value"] == scale_revisions[1]["value"]
    assert scale_revisions[0]["payload_hash"] != scale_revisions[1]["payload_hash"]

    idempotent = upsert_market_data(
        instrument_id="fund-us-agg",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 4, 15),
        value="96.820000",
        currency="USD",
        source_ref="test_fixture",
        status="complete",
    )
    assert idempotent is not None
    assert (
        len(
            list_quote_observation_revisions(
                instrument_id="fund-us-agg",
                as_of_date=date(2026, 4, 15),
            )
        )
        == 2
    )

    changed = upsert_market_data(
        instrument_id="fund-us-agg",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 4, 15),
        value="97.0100",
        currency="USD",
        source_ref="test_fixture",
        status="complete",
    )
    assert changed is not None
    assert changed["market_data_updated_at"] > str(before_watermark)
    revisions = list_quote_observation_revisions(
        instrument_id="fund-us-agg",
        as_of_date=date(2026, 4, 15),
    )
    assert [item["revision_number"] for item in revisions] == [3, 2, 1]
    assert len({item["quote_series_id"] for item in revisions}) == 1
    assert len({item["observation_id"] for item in revisions}) == 1
    assert revisions[0]["is_current"] is True
    assert revisions[0]["value"] == Decimal("97.01")
    assert revisions[0]["source_ref"] == "test_fixture"
    assert revisions[0]["source_published_at"] is None
    assert revisions[0]["ingested_at"] is not None
    assert revisions[1]["is_current"] is False
    assert revisions[1]["superseded_at"] is not None

    source_changed = upsert_market_data(
        instrument_id="fund-us-agg",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 4, 15),
        value="97.01",
        currency="USD",
        source_ref="corrected-source",
        status="complete",
    )
    assert source_changed is not None
    revisions = list_quote_observation_revisions(
        instrument_id="fund-us-agg",
        as_of_date=date(2026, 4, 15),
    )
    assert [item["revision_number"] for item in revisions] == [4, 3, 2, 1]
    assert revisions[0]["source_ref"] == "corrected-source"
    assert sum(1 for item in revisions if item["is_current"]) == 1

    published = upsert_market_data(
        instrument_id="fund-us-agg",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 4, 15),
        value="97.01",
        currency="USD",
        source_ref="corrected-source",
        status="complete",
        source_published_at=datetime(2026, 4, 16, 8, 30, tzinfo=UTC),
    )
    assert published is not None
    revisions = list_quote_observation_revisions(
        instrument_id="fund-us-agg",
        as_of_date=date(2026, 4, 15),
    )
    assert [item["revision_number"] for item in revisions] == [5, 4, 3, 2, 1]
    assert revisions[0]["source_published_at"] == "2026-04-16T08:30:00.000000Z"
    assert sum(1 for item in revisions if item["is_current"]) == 1


def test_late_observation_advances_series_instrument_and_global_watermarks(
    isolated_store: Path,
) -> None:
    before = get_instrument("fund-us-agg")
    assert before is not None
    before_watermark = str(before["market_data_updated_at"])

    changed = upsert_market_data(
        instrument_id="fund-us-agg",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2020, 1, 2),
        value="80",
        currency="USD",
        source_ref="late-source",
        status="complete",
    )
    assert changed is not None
    assert str(changed["market_data_updated_at"]) > before_watermark

    revisions = list_quote_observation_revisions(
        instrument_id="fund-us-agg",
        as_of_date=date(2020, 1, 2),
    )
    assert len(revisions) == 1
    series_id = revisions[0]["quote_series_id"]
    from platform_app.db.session import get_session_factory
    from portfolio_ops_instrument_core.db_models import QuoteSeries, RegistryMetadata

    with get_session_factory()() as session:
        series = session.get(QuoteSeries, series_id)
        metadata = session.get(RegistryMetadata, "shared")
        assert series is not None
        assert metadata is not None
        assert series.data_updated_at == changed["market_data_updated_at"]
        assert metadata.market_data_updated_at == changed["market_data_updated_at"]


def test_nav_replace_withdraws_hides_and_restores_logical_observation(
    isolated_store: Path,
) -> None:
    original_rows = [
        {
            "as_of_date": "2026-05-31",
            "nav": "100.0",
            "nav_with_dividend": "101.0",
            "currency": "USD",
        }
    ]
    replace_nav_history(
        instrument_id="fund-us-agg",
        rows=original_rows,
        source_ref="nav-source",
        point_status="complete",
        refresh_status="imported",
        updated_by="pytest",
        message="initial",
    )
    withdrawn = replace_nav_history(
        instrument_id="fund-us-agg",
        rows=[
            {
                "as_of_date": "2026-05-31",
                "nav": "100.00",
                "currency": "USD",
            }
        ],
        source_ref="nav-source",
        point_status="complete",
        refresh_status="imported",
        updated_by="pytest",
        message="withdraw total return",
    )
    assert withdrawn is not None
    assert not any(
        point["quote_basis"] == "total_return_nav"
        and point["as_of_date"] == "2026-05-31"
        for point in get_instrument("fund-us-agg")["market_data"]
    )
    history = [
        item
        for item in list_quote_observation_revisions(
            instrument_id="fund-us-agg",
            as_of_date=date(2026, 5, 31),
        )
        if item["quote_basis"] == "total_return_nav"
    ]
    assert [item["status"] for item in history] == ["withdrawn", "complete"]
    assert history[0]["value"] is None
    assert history[0]["is_current"] is True

    restored = replace_nav_history(
        instrument_id="fund-us-agg",
        rows=original_rows,
        source_ref="nav-source",
        point_status="complete",
        refresh_status="imported",
        updated_by="pytest",
        message="restore total return",
    )
    assert restored is not None
    restored_history = [
        item
        for item in list_quote_observation_revisions(
            instrument_id="fund-us-agg",
            as_of_date=date(2026, 5, 31),
        )
        if item["quote_basis"] == "total_return_nav"
    ]
    assert [item["status"] for item in restored_history] == [
        "complete",
        "withdrawn",
        "complete",
    ]
    assert restored_history[0]["revision_number"] == 3
    assert any(
        point["quote_basis"] == "total_return_nav"
        and point["as_of_date"] == "2026-05-31"
        and point["value"] == "101"
        for point in get_instrument("fund-us-agg")["market_data"]
    )


def test_full_nav_replace_withdraws_dates_missing_from_replacement(
    isolated_store: Path,
) -> None:
    replace_nav_history(
        instrument_id="fund-us-agg",
        rows=[
            {"as_of_date": "2026-05-30", "nav": "99", "currency": "USD"},
            {"as_of_date": "2026-05-31", "nav": "100", "currency": "USD"},
        ],
        source_ref="full-source",
        point_status="complete",
        refresh_status="imported",
        updated_by="pytest",
        message="initial full",
        replace_all=True,
    )
    replace_nav_history(
        instrument_id="fund-us-agg",
        rows=[
            {"as_of_date": "2026-05-31", "nav": "100", "currency": "USD"},
        ],
        source_ref="full-source",
        point_status="complete",
        refresh_status="imported",
        updated_by="pytest",
        message="second full",
        replace_all=True,
    )
    assert not any(
        point["quote_basis"] == "official_nav" and point["as_of_date"] == "2026-05-30"
        for point in get_instrument("fund-us-agg")["market_data"]
    )
    history = list_quote_observation_revisions(
        instrument_id="fund-us-agg",
        as_of_date=date(2026, 5, 30),
    )
    assert [item["status"] for item in history] == ["withdrawn", "complete"]


def test_partial_and_rejected_current_revisions_are_auditable_but_not_adopted(
    isolated_store: Path,
) -> None:
    point_date = date(2026, 6, 30)
    for status in ("complete", "partial", "rejected"):
        upsert_market_data(
            instrument_id="fund-us-agg",
            metric_family="price",
            quote_basis="close",
            as_of_date=point_date,
            value="101.00",
            currency="USD",
            source_ref="quality-source",
            status=status,
        )
    detail = get_instrument("fund-us-agg")
    assert detail is not None
    assert not any(
        point["as_of_date"] == point_date.isoformat()
        and point["quote_basis"] == "close"
        for point in detail["market_data"]
    )
    revisions = list_quote_observation_revisions(
        instrument_id="fund-us-agg",
        as_of_date=point_date,
    )
    assert [item["status"] for item in revisions] == [
        "rejected",
        "partial",
        "complete",
    ]
    assert [item["is_current"] for item in revisions] == [True, False, False]

    with pytest.raises(ValueError, match="unsupported quote revision status"):
        upsert_market_data(
            instrument_id="fund-us-agg",
            metric_family="price",
            quote_basis="close",
            as_of_date=date(2026, 7, 1),
            value="101",
            currency="USD",
            source_ref="quality-source",
            status="unavailable",
        )
    with pytest.raises(ValueError, match="only be created by replacement"):
        upsert_market_data_points(
            instrument_id="fund-us-agg",
            rows=[
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": date(2026, 7, 1),
                    "value": None,
                    "currency": "USD",
                    "source_ref": "quality-source",
                    "status": "withdrawn",
                }
            ],
        )


def test_latest_market_data_partitions_by_full_series_identity(
    isolated_store: Path,
) -> None:
    upsert_market_data(
        instrument_id="fund-us-agg",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 4, 16),
        value="700",
        currency="CNY",
        source_ref="cny-source",
        status="complete",
    )
    record = next(
        item for item in list_instruments() if item["instrument_id"] == "fund-us-agg"
    )
    close_points = [
        point
        for point in record["latest_market_data"]
        if point["quote_basis"] == "close"
    ]
    assert {(point["currency"], point["value"]) for point in close_points} == {
        ("USD", "96.82"),
        ("CNY", "700"),
    }
    assert len({point["quote_series_id"] for point in close_points}) == 2


def test_quote_revision_history_api_exposes_auditable_revisions(
    isolated_store: Path,
) -> None:
    from fastapi.testclient import TestClient
    from platform_app.main import app

    client = TestClient(app)
    response = client.get(
        "/api/instruments/fund-us-agg/quote-revisions",
        params={"as_of_date": "2026-04-15"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["instrument_id"] == "fund-us-agg"
    assert payload["limit"] == 500
    assert payload["truncated"] is False
    assert len(payload["revisions"]) == 1
    revision = payload["revisions"][0]
    assert revision["quote_series_id"]
    assert revision["observation_id"]
    assert revision["revision_id"]
    assert revision["revision_number"] == 1
    assert revision["status"] == "complete"
    assert revision["ingested_at"] is not None
    assert revision["ingestion_time_state"] == "observed"

    for value in ("97", "98"):
        upsert_market_data(
            instrument_id="fund-us-agg",
            metric_family="price",
            quote_basis="close",
            as_of_date=date(2026, 4, 15),
            value=value,
            currency="USD",
            source_ref="history-api",
            status="complete",
        )
    limited = client.get(
        "/api/instruments/fund-us-agg/quote-revisions",
        params={"as_of_date": "2026-04-15", "limit": 2},
    )
    assert limited.status_code == 200
    limited_payload = limited.json()
    assert limited_payload["limit"] == 2
    assert limited_payload["truncated"] is True
    assert [
        revision["revision_number"] for revision in limited_payload["revisions"]
    ] == [3, 2]

    missing = client.get("/api/instruments/missing/quote-revisions")
    assert missing.status_code == 404


def test_market_write_apis_reject_json_numbers_and_preserve_string_scale(
    isolated_store: Path,
) -> None:
    from fastapi.testclient import TestClient
    from platform_app.main import app

    client = TestClient(app)
    market_payload = {
        "metric_family": "nav",
        "quote_basis": "official_nav",
        "as_of_date": "2026-04-16",
        "currency": "USD",
        "source_ref": "api-scale-test",
        "status": "complete",
    }
    assert (
        client.post(
            "/api/instruments/fund-us-agg/market-data",
            json={**market_payload, "value": 1.23},
        ).status_code
        == 422
    )
    accepted_market = client.post(
        "/api/instruments/fund-us-agg/market-data",
        json={**market_payload, "value": "1.2300"},
    )
    assert accepted_market.status_code == 200
    market_revision = list_quote_observation_revisions(
        instrument_id="fund-us-agg",
        as_of_date=date(2026, 4, 16),
    )[0]
    assert market_revision["value"] == Decimal("1.23")
    assert market_revision["value_input_scale"] == 4
    assert market_revision["numeric_scale_state"] == "declared"

    fx_instrument = create_instrument(
        instrument_name="USD/HKD Spot",
        instrument_type="fx",
        currency="HKD",
        identifiers=[
            {
                "identifier_type": "internal",
                "identifier_value": "fx-usd-hkd",
                "is_primary": True,
            }
        ],
        quote_selection_policy={
            "trading": ["spot"],
            "valuation": ["spot"],
            "total_return": [],
            "chart": ["spot"],
            "reference": ["spot"],
        },
    )
    assert fx_instrument["instrument_id"] == "fx-usd-hkd"
    fx_payload = {
        "base_currency": "USD",
        "quote_currency": "HKD",
        "as_of_date": "2026-04-16",
        "source_ref": "api-scale-test",
        "status": "complete",
    }
    assert (
        client.post(
            "/api/fx-rates",
            json={**fx_payload, "rate": 7.8},
        ).status_code
        == 422
    )
    accepted_fx = client.post(
        "/api/fx-rates",
        json={**fx_payload, "rate": "7.8000"},
    )
    assert accepted_fx.status_code == 200
    fx_revision = list_quote_observation_revisions(
        instrument_id="fx-usd-hkd",
        as_of_date=date(2026, 4, 16),
    )[0]
    assert fx_revision["value"] == Decimal("7.8")
    assert fx_revision["value_input_scale"] == 4
    assert fx_revision["numeric_scale_state"] == "declared"


def test_bulk_market_data_normalization_is_all_or_nothing(
    isolated_store: Path,
) -> None:
    before = get_instrument("fund-us-agg")
    assert before is not None

    with pytest.raises(ValueError, match="invalid canonical quote identity"):
        upsert_market_data_points(
            instrument_id="fund-us-agg",
            rows=[
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-04-16",
                    "value": "97",
                    "currency": "USD",
                    "source_ref": "batch",
                    "status": "complete",
                },
                {
                    "metric_family": "nav",
                    "quote_basis": "close",
                    "as_of_date": "2026-04-17",
                    "value": "98",
                    "currency": "USD",
                    "source_ref": "batch",
                    "status": "complete",
                },
            ],
        )
    with pytest.raises(ValueError, match="duplicate canonical observation"):
        upsert_market_data_points(
            instrument_id="fund-us-agg",
            rows=[
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-04-16",
                    "value": "97",
                    "currency": "USD",
                    "source_ref": "a",
                    "status": "complete",
                },
                {
                    "metric_family": "PRICE",
                    "quote_basis": "CLOSE",
                    "as_of_date": "2026-04-16",
                    "value": "98",
                    "currency": "usd",
                    "source_ref": "b",
                    "status": "complete",
                },
            ],
        )
    with pytest.raises(ValueError, match="at least one row"):
        upsert_market_data_points(instrument_id="fund-us-agg", rows=[])

    after = get_instrument("fund-us-agg")
    assert after is not None
    assert after["market_data"] == before["market_data"]
    assert after["market_data_updated_at"] == before["market_data_updated_at"]


def test_full_history_replace_invalid_batch_never_withdraws_existing_points(
    isolated_store: Path,
) -> None:
    seeded = replace_nav_history(
        instrument_id="fund-us-agg",
        rows=[
            {"as_of_date": "2026-04-14", "nav": "100", "currency": "USD"},
            {"as_of_date": "2026-04-15", "nav": "101", "currency": "USD"},
        ],
        source_ref="seed",
        point_status="complete",
        refresh_status="imported",
        updated_by="pytest",
        message="seed",
        replace_all=True,
    )
    assert seeded is not None
    before_revisions = list_quote_observation_revisions(
        instrument_id="fund-us-agg",
        limit=500,
    )

    with pytest.raises(ValueError, match="invalid as_of_date"):
        replace_nav_history(
            instrument_id="fund-us-agg",
            rows=[
                {"as_of_date": "2026-04-16", "nav": "102", "currency": "USD"},
                {"as_of_date": "not-a-date", "nav": "103", "currency": "USD"},
            ],
            source_ref="invalid-replace",
            point_status="complete",
            refresh_status="imported",
            updated_by="pytest",
            message="must fail",
            replace_all=True,
        )
    with pytest.raises(ValueError, match="at least one row"):
        replace_nav_history(
            instrument_id="fund-us-agg",
            rows=[],
            source_ref="empty-replace",
            point_status="complete",
            refresh_status="imported",
            updated_by="pytest",
            message="must fail",
            replace_all=True,
        )

    after_revisions = list_quote_observation_revisions(
        instrument_id="fund-us-agg",
        limit=500,
    )
    assert after_revisions == before_revisions


def test_policy_change_advances_global_watermark_and_noop_does_not(
    isolated_store: Path,
) -> None:
    before = get_instrument("fund-us-agg")
    assert before is not None
    policy = dict(before["quote_selection_policy"])
    with instrument_store.get_session_factory()() as session:
        global_before = session.scalar(
            select(RegistryMetadata.market_data_updated_at).where(
                RegistryMetadata.registry_key == "shared"
            )
        )

    no_op = upsert_quote_selection_policy(
        instrument_id="fund-us-agg",
        quote_selection_policy=policy,
    )
    assert no_op is not None
    assert (
        no_op["quote_selection_policy_revision"]
        == before["quote_selection_policy_revision"]
    )
    assert no_op["market_data_updated_at"] == before["market_data_updated_at"]
    with instrument_store.get_session_factory()() as session:
        assert (
            session.scalar(
                select(RegistryMetadata.market_data_updated_at).where(
                    RegistryMetadata.registry_key == "shared"
                )
            )
            == global_before
        )

    changed_policy = dict(policy)
    changed_policy["chart"] = ["close", "official_nav"]
    changed = upsert_quote_selection_policy(
        instrument_id="fund-us-agg",
        quote_selection_policy=changed_policy,
    )
    assert changed is not None
    assert (
        changed["quote_selection_policy_revision"]
        != before["quote_selection_policy_revision"]
    )
    assert changed["market_data_updated_at"] > before["market_data_updated_at"]
    with instrument_store.get_session_factory()() as session:
        assert (
            session.scalar(
                select(RegistryMetadata.market_data_updated_at).where(
                    RegistryMetadata.registry_key == "shared"
                )
            )
            == changed["market_data_updated_at"]
        )


def test_platform_quote_resolver_api_returns_revisioned_source_ref_only(
    isolated_store: Path,
) -> None:
    from fastapi.testclient import TestClient
    from platform_app.main import app

    client = TestClient(app)
    response = client.post(
        "/api/quotes/resolve-explicit",
        json={
            "resolver_strategy_version": "canonical_quote_resolver.v1",
            "instrument_id": "fund-us-agg",
            "metric_family": "price",
            "quote_basis": "close",
            "currency": "USD",
            "requested_as_of_date": "2026-04-15",
            "freshness_policy": {
                "policy_version": "canonical_quote_freshness.v1",
                "mode": "exact_only",
                "max_age_days": 0,
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["resolution_status"] == "resolved"
    assert payload["value"] == "96.82"
    assert payload["source_ref"] == "test_fixture"
    assert "provider" not in payload
    assert payload["revision_id"]
    assert payload["calculation_dependency"]["fingerprint"].startswith("sha256:")

    unknown_version = client.post(
        "/api/quotes/resolve-role",
        json={
            "resolver_strategy_version": "canonical_quote_resolver.v2",
            "quote_selection_policy_version": "quote_selection_policy.v1",
            "instrument_id": "fund-us-agg",
            "role": "valuation",
            "currency": "USD",
            "requested_as_of_date": "2026-04-15",
            "freshness_policy": {
                "policy_version": "canonical_quote_freshness.v1",
                "mode": "exact_only",
                "max_age_days": 0,
            },
        },
    )
    assert unknown_version.status_code == 422
