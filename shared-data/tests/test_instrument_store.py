from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from investment_studio_instrument_core import (
    MarketDataPoint,
)
from investment_studio_instrument_core import instrument_store as shared_store
from investment_studio_instrument_core.db_models import InstrumentMarketData, InstrumentRegistryBase

from studio_data.services import instrument_store
from studio_data.services.instrument_store import (
    DEFAULT_REGISTRY_NAME,
    _normalize_store,
    archive_instrument,
    create_instrument,
    find_instrument_by_identifier,
    get_instrument,
    instrument_registry_name,
    list_instrument_ids_with_nav_history_before,
    list_instruments,
    list_stale_current_fund_nav_projections,
    restore_instrument,
    publish_fund_nav_history,
    update_refresh_status,
    upsert_corporate_action_event,
    upsert_market_data,
    upsert_market_data_points,
    upsert_source_settings,
)
from studio_data.services.market_data_ops import import_nav_file, preview_nav_import

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
                    "price_unit": "per_unit",
                    "price_scale": "1",
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
            "instrument_type": "public_fund",
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
                    "price_unit": "per_unit",
                    "price_scale": "1",
                    "provider": "test_fixture",
                    "status": "complete",
                }
            ],
            "quote_selection_policy": {
                "trading": ["last", "close", "official_nav"],
                "valuation": ["official_nav", "close", "last"],
                "total_return": ["total_return_nav"],
                "chart": ["total_return_nav"],
                "reference": ["official_nav", "close", "last"],
            },
        },
    ],
}


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parent
SHARED_ASSET_MIGRATIONS_ROOT = WORKSPACE_ROOT / "shared-data" / "instruments"
DATA_MIGRATIONS_ROOT = BACKEND_ROOT


