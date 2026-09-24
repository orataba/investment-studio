from __future__ import annotations

import importlib
import os
import sys
from shutil import copyfile
from uuid import uuid4
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from fastapi import Request
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault(
    "INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL",
    "sqlite+pysqlite:///:memory:",
)
BACKEND_ROOT_STR = str(BACKEND_ROOT)
if BACKEND_ROOT_STR in sys.path:
    sys.path.remove(BACKEND_ROOT_STR)
sys.path.insert(0, BACKEND_ROOT_STR)

from tests.store_fixture import TEST_PORTFOLIO_STORE

from portfolio_app.api.routes import transactions as transaction_routes
from portfolio_app.services import instrument_charts, ledger, performance, portfolio_store
from investment_studio_instrument_core import instrument_store as shared_store
from investment_studio_instrument_core.db_models import InstrumentRegistryBase


def _market_point(
    metric_family: str,
    quote_basis: str,
    as_of_date: str,
    value: str,
    currency: str,
) -> dict[str, object]:
    price_unit = "rate" if metric_family == "fx" else "per_unit"
    return {
        "metric_family": metric_family,
        "quote_basis": quote_basis,
        "as_of_date": as_of_date,
        "value": value,
        "currency": currency,
        "price_unit": price_unit,
        "price_scale": "1",
        "status": "complete",
    }


def _canonical_quote_policy(instrument_type: str) -> dict[str, list[str]]:
    policies = {
        "equity": {
            "trading": ["last", "close"],
            "valuation": ["close", "last"],
            "total_return": ["adjusted_close", "close", "last"],
            "chart": ["adjusted_close", "close", "last"],
            "reference": ["close", "last"],
        },
        "etf": {
            "trading": ["last", "close"],
            "valuation": ["close", "last"],
            "total_return": ["adjusted_close", "close", "last"],
            "chart": ["adjusted_close", "close", "last"],
            "reference": ["close", "last"],
        },
        "fx": {
            "trading": ["spot"],
            "valuation": ["spot"],
            "total_return": ["spot"],
            "chart": ["spot"],
            "reference": ["spot"],
        },
    }
    return deepcopy(policies[instrument_type])


