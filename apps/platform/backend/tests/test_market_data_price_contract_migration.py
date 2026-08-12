from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from decimal import Decimal
import json
import os
from pathlib import Path
from queue import Queue
import time
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
MIGRATIONS_ROOT = WORKSPACE_ROOT / "infra" / "instrument_registry"
def _alembic_config(
    database_url: str,
    *,
    schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Config:
    monkeypatch.setenv(
        "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL",
        database_url,
    )
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", schema)
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _set_search_path(connection: sa.Connection, schema: str | None) -> None:
    if schema:
        connection.exec_driver_sql(f'SET search_path TO "{schema}", public')


def _insert_instruments(
    connection: sa.Connection,
    *,
    schema: str | None = None,
) -> None:
    _set_search_path(connection, schema)
    connection.execute(
        sa.text(
            """
            INSERT INTO instrument (
                instrument_id, instrument_name, instrument_type, currency,
                quote_selection_policy_json, source_settings_json,
                refresh_status_json, lifecycle_state_json, market_data_updated_at
            ) VALUES (
                :instrument_id, :instrument_name, :instrument_type, :currency,
                '{}', '{}', '{}', '{}', NULL
            )
            """
        ),
        [
            {
                "instrument_id": "bond-contract",
                "instrument_name": "Bond Contract",
                "instrument_type": "bond",
                "currency": "USD",
            },
            {
                "instrument_id": "equity-contract",
                "instrument_name": "Equity Contract",
                "instrument_type": "equity",
                "currency": "USD",
            },
            {
                "instrument_id": "fx-usd-cny",
                "instrument_name": "FX Contract",
                "instrument_type": "fx",
                "currency": "CNY",
            },
        ],
    )


def _insert_market_data(
    connection: sa.Connection,
    *,
    instrument_id: str,
    metric_family: str,
    quote_basis: str,
    as_of_date: str,
    price_unit: str | None,
    price_scale: Decimal | str | None,
    currency: str = "USD",
    value: str = "1",
    status: str = "complete",
    schema: str | None = None,
) -> None:
    _set_search_path(connection, schema)
    connection.execute(
        sa.text(
            """
            INSERT INTO instrument_market_data (
                instrument_id, metric_family, quote_basis, as_of_date,
                value, currency, price_unit, price_scale, provider, status
            ) VALUES (
                :instrument_id, :metric_family, :quote_basis, :as_of_date,
                :value, :currency, :price_unit, :price_scale, 'migration-test', :status
            )
            """
        ),
        {
            "instrument_id": instrument_id,
            "metric_family": metric_family,
            "quote_basis": quote_basis,
            "as_of_date": as_of_date,
            "price_unit": price_unit,
            "price_scale": price_scale,
            "currency": currency,
            "value": value,
            "status": status,
        },
    )


def _constraint_names(engine: Engine, *, schema: str | None = None) -> set[str]:
    return {
        str(item["name"])
        for item in sa.inspect(engine).get_check_constraints(
            "instrument_market_data",
            schema=schema,
        )
    }


def _insert_contract_test_instrument(
    connection: sa.Connection,
    *,
    instrument_id: str,
    schema: str,
) -> None:
    _set_search_path(connection, schema)
    connection.execute(
        sa.text(
            """
            INSERT INTO instrument (
                instrument_id, instrument_name, instrument_type, currency,
                quote_selection_policy_json, source_settings_json,
                refresh_status_json, lifecycle_state_json, market_data_updated_at
            ) VALUES (
                :instrument_id, :instrument_name, 'equity', 'USD',
                '{}', '{}', '{}', '{}', NULL
            )
            """
        ),
        {
            "instrument_id": instrument_id,
            "instrument_name": f"Concurrent contract {instrument_id}",
        },
    )


def _insert_equity_contract_in_worker(
    engine: Engine,
    *,
    schema: str,
    instrument_id: str,
    pid_queue: Queue[int],
) -> None:
    with engine.begin() as connection:
        _set_search_path(connection, schema)
        connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
        pid_queue.put(int(connection.scalar(sa.text("SELECT pg_backend_pid()"))))
        _insert_market_data(
            connection,
            schema=schema,
            instrument_id=instrument_id,
            metric_family="price",
            quote_basis="close",
            as_of_date="2026-07-18",
            price_unit="per_unit",
            price_scale="1",
        )


def _update_instrument_currency_in_worker(
    engine: Engine,
    *,
    schema: str,
    instrument_id: str,
    pid_queue: Queue[int],
) -> None:
    with engine.begin() as connection:
        _set_search_path(connection, schema)
        connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
        pid_queue.put(int(connection.scalar(sa.text("SELECT pg_backend_pid()"))))
        connection.execute(
            sa.text(
                "UPDATE instrument SET currency = 'CNY' "
                "WHERE instrument_id = :instrument_id"
            ),
            {"instrument_id": instrument_id},
        )


def _wait_for_advisory_lock(
    engine: Engine,
    *,
    pid: int,
    future: Future[None],
) -> None:
    deadline = time.monotonic() + 5
    last_wait: tuple[str | None, str | None] | None = None
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT wait_event_type, wait_event "
                    "FROM pg_stat_activity WHERE pid = :pid"
                ),
                {"pid": pid},
            ).one_or_none()
        if row is not None:
            last_wait = (row[0], row[1])
            if last_wait == ("Lock", "advisory"):
                return
        if future.done():
            future.result()
            pytest.fail("Concurrent contract write completed without waiting for its mutex.")
        time.sleep(0.01)
    pytest.fail(f"Concurrent contract write did not wait on an advisory lock: {last_wait!r}")