def _run_alembic_upgrade(database_url: str, revision: str = "head") -> None:
    config = Config(str(SHARED_ASSET_MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SHARED_ASSET_MIGRATIONS_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)


def _run_platform_alembic_upgrade(database_url: str) -> None:
    config = Config(str(DATA_MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(DATA_MIGRATIONS_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _alembic_config(database_url: str) -> Config:
    config = Config(str(SHARED_ASSET_MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SHARED_ASSET_MIGRATIONS_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_market_data_point_contract_requires_persisted_price_identity() -> None:
    point = MarketDataPoint(
        instrument_id="equity-contract",
        metric_family="price",
        quote_basis="close",
        as_of_date=date(2026, 7, 15),
        value=Decimal("100"),
        currency="USD",
        price_unit="per_unit",
        price_scale=Decimal("1"),
        status="complete",
    )

    assert point.price_unit == "per_unit"
    assert point.price_scale == Decimal("1")

    with pytest.raises(ValueError, match="price_unit"):
        MarketDataPoint(
            instrument_id="missing-contract",
            metric_family="price",
            quote_basis="close",
            as_of_date=date(2026, 7, 15),
            value=Decimal("100"),
            currency="USD",
            status="complete",
        )

    with pytest.raises(ValueError, match='requires metric_family "price"'):
        MarketDataPoint(
            instrument_id="invalid-contract",
            metric_family="nav",
            quote_basis="close",
            as_of_date=date(2026, 7, 15),
            value=Decimal("100"),
            currency="USD",
            price_unit="per_unit",
            price_scale=Decimal("1"),
            status="complete",
        )


def test_market_data_point_requires_auditable_nav_lineage() -> None:
    with pytest.raises(ValueError, match="require nav_lineage"):
        MarketDataPoint(
            instrument_id="lineage-fund",
            metric_family="nav",
            quote_basis="official_nav",
            as_of_date=date(2026, 7, 15),
            value=Decimal("1.25"),
            currency="CNY",
            price_unit="per_unit",
            price_scale=Decimal("1"),
            status="complete",
        )

    derived = MarketDataPoint(
        instrument_id="lineage-fund",
        metric_family="nav",
        quote_basis="total_return_nav",
        as_of_date=date(2026, 7, 15),
        value=Decimal("1.30"),
        currency="CNY",
        price_unit="per_unit",
        price_scale=Decimal("1"),
        status="complete",
        nav_lineage={
            "kind": "derived_dividend_reinvestment",
            "method_version": "dividend_reinvestment/v1",
            "anchor_date": "2026-01-01",
            "evidence": {
                "factor_record_id": "factor-2026-06-30",
                "distribution_dates": ["2026-06-30"],
            },
        },
    )

    assert derived.nav_lineage is not None
    assert derived.nav_lineage.kind == "derived_dividend_reinvestment"

    with pytest.raises(ValueError, match="method_version and anchor_date"):
        MarketDataPoint(
            instrument_id="lineage-fund",
            metric_family="nav",
            quote_basis="total_return_nav",
            as_of_date=date(2026, 7, 15),
            value=Decimal("1.30"),
            currency="CNY",
            price_unit="per_unit",
            price_scale=Decimal("1"),
            status="complete",
            nav_lineage={
                "kind": "derived_dividend_reinvestment",
                "evidence": {"distribution_dates": ["2026-06-30"]},
            },
        )


@pytest.mark.parametrize(
    "retired_basis",
    [
        "cumulative_nav",
        "accumulated_nav",
        "cum_nav",
        "dividend_adjusted_nav",
        "reinvested_nav",
    ],
)
def test_retired_nav_quote_bases_are_not_part_of_the_shared_contract(
    retired_basis: str,
) -> None:
    with pytest.raises(ValueError):
        MarketDataPoint(
            instrument_id="lineage-fund",
            metric_family="nav",
            quote_basis=retired_basis,
            as_of_date=date(2026, 7, 15),
            value=Decimal("1.25"),
            currency="CNY",
            price_unit="per_unit",
            price_scale=Decimal("1"),
            status="complete",
            nav_lineage={
                "kind": "provider_explicit",
                "evidence": {"source_field": retired_basis},
            },
        )


def test_bond_is_not_a_shared_registry_instrument(
    isolated_store: Path,
) -> None:
    with pytest.raises(ValueError, match="Unsupported instrument_type"):
        create_instrument(
            instrument_name="Portfolio-local Bond",
            instrument_type="bond",
            currency="USD",
            identifiers=[
                {
                    "identifier_type": "internal",
                    "identifier_value": "PORTFOLIO-LOCAL-BOND",
                    "is_primary": True,
                }
            ],
        )


def test_market_data_price_contract_migration_backfills_and_is_reversible(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "price-contract-migration.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260712_0007")

    connection = sqlite3.connect(database_path)
    try:
        instruments = [
            ("fx-usd-cny", "Pre-contract FX", "fx", "CNY"),
            ("equity-pre-contract", "Pre-contract Equity", "equity", "USD"),
        ]
        for instrument_id, instrument_name, instrument_type, currency in instruments:
            connection.execute(
                """
                INSERT INTO instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json, market_data_updated_at
                ) VALUES (?, ?, ?, ?, '{}', '{}', '{}', '{}', NULL)
                """,
                (instrument_id, instrument_name, instrument_type, currency),
            )
        connection.executemany(
            """
            INSERT INTO instrument_market_data (
                instrument_id, metric_family, quote_basis, as_of_date,
                value, currency, provider, status
            ) VALUES (?, ?, ?, '2026-07-15', '1', ?, 'pre_contract', 'complete')
            """,
            [
                ("fx-usd-cny", "fx", "spot", "CNY"),
                ("equity-pre-contract", "price", "close", "USD"),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    command.upgrade(config, "20260715_0008")

    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            """
            SELECT instrument_id, quote_basis, price_unit, price_scale
            FROM instrument_market_data
            ORDER BY instrument_id, quote_basis
            """
        ).fetchall()
    finally:
        connection.close()

    assert [
        (instrument_id, quote_basis, price_unit, Decimal(str(price_scale)))
        for instrument_id, quote_basis, price_unit, price_scale in rows
    ] == [
        ("equity-pre-contract", "close", "per_unit", Decimal("1")),
        ("fx-usd-cny", "spot", "rate", Decimal("1")),
    ]

    command.downgrade(config, "20260712_0007")
    connection = sqlite3.connect(database_path)
    try:
        column_names = {
            row[1]
            for row in connection.execute("PRAGMA table_info(instrument_market_data)")
        }
    finally:
        connection.close()
    assert "price_unit" not in column_names
    assert "price_scale" not in column_names


def test_market_data_price_contract_migration_blocks_ambiguous_pre_contract_bond_prices(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "pre-contract-bond-price-contract-migration.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260712_0007")

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO instrument (
                instrument_id, instrument_name, instrument_type, currency,
                quote_selection_policy_json, source_settings_json,
                refresh_status_json, lifecycle_state_json, market_data_updated_at
            ) VALUES ('bond-pre-contract', 'Pre-contract Bond', 'bond', 'USD',
                      '{}', '{}', '{}', '{}', NULL)
            """
        )
        connection.execute(
            """
            INSERT INTO instrument_market_data (
                instrument_id, metric_family, quote_basis, as_of_date,
                value, currency, provider, status
            ) VALUES ('bond-pre-contract', 'price', 'dirty_price', '2026-07-15',
                      '98.5', 'USD', 'pre_contract', 'complete')
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="bond price row"):
        command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        column_names = {
            row[1]
            for row in connection.execute("PRAGMA table_info(instrument_market_data)")
        }
        pre_contract_row = connection.execute(
            """
            SELECT value, provider, status
            FROM instrument_market_data
            WHERE instrument_id = 'bond-pre-contract'
              AND metric_family = 'price'
              AND quote_basis = 'dirty_price'
            """
        ).fetchone()
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    finally:
        connection.close()

    assert "price_unit" not in column_names
    assert "price_scale" not in column_names
    assert pre_contract_row == ("98.5", "pre_contract", "complete")
    assert revision == ("20260712_0007",)


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
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA", "")

    from studio_data.core import settings as settings_module
    from studio_data.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()

    _run_alembic_upgrade(database_url, "20260715_0011")
    _run_platform_alembic_upgrade(database_url)
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


def test_raw_price_bars_are_idempotent_and_validate_ohlc(
    isolated_store: Path,
) -> None:
    created = create_instrument(
        instrument_name="Price Bar ETF",
        instrument_type="etf",
        currency="CNY",
        exchange_code="XSHG",
        identifiers=[
            {
                "identifier_type": "ticker",
                "identifier_value": "510999.SH",
                "is_primary": True,
            }
        ],
        quote_selection_policy={
            "trading": ["last", "close"],
            "valuation": ["close", "last"],
            "total_return": ["adjusted_close", "close", "last"],
            "chart": ["adjusted_close", "close", "last"],
            "reference": ["close", "last"],
        },
    )
    instrument_id = str(created["instrument_id"])
    assert instrument_store.get_price_bar_coverage(instrument_id=instrument_id) == {
        "row_count": 0,
        "first_date": None,
        "latest_date": None,
        "adjustment_factor_count": 0,
    }
    rows = [
        {
            "as_of_date": "2026-07-15",
            "open": "1.0100",
            "high": "1.0300",
            "low": "1.0000",
            "close": "1.0200",
            "previous_close": "1.0050",
            "volume": "123456",
            "turnover": "126000",
            "adjustment_factor": "1.25",
            "currency": "CNY",
            "volume_unit": "lot",
            "turnover_unit": "thousand_cny",
            "provider": "tushare:fund_daily+adjustment_factor",
            "status": "complete",
        }
    ]

    assert instrument_store.upsert_price_bars(
        instrument_id=instrument_id,
        rows=rows,
    ) == 1
    assert instrument_store.upsert_price_bars(
        instrument_id=instrument_id,
        rows=rows,
    ) == 0
    assert instrument_store.get_price_bars(instrument_id=instrument_id) == [
        {
            "date": "2026-07-15",
            "open": "1.01",
            "high": "1.03",
            "low": "1",
            "close": "1.02",
            "previous_close": "1.005",
            "volume": "123456",
            "turnover": "126000",
            "adjustment_factor": "1.25",
            "currency": "CNY",
            "volume_unit": "lot",
            "turnover_unit": "thousand_cny",
            "provider": "tushare:fund_daily+adjustment_factor",
            "status": "complete",
        }
    ]
    assert instrument_store.get_price_bar_coverage(instrument_id=instrument_id) == {
        "row_count": 1,
        "first_date": "2026-07-15",
        "latest_date": "2026-07-15",
        "adjustment_factor_count": 1,
    }

    with pytest.raises(ValueError, match="OHLC high/low ordering"):
        instrument_store.upsert_price_bars(
            instrument_id=instrument_id,
            rows=[
                {
                    **rows[0],
                    "high": "1.01",
                    "close": "1.02",
                }
            ],
        )


def _market_data_persistence_state(
    database_path: Path,
    instrument_id: str,
) -> tuple[int, str | None, str | None]:
    connection = sqlite3.connect(database_path)
    try:
        point_count = connection.execute(
            """
            SELECT count(*)
            FROM instrument_market_data
            WHERE instrument_id = ?
            """,
            (instrument_id,),
        ).fetchone()
        instrument_watermark = connection.execute(
            """
            SELECT market_data_updated_at
            FROM instrument
            WHERE instrument_id = ?
            """,
            (instrument_id,),
        ).fetchone()
        registry_watermark = connection.execute(
            """
            SELECT market_data_updated_at
            FROM registry_metadata
            WHERE registry_key = 'shared'
            """
        ).fetchone()
    finally:
        connection.close()

    assert point_count is not None
    assert instrument_watermark is not None
    assert registry_watermark is not None
    return point_count[0], instrument_watermark[0], registry_watermark[0]


def test_atomic_fund_nav_publication_keeps_unprovable_total_return_absent(
    isolated_store: Path,
) -> None:
    initial = get_instrument("fund-us-agg")
    assert initial is not None
    publication_args = {
        "instrument_id": "fund-us-agg",
        "rows": [
            {
                "as_of_date": "2026-07-15",
                "nav": "1.25",
                "nav_status": "complete",
                "nav_source_provider": "pytest-unit-nav",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "unit_nav"},
                },
                "currency": "USD",
            }
        ],
        "projection_run": {
            "source_observation_fingerprint": "a" * 64,
            "projection_kind": "event_derived",
            "projection_status": "unavailable",
            "method_version": "dividend_reinvestment/v2",
            "anchor_date": None,
            "source_provider": "pytest-unit-nav",
            "evidence": {
                "unavailable_reason": "reinvestment_evidence_not_observed",
                "source_row_count": 1,
                "published_total_return_dates": [],
                "missing_total_return_dates": ["2026-07-15"],
            },
            "created_by": "pytest",
        },
        "current_fund_nav_event_ids": [],
        "current_fund_nav_reinvestment_evidence_ids": [],
        "refresh_status": "imported",
        "updated_by": "pytest",
        "message": "Publish exact unit NAV without an unprovable total-return curve.",
    }

    first = publish_fund_nav_history(
        **publication_args,
        expected_market_data_updated_at=initial["market_data_updated_at"],
    )
    assert first is not None
    assert first["changed"] is True
    assert first["dirty_from"] == "2026-07-15"
    detail = first["record"]
    assert detail["current_fund_nav_projection_run_id"] == first[
        "published_projection_run_id"
    ]
    assert detail["fund_nav_projection_runs"][0]["projection_status"] == "unavailable"
    assert {
        point["quote_basis"]
        for point in detail["market_data"]
        if point["metric_family"] == "nav"
    } == {"official_nav"}
    assert detail["fund_nav_adjustment_factors"] == []

    assert list_stale_current_fund_nav_projections(
        method_version="fund_nav_reinvestment_projection/v3"
    ) == [
        {
            "instrument_id": "fund-us-agg",
            "method_version": "dividend_reinvestment/v2",
            "source_provider": "pytest-unit-nav",
        }
    ]
    assert list_stale_current_fund_nav_projections(
        method_version="dividend_reinvestment/v2"
    ) == []

    replay = publish_fund_nav_history(
        **publication_args,
        expected_market_data_updated_at=first["market_data_updated_at"],
    )
    assert replay is not None
    assert replay["changed"] is False
    assert replay["dirty_from"] is None
    assert replay["market_data_updated_at"] == first["market_data_updated_at"]


def test_instrument_summary_batch_is_bounded_and_does_not_load_histories(
    isolated_store: Path,
) -> None:
    records = instrument_store.get_instrument_summaries(
        ["fund-us-agg", "missing", "fund-us-agg"]
    )

    assert list(records) == ["fund-us-agg", "missing"]
    assert records["missing"] is None
    summary = records["fund-us-agg"]
    assert summary is not None
    assert summary["instrument_id"] == "fund-us-agg"
    assert summary["latest_market_data"]
    assert "market_data" not in summary
    assert "fund_nav_event_revisions" not in summary


def test_calculation_details_keep_history_without_hydrating_market_data_models(
    isolated_store: Path,
) -> None:
    from studio_data.db.session import get_session_factory

    factory = get_session_factory()
    expected = get_instrument("fund-us-agg")
    hydrated_market_data: list[InstrumentMarketData] = []

    def record_loaded(_session, instance) -> None:
        if isinstance(instance, InstrumentMarketData):
            hydrated_market_data.append(instance)

    event.listen(factory, "loaded_as_persistent", record_loaded)
    try:
        details = shared_store.get_instrument_details(
            factory,
            ["fund-us-agg", "missing", "fund-us-agg"],
            include_fund_nav_ledger=False,
        )
    finally:
        event.remove(factory, "loaded_as_persistent", record_loaded)

    assert list(details) == ["fund-us-agg", "missing"]
    assert details["missing"] is None
    assert details["fund-us-agg"] == expected
    assert hydrated_market_data == []


def test_detail_history_latest_and_source_remain_independently_mutable() -> None:
    source = deepcopy(TEST_SHARED_STORE["instruments"][1])
    source["market_data"] = [
        {
            "metric_family": "nav",
            "quote_basis": "official_nav",
            "as_of_date": "2026-04-15",
            "value": "1.25",
            "currency": "USD",
            "price_unit": "per_unit",
            "price_scale": "1",
            "provider": "test_fixture",
            "status": "complete",
            "nav_lineage": {
                "kind": "provider_explicit",
                "evidence": {"observations": [{"source": "provider_file"}]},
            },
        }
    ]
    detail = shared_store._serialize_detail_record(source)
    detail["market_data"][0]["nav_lineage"]["evidence"]["observations"][0]["source"] = "edited"

    assert detail["latest_market_data"][0]["nav_lineage"]["evidence"]["observations"][0]["source"] == "provider_file"
    assert source["market_data"][0]["nav_lineage"]["evidence"]["observations"][0]["source"] == "provider_file"
    detail["latest_market_data"][0]["value"] = "999"
    assert detail["market_data"][0]["value"] == "1.25"


def test_calculation_details_still_reject_corrupt_historical_observations() -> None:
    # A pre-existing invalid store must still fail at the read boundary. The
    # current migration's write triggers correctly prevent creating this state.
    engine = create_engine("sqlite+pysqlite:///:memory:")
    InstrumentRegistryBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    shared_store.reset_store(factory, deepcopy(TEST_SHARED_STORE))
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE instrument_market_data SET value = 'NaN' "
            "WHERE instrument_id = 'fund-us-agg'"
        )
    try:
        with pytest.raises(ValueError):
            shared_store.get_instrument_details(
                factory,
                ["fund-us-agg"],
                include_fund_nav_ledger=False,
            )
    finally:
        engine.dispose()


def test_runtime_rejects_missing_persisted_quote_policy_without_fallback(
    isolated_store: Path,
) -> None:
    connection = sqlite3.connect(isolated_store)
    try:
        connection.execute(
            "UPDATE instrument SET quote_selection_policy_json = '{}' "
            "WHERE instrument_id = 'fund-us-agg'"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValueError, match="Field required"):
        get_instrument("fund-us-agg")


def test_quote_policy_write_requires_every_nonempty_role(
    isolated_store: Path,
) -> None:
    with pytest.raises(ValueError, match="Field required"):
        instrument_store.upsert_quote_selection_policy(
            instrument_id="fund-us-agg",
            quote_selection_policy={"valuation": ["official_nav"]},
        )


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
            instrument_type="public_fund",
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
        instrument_type="public_fund",
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


@pytest.mark.parametrize("instrument_type", ["equity", "etf", "index"])
def test_listed_security_with_only_adjusted_close_has_no_valuation_quote(
    isolated_store: Path,
    instrument_type: str,
) -> None:
    created = create_instrument(
        instrument_name=f"Adjusted-only {instrument_type}",
        instrument_type=instrument_type,
        currency="CNY",
        exchange_code="XSHG" if instrument_type in {"equity", "etf"} else None,
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
        provider="pytest",
        status="complete",
    )

    detail = get_instrument(created["instrument_id"])
    assert detail is not None
    assert detail["quote_selection_policy"]["valuation"] == ["close", "last"]
    available_bases = {
        point["quote_basis"] for point in detail["market_data"]
    }
    assert not available_bases.intersection(
        detail["quote_selection_policy"]["valuation"]
    )


def test_create_rejects_same_identifier_type_value_in_request(
    isolated_store: Path,
) -> None:
    with pytest.raises(ValueError, match='Duplicate identifier "ticker:dup"'):
        create_instrument(
            instrument_name="Duplicate Request Identifier",
            instrument_type="public_fund",
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
            instrument_type="public_fund",
            currency="USD",
            identifiers=[
                {
                    "identifier_type": "ticker",
                    "identifier_value": "NEW",
                    "is_primary": True,
                }
            ],
        )

    with pytest.raises(ValueError, match="Exactly one instrument identifier must be primary"):
        create_instrument(
            instrument_name="Two Primary Identifiers",
            instrument_type="public_fund",
            currency="USD",
            identifiers=[
                {"identifier_type": "ticker", "identifier_value": "NEW", "is_primary": True},
                {"identifier_type": "isin", "identifier_value": "US0000000001", "is_primary": True},
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
        provider="pytest",
        status="complete",
    )

    records = list_instruments(search="agg", instrument_type="public_fund", limit=1)

    assert [record["instrument_id"] for record in records] == ["fund-us-agg"]
    assert records[0]["latest_market_data"] == [
        {
            "metric_family": "price",
            "quote_basis": "close",
            "as_of_date": "2026-04-16",
            "value": "97.0100",
            "currency": "USD",
            "price_unit": "per_unit",
            "price_scale": "1",
                "provider": "pytest",
                "status": "complete",
                "nav_lineage": None,
            }
    ]


def test_market_data_upsert_rejects_noncanonical_currency(
    isolated_store: Path,
) -> None:
    with pytest.raises(ValueError, match="does not match instrument currency"):
        upsert_market_data(
            instrument_id="fund-us-agg",
            metric_family="price",
            quote_basis="close",
            as_of_date=date(2026, 4, 16),
            value="89.2500",
            currency="EUR",
            provider="pytest",
            status="complete",
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
    assert record["market_data_updated_at"] is not None
    assert any(
        point["as_of_date"] == "2026-04-16" and point["value"] == "97.0100"
        for point in record["latest_market_data"]
    )


def test_nav_history_boundary_query_uses_canonical_nav_only(
    isolated_store: Path,
) -> None:
    current = get_instrument("fund-us-agg")
    assert current is not None
    published = publish_fund_nav_history(
        instrument_id="fund-us-agg",
        rows=[
            {
                "as_of_date": "2025-12-25",
                "nav": "96.50",
                "nav_status": "complete",
                "nav_source_provider": "pytest",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "unit_nav"},
                },
                "currency": "USD",
            }
        ],
        projection_run={
            "source_observation_fingerprint": "e" * 64,
            "projection_kind": "event_derived",
            "projection_status": "unavailable",
            "method_version": "dividend_reinvestment/v2",
            "anchor_date": None,
            "source_provider": "pytest",
            "evidence": {
                "unavailable_reason": "reinvestment_evidence_not_observed",
                "published_total_return_dates": [],
                "missing_total_return_dates": ["2025-12-25"],
            },
            "created_by": "pytest",
        },
        current_fund_nav_event_ids=[],
        current_fund_nav_reinvestment_evidence_ids=[],
        expected_market_data_updated_at=current.get("market_data_updated_at"),
        refresh_status="imported",
        updated_by="pytest",
        message="boundary fixture",
        mode="manual",
    )
    assert published is not None
    upsert_market_data(
        instrument_id="cash-usd",
        metric_family="price",
        quote_basis="par",
        as_of_date=date(2025, 12, 20),
        value="1",
        currency="USD",
        provider="pytest",
        status="complete",
    )

    assert list_instrument_ids_with_nav_history_before(
        instrument_ids={"fund-us-agg", "cash-usd"},
        before_date=date(2025, 12, 26),
    ) == {"fund-us-agg"}
    assert list_instrument_ids_with_nav_history_before(
        instrument_ids=set(),
        before_date=date(2025, 12, 26),
    ) == set()


def test_market_data_price_contract_is_derived_for_all_write_paths(
    isolated_store: Path,
) -> None:
    equity = create_instrument(
        instrument_name="Contract Equity",
        instrument_type="equity",
        currency="USD",
        exchange_code="XNAS",
        identifiers=[
            {
                "identifier_type": "internal",
                "identifier_value": "CONTRACT-EQUITY",
                "is_primary": True,
            }
        ],
    )
    changed = upsert_market_data_points(
        instrument_id=equity["instrument_id"],
        rows=[
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-07-15",
                "value": "100",
                "currency": "USD",
                "provider": "pytest",
                "status": "complete",
            },
            {
                "metric_family": "price",
                "quote_basis": "adjusted_close",
                "as_of_date": "2026-07-15",
                "value": "101",
                "currency": "USD",
                "provider": "pytest",
                "status": "complete",
            },
        ],
    )
    assert changed == 2

    upsert_market_data(
        instrument_id=equity["instrument_id"],
        metric_family="price",
        quote_basis="last",
        as_of_date=date(2026, 7, 14),
        value="99",
        currency="USD",
        provider="derived_contract_test",
        status="complete",
    )
    detail = get_instrument(equity["instrument_id"])
    assert detail is not None
    points = {
        (point["quote_basis"], point["as_of_date"]): point
        for point in detail["market_data"]
    }
    assert points[("close", "2026-07-15")]["price_unit"] == "per_unit"
    assert points[("close", "2026-07-15")]["price_scale"] == "1"
    assert points[("adjusted_close", "2026-07-15")]["price_unit"] == "per_unit"
    assert points[("adjusted_close", "2026-07-15")]["price_scale"] == "1"
    assert points[("last", "2026-07-14")]["price_unit"] == "per_unit"
    assert points[("last", "2026-07-14")]["price_scale"] == "1"

    listed = next(
        item for item in list_instruments() if item["instrument_id"] == equity["instrument_id"]
    )
    latest = {point["quote_basis"]: point for point in listed["latest_market_data"]}
    assert latest["adjusted_close"]["price_scale"] == "1"
    assert latest["close"]["price_unit"] == "per_unit"

    fx = create_instrument(
        instrument_name="USD CNY Spot",
        instrument_type="fx",
        currency="CNY",
        identifiers=[
            {
                "identifier_type": "internal",
                "identifier_value": "FX-USD-CNY",
                "is_primary": True,
            }
        ],
    )
    assert upsert_market_data_points(
        instrument_id=fx["instrument_id"],
        rows=[
            {
                "metric_family": "fx",
                "quote_basis": "spot",
                "as_of_date": "2026-07-15",
                "value": "7.15",
                "currency": "CNY",
                "status": "complete",
            }
        ],
    ) == 1
    fx_detail = get_instrument(fx["instrument_id"])
    assert fx_detail is not None
    assert fx_detail["market_data"][0]["price_unit"] == "rate"
    assert fx_detail["market_data"][0]["price_scale"] == "1"

    seeded_fund = get_instrument("fund-us-agg")
    assert seeded_fund is not None
    assert seeded_fund["market_data"][0]["price_unit"] == "per_unit"
    assert seeded_fund["market_data"][0]["price_scale"] == "1"


def test_fx_market_data_contract_rejects_invalid_single_and_batch_writes(
    isolated_store: Path,
) -> None:
    fx = create_instrument(
        instrument_name="USD HKD Spot",
        instrument_type="fx",
        currency="HKD",
        identifiers=[
            {
                "identifier_type": "internal",
                "identifier_value": "FX-USD-HKD",
                "is_primary": True,
            }
        ],
    )
    fx_state_before = _market_data_persistence_state(
        isolated_store,
        str(fx["instrument_id"]),
    )

    invalid_single_rows = [
        ("fx", "spot", "7.8", "CNY", "does not match instrument currency"),
        ("fx", "spot", "0", "HKD", "finite positive decimal"),
        ("fx", "spot", "-7.8", "HKD", "finite positive decimal"),
        ("fx", "spot", "NaN", "HKD", "finite positive decimal"),
        ("fx", "spot", "Infinity", "HKD", "finite positive decimal"),
        ("price", "close", "7.8", "HKD", "FX market data requires"),
    ]
    for metric_family, quote_basis, value, currency, message in invalid_single_rows:
        with pytest.raises(ValueError, match=message):
            upsert_market_data(
                instrument_id=str(fx["instrument_id"]),
                metric_family=metric_family,
                quote_basis=quote_basis,
                as_of_date=date(2026, 7, 15),
                value=value,
                currency=currency,
                provider="pytest",
                status="complete",
            )
        assert _market_data_persistence_state(
            isolated_store,
            str(fx["instrument_id"]),
        ) == fx_state_before

    with pytest.raises(ValueError, match="FX market data requires"):
        upsert_market_data(
            instrument_id="fund-us-agg",
            metric_family="fx",
            quote_basis="spot",
            as_of_date=date(2026, 7, 15),
            value="7.8",
            currency="HKD",
            provider="pytest",
            status="complete",
        )

    with pytest.raises(ValueError, match="requires master currency"):
        create_instrument(
            instrument_name="Mismatched USD CNY Spot",
            instrument_type="fx",
            currency="HKD",
            identifiers=[
                {
                    "identifier_type": "internal",
                    "identifier_value": "FX-USD-CNY",
                    "is_primary": True,
                }
            ],
        )

    with pytest.raises(ValueError, match="has no maintained identity"):
        create_instrument(
            instrument_name="Unknown FX Spot",
            instrument_type="fx",
            currency="USD",
            identifiers=[
                {
                    "identifier_type": "internal",
                    "identifier_value": "FX-EUR-USD",
                    "is_primary": True,
                }
            ],
        )

    with pytest.raises(
        ValueError,
        match="Market-data row 2.*does not match instrument currency",
    ):
        upsert_market_data_points(
            instrument_id=str(fx["instrument_id"]),
            rows=[
                {
                    "metric_family": "fx",
                    "quote_basis": "spot",
                    "as_of_date": "2026-07-14",
                    "value": "7.8",
                    "currency": "HKD",
                    "status": "complete",
                },
                {
                    "metric_family": "fx",
                    "quote_basis": "spot",
                    "as_of_date": "2026-07-15",
                    "value": "7.2",
                    "currency": "CNY",
                    "status": "complete",
                },
            ],
        )
    assert _market_data_persistence_state(
        isolated_store,
        str(fx["instrument_id"]),
    ) == fx_state_before


def test_market_data_price_contract_rejects_invalid_batch_atomically(
    isolated_store: Path,
) -> None:
    equity = create_instrument(
        instrument_name="Fail Closed Equity",
        instrument_type="equity",
        currency="USD",
        exchange_code="XNAS",
        identifiers=[
            {
                "identifier_type": "internal",
                "identifier_value": "FAIL-CLOSED-EQUITY",
                "is_primary": True,
            }
        ],
    )
    equity_state_before = _market_data_persistence_state(
        isolated_store,
        str(equity["instrument_id"]),
    )

    with pytest.raises(ValueError, match="must not supply derived field"):
        upsert_market_data_points(
            instrument_id=equity["instrument_id"],
            rows=[
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-07-15",
                    "value": "100",
                    "currency": "USD",
                    "status": "complete",
                },
                {
                    "metric_family": "price",
                    "quote_basis": "last",
                    "as_of_date": "2026-07-14",
                    "value": "99",
                    "currency": "USD",
                    "price_unit": "per_unit",
                    "price_scale": "1",
                },
            ],
        )

    detail = get_instrument(equity["instrument_id"])
    assert detail is not None
    assert detail["market_data"] == []
    assert _market_data_persistence_state(
        isolated_store,
        str(equity["instrument_id"]),
    ) == equity_state_before

    with pytest.raises(ValueError, match="must not supply derived field"):
        upsert_market_data_points(
            instrument_id=equity["instrument_id"],
            rows=[
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-07-15",
                    "value": "100",
                    "currency": "USD",
                    "price_unit": "per_unit",
                }
            ],
        )

    with pytest.raises(ValueError, match="audited raw-to-canonical NAV import path"):
        upsert_market_data_points(
            instrument_id=equity["instrument_id"],
            rows=[
                {
                    "metric_family": "nav",
                    "quote_basis": "official_nav",
                    "as_of_date": "2026-07-15",
                    "value": "1.25",
                    "currency": "USD",
                    "status": "complete",
                }
            ],
        )
    assert _market_data_persistence_state(
        isolated_store,
        str(equity["instrument_id"]),
    ) == equity_state_before

    with pytest.raises(ValueError, match="Unsupported quote_basis"):
        upsert_market_data_points(
            instrument_id=equity["instrument_id"],
            rows=[
                {
                    "metric_family": "price",
                    "quote_basis": "clean_price",
                    "as_of_date": "2026-07-15",
                    "value": "1.25",
                    "currency": "USD",
                    "status": "complete",
                }
            ],
        )
    assert _market_data_persistence_state(
        isolated_store,
        str(equity["instrument_id"]),
    ) == equity_state_before


