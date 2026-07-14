from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import close_all_sessions
from sqlalchemy.pool import NullPool

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT_STR = str(BACKEND_ROOT)
if BACKEND_ROOT_STR in sys.path:
    sys.path.remove(BACKEND_ROOT_STR)
sys.path.insert(0, BACKEND_ROOT_STR)

from tests.store_fixture import TEST_PORTFOLIO_STORE
from tests.postgres_test_database import MigratedPostgresTemplate

from portfolio_app.api.routes import transactions as transaction_routes
from portfolio_app.services import portfolio_store
from portfolio_ops_instrument_core import instrument_store as shared_store


DEFAULT_POSTGRES_URL = (
    "postgresql+psycopg://portfolio_ops_test:portfolio_ops_test@127.0.0.1:5432/postgres"
)


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


def _database_environment(database_url: str, database_name: str) -> dict[str, str]:
    return {
        "PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE": database_name,
        "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL": database_url,
        "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL": database_url,
        "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA": "instrument_registry",
        "PORTFOLIO_OPS_CALCULATION_REGISTRY_DATABASE_URL": database_url,
        "PORTFOLIO_OPS_CALCULATION_REGISTRY_ALEMBIC_DATABASE_URL": database_url,
        "PORTFOLIO_OPS_CALCULATION_REGISTRY_SCHEMA": "calculation_registry",
        "PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL": database_url,
        "PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL": database_url,
        "PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA": "portfolio",
        "PORTFOLIO_OPS_PORTFOLIO_ENVIRONMENT": "test",
    }


@contextmanager
def _temporary_environment(values: dict[str, str]) -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, previous_value in previous.items():
            if previous_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous_value


def _migration_config(root: Path, database_url: str) -> Config:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _dispose_portfolio_database_caches(*engines: Engine | None) -> None:
    from portfolio_app.core import settings as settings_module
    from portfolio_app.db import session as session_module

    close_all_sessions()
    cached_engine = (
        session_module.get_engine()
        if session_module.get_engine.cache_info().currsize
        else None
    )
    session_module.get_session_factory.cache_clear()
    session_module.get_engine.cache_clear()
    settings_module.get_settings.cache_clear()
    unique_engines = {
        id(engine): engine
        for engine in (*engines, cached_engine)
        if engine is not None
    }
    for engine in unique_engines.values():
        engine.dispose()


def _assert_migration_heads(database_url: str, configs: tuple[Config, ...]) -> None:
    from alembic.script import ScriptDirectory

    schemas = ("instrument_registry", "calculation_registry", "portfolio")
    expected_heads = [
        set(ScriptDirectory.from_config(config).get_heads()) for config in configs
    ]
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            for schema, expected in zip(schemas, expected_heads, strict=True):
                actual = set(
                    connection.scalars(
                        text(f'SELECT version_num FROM "{schema}".alembic_version')
                    )
                )
                if actual != expected:
                    raise RuntimeError(
                        "PostgreSQL pytest template migration head mismatch: "
                        f"schema={schema!r}, expected={sorted(expected)!r}, "
                        f"actual={sorted(actual)!r}"
                    )
    finally:
        engine.dispose()


def _seed_postgres_instrument_registry() -> None:
    from portfolio_app.db import session as session_module

    session_factory = session_module.get_session_factory()
    shared_store.reset_store(
        session_factory,
        {
            "registry_name": "Test Shared Instruments",
            "instruments": [
                {
                    **{
                        key: deepcopy(value)
                        for key, value in detail.items()
                        if key != "market_data"
                    },
                    "market_data": [],
                }
                for detail in REGISTRY_INSTRUMENT_DETAILS
            ],
        },
    )
    for detail in REGISTRY_INSTRUMENT_DETAILS:
        changed_count = shared_store.upsert_market_data_points(
            session_factory,
            instrument_id=str(detail["instrument_id"]),
            rows=deepcopy(detail["market_data"]),
        )
        if changed_count != len(detail["market_data"]):
            raise RuntimeError(
                "PostgreSQL fixture quote bootstrap did not append every source "
                f"observation: instrument={detail['instrument_id']!r}, "
                f"expected={len(detail['market_data'])}, actual={changed_count!r}"
            )