def test_market_data_price_contract_hardening_backfills_and_enforces_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'price-contract-hardening.db'}"
    config = _alembic_config(database_url, schema="", monkeypatch=monkeypatch)
    command.upgrade(config, "20260715_0008")
    engine = sa.create_engine(database_url)

    with engine.begin() as connection:
        _insert_instruments(connection)
        _insert_market_data(
            connection,
            instrument_id="bond-contract",
            metric_family="price",
            quote_basis="dirty_price",
            as_of_date="2026-07-15",
            price_unit=None,
            price_scale=None,
        )
        _insert_market_data(
            connection,
            instrument_id="equity-contract",
            metric_family="price",
            quote_basis="close",
            as_of_date="2026-07-15",
            price_unit=None,
            price_scale=None,
        )
        _insert_market_data(
            connection,
            instrument_id="fx-usd-cny",
            metric_family="fx",
            quote_basis="spot",
            as_of_date="2026-07-15",
            price_unit=None,
            price_scale=None,
            currency="CNY",
        )

    command.upgrade(config, "20260715_0009")

    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                """
                SELECT instrument_id, price_unit, price_scale
                FROM instrument_market_data
                ORDER BY instrument_id
                """
            )
        ).all()
        trigger_names = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' ORDER BY name"
                )
            )
        }

    assert [
        (instrument_id, price_unit, Decimal(str(price_scale)))
        for instrument_id, price_unit, price_scale in rows
    ] == [
        ("bond-contract", "percent_of_par", Decimal("0.01")),
        ("equity-contract", "per_unit", Decimal("1")),
        ("fx-usd-cny", "rate", Decimal("1")),
    ]
    columns = {
        str(item["name"]): item for item in sa.inspect(engine).get_columns("instrument_market_data")
    }
    assert columns["price_unit"]["nullable"] is False
    assert columns["price_scale"]["nullable"] is False
    assert _constraint_names(engine) == {
        "ck_instrument_market_data_price_unit_scale_contract",
        "ck_instrument_market_data_quote_identity_contract",
    }
    assert trigger_names.issuperset(
        {
            "trg_instrument_market_data_price_contract_insert",
            "trg_instrument_market_data_price_contract_update",
            "trg_instrument_type_price_contract",
        }
    )

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_market_data(
                connection,
                instrument_id="equity-contract",
                metric_family="price",
                quote_basis="last",
                as_of_date="2026-07-16",
                price_unit=None,
                price_scale=None,
            )
    with pytest.raises(IntegrityError, match="price contract is not canonical"):
        with engine.begin() as connection:
            _insert_market_data(
                connection,
                instrument_id="bond-contract",
                metric_family="price",
                quote_basis="clean_price",
                as_of_date="2026-07-16",
                price_unit="per_unit",
                price_scale="1",
            )
    with pytest.raises(IntegrityError, match="price contract is not canonical"):
        with engine.begin() as connection:
            _insert_market_data(
                connection,
                instrument_id="equity-contract",
                metric_family="price",
                quote_basis="accrued_interest",
                as_of_date="2026-07-16",
                price_unit="percent_of_par",
                price_scale="0.01",
            )
    with pytest.raises(IntegrityError, match="instrument_type update conflicts"):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE instrument SET instrument_type = 'bond' "
                    "WHERE instrument_id = 'equity-contract'"
                )
            )

    with engine.begin() as connection:
        _insert_market_data(
            connection,
            instrument_id="bond-contract",
            metric_family="price",
            quote_basis="clean_price",
            as_of_date="2026-07-16",
            price_unit="percent_of_par",
            price_scale="0.01",
        )

    command.downgrade(config, "20260715_0008")
    columns = {
        str(item["name"]): item for item in sa.inspect(engine).get_columns("instrument_market_data")
    }
    assert columns["price_unit"]["nullable"] is True
    assert columns["price_scale"]["nullable"] is True
    assert _constraint_names(engine) == set()
    with engine.begin() as connection:
        _insert_market_data(
            connection,
            instrument_id="equity-contract",
            metric_family="price",
            quote_basis="last",
            as_of_date="2026-07-17",
            price_unit=None,
            price_scale=None,
        )