@pytest.mark.parametrize(
    "malformed_row",
    [
        pytest.param("not-an-object", id="not-an-object"),
        pytest.param(
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "not-a-date",
                "value": "97.25",
                "currency": "USD",
            },
            id="bad-date",
        ),
        pytest.param(
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": "2026-07-14",
                "value": "97.25",
            },
            id="missing-field",
        ),
    ],
)
def test_market_data_batch_rejects_malformed_rows_atomically(
    isolated_store: Path,
    malformed_row: object,
) -> None:
    equity = create_instrument(
        instrument_name="Malformed Batch Equity",
        instrument_type="equity",
        currency="USD",
        exchange_code="XNAS",
        identifiers=[
            {
                "identifier_type": "internal",
                "identifier_value": "MALFORMED-BATCH-EQUITY",
                "is_primary": True,
            }
        ],
    )
    before = _market_data_persistence_state(
        isolated_store,
        str(equity["instrument_id"]),
    )

    with pytest.raises(ValueError, match="Market-data row 2"):
        upsert_market_data_points(
            instrument_id=str(equity["instrument_id"]),
            rows=[
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-07-15",
                    "value": "100",
                    "currency": "USD",
                    "status": "complete",
                },
                malformed_row,
            ],
        )

    assert _market_data_persistence_state(
        isolated_store,
        str(equity["instrument_id"]),
    ) == before