REGISTRY_INSTRUMENT_DETAILS = [
    {
        "instrument_id": "equity-us-abbv",
        "instrument_name": "AbbVie Inc",
        "instrument_type": "equity",
        "currency": "USD",
        "exchange_code": "XNYS",
        "identifiers": [
            {"identifier_type": "ticker", "identifier_value": "ABBV", "is_primary": True},
            {"identifier_type": "isin", "identifier_value": "US00287Y1091", "is_primary": False},
        ],
        "quote_selection_policy": _canonical_quote_policy("equity"),
        "market_data": [
            _market_point("price", "close", "2026-02-10", "206.47", "USD"),
            _market_point("price", "close", "2026-03-15", "210.20", "USD"),
            _market_point("price", "close", "2026-04-08", "207.18", "USD"),
            _market_point("price", "close", "2026-04-15", "206.47", "USD"),
            _market_point("price", "adjusted_close", "2026-02-10", "206.47", "USD"),
            _market_point("price", "adjusted_close", "2026-03-15", "210.20", "USD"),
            _market_point("price", "adjusted_close", "2026-04-08", "207.18", "USD"),
            _market_point("price", "adjusted_close", "2026-04-15", "206.47", "USD"),
        ],
    },
    {
        "instrument_id": "fund-us-agg",
        "instrument_name": "iShares Core U.S. Aggregate Bond ETF",
        "instrument_type": "etf",
        "currency": "USD",
        "exchange_code": "XNAS",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "AGG", "is_primary": True}],
        "quote_selection_policy": _canonical_quote_policy("etf"),
        "market_data": [
            _market_point("price", "close", "2026-03-05", "96.82", "USD"),
            _market_point("price", "close", "2026-03-28", "97.62", "USD"),
            _market_point("price", "close", "2026-04-14", "96.97", "USD"),
            _market_point("price", "close", "2026-04-15", "91.62", "USD"),
        ],
    },
    {
        "instrument_id": "fund-hk-2800",
        "instrument_name": "Tracker Fund of Hong Kong",
        "instrument_type": "etf",
        "currency": "HKD",
        "exchange_code": "XHKG",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "2800.HK", "is_primary": True}],
        "quote_selection_policy": _canonical_quote_policy("etf"),
        "market_data": [
            _market_point("price", "close", "2026-03-04", "21.30", "HKD"),
            _market_point("price", "close", "2026-04-02", "21.05", "HKD"),
            _market_point("price", "close", "2026-04-15", "21.34", "HKD"),
        ],
    },
    {
        "instrument_id": "fund-us-watch",
        "instrument_name": "Watchlist Fund",
        "instrument_type": "etf",
        "currency": "USD",
        "exchange_code": "XNAS",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "WATCH", "is_primary": True}],
        "quote_selection_policy": _canonical_quote_policy("etf"),
        "market_data": [
            _market_point("price", "close", "2026-04-15", "100.00", "USD"),
        ],
    },
    {
        "instrument_id": "fx-usd-hkd",
        "instrument_name": "USD/HKD Spot",
        "instrument_type": "fx",
        "currency": "HKD",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDHKD", "is_primary": True}],
        "quote_selection_policy": _canonical_quote_policy("fx"),
        "market_data": [
            _market_point("fx", "spot", "2026-02-20", "7.80", "HKD"),
            _market_point("fx", "spot", "2026-03-04", "7.79", "HKD"),
            _market_point("fx", "spot", "2026-04-02", "7.82", "HKD"),
            _market_point("fx", "spot", "2026-04-15", "7.80", "HKD"),
        ],
    },
    {
        "instrument_id": "fx-usd-cny",
        "instrument_name": "USD/CNY Spot",
        "instrument_type": "fx",
        "currency": "CNY",
        "identifiers": [{"identifier_type": "ticker", "identifier_value": "USDCNY", "is_primary": True}],
        "quote_selection_policy": _canonical_quote_policy("fx"),
        "market_data": [
            _market_point("fx", "spot", "2026-04-02", "7.29", "CNY"),
            _market_point("fx", "spot", "2026-04-15", "7.20", "CNY"),
        ],
    },
]


def _with_daily_test_history(detail: dict[str, object]) -> dict[str, object]:
    """Define complete synthetic fixed-price segments for the shared API fixture.

    These are explicit test-source observations, not a production missing-price
    fallback. Existing dated anchors (including every latest quote) stay intact.
    Tests for actual data gaps replace this history with their own sparse facts.
    """

    first_holding_dates = [
        str(transaction["trade_date"])
        for transaction in TEST_PORTFOLIO_STORE["transactions"]
        if transaction.get("instrument_id") == detail["instrument_id"]
    ]
    start = date.fromisoformat(
        "2026-01-02"
        if detail["instrument_type"] == "fx"
        else min(first_holding_dates)
        if first_holding_dates
        else min(str(point["as_of_date"]) for point in detail["market_data"])
    )
    series: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for point in detail["market_data"]:
        key = (point["metric_family"], point["quote_basis"], point["currency"])
        series.setdefault(key, []).append(point)
    observations = []
    for anchors in series.values():
        anchors.sort(key=lambda point: str(point["as_of_date"]))
        by_date = {str(point["as_of_date"]): point for point in anchors}
        previous = anchors[0]
        day = min(start, date.fromisoformat(str(anchors[0]["as_of_date"])))
        while day <= date(2026, 4, 15):
            previous = by_date.get(day.isoformat(), previous)
            observations.append({**previous, "as_of_date": day.isoformat()})
            day += timedelta(days=1)
    return {**detail, "market_data": observations}