def test_observation_contract_normalizes_legacy_refresh_cursor_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'observation-refresh.db'}"
    config = _alembic_config(database_url, schema="", monkeypatch=monkeypatch)
    command.upgrade(config, "20260715_0010")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        _insert_contract_test_instrument(
            connection,
            instrument_id="legacy-email-refresh",
            schema="",
        )
        connection.execute(
            sa.text(
                "UPDATE instrument SET source_settings_json = :settings, "
                "refresh_status_json = :status WHERE instrument_id = :instrument_id"
            ).bindparams(
                sa.bindparam("settings", type_=sa.JSON()),
                sa.bindparam("status", type_=sa.JSON()),
            ),
            {
                "instrument_id": "legacy-email-refresh",
                "settings": {"source_mode": "email"},
                "status": {
                    "status": "imported",
                    "message": "legacy success",
                    "requested_at": "2026-07-09T13:00:00Z",
                    "requested_by": "legacy",
                    "mode": "email",
                },
            },
        )

    command.upgrade(config, "20260715_0011")
    with engine.connect() as connection:
        payload, policy_payload = connection.execute(
            sa.text(
                "SELECT refresh_status_json, quote_selection_policy_json FROM instrument "
                "WHERE instrument_id = 'legacy-email-refresh'"
            )
        ).one()
    normalized = payload if isinstance(payload, dict) else json.loads(payload)
    normalized_policy = (
        policy_payload
        if isinstance(policy_payload, dict)
        else json.loads(policy_payload)
    )
    assert normalized == {
        "status": "imported",
        "message": "legacy success",
        "requested_at": "2026-07-09T13:00:00Z",
        "requested_by": "legacy",
        "mode": "email",
        "last_successful_requested_at": "2026-07-09T13:00:00Z",
    }
    assert normalized_policy == {
        "trading": ["last", "close"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close", "close", "last"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    }


def test_observation_contract_downgrade_restores_working_0010_sqlite_triggers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'observation-downgrade.db'}"
    config = _alembic_config(database_url, schema="", monkeypatch=monkeypatch)
    command.upgrade(config, "20260715_0010")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        _insert_contract_test_instrument(
            connection,
            instrument_id="downgrade-contract",
            schema="",
        )
        connection.execute(
            sa.text(
                "UPDATE instrument SET source_settings_json = :settings, "
                "refresh_status_json = :status WHERE instrument_id = :instrument_id"
            ).bindparams(
                sa.bindparam("settings", type_=sa.JSON()),
                sa.bindparam("status", type_=sa.JSON()),
            ),
            {
                "instrument_id": "downgrade-contract",
                "settings": {"source_mode": "email"},
                "status": {
                    "status": "imported",
                    "requested_at": "2026-07-10T08:30:00Z",
                    "mode": "email",
                },
            },
        )

    command.upgrade(config, "20260715_0011")
    command.downgrade(config, "20260715_0010")

    with engine.begin() as connection:
        # Revision 0010 deliberately allowed fx/spot identity to derive a rate
        # unit independently of the instrument type.  This insert executes the
        # restored relaxed CASE expression instead of merely inspecting SQL.
        _insert_market_data(
            connection,
            instrument_id="downgrade-contract",
            metric_family="fx",
            quote_basis="spot",
            as_of_date="2026-07-16",
            price_unit="rate",
            price_scale="1",
        )

    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260715_0010"
        )
        refresh_payload, policy_payload = connection.execute(
            sa.text(
                "SELECT refresh_status_json, quote_selection_policy_json "
                "FROM instrument WHERE instrument_id = 'downgrade-contract'"
            )
        ).one()
    normalized_refresh = (
        refresh_payload
        if isinstance(refresh_payload, dict)
        else json.loads(refresh_payload)
    )
    normalized_policy = (
        policy_payload if isinstance(policy_payload, dict) else json.loads(policy_payload)
    )
    assert normalized_refresh["last_successful_requested_at"] == "2026-07-10T08:30:00Z"
    assert set(normalized_policy) == {
        "trading",
        "valuation",
        "total_return",
        "chart",
        "reference",
    }


def test_observation_contract_preflight_rejects_illegal_quote_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'observation-policy-drift.db'}"
    config = _alembic_config(database_url, schema="", monkeypatch=monkeypatch)
    command.upgrade(config, "20260715_0010")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        _insert_contract_test_instrument(
            connection,
            instrument_id="policy-drift",
            schema="",
        )
        connection.execute(
            sa.text(
                "UPDATE instrument SET quote_selection_policy_json = :policy "
                "WHERE instrument_id = 'policy-drift'"
            ).bindparams(sa.bindparam("policy", type_=sa.JSON())),
            {
                "policy": {
                    "trading": ["close"],
                    "valuation": ["adjusted_close"],
                    "total_return": ["adjusted_close"],
                    "chart": ["adjusted_close"],
                    "reference": ["close"],
                }
            },
        )

    with pytest.raises(RuntimeError, match="valuation contains total-return"):
        command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260715_0010"
        )


def test_observation_contract_preflight_rejects_existing_currency_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'observation-drift.db'}"
    config = _alembic_config(database_url, schema="", monkeypatch=monkeypatch)
    command.upgrade(config, "20260715_0010")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        _insert_contract_test_instrument(
            connection,
            instrument_id="currency-drift",
            schema="",
        )
        _insert_market_data(
            connection,
            instrument_id="currency-drift",
            metric_family="price",
            quote_basis="close",
            as_of_date="2026-07-15",
            price_unit="per_unit",
            price_scale="1",
            currency="HKD",
        )

    with pytest.raises(RuntimeError, match="point currency does not match"):
        command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260715_0010"
        )