@pytest.mark.parametrize(
    "duplicate_overrides",
    [
        pytest.param({}, id="identical"),
        pytest.param(
            {"value": "97.25", "provider": "conflicting-provider"},
            id="conflicting",
        ),
    ],
)
def test_market_data_batch_rejects_duplicate_keys_atomically(
    isolated_store: Path,
    duplicate_overrides: dict[str, object],
) -> None:
    equity = create_instrument(
        instrument_name="Duplicate Batch Equity",
        instrument_type="equity",
        currency="USD",
        exchange_code="XNAS",
        identifiers=[
            {
                "identifier_type": "internal",
                "identifier_value": "DUPLICATE-BATCH-EQUITY",
                "is_primary": True,
            }
        ],
    )
    before = _market_data_persistence_state(
        isolated_store,
        str(equity["instrument_id"]),
    )
    first_row: dict[str, object] = {
        "metric_family": "price",
        "quote_basis": "close",
        "as_of_date": "2026-07-15",
        "value": "100",
        "currency": "USD",
        "provider": "same-provider",
        "status": "complete",
    }

    with pytest.raises(ValueError, match="Duplicate market-data row key"):
        upsert_market_data_points(
            instrument_id=str(equity["instrument_id"]),
            rows=[first_row, {**first_row, **duplicate_overrides}],
        )

    assert _market_data_persistence_state(
        isolated_store,
        str(equity["instrument_id"]),
    ) == before


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
        provider="pytest",
        status="complete",
    )
    second = upsert_market_data(
        instrument_id="cash-usd",
        metric_family="price",
        quote_basis="par",
        as_of_date=date(2026, 4, 16),
        value="5300.0000",
        currency="USD",
        provider="pytest",
        status="complete",
    )

    assert first is not None
    assert second is not None
    assert first["market_data_updated_at"] == "2099-01-01T00:00:00.000000Z"
    assert second["market_data_updated_at"] == "2099-01-01T00:00:00.000001Z"