REGISTRY_INSTRUMENT_DETAILS = [
    _with_daily_test_history(detail) for detail in REGISTRY_INSTRUMENT_DETAILS
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
        "coverage_state": "complete",
    }
    for detail in REGISTRY_INSTRUMENT_DETAILS
]


FX_PAYLOAD = {
    "supported_currencies": ["USD", "HKD", "CNY", "EUR", "GBP", "CHF"],
    "maintained_pairs": ["USD/HKD", "USD/CNY", "USD/EUR", "USD/GBP", "USD/CHF"],
    "rates": [
        {
            "base_currency": "USD",
            "quote_currency": "HKD",
            "rate": 7.8,
            "as_of_date": "2026-04-15",
            "source_kind": "direct",
            "instrument_id": "fx-usd-hkd",
            "source_instrument_ids": ["fx-usd-hkd"],
            "provider": "test",
            "status": "complete",
        },
        {
            "base_currency": "HKD",
            "quote_currency": "USD",
            "rate": 1 / 7.8,
            "as_of_date": "2026-04-15",
            "source_kind": "inverse",
            "instrument_id": "fx-usd-hkd",
            "source_instrument_ids": ["fx-usd-hkd"],
            "provider": "test",
            "status": "complete",
        },
        {
            "base_currency": "USD",
            "quote_currency": "CNY",
            "rate": 7.2,
            "as_of_date": "2026-04-15",
            "source_kind": "direct",
            "instrument_id": "fx-usd-cny",
            "source_instrument_ids": ["fx-usd-cny"],
            "provider": "test",
            "status": "complete",
        },
        {
            "base_currency": "CNY",
            "quote_currency": "USD",
            "rate": 1 / 7.2,
            "as_of_date": "2026-04-15",
            "source_kind": "inverse",
            "instrument_id": "fx-usd-cny",
            "source_instrument_ids": ["fx-usd-cny"],
            "provider": "test",
            "status": "complete",
        },
        {
            "base_currency": "HKD",
            "quote_currency": "CNY",
            "rate": 7.2 / 7.8,
            "as_of_date": "2026-04-15",
            "source_kind": "cross",
            "instrument_id": None,
            "source_instrument_ids": ["fx-usd-hkd", "fx-usd-cny"],
            "provider": "test",
            "status": "complete",
        },
        {
            "base_currency": "CNY",
            "quote_currency": "HKD",
            "rate": 7.8 / 7.2,
            "as_of_date": "2026-04-15",
            "source_kind": "cross",
            "instrument_id": None,
            "source_instrument_ids": ["fx-usd-hkd", "fx-usd-cny"],
            "provider": "test",
            "status": "complete",
        },
    ],
}


def _run_alembic_upgrade(database_url: str, revision: str = "head") -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision)


def _get_registry_instrument_detail(instrument_id: str):
    return deepcopy(next((item for item in REGISTRY_INSTRUMENT_DETAILS if item["instrument_id"] == instrument_id), None))


def _get_registry_instrument_details(instrument_ids):
    return {
        instrument_id: _get_registry_instrument_detail(instrument_id)
        for instrument_id in instrument_ids
    }


@pytest.fixture(scope="session")
def migrated_portfolio_schema(tmp_path_factory):
    return tmp_path_factory.mktemp("portfolio-schema") / "head.db"