def _seed_postgres_portfolio_template() -> None:
    from portfolio_app.calculations.portfolio_daily.release_gate import (
        drain_portfolio_daily_publications,
    )
    from portfolio_app.core.settings import get_settings
    from portfolio_app.db import models as db_models
    from portfolio_app.db import session as session_module
    from portfolio_app.services.fact_currency import require_portfolio_fact_currency

    normalized = portfolio_store._normalize_store(deepcopy(TEST_PORTFOLIO_STORE))
    session_factory = session_module.get_session_factory()
    with session_factory() as session:
        for raw_portfolio in normalized["portfolios"]:
            portfolio_id = str(raw_portfolio["portfolio_id"])
            session.add(
                db_models.PortfolioRecordModel(
                    portfolio_id=portfolio_id,
                    portfolio_name=str(raw_portfolio["portfolio_name"]),
                    base_currency=require_portfolio_fact_currency(
                        raw_portfolio["base_currency"],
                        context=f"Portfolio '{portfolio_id}' base",
                    ),
                    operating_profile=str(raw_portfolio["operating_profile"]),
                    valuation_timezone=str(raw_portfolio["valuation_timezone"]),
                    valuation_cutoff_policy=str(
                        raw_portfolio["valuation_cutoff_policy"]
                    ),
                    sort_order=int(raw_portfolio.get("sort_order") or 0),
                    default_planning_taxonomy_id=None,
                    risk_policy_json=None,
                )
            )

        for raw_account in normalized["accounts"]:
            account_id = str(raw_account["account_id"])
            opened_at = raw_account.get("opened_at")
            closed_at = raw_account.get("closed_at")
            allowed_instrument_types = raw_account.get("allowed_instrument_types")
            session.add(
                db_models.AccountRecordModel(
                    account_id=account_id,
                    portfolio_id=str(raw_account["portfolio_id"]),
                    account_name=str(raw_account["account_name"]),
                    account_type=str(raw_account["account_type"]),
                    currency=require_portfolio_fact_currency(
                        raw_account["currency"],
                        context=f"Account '{account_id}'",
                    ),
                    institution=(
                        str(raw_account["institution"])
                        if raw_account.get("institution")
                        else None
                    ),
                    default_settlement_cash_account_id=(
                        str(raw_account["default_settlement_cash_account_id"])
                        if raw_account.get("default_settlement_cash_account_id")
                        else None
                    ),
                    cost_basis_method=(
                        str(raw_account["cost_basis_method"])
                        if raw_account.get("cost_basis_method")
                        else None
                    ),
                    allowed_instrument_types_json=(
                        sorted(str(item) for item in allowed_instrument_types)
                        if isinstance(allowed_instrument_types, list)
                        else None
                    ),
                    opened_at=date.fromisoformat(str(opened_at)) if opened_at else None,
                    closed_at=date.fromisoformat(str(closed_at)) if closed_at else None,
                    status=str(raw_account.get("status") or "active"),
                )
            )
        session.commit()

    transaction_records: list[dict[str, object]] = []
    expected_transaction_ids: list[str] = []
    decimal_fact_fields = (
        "quantity",
        "price",
        "gross_amount",
        "counter_amount",
        "quoted_fx_rate",
        "fees",
        "taxes",
    )
    for raw_transaction in normalized["transactions"]:
        expected_transaction_ids.append(str(raw_transaction["transaction_id"]))
        entitlement_date = raw_transaction.get("entitlement_date")
        acquisition_date = raw_transaction.get("acquisition_date")
        transaction_record = {
            **deepcopy(raw_transaction),
            "trade_date": date.fromisoformat(str(raw_transaction["trade_date"])),
            "settlement_date": date.fromisoformat(
                str(raw_transaction["settlement_date"])
            ),
            "entitlement_date": (
                date.fromisoformat(str(entitlement_date)) if entitlement_date else None
            ),
            "acquisition_date": (
                date.fromisoformat(str(acquisition_date)) if acquisition_date else None
            ),
        }
        for field_name in decimal_fact_fields:
            value = transaction_record.get(field_name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                transaction_record[field_name] = str(value)
        transaction_records.append(transaction_record)

    created_transactions = portfolio_store.create_transactions(
        "portfolio-ops",
        records=transaction_records,
        actor={
            "actor_type": "service",
            "actor_id": "system:test-fixture",
            "display_name": "PostgreSQL template fixture loader",
            "actor_source": "trusted_service",
        },
        change_reason="Loaded canonical PostgreSQL test baseline",
        source_kind="system",
    )
    actual_transaction_ids = [
        str(transaction["transaction_id"]) for transaction in created_transactions
    ]
    if actual_transaction_ids != expected_transaction_ids:
        raise RuntimeError(
            "PostgreSQL fixture transaction allocator diverged from canonical IDs: "
            f"expected={expected_transaction_ids!r}, actual={actual_transaction_ids!r}"
        )

    try:
        publication_state = drain_portfolio_daily_publications(
            session_module.get_engine(),
            settings=get_settings(),
            requested_as_of=date(2026, 4, 15),
            timeout_seconds=30.0,
        )
    except Exception as error:
        with session_factory() as diagnostic_session:
            failure = diagnostic_session.execute(
                text(
                    """
                    SELECT run_id, failure_code, failure_diagnostic
                    FROM calculation_registry.calculation_job
                    WHERE status = 'failed'
                    ORDER BY completed_at DESC, job_id DESC
                    LIMIT 1
                    """
                )
            ).mappings().one_or_none()
        raise RuntimeError(
            "PostgreSQL fixture Portfolio Daily publication failed with "
            f"durable worker diagnostic: {dict(failure) if failure else None!r}"
        ) from error
    if (
        not publication_state.complete
        or publication_state.pending_intent_count != 0
        or publication_state.active_run_count != 0
    ):
        raise RuntimeError(
            "PostgreSQL fixture baseline must be fully published and quiescent: "
            f"state={publication_state!r}"
        )

def _migrate_postgres_template(database_url: str, database_name: str) -> None:
    workspace_root = BACKEND_ROOT.parents[2]
    roots = (
        workspace_root / "infra" / "instrument_registry",
        workspace_root / "infra" / "calculation_registry",
        BACKEND_ROOT,
    )
    configs = tuple(_migration_config(root, database_url) for root in roots)
    with _temporary_environment(
        _database_environment(database_url, database_name)
    ):
        _dispose_portfolio_database_caches()
        try:
            for config in configs[:2]:
                command.upgrade(config, "head")
            _seed_postgres_instrument_registry()
            _dispose_portfolio_database_caches()
            command.upgrade(configs[2], "head")
            _assert_migration_heads(database_url, configs)
            _seed_postgres_portfolio_template()
        finally:
            _dispose_portfolio_database_caches()


@pytest.fixture(scope="session")
def migrated_postgres_template() -> Iterator[MigratedPostgresTemplate]:
    template = MigratedPostgresTemplate(
        os.getenv("PORTFOLIO_OPS_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL),
        _migrate_postgres_template,
    )
    try:
        yield template
    finally:
        template.close()


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
    if (
        request.node.get_closest_marker("no_database") is not None
        or request.node.get_closest_marker("postgresql_integration") is not None
    ):
        yield
        return

    template: MigratedPostgresTemplate = request.getfixturevalue(
        "migrated_postgres_template"
    )
    allocation_research_outputs_root = tmp_path / "allocation_research_outputs"
    with template.cloned_database() as database:
        for name, value in _database_environment(database.url, database.name).items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv(
            "PORTFOLIO_OPS_PORTFOLIO_ALLOCATION_RESEARCH_OUTPUTS_ROOT",
            str(allocation_research_outputs_root),
        )

        from portfolio_app.db import session as session_module

        _dispose_portfolio_database_caches()
        test_engine: Engine | None = None
        try:
            test_engine = session_module.get_engine()

            monkeypatch.setattr(
                transaction_routes,
                "get_registry_instrument",
                _get_registry_instrument,
            )
            monkeypatch.setattr(
                transaction_routes,
                "list_registry_instruments",
                lambda: deepcopy(REGISTRY_INSTRUMENTS),
            )
            yield
        finally:
            _dispose_portfolio_database_caches(test_engine)


@pytest.fixture
def client():
    import portfolio_app.main as main_module

    main_module = importlib.reload(main_module)
    with TestClient(main_module.app) as test_client:
        yield test_client