def test_publish_fund_nav_history_updates_shared_store_without_app_callbacks(
    isolated_store: Path,
) -> None:
    current = get_instrument("fund-us-agg")
    assert current is not None
    record = publish_fund_nav_history(
        instrument_id="fund-us-agg",
        rows=[
            {
                "as_of_date": "2026-04-14",
                "nav": "100.0000",
                "nav_status": "complete",
                "nav_source_provider": "pytest",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "nav"},
                },
                "nav_with_dividend": "100.5000",
                "nav_with_dividend_status": "complete",
                "nav_with_dividend_source_provider": "pytest",
                "nav_with_dividend_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {
                        "source_field": "test_total_nav",
                        "factor_logical_key": "provider:2026-04-14",
                    },
                },
                "currency": "USD",
            },
            {
                "as_of_date": "2026-04-15",
                "nav": "100.2000",
                "nav_status": "complete",
                "nav_source_provider": "row-specific-provider",
                "nav_with_dividend_source_provider": "total-return-provider",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {
                        "source_field": "nav",
                        "raw_observation_id": 42,
                    },
                },
                "nav_with_dividend": "100.7010",
                "nav_with_dividend_status": "complete",
                "nav_with_dividend_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {
                        "source_field": "test_total_nav",
                        "factor_logical_key": "provider:2026-04-15",
                    },
                },
                "currency": "USD",
            },
        ],
        projection_run={
            "source_observation_fingerprint": "b" * 64,
            "projection_kind": "provider_explicit",
            "projection_status": "complete",
            "method_version": "dividend_reinvestment/v2",
            "anchor_date": "2026-04-14",
            "source_provider": "pytest",
            "evidence": {
                "published_total_return_dates": ["2026-04-14", "2026-04-15"],
                "missing_total_return_dates": [],
            },
            "created_by": "pytest",
        },
        current_fund_nav_event_ids=[],
        current_fund_nav_reinvestment_evidence_ids=[],
        adjustment_factors=[
            {
                "factor_logical_key": "provider:2026-04-14",
                "as_of_date": "2026-04-14",
                "factor_level": "1.005",
                "factor_kind": "provider_implied",
                "evidence_kind": "provider_total_return",
                "method_version": "dividend_reinvestment/v2",
                "anchor_date": "2026-04-14",
                "source_provider": "pytest",
                "evidence": {"source_field": "test_total_nav"},
            },
            {
                "factor_logical_key": "provider:2026-04-15",
                "as_of_date": "2026-04-15",
                "factor_level": "1.005",
                "factor_kind": "provider_implied",
                "evidence_kind": "provider_total_return",
                "method_version": "dividend_reinvestment/v2",
                "anchor_date": "2026-04-15",
                "source_provider": "total-return-provider",
                "evidence": {"source_field": "test_total_nav"},
            },
        ],
        expected_market_data_updated_at=current.get("market_data_updated_at"),
        refresh_status="ready",
        updated_by="pytest",
        message="nav import",
    )

    assert record is not None
    assert record["market_data_updated_at"] is not None
    assert any(
        point["quote_basis"] == "official_nav"
        and point["as_of_date"] == "2026-04-15"
        and point["value"] == "100.2000"
        and point["price_unit"] == "per_unit"
        and point["price_scale"] == "1"
        and point["provider"] == "row-specific-provider"
        and point["nav_lineage"]["evidence"]["raw_observation_id"] == 42
        for point in record["record"]["latest_market_data"]
    )