@pytest.fixture(autouse=True)
def isolated_portfolio_store(request, tmp_path, monkeypatch, migrated_portfolio_schema):
    if request.node.get_closest_marker("postgresql_integration") is not None:
        yield
        return

    database_path = tmp_path / "portfolio.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    research_outputs_root = tmp_path / "research_outputs"
    monkeypatch.setenv("INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_PORTFOLIO_DATABASE_SCHEMA", "")
    monkeypatch.setenv("INVESTMENT_STUDIO_PORTFOLIO_RESEARCH_OUTPUTS_ROOT", str(research_outputs_root))
    monkeypatch.setenv("INVESTMENT_STUDIO_PORTFOLIO_RESEARCH_ENABLED", "true")
    # Queue/worker tests start an explicit worker.  Keeping the application
    # lifespan worker off makes all other request tests deterministic.
    monkeypatch.setenv(
        "INVESTMENT_STUDIO_PORTFOLIO_DAILY_SNAPSHOT_WORKER_ENABLED",
        "false",
    )

    from portfolio_app.core import settings as settings_module
    from portfolio_app.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()

    migration_base = request.node.get_closest_marker("migration_base_revision")
    initial_revision = "head"
    if migration_base is not None:
        if len(migration_base.args) != 1 or not isinstance(migration_base.args[0], str):
            raise ValueError("migration_base_revision requires exactly one revision string.")
        initial_revision = migration_base.args[0]
    if migration_base is None and migrated_portfolio_schema.exists():
        copyfile(migrated_portfolio_schema, database_path)
    else:
        _run_alembic_upgrade(database_url, initial_revision)
        if migration_base is None:
            # Alembic's NullPool connection is closed before copying.  Reuse
            # only the migrated schema, never a test's seeded or mutated data.
            # Historical migration tests always execute their own full chain.
            copyfile(database_path, migrated_portfolio_schema)
    InstrumentRegistryBase.metadata.create_all(bind=session_module.get_engine())

    # Migration tests intentionally pin the database below head while the
    # reusable store seed follows the current runtime model.  Bridge additive
    # runtime columns only for the duration of seeding, then restore the exact
    # historical schema before the test runs.
    temporary_seed_columns: list[tuple[str, str]] = []
    temporary_seed_tables: list[str] = []
    if migration_base is not None:
        engine = session_module.get_engine()
        with engine.begin() as connection:
            portfolio_columns = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns("portfolio_record")
            }
            if "inception_date" not in portfolio_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE portfolio_record ADD COLUMN inception_date DATE"
                )
                temporary_seed_columns.append(
                    ("portfolio_record", "inception_date")
                )
            account_columns = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns("account_record")
            }
            if "account_category" not in account_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE account_record ADD COLUMN account_category VARCHAR"
                )
                temporary_seed_columns.append(
                    ("account_record", "account_category")
                )
            for column_name in ("cash_purpose", "collateral_reference"):
                if column_name not in account_columns:
                    connection.exec_driver_sql(f"ALTER TABLE account_record ADD COLUMN {column_name} VARCHAR")
                    temporary_seed_columns.append(("account_record", column_name))
            transaction_columns = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns("transaction_record")
            }
            if "position_effective_date" not in transaction_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE transaction_record "
                    "ADD COLUMN position_effective_date DATE"
                )
                temporary_seed_columns.append(
                    ("transaction_record", "position_effective_date")
                )
            additive_transaction_columns = {
                "asset_deliveries_json": "JSON",
                "settlement_cashflows_json": "JSON",
                "lot_selections_json": "JSON",
                "transaction_sequence": "INTEGER NOT NULL DEFAULT 1",
                "lifecycle_event_type": "VARCHAR",
                "source_system": "VARCHAR(100)",
                "external_reference": "VARCHAR(200)",
                "derivative_contract_id": "VARCHAR",
            }
            for column_name, column_type in additive_transaction_columns.items():
                if column_name in transaction_columns:
                    continue
                connection.exec_driver_sql(
                    f"ALTER TABLE transaction_record ADD COLUMN {column_name} {column_type}"
                )
                temporary_seed_columns.append(
                    ("transaction_record", column_name)
                )
            research_settings_columns = {
                str(column["name"])
                for column in sa.inspect(connection).get_columns(
                    "research_settings_record"
                )
            }
            additive_research_settings_columns = {
                "backtest_cash_yield_annual": "FLOAT NOT NULL DEFAULT 0",
                "backtest_commission_bps": "FLOAT NOT NULL DEFAULT 0",
                "backtest_tax_bps": "FLOAT NOT NULL DEFAULT 0",
                "backtest_slippage_bps": "FLOAT NOT NULL DEFAULT 0",
                "backtest_implementation_delay_days": "INTEGER NOT NULL DEFAULT 1",
            }
            for column_name, column_type in additive_research_settings_columns.items():
                if column_name in research_settings_columns:
                    continue
                connection.exec_driver_sql(
                    "ALTER TABLE research_settings_record "
                    f"ADD COLUMN {column_name} {column_type}"
                )
                temporary_seed_columns.append(
                    ("research_settings_record", column_name)
                )
            if "derivative_contract_record" not in sa.inspect(
                connection
            ).get_table_names():
                connection.exec_driver_sql(
                    "CREATE TABLE derivative_contract_record ("
                    "derivative_contract_id VARCHAR NOT NULL, "
                    "portfolio_id VARCHAR NOT NULL, account_id VARCHAR NOT NULL, "
                    "contract_name VARCHAR NOT NULL, contract_type VARCHAR NOT NULL, "
                    "currency VARCHAR NOT NULL, external_reference VARCHAR, "
                    "terms_json JSON NOT NULL, created_at VARCHAR NOT NULL, "
                    "PRIMARY KEY (portfolio_id, derivative_contract_id))"
                )
                temporary_seed_tables.append("derivative_contract_record")
            contract_columns = {column["name"] for column in sa.inspect(connection).get_columns("derivative_contract_record")}
            for name, column_type in {"row_version": "INTEGER NOT NULL DEFAULT 1", "amendments_json": "JSON"}.items():
                if name not in contract_columns:
                    connection.exec_driver_sql(f"ALTER TABLE derivative_contract_record ADD COLUMN {name} {column_type}")
                    if "derivative_contract_record" not in temporary_seed_tables:
                        temporary_seed_columns.append(("derivative_contract_record", name))
            if "option_delivery_link" not in sa.inspect(
                connection
            ).get_table_names():
                connection.exec_driver_sql(
                    "CREATE TABLE option_delivery_link ("
                    "option_transaction_id VARCHAR NOT NULL PRIMARY KEY, "
                    "stock_transaction_id VARCHAR NOT NULL UNIQUE, "
                    "portfolio_id VARCHAR NOT NULL, "
                    "underlying_instrument_id VARCHAR NOT NULL, "
                    "created_at VARCHAR NOT NULL)"
                )
                temporary_seed_tables.append("option_delivery_link")

    portfolio_store.reset_store(deepcopy(TEST_PORTFOLIO_STORE))
    shared_store.reset_store(
        session_module.get_session_factory(),
        {
            "registry_name": "Test Shared Instruments",
            "instruments": deepcopy(REGISTRY_INSTRUMENT_DETAILS),
        },
    )
    if temporary_seed_columns or temporary_seed_tables:
        engine = session_module.get_engine()
        with engine.begin() as connection:
            for table_name, column_name in reversed(temporary_seed_columns):
                connection.exec_driver_sql(
                    f"ALTER TABLE {table_name} DROP COLUMN {column_name}"
                )
            for table_name in reversed(temporary_seed_tables):
                connection.exec_driver_sql(f"DROP TABLE {table_name}")

    from studio_identity import Principal
    from portfolio_app.services import daily_snapshot_worker
    monkeypatch.setattr(daily_snapshot_worker, "service_principal", lambda audience: Principal(None, "Test Valuation", "default", kind="service", service_id="test-valuation", scopes=["portfolio:maintain"]))

    monkeypatch.setattr(transaction_routes, "list_registry_instruments", lambda: deepcopy(REGISTRY_INSTRUMENTS))
    monkeypatch.setattr(instrument_charts, "get_registry_instrument_detail", _get_registry_instrument_detail)
    monkeypatch.setattr(ledger, "list_registry_instruments", lambda: deepcopy(REGISTRY_INSTRUMENTS))
    monkeypatch.setattr(ledger, "get_registry_instrument_details", _get_registry_instrument_details)
    monkeypatch.setattr(ledger, "get_shared_fx_rates", lambda: deepcopy(FX_PAYLOAD))
    monkeypatch.setattr(performance, "get_registry_instrument_detail", _get_registry_instrument_detail)
    # Both registry read shapes use the same per-test synthetic market facts;
    # individual tests can still replace one asset's detail loader.
    monkeypatch.setattr(performance, "get_registry_instrument_details", lambda instrument_ids: {
        instrument_id: performance.get_registry_instrument_detail(instrument_id)
        for instrument_id in instrument_ids
    })
    monkeypatch.setattr(performance, "get_shared_fx_rates", lambda: deepcopy(FX_PAYLOAD))

    yield

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()