@pytest.mark.parametrize(
    ("value", "currency", "status", "metric_family", "quote_basis", "price_unit"),
    [
        ("0", "USD", "complete", "price", "close", "per_unit"),
        ("NaN", "USD", "complete", "price", "close", "per_unit"),
        ("1abc", "USD", "complete", "price", "close", "per_unit"),
        ("1.2.3", "USD", "complete", "price", "close", "per_unit"),
        ("1e", "USD", "complete", "price", "close", "per_unit"),
        ("1", "HKD", "complete", "price", "close", "per_unit"),
        ("1", "USD", "stale", "price", "close", "per_unit"),
        ("1", "USD", "complete", "fx", "spot", "rate"),
    ],
    ids=(
        "zero",
        "non-finite",
        "trailing-garbage",
        "multiple-decimal-points",
        "missing-exponent",
        "wrong-currency",
        "bad-status",
        "non-fx-spot",
    ),
)
def test_observation_contract_rejects_direct_sqlite_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    currency: str,
    status: str,
    metric_family: str,
    quote_basis: str,
    price_unit: str,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / ('observation-' + value + currency + status + metric_family + '.db')}"
    config = _alembic_config(database_url, schema="", monkeypatch=monkeypatch)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        _insert_contract_test_instrument(
            connection,
            instrument_id="strict-observation",
            schema="",
        )
    with pytest.raises(IntegrityError, match="contract is not canonical"):
        with engine.begin() as connection:
            _insert_market_data(
                connection,
                instrument_id="strict-observation",
                metric_family=metric_family,
                quote_basis=quote_basis,
                as_of_date="2026-07-15",
                price_unit=price_unit,
                price_scale="1",
                value=value,
                currency=currency,
                status=status,
            )


@pytest.mark.parametrize(
    ("metric_family", "quote_basis", "price_unit", "price_scale"),
    [
        ("price", "close", "per_unit", None),
        ("nav", "close", None, None),
        ("price", "dirty_price", "per_unit", "1"),
    ],
    ids=("partial-contract", "unknown-quote-identity", "explicit-conflict"),
)
def test_market_data_price_contract_hardening_fails_closed_on_existing_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metric_family: str,
    quote_basis: str,
    price_unit: str | None,
    price_scale: str | None,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'price-contract-drift.db'}"
    config = _alembic_config(database_url, schema="", monkeypatch=monkeypatch)
    command.upgrade(config, "20260715_0008")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        _insert_instruments(connection)
        _insert_market_data(
            connection,
            instrument_id="bond-contract",
            metric_family=metric_family,
            quote_basis=quote_basis,
            as_of_date="2026-07-15",
            price_unit=price_unit,
            price_scale=price_scale,
        )

    with pytest.raises(RuntimeError, match="existing market-data rows violate"):
        command.upgrade(config, "head")

    with engine.connect() as connection:
        revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
        row = connection.execute(
            sa.text(
                "SELECT metric_family, quote_basis, price_unit, price_scale "
                "FROM instrument_market_data"
            )
        ).one()
    assert revision == "20260715_0008"
    assert tuple(row[:3]) == (metric_family, quote_basis, price_unit)
    actual_scale = None if row[3] is None else Decimal(str(row[3]))
    expected_scale = None if price_scale is None else Decimal(price_scale)
    assert actual_scale == expected_scale