def test_fund_return_policy_requires_the_canonical_total_return_nav(
    isolated_store: Path,
) -> None:
    created = create_instrument(
        instrument_name="Strict Return Fund",
        instrument_type="public_fund",
        currency="CNY",
        identifiers=[
            {
                "identifier_type": "internal",
                "identifier_value": "STRICT-RETURN-FUND",
                "is_primary": True,
            }
        ],
    )

    assert created["quote_selection_policy"]["total_return"] == ["total_return_nav"]
    assert created["quote_selection_policy"]["chart"] == ["total_return_nav"]

    invalid_policy = dict(created["quote_selection_policy"])
    invalid_policy["total_return"] = ["total_return_nav", "official_nav"]
    with pytest.raises(ValueError, match="fund total_return must use only"):
        instrument_store.upsert_quote_selection_policy(
            instrument_id=str(created["instrument_id"]),
            quote_selection_policy=invalid_policy,
        )


def test_publish_fund_nav_history_never_persists_cash_cumulative_nav(
    isolated_store: Path,
) -> None:
    current = get_instrument("fund-us-agg")
    assert current is not None
    record = publish_fund_nav_history(
        instrument_id="fund-us-agg",
        rows=[
            {
                "as_of_date": "2026-04-16",
                "nav": "100.3000",
                "nav_status": "complete",
                "nav_source_provider": "pytest",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "nav"},
                },
                "cash_cumulative_nav": "105.3000",
                "nav_with_dividend": "105.3150",
                "nav_with_dividend_status": "complete",
                "nav_with_dividend_source_provider": "pytest",
                "nav_with_dividend_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {
                        "source_field": "test_total_nav",
                        "factor_logical_key": "provider:2026-04-16",
                    },
                },
                "currency": "USD",
            }
        ],
        projection_run={
            "source_observation_fingerprint": "c" * 64,
            "projection_kind": "provider_explicit",
            "projection_status": "complete",
            "method_version": "dividend_reinvestment/v2",
            "anchor_date": "2026-04-16",
            "source_provider": "pytest",
            "evidence": {
                "published_total_return_dates": ["2026-04-16"],
                "missing_total_return_dates": [],
            },
            "created_by": "pytest",
        },
        current_fund_nav_event_ids=[],
        current_fund_nav_reinvestment_evidence_ids=[],
        adjustment_factors=[
            {
                "factor_logical_key": "provider:2026-04-16",
                "as_of_date": "2026-04-16",
                "factor_level": "1.05",
                "factor_kind": "provider_implied",
                "evidence_kind": "provider_total_return",
                "method_version": "dividend_reinvestment/v2",
                "anchor_date": "2026-04-16",
                "source_provider": "pytest",
                "evidence": {"source_field": "test_total_nav"},
            }
        ],
        expected_market_data_updated_at=current.get("market_data_updated_at"),
        refresh_status="ready",
        updated_by="pytest",
        message="strict nav import",
    )

    assert record is not None
    imported_bases = {
        point["quote_basis"]
        for point in record["record"]["latest_market_data"]
        if point["as_of_date"] == "2026-04-16"
    }
    assert imported_bases == {"official_nav", "total_return_nav"}

    with pytest.raises(ValueError, match="has no NAV observation"):
        latest = get_instrument("fund-us-agg")
        assert latest is not None
        publish_fund_nav_history(
            instrument_id="fund-us-agg",
            rows=[
                {
                    "as_of_date": "2026-04-17",
                    "cash_cumulative_nav": "105.5000",
                    "currency": "USD",
                }
            ],
            projection_run={
                "source_observation_fingerprint": "d" * 64,
                "projection_kind": "event_derived",
                "projection_status": "unavailable",
                "method_version": "dividend_reinvestment/v2",
                "anchor_date": None,
                "source_provider": "pytest",
                "evidence": {
                    "unavailable_reason": "no_unit_nav_observation",
                    "published_total_return_dates": [],
                    "missing_total_return_dates": [],
                },
                "created_by": "pytest",
            },
            current_fund_nav_event_ids=[],
            current_fund_nav_reinvestment_evidence_ids=[],
            expected_market_data_updated_at=latest.get("market_data_updated_at"),
            refresh_status="ready",
            updated_by="pytest",
            message="cash cumulative only",
        )