def _grant_financial_fixture_portfolios(principal):
    from portfolio_app.db.models import PortfolioAccessStateModel, PortfolioMembershipModel, PortfolioRecordModel
    from portfolio_app.db.session import get_session_factory
    with get_session_factory()() as session:
        for portfolio in session.scalars(sa.select(PortfolioRecordModel)):
            if session.get(PortfolioAccessStateModel, portfolio.portfolio_id) is None:
                session.add(PortfolioAccessStateModel(portfolio_id=portfolio.portfolio_id, team_id="default"))
            if session.get(PortfolioMembershipModel, (portfolio.portfolio_id, principal.user_id)) is None:
                session.add(PortfolioMembershipModel(portfolio_id=portfolio.portfolio_id, user_id=principal.user_id, display_name=principal.display_name, role="manager", granted_by=principal.user_id, granted_at="2026-09-08T00:00:00+00:00"))
        session.commit()


def authorize_fixture_client(app):
    """Explicit manager for financial tests, including their replacement store seeds.

    The security suite constructs its own client and uses real request resolution;
    none of these synthetic grants or dependency overrides exist in that suite.
    """
    from studio_identity import Principal
    from portfolio_app.api.authorization import authenticated_principal
    from dataclasses import replace
    principal = Principal(user_id="test-manager", display_name="Test Manager", team_id="default", is_team_owner=True, team_role="admin", credential="test-session")
    _grant_financial_fixture_portfolios(principal)
    async def fixture_identity(request: Request):
        _grant_financial_fixture_portfolios(principal)
        batch_id = request.headers.get("X-Test-Capture-Batch")
        return replace(principal, resource_scope={"kind": "capture", "id": batch_id}) if batch_id else principal
    app.dependency_overrides[authenticated_principal] = fixture_identity