@pytest.fixture()
def postgres_registry_contract_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, str, Config]]:
    database_url = os.getenv("PORTFOLIO_OPS_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("PORTFOLIO_OPS_TEST_POSTGRES_URL is not explicitly configured.")
    engine = sa.create_engine(database_url)
    schema = f"registry_contract_{uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    except (OperationalError, sa.exc.DBAPIError) as error:
        engine.dispose()
        pytest.skip(f"PostgreSQL test schema is unavailable: {error}")

    config = _alembic_config(database_url, schema=schema, monkeypatch=monkeypatch)
    try:
        yield engine, schema, config
    finally:
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            engine.dispose()


@pytest.mark.postgresql_integration
def test_market_data_price_contract_hardening_on_postgresql(
    postgres_registry_contract_schema: tuple[Engine, str, Config],
) -> None:
    engine, schema, config = postgres_registry_contract_schema
    command.upgrade(config, "20260715_0008")
    with engine.begin() as connection:
        _insert_instruments(connection, schema=schema)
        _insert_market_data(
            connection,
            schema=schema,
            instrument_id="bond-contract",
            metric_family="price",
            quote_basis="dirty_price",
            as_of_date="2026-07-15",
            price_unit=None,
            price_scale=None,
        )
        _insert_market_data(
            connection,
            schema=schema,
            instrument_id="equity-contract",
            metric_family="price",
            quote_basis="close",
            as_of_date="2026-07-15",
            price_unit="per_unit",
            price_scale=None,
        )

    with pytest.raises(RuntimeError, match="existing market-data rows violate"):
        command.upgrade(config, "20260715_0009")

    with engine.begin() as connection:
        _set_search_path(connection, schema)
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260715_0008"
        )
        connection.execute(
            sa.text(
                "UPDATE instrument_market_data SET price_scale = 1 "
                "WHERE instrument_id = 'equity-contract'"
            )
        )

    command.upgrade(config, "20260715_0009")
    columns = {
        str(item["name"]): item
        for item in sa.inspect(engine).get_columns(
            "instrument_market_data",
            schema=schema,
        )
    }
    assert columns["price_unit"]["nullable"] is False
    assert columns["price_scale"]["nullable"] is False
    assert _constraint_names(engine, schema=schema) == {
        "ck_instrument_market_data_price_unit_scale_contract",
        "ck_instrument_market_data_quote_identity_contract",
    }
    with engine.connect() as connection:
        _set_search_path(connection, schema)
        assert connection.execute(
            sa.text(
                "SELECT price_unit, price_scale FROM instrument_market_data "
                "WHERE instrument_id = 'bond-contract'"
            )
        ).one() == ("percent_of_par", Decimal("0.010000000000"))

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_market_data(
                connection,
                schema=schema,
                instrument_id="bond-contract",
                metric_family="price",
                quote_basis="clean_price",
                as_of_date="2026-07-16",
                price_unit="per_unit",
                price_scale="1",
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _set_search_path(connection, schema)
            connection.execute(
                sa.text(
                    "UPDATE instrument SET instrument_type = 'bond' "
                    "WHERE instrument_id = 'equity-contract'"
                )
            )

    with engine.begin() as connection:
        _insert_market_data(
            connection,
            schema=schema,
            instrument_id="bond-contract",
            metric_family="price",
            quote_basis="clean_price",
            as_of_date="2026-07-16",
            price_unit="percent_of_par",
            price_scale="0.01",
        )

    command.downgrade(config, "20260715_0008")
    downgraded_columns = {
        str(item["name"]): item
        for item in sa.inspect(engine).get_columns(
            "instrument_market_data",
            schema=schema,
        )
    }
    assert downgraded_columns["price_unit"]["nullable"] is True
    assert downgraded_columns["price_scale"]["nullable"] is True
    assert _constraint_names(engine, schema=schema) == set()
    with engine.connect() as connection:
        remaining_contract_objects = connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM pg_trigger AS trigger
                JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = :schema
                  AND NOT trigger.tgisinternal
                  AND trigger.tgname IN (
                      'trg_instrument_market_data_price_contract',
                      'trg_instrument_type_price_contract'
                  )
                """
            ),
            {"schema": schema},
        )
    assert remaining_contract_objects == 0
    with engine.begin() as connection:
        _insert_market_data(
            connection,
            schema=schema,
            instrument_id="equity-contract",
            metric_family="price",
            quote_basis="last",
            as_of_date="2026-07-17",
            price_unit=None,
            price_scale=None,
        )


@pytest.mark.postgresql_integration
def test_price_contract_serialization_preflight_rejects_existing_write_skew(
    postgres_registry_contract_schema: tuple[Engine, str, Config],
) -> None:
    engine, schema, config = postgres_registry_contract_schema
    command.upgrade(config, "20260715_0009")
    instrument_id = "preexisting-write-skew"
    with engine.begin() as connection:
        _insert_contract_test_instrument(
            connection,
            instrument_id=instrument_id,
            schema=schema,
        )

    update_connection = engine.connect()
    insert_connection = engine.connect()
    try:
        _set_search_path(update_connection, schema)
        update_connection.commit()
        _set_search_path(insert_connection, schema)
        insert_connection.commit()

        update_transaction = update_connection.begin()
        update_connection.execute(
            sa.text(
                "UPDATE instrument SET instrument_type = 'bond' "
                "WHERE instrument_id = :instrument_id"
            ),
            {"instrument_id": instrument_id},
        )
        insert_transaction = insert_connection.begin()
        _insert_market_data(
            insert_connection,
            schema=schema,
            instrument_id=instrument_id,
            metric_family="price",
            quote_basis="close",
            as_of_date="2026-07-18",
            price_unit="per_unit",
            price_scale="1",
        )
        insert_transaction.commit()
        update_transaction.commit()
    finally:
        if update_connection.in_transaction():
            update_connection.rollback()
        if insert_connection.in_transaction():
            insert_connection.rollback()
        update_connection.close()
        insert_connection.close()

    with pytest.raises(RuntimeError, match="concurrent price-contract drift already exists"):
        command.upgrade(config, "head")

    with engine.connect() as connection:
        _set_search_path(connection, schema)
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260715_0009"
        )
        assert connection.execute(
            sa.text(
                """
                SELECT instrument.instrument_type, market_data.price_unit,
                       market_data.price_scale
                FROM instrument
                JOIN instrument_market_data AS market_data USING (instrument_id)
                WHERE instrument.instrument_id = :instrument_id
                """
            ),
            {"instrument_id": instrument_id},
        ).one() == ("bond", "per_unit", Decimal("1.000000000000"))


@pytest.mark.postgresql_integration
def test_price_contract_serialization_blocks_both_concurrent_write_orders(
    postgres_registry_contract_schema: tuple[Engine, str, Config],
) -> None:
    engine, schema, config = postgres_registry_contract_schema
    command.upgrade(config, "head")
    update_first_id = "serialized-update-first"
    insert_first_id = "serialized-insert-first"
    with engine.begin() as connection:
        _insert_contract_test_instrument(
            connection,
            instrument_id=update_first_id,
            schema=schema,
        )
        _insert_contract_test_instrument(
            connection,
            instrument_id=insert_first_id,
            schema=schema,
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        update_connection = engine.connect()
        update_transaction = update_connection.begin()
        insert_future: Future[None] | None = None
        try:
            _set_search_path(update_connection, schema)
            update_connection.execute(
                sa.text(
                    "UPDATE instrument SET currency = 'CNY' "
                    "WHERE instrument_id = :instrument_id"
                ),
                {"instrument_id": update_first_id},
            )
            insert_pid_queue: Queue[int] = Queue()
            insert_future = executor.submit(
                _insert_equity_contract_in_worker,
                engine,
                schema=schema,
                instrument_id=update_first_id,
                pid_queue=insert_pid_queue,
            )
            _wait_for_advisory_lock(
                engine,
                pid=insert_pid_queue.get(timeout=5),
                future=insert_future,
            )
            update_transaction.commit()
            with pytest.raises(
                IntegrityError,
                match="market-data currency must match instrument currency",
            ):
                insert_future.result(timeout=5)
        finally:
            if update_transaction.is_active:
                update_transaction.rollback()
            update_connection.close()
            if insert_future is not None and not insert_future.done():
                insert_future.result(timeout=5)

        insert_connection = engine.connect()
        insert_transaction = insert_connection.begin()
        update_future: Future[None] | None = None
        try:
            _insert_market_data(
                insert_connection,
                schema=schema,
                instrument_id=insert_first_id,
                metric_family="price",
                quote_basis="close",
                as_of_date="2026-07-18",
                price_unit="per_unit",
                price_scale="1",
            )
            update_pid_queue: Queue[int] = Queue()
            update_future = executor.submit(
                _update_instrument_currency_in_worker,
                engine,
                schema=schema,
                instrument_id=insert_first_id,
                pid_queue=update_pid_queue,
            )
            _wait_for_advisory_lock(
                engine,
                pid=update_pid_queue.get(timeout=5),
                future=update_future,
            )
            insert_transaction.commit()
            with pytest.raises(IntegrityError, match="instrument_type update conflicts"):
                update_future.result(timeout=5)
        finally:
            if insert_transaction.is_active:
                insert_transaction.rollback()
            insert_connection.close()
            if update_future is not None and not update_future.done():
                update_future.result(timeout=5)

    with engine.connect() as connection:
        _set_search_path(connection, schema)
        rows = connection.execute(
            sa.text(
                """
                SELECT instrument.instrument_id, instrument.instrument_type,
                       instrument.currency,
                       count(market_data.instrument_market_data_id)
                FROM instrument
                LEFT JOIN instrument_market_data AS market_data USING (instrument_id)
                WHERE instrument.instrument_id IN (:update_first_id, :insert_first_id)
                GROUP BY instrument.instrument_id, instrument.instrument_type,
                         instrument.currency
                ORDER BY instrument.instrument_id
                """
            ),
            {
                "update_first_id": update_first_id,
                "insert_first_id": insert_first_id,
            },
        ).all()
    assert rows == [
        (insert_first_id, "equity", "USD", 1),
        (update_first_id, "equity", "CNY", 0),
    ]


@pytest.mark.postgresql_integration
def test_option_contract_identity_migration_on_postgresql(
    postgres_registry_contract_schema: tuple[Engine, str, Config],
) -> None:
    engine, schema, config = postgres_registry_contract_schema
    command.upgrade(config, "20260804_0017")
    base_insert = sa.text(
        """
        INSERT INTO instrument (
            instrument_id, instrument_name, instrument_type, currency,
            quote_selection_policy_json, source_settings_json,
            refresh_status_json, lifecycle_state_json,
            market_data_updated_at
        ) VALUES (
            :instrument_id, :instrument_id, :instrument_type, 'USD',
            '{}', '{}', '{}', '{}', NULL
        )
        """
    )
    with engine.begin() as connection:
        _set_search_path(connection, schema)
        connection.execute(
            base_insert,
            {
                "instrument_id": "option-underlying",
                "instrument_type": "equity",
            },
        )
        connection.execute(
            base_insert,
            {
                "instrument_id": "identity-less-option",
                "instrument_type": "option",
            },
        )

    with pytest.raises(RuntimeError, match="identity-less option instruments exist"):
        command.upgrade(config, "20260806_0018")

    with engine.begin() as connection:
        _set_search_path(connection, schema)
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260804_0017"
        )
        connection.execute(
            sa.text("DELETE FROM instrument WHERE instrument_id = 'identity-less-option'")
        )

    command.upgrade(config, "20260806_0018")
    with engine.connect() as connection:
        _set_search_path(connection, schema)
        constraint_rows = {
            str(row["constraint_name"]): dict(row)
            for row in connection.execute(
                sa.text(
                    """
                    SELECT
                        con.conname AS constraint_name,
                        con.contype AS constraint_type,
                        pg_get_constraintdef(con.oid) AS definition,
                        ref_ns.nspname AS referred_schema,
                        ref_cls.relname AS referred_table
                    FROM pg_constraint AS con
                    JOIN pg_class AS cls ON cls.oid = con.conrelid
                    JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
                    LEFT JOIN pg_class AS ref_cls ON ref_cls.oid = con.confrelid
                    LEFT JOIN pg_namespace AS ref_ns ON ref_ns.oid = ref_cls.relnamespace
                    WHERE ns.nspname = :schema
                      AND cls.relname = 'instrument'
                    """
                ),
                {"schema": schema},
            ).mappings()
        }
        indexes = {
            str(row.indexname)
            for row in connection.execute(
                sa.text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = :schema AND tablename = 'instrument'"
                ),
                {"schema": schema},
            )
        }

    assert constraint_rows["ck_instrument_option_contract_identity"][
        "constraint_type"
    ] == "c"
    assert constraint_rows["ck_instrument_option_underlying_distinct"][
        "constraint_type"
    ] == "c"
    option_foreign_key = constraint_rows[
        "fk_instrument_option_underlying_instrument_id_instrument"
    ]
    assert option_foreign_key["constraint_type"] == "f"
    assert option_foreign_key["referred_schema"] == schema
    assert option_foreign_key["referred_table"] == "instrument"
    assert "ON DELETE RESTRICT" in str(option_foreign_key["definition"])
    assert "ix_instrument_option_underlying" in indexes

    identity_insert = sa.text(
        """
        INSERT INTO instrument (
            instrument_id, instrument_name, instrument_type, currency,
            option_underlying_instrument_id, option_type, option_expiry_date,
            option_strike, option_contract_multiplier, option_settlement_type,
            option_contract_currency, quote_selection_policy_json,
            source_settings_json, refresh_status_json, lifecycle_state_json,
            market_data_updated_at
        ) VALUES (
            :instrument_id, :instrument_id, :instrument_type, :currency,
            :underlying_id, :option_type, :expiry_date,
            :strike, :multiplier, :settlement_type,
            :contract_currency, '{}', '{}', '{}', '{}', NULL
        )
        """
    )
    complete_identity = {
        "instrument_id": "complete-option",
        "instrument_type": "option",
        "currency": "USD",
        "underlying_id": "option-underlying",
        "option_type": "call",
        "expiry_date": "2026-12-18",
        "strike": "100",
        "multiplier": "100",
        "settlement_type": "physical",
        "contract_currency": "USD",
    }
    with engine.begin() as connection:
        _set_search_path(connection, schema)
        connection.execute(identity_insert, complete_identity)

    invalid_identities = [
        {
            **complete_identity,
            "instrument_id": "partial-option",
            "contract_currency": None,
        },
        {
            **complete_identity,
            "instrument_id": "missing-underlying-option",
            "underlying_id": "missing-underlying",
        },
        {
            **complete_identity,
            "instrument_id": "self-option",
            "underlying_id": "self-option",
        },
    ]
    for invalid_identity in invalid_identities:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _set_search_path(connection, schema)
            connection.execute(identity_insert, invalid_identity)

    with pytest.raises(RuntimeError, match="option contract identity exists"):
        command.downgrade(config, "20260804_0017")

    with engine.begin() as connection:
        _set_search_path(connection, schema)
        connection.execute(
            sa.text("DELETE FROM instrument WHERE instrument_id = 'complete-option'")
        )
    command.downgrade(config, "20260804_0017")
    downgraded_columns = {
        str(column["name"])
        for column in sa.inspect(engine).get_columns("instrument", schema=schema)
    }
    assert "option_underlying_instrument_id" not in downgraded_columns

    command.upgrade(config, "head")
    with engine.connect() as connection:
        _set_search_path(connection, schema)
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260812_0024"
        )


@pytest.mark.postgresql_integration
def test_derivative_contract_reconciliation_migration_on_postgresql(
    postgres_registry_contract_schema: tuple[Engine, str, Config],
) -> None:
    engine, schema, config = postgres_registry_contract_schema
    command.upgrade(config, "20260806_0018")
    base_insert = sa.text(
        """
        INSERT INTO instrument (
            instrument_id, instrument_name, instrument_type, currency,
            quote_selection_policy_json, source_settings_json,
            refresh_status_json, lifecycle_state_json,
            market_data_updated_at
        ) VALUES (
            :instrument_id, :instrument_id, :instrument_type, 'USD',
            '{}', '{}', '{}', '{}', NULL
        )
        """
    )
    with engine.begin() as connection:
        _set_search_path(connection, schema)
        connection.execute(
            base_insert,
            {"instrument_id": "derivative-underlying", "instrument_type": "equity"},
        )
        connection.execute(
            base_insert,
            {"instrument_id": "ungoverned-fcn", "instrument_type": "fcn"},
        )

    with pytest.raises(RuntimeError, match="derivative instruments lack"):
        command.upgrade(config, "20260807_0019")
    with engine.begin() as connection:
        _set_search_path(connection, schema)
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
            "20260806_0018"
        )
        connection.execute(
            sa.text("DELETE FROM instrument WHERE instrument_id = 'ungoverned-fcn'")
        )

    command.upgrade(config, "20260807_0019")
    inspector = sa.inspect(engine)
    assert {
        "fcn_contract_json",
        "derivative_adjustment_policy_json",
    }.issubset(
        {
            str(column["name"])
            for column in inspector.get_columns("instrument", schema=schema)
        }
    )
    assert "instrument_broker_identifier" in inspector.get_table_names(schema=schema)
    assert {
        str(constraint["name"])
        for constraint in inspector.get_check_constraints("instrument", schema=schema)
    }.issuperset(
        {
            "ck_instrument_fcn_contract_metadata",
            "ck_instrument_derivative_adjustment_policy",
        }
    )
    assert {
        str(constraint["name"])
        for constraint in inspector.get_check_constraints(
            "instrument_broker_identifier",
            schema=schema,
        )
    } == {"ck_instrument_broker_identifier_broker_identifier_type"}
    assert {
        str(constraint["name"])
        for constraint in inspector.get_unique_constraints(
            "instrument_broker_identifier",
            schema=schema,
        )
    } == {"uq_instrument_broker_identifier_identity"}
    broker_foreign_keys = inspector.get_foreign_keys(
        "instrument_broker_identifier",
        schema=schema,
    )
    assert len(broker_foreign_keys) == 1
    assert broker_foreign_keys[0]["referred_schema"] == schema
    assert broker_foreign_keys[0]["referred_table"] == "instrument"

    derivative_insert = sa.text(
        """
        INSERT INTO instrument (
            instrument_id, instrument_name, instrument_type, currency,
            option_underlying_instrument_id, option_type, option_expiry_date,
            option_strike, option_contract_multiplier, option_settlement_type,
            option_contract_currency, fcn_contract_json,
            derivative_adjustment_policy_json, quote_selection_policy_json,
            source_settings_json, refresh_status_json, lifecycle_state_json,
            market_data_updated_at
        ) VALUES (
            :instrument_id, :instrument_id, :instrument_type, 'USD',
            :underlying_id, :option_type, :expiry_date,
            :strike, :multiplier, :settlement_type,
            :contract_currency, :fcn_contract,
            :adjustment_policy, '{}', '{}', '{}', '{}', NULL
        )
        """
    )
    complete_option = {
        "instrument_id": "governed-option",
        "instrument_type": "option",
        "underlying_id": "derivative-underlying",
        "option_type": "call",
        "expiry_date": "2027-06-18",
        "strike": "100",
        "multiplier": "100",
        "settlement_type": "physical",
        "contract_currency": "USD",
        "fcn_contract": None,
        "adjustment_policy": json.dumps({"policy_type": "contract_terms"}),
    }
    complete_fcn = {
        "instrument_id": "governed-fcn",
        "instrument_type": "fcn",
        "underlying_id": None,
        "option_type": None,
        "expiry_date": None,
        "strike": None,
        "multiplier": None,
        "settlement_type": None,
        "contract_currency": None,
        "fcn_contract": json.dumps({"notional": "100000", "issuer": "Bank"}),
        "adjustment_policy": json.dumps({"policy_type": "contract_terms"}),
    }
    with engine.begin() as connection:
        _set_search_path(connection, schema)
        connection.execute(derivative_insert, complete_option)
        connection.execute(derivative_insert, complete_fcn)

    for invalid_derivative in (
        {**complete_option, "instrument_id": "option-without-policy", "adjustment_policy": None},
        {**complete_fcn, "instrument_id": "fcn-without-contract", "fcn_contract": None},
        {
            **complete_fcn,
            "instrument_id": "equity-with-derivative-metadata",
            "instrument_type": "equity",
        },
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _set_search_path(connection, schema)
            connection.execute(derivative_insert, invalid_derivative)

    broker_insert = sa.text(
        """
        INSERT INTO instrument_broker_identifier (
            instrument_id, broker, identifier_type, identifier_value, is_primary
        ) VALUES (
            :instrument_id, :broker, :identifier_type, :identifier_value, true
        )
        """
    )
    broker_identity = {
        "instrument_id": "governed-option",
        "broker": "ibkr",
        "identifier_type": "contract_id",
        "identifier_value": "987654321",
    }
    with engine.begin() as connection:
        _set_search_path(connection, schema)
        connection.execute(broker_insert, broker_identity)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        _set_search_path(connection, schema)
        connection.execute(
            broker_insert,
            {**broker_identity, "instrument_id": "governed-fcn"},
        )

    with pytest.raises(RuntimeError, match="broker reconciliation identity"):
        command.downgrade(config, "20260806_0018")

    with engine.begin() as connection:
        _set_search_path(connection, schema)
        connection.execute(
            sa.text("DELETE FROM instrument WHERE instrument_type IN ('fcn', 'option')")
        )
        connection.execute(
            broker_insert,
            {
                "instrument_id": "derivative-underlying",
                "broker": "custodian",
                "identifier_type": "symbol",
                "identifier_value": "UNDERLYING",
            },
        )
    with pytest.raises(RuntimeError, match="broker reconciliation identity"):
        command.downgrade(config, "20260806_0018")

    with engine.begin() as connection:
        _set_search_path(connection, schema)
        connection.execute(sa.text("DELETE FROM instrument_broker_identifier"))
    command.downgrade(config, "20260806_0018")
    assert "instrument_broker_identifier" not in sa.inspect(engine).get_table_names(
        schema=schema
    )