def test_publish_fund_nav_history_rejects_fx_before_persistence(
    isolated_store: Path,
) -> None:
    fx = create_instrument(
        instrument_name="NAV Boundary FX",
        instrument_type="fx",
        currency="CNY",
        identifiers=[
            {
                "identifier_type": "internal",
                "identifier_value": "FX-USD-CNY",
                "is_primary": True,
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="NAV history import is only supported for public or private fund instruments",
    ):
        publish_fund_nav_history(
            instrument_id=str(fx["instrument_id"]),
            rows=[
                {
                    "as_of_date": "2026-04-15",
                    "nav": "7.1000",
                    "currency": "CNY",
                }
            ],
            projection_run={
                "source_observation_fingerprint": "e" * 64,
                "projection_kind": "event_derived",
                "projection_status": "unavailable",
                "method_version": "dividend_reinvestment/v2",
                "anchor_date": None,
                "source_provider": "pytest",
                "evidence": {
                    "unavailable_reason": "not_a_fund",
                    "published_total_return_dates": [],
                    "missing_total_return_dates": ["2026-04-15"],
                },
                "created_by": "pytest",
            },
            current_fund_nav_event_ids=[],
            current_fund_nav_reinvestment_evidence_ids=[],
            expected_market_data_updated_at=fx.get("market_data_updated_at"),
            refresh_status="ready",
            updated_by="pytest",
            message="invalid FX NAV import",
        )

    detail = get_instrument(str(fx["instrument_id"]))
    assert detail is not None
    assert detail["market_data"] == []


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
    assert no_new_data["refresh_status"]["last_successful_requested_at"] == successful_at

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


def test_source_schedule_semantics_default_and_roundtrip_by_instrument_type(
    isolated_store: Path,
) -> None:
    fund = get_instrument("fund-us-agg")
    cash = get_instrument("cash-usd")
    assert fund is not None
    assert cash is not None
    assert fund["source_settings"]["expected_frequency"] == "daily"
    assert fund["source_settings"]["market_calendar"] is None
    assert fund["source_settings"]["release_lag_days"] == 1
    assert cash["source_settings"]["expected_frequency"] == "event_driven"
    assert cash["source_settings"]["market_calendar"] is None
    assert cash["source_settings"]["release_lag_days"] == 0

    equity = create_instrument(
        instrument_name="Shanghai Schedule Equity",
        instrument_type="equity",
        currency="CNY",
        exchange_code="XSHG",
        identifiers=[
            {
                "identifier_type": "ticker",
                "identifier_value": "600000.SH",
                "is_primary": True,
            }
        ],
    )
    assert equity["source_settings"]["expected_frequency"] == "daily"
    assert equity["source_settings"]["market_calendar"] == "XSHG"
    assert equity["source_settings"]["release_lag_days"] == 0
    assert equity["source_settings"]["return_semantics"] == "unknown"

    index = create_instrument(
        instrument_name="Shanghai Total Return Index",
        instrument_type="index",
        currency="CNY",
        identifiers=[
            {
                "identifier_type": "ts_code",
                "identifier_value": "H00300.CSI",
                "is_primary": True,
            }
        ],
    )
    index_update = upsert_source_settings(
        instrument_id=index["instrument_id"],
        source_mode="api",
        source_email=None,
        source_location=None,
        source_api_profile="tushare",
        source_email_rules=None,
        return_semantics="total_return",
    )
    assert index_update is not None
    assert index_update["source_settings"]["return_semantics"] == "total_return"
    etf = create_instrument(
        instrument_name="FMP Schedule ETF",
        instrument_type="etf",
        currency="USD",
        exchange_code="BATS",
        identifiers=[
            {
                "identifier_type": "exchange_ticker",
                "identifier_value": "MAGS",
                "is_primary": True,
            }
        ],
    )
    etf_update = upsert_source_settings(
        instrument_id=etf["instrument_id"],
        source_mode="api",
        source_email=None,
        source_location="FMP API",
        source_api_profile="fmp",
        source_email_rules=None,
        return_semantics="price_return",
    )
    assert etf_update is not None
    assert etf_update["source_settings"]["return_semantics"] == "price_return"
    with pytest.raises(ValueError, match="only supported for indexes and listed securities"):
        upsert_source_settings(
            instrument_id=fund["instrument_id"],
            source_mode="api",
            source_email=None,
            source_location=None,
            source_api_profile="tushare",
            source_email_rules=None,
            return_semantics="total_return",
        )

    updated = upsert_source_settings(
        instrument_id=equity["instrument_id"],
        source_mode="api",
        source_email=None,
        source_location="Tushare daily queue",
        source_api_profile="tushare",
        source_email_rules=None,
        expected_frequency="daily",
        market_calendar="CN_FUND_WEEKLY",
        release_lag_days=2,
    )
    assert updated is not None
    assert updated["source_settings"]["expected_frequency"] == "daily"
    assert updated["source_settings"]["market_calendar"] == "CN_FUND_WEEKLY"
    assert updated["source_settings"]["release_lag_days"] == 2

    refreshed = get_instrument(equity["instrument_id"])
    assert refreshed is not None
    assert refreshed["source_settings"] == updated["source_settings"]

    mode_only_update = upsert_source_settings(
        instrument_id=equity["instrument_id"],
        source_mode="manual",
        source_email=None,
        source_location=None,
        source_api_profile=None,
        source_email_rules=None,
    )
    assert mode_only_update is not None
    assert mode_only_update["source_settings"]["expected_frequency"] == "daily"
    assert mode_only_update["source_settings"]["market_calendar"] == "CN_FUND_WEEKLY"
    assert mode_only_update["source_settings"]["release_lag_days"] == 2

    with pytest.raises(ValueError, match="expected_frequency"):
        upsert_source_settings(
            instrument_id=equity["instrument_id"],
            source_mode="api",
            source_email=None,
            source_location=None,
            source_api_profile="tushare",
            source_email_rules=None,
            expected_frequency="weekly",
        )
    with pytest.raises(ValueError, match="expected_frequency"):
        upsert_source_settings(
            instrument_id=equity["instrument_id"],
            source_mode="api",
            source_email=None,
            source_location=None,
            source_api_profile="tushare",
            source_email_rules=None,
            expected_frequency="monthly",
        )

    with pytest.raises(ValueError, match="release_lag_days"):
        upsert_source_settings(
            instrument_id=equity["instrument_id"],
            source_mode="api",
            source_email=None,
            source_location=None,
            source_api_profile=None,
            source_email_rules=None,
            release_lag_days=-1,
        )
    after_rejection = get_instrument(equity["instrument_id"])
    assert after_rejection is not None
    assert after_rejection["source_settings"]["release_lag_days"] == 2


def test_registry_rejects_portfolio_local_derivative_types_and_contract_identifiers(
    isolated_store: Path,
) -> None:
    for instrument_type in ("fcn", "option"):
        with pytest.raises(ValueError, match="Unsupported instrument_type"):
            create_instrument(
                instrument_name=f"Portfolio-local {instrument_type}",
                instrument_type=instrument_type,
                currency="USD",
                identifiers=[
                    {
                        "identifier_type": "internal",
                        "identifier_value": f"PORTFOLIO-{instrument_type.upper()}-001",
                        "is_primary": True,
                    }
                ],
            )

    with pytest.raises(ValueError, match="identifier_type"):
        create_instrument(
            instrument_name="Ordinary Equity With Contract ID",
            instrument_type="equity",
            currency="USD",
            exchange_code="XNAS",
            identifiers=[
                {
                    "identifier_type": "internal",
                    "identifier_value": "EQUITY-CONTRACT-ID-TEST",
                    "is_primary": True,
                }
            ],
            broker_identifiers=[
                {
                    "broker": "example",
                    "identifier_type": "contract_id",
                    "identifier_value": "123456",
                    "is_primary": True,
                }
            ],
        )


def test_email_failure_does_not_infer_a_missing_persistent_cursor(
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
    instrument_store.reset_store(legacy_store)

    failed = update_refresh_status(
        instrument_id="fund-us-agg",
        status="failed",
        message="timeout",
        updated_by="pytest",
        mode="email",
    )

    assert failed is not None
    assert failed["refresh_status"]["last_successful_requested_at"] is None


def test_publish_fund_nav_history_advances_only_email_success_cursor(
    isolated_store: Path,
) -> None:
    current = get_instrument("fund-us-agg")
    assert current is not None
    manual = publish_fund_nav_history(
        instrument_id="fund-us-agg",
        rows=[
            {
                "as_of_date": "2026-04-16",
                "nav": "100.3000",
                "nav_status": "complete",
                "nav_source_provider": "pytest",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "nav"},
                },
                "currency": "USD",
            }
        ],
        projection_run={
            "source_observation_fingerprint": "f" * 64,
            "projection_kind": "event_derived",
            "projection_status": "unavailable",
            "method_version": "dividend_reinvestment/v2",
            "anchor_date": None,
            "source_provider": "pytest",
            "evidence": {
                "unavailable_reason": "reinvestment_evidence_not_observed",
                "published_total_return_dates": [],
                "missing_total_return_dates": ["2026-04-16"],
            },
            "created_by": "pytest",
        },
        current_fund_nav_event_ids=[],
        current_fund_nav_reinvestment_evidence_ids=[],
        expected_market_data_updated_at=current.get("market_data_updated_at"),
        refresh_status="imported",
        updated_by="pytest",
        message="manual import",
        mode="manual",
    )
    assert manual is not None
    assert manual["record"]["refresh_status"]["last_successful_requested_at"] is None
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
    current = get_instrument("fund-us-agg")
    assert current is not None
    email = publish_fund_nav_history(
        instrument_id="fund-us-agg",
        rows=[
            {
                "as_of_date": "2026-04-17",
                "nav": "100.4000",
                "nav_status": "complete",
                "nav_source_provider": "pytest",
                "nav_lineage": {
                    "kind": "provider_explicit",
                    "evidence": {"source_field": "nav"},
                },
                "currency": "USD",
            }
        ],
        projection_run={
            "source_observation_fingerprint": "0" * 64,
            "projection_kind": "event_derived",
            "projection_status": "unavailable",
            "method_version": "dividend_reinvestment/v2",
            "anchor_date": None,
            "source_provider": "pytest",
            "evidence": {
                "unavailable_reason": "reinvestment_evidence_not_observed",
                "published_total_return_dates": [],
                "missing_total_return_dates": ["2026-04-17"],
            },
            "created_by": "pytest",
        },
        current_fund_nav_event_ids=[],
        current_fund_nav_reinvestment_evidence_ids=[],
        expected_market_data_updated_at=current.get("market_data_updated_at"),
        refresh_status="imported",
        updated_by="pytest",
        message="email import",
        mode="email",
    )
    assert email is not None
    assert (
        email["record"]["refresh_status"]["last_successful_requested_at"]
        == email["record"]["refresh_status"]["requested_at"]
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
    assert {
        (row["as_of_date"], "official_nav" if row.get("nav") else "total_return_nav")
        for row in rows
    } == {("2026-04-15", "official_nav")}


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