@pytest.fixture
def raw_client():
    import portfolio_app.main as main_module

    main_module = importlib.reload(main_module)
    authorize_fixture_client(main_module.app)

    with TestClient(main_module.app) as test_client:
        yield test_client


@pytest.fixture
def client():
    import portfolio_app.main as main_module
    from portfolio_app.services.daily_snapshots import _run_portfolio_daily_snapshot_recalculation_synchronously

    main_module = importlib.reload(main_module)
    authorize_fixture_client(main_module.app)
    class MutationClient(TestClient):
        """Drive queued work between GET attempts; raw_client exposes first responses."""
        def request(self, method, url, **kwargs):
            if method.upper() == "POST" and str(url).endswith(("/transactions", "/options/outcomes", "/transactions/internal-transfer")):
                headers = dict(kwargs.get("headers") or {})
                if not any(key.lower() == "idempotency-key" for key in headers):
                    headers["Idempotency-Key"] = str(uuid4())
                kwargs["headers"] = headers
            response = super().request(method, url, **kwargs)
            if method.upper() == "GET" and response.status_code == 503:
                detail = response.json().get("detail")
                if isinstance(detail, dict) and detail.get("code") == "portfolio_calculation_pending":
                    # The production handler has already returned without
                    # calculating. Deterministically finish its durable job in
                    # this test process, then simulate the caller's next GET.
                    _run_portfolio_daily_snapshot_recalculation_synchronously(detail["portfolio_id"])
                    response = super().request(method, url, **kwargs)
            return response
    with MutationClient(main_module.app) as test_client:
        yield test_client
