from __future__ import annotations

from datetime import date
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]

pytestmark = pytest.mark.postgresql_integration

RECONCILIATION_REVISION = "20260715_0032r"
RECONCILIATION_PARENT = "20260711_0032"
LEGACY_FOREIGN_KEY = "fk_transaction_record_asset_id_instrument"
CURRENT_FOREIGN_KEY = "fk_transaction_record_instrument_id_instrument"


def _admin_database_url(database_url: str) -> str:
    return make_url(database_url).set(database="postgres").render_as_string(
        hide_password=False
    )


def _portfolio_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _run_registry_upgrade(revision: str = "20260902_0029") -> None:
    root = WORKSPACE_ROOT / "shared-data" / "instruments"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, revision)


@pytest.fixture
def postgres_reconciliation_database(
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    base_database_url = os.getenv("INVESTMENT_STUDIO_TEST_POSTGRES_URL")
    if not base_database_url:
        pytest.skip("INVESTMENT_STUDIO_TEST_POSTGRES_URL is not explicitly configured.")
    database_name = f"investment_studio_reconcile_{uuid4().hex[:8]}"
    database_url = make_url(base_database_url).set(database=database_name).render_as_string(
        hide_password=False
    )
    admin_engine = sa.create_engine(
        _admin_database_url(base_database_url),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
            connection.execute(sa.text(f'CREATE DATABASE "{database_name}"'))
    except Exception as error:  # pragma: no cover - depends on configured service
        pytest.fail(f"Configured PostgreSQL integration target is unavailable: {error}")
    finally:
        admin_engine.dispose()

    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "instrument_data")
    monkeypatch.setenv("INVESTMENT_STUDIO_PORTFOLIO_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_PORTFOLIO_ALEMBIC_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_PORTFOLIO_DATABASE_SCHEMA", "portfolio")

    from portfolio_app.core import settings as settings_module
    from portfolio_app.db import session as session_module

    settings_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()

    _run_registry_upgrade()
    command.upgrade(_portfolio_config(database_url), "20260715_0038")

    yield database_url

    cleanup_engine = sa.create_engine(
        _admin_database_url(base_database_url),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with cleanup_engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database_name
                      AND pid <> pg_backend_pid()
                    """
                ),
                {"database_name": database_name},
            )
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{database_name}"'))
    finally:
        cleanup_engine.dispose()
        settings_module.get_settings.cache_clear()
        session_module.get_engine.cache_clear()
        session_module.get_session_factory.cache_clear()


def _set_search_path(connection: sa.Connection) -> None:
    connection.exec_driver_sql(
        "SET search_path TO portfolio, instrument_registry, public"
    )


def _constraint_names(connection: sa.Connection) -> set[str]:
    return {
        str(name)
        for name in connection.scalars(
            sa.text(
                """
                SELECT con.conname
                FROM pg_constraint AS con
                JOIN pg_class AS cls ON cls.oid = con.conrelid
                JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
                WHERE ns.nspname = 'portfolio'
                  AND cls.relname = 'transaction_record'
                  AND con.contype = 'f'
                """
            )
        )
    }


def _object_oid(
    connection: sa.Connection,
    *,
    object_name: str,
    constraint: bool = False,
) -> int:
    if constraint:
        statement = sa.text(
            """
            SELECT con.oid
            FROM pg_constraint AS con
            JOIN pg_class AS cls ON cls.oid = con.conrelid
            JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
            WHERE ns.nspname = 'portfolio'
              AND cls.relname = 'transaction_record'
              AND con.conname = :object_name
            """
        )
    else:
        statement = sa.text(
            """
            SELECT cls.oid
            FROM pg_class AS cls
            JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
            WHERE ns.nspname = 'portfolio'
              AND cls.relname = :object_name
            """
        )
    value = connection.scalar(statement, {"object_name": object_name})
    assert value is not None
    return int(value)


def _install_postgres_reconciliation_drift(connection: sa.Connection) -> None:
    _set_search_path(connection)
    for table_name in (
        "taxonomy_record",
        "taxonomy_assignment_record",
        "target_set_record",
    ):
        connection.exec_driver_sql(
            f"ALTER TABLE portfolio.{table_name} ADD COLUMN effective_from date"
        )
        connection.exec_driver_sql(
            f"ALTER TABLE portfolio.{table_name} ADD COLUMN effective_to date"
        )

    connection.exec_driver_sql(
        "DROP INDEX portfolio.ix_target_set_record_taxonomy_scope_type"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_target_set_record_taxonomy_scope_type_effective "
        "ON portfolio.target_set_record ("
        "taxonomy_id, comparator_taxonomy_node_id, target_set_type, "
        "effective_from, target_set_id)"
    )
    connection.exec_driver_sql(
        "ALTER INDEX portfolio.ix_portfolio_daily_holding_instrument_date "
        "RENAME TO ix_portfolio_daily_holding_asset_date"
    )
    connection.exec_driver_sql(
        "ALTER INDEX portfolio.ix_transaction_record_portfolio_instrument_trade "
        "RENAME TO ix_transaction_record_portfolio_asset_trade"
    )
    connection.exec_driver_sql(
        "ALTER TABLE portfolio.research_settings_record "
        "ALTER COLUMN backtest_rebalance_frequency SET DEFAULT '1m'"
    )


def _assert_postgres_reconciled(connection: sa.Connection) -> None:
    _set_search_path(connection)
    unexpected_columns = connection.execute(
        sa.text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'portfolio'
              AND table_name IN (
                  'taxonomy_record',
                  'taxonomy_assignment_record',
                  'target_set_record'
              )
              AND column_name IN ('effective_from', 'effective_to')
            """
        )
    ).all()
    assert unexpected_columns == []

    indexes = {
        str(row.indexname): str(row.indexdef)
        for row in connection.execute(
            sa.text(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'portfolio'
                  AND tablename IN (
                      'target_set_record',
                      'portfolio_daily_holding_snapshot',
                      'transaction_record',
                      'taxonomy_assignment_record'
                  )
                """
            )
        )
    }
    assert "ix_target_set_record_taxonomy_scope_type_effective" not in indexes
    assert "ix_target_set_record_taxonomy_scope_type" in indexes
    assert "ix_portfolio_daily_holding_asset_date" not in indexes
    assert "ix_portfolio_daily_holding_instrument_date" in indexes
    assert "ix_transaction_record_portfolio_asset_trade" not in indexes
    assert "ix_transaction_record_portfolio_instrument_trade" in indexes
    assert "uq_taxonomy_assignment_target" not in indexes
    assert "uq_target_set_active_scope" not in indexes

    assert _constraint_names(connection).issuperset({CURRENT_FOREIGN_KEY})
    assert LEGACY_FOREIGN_KEY not in _constraint_names(connection)
    foreign_key = connection.execute(
        sa.text(
            """
            SELECT
                pg_get_constraintdef(con.oid) AS definition,
                ref_ns.nspname AS referred_schema
            FROM pg_constraint AS con
            JOIN pg_class AS cls ON cls.oid = con.conrelid
            JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
            JOIN pg_class AS ref_cls ON ref_cls.oid = con.confrelid
            JOIN pg_namespace AS ref_ns ON ref_ns.oid = ref_cls.relnamespace
            WHERE ns.nspname = 'portfolio'
              AND cls.relname = 'transaction_record'
              AND con.conname = :constraint_name
            """
        ),
        {"constraint_name": CURRENT_FOREIGN_KEY},
    ).mappings().one()
    assert foreign_key["referred_schema"] == "instrument_registry"
    assert foreign_key["definition"] in {
        "FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id) ON DELETE RESTRICT",
        (
            "FOREIGN KEY (instrument_id) REFERENCES "
            "instrument_registry.instrument(instrument_id) ON DELETE RESTRICT"
        ),
    }
    assert connection.scalar(
        sa.text(
            """
            SELECT column_default
            FROM information_schema.columns
            WHERE table_schema = 'portfolio'
              AND table_name = 'research_settings_record'
              AND column_name = 'backtest_rebalance_frequency'
            """
        )
    ) is None


def _make_foreign_key_canonical(connection: sa.Connection) -> None:
    _set_search_path(connection)
    names = _constraint_names(connection)
    if LEGACY_FOREIGN_KEY in names:
        connection.exec_driver_sql(
            f"ALTER TABLE portfolio.transaction_record DROP CONSTRAINT {LEGACY_FOREIGN_KEY}"
        )
    if CURRENT_FOREIGN_KEY not in names:
        connection.exec_driver_sql(
            f"ALTER TABLE portfolio.transaction_record ADD CONSTRAINT {CURRENT_FOREIGN_KEY} "
            "FOREIGN KEY (instrument_id) "
            "REFERENCES instrument_registry.instrument(instrument_id) ON DELETE RESTRICT"
        )


def test_postgres_schema_reconciliation_is_lossless_and_fail_closed(
    postgres_reconciliation_database: str,
) -> None:
    database_url = postgres_reconciliation_database
    config = _portfolio_config(database_url)
    engine = sa.create_engine(database_url)
    command.downgrade(config, RECONCILIATION_PARENT)

    try:
        with engine.begin() as connection:
            _set_search_path(connection)
            connection.exec_driver_sql(
                f"ALTER TABLE portfolio.transaction_record "
                f"DROP CONSTRAINT {CURRENT_FOREIGN_KEY}"
            )
            connection.exec_driver_sql(
                f"ALTER TABLE portfolio.transaction_record "
                f"ADD CONSTRAINT {LEGACY_FOREIGN_KEY} FOREIGN KEY (instrument_id) "
                "REFERENCES instrument_registry.instrument(instrument_id) ON DELETE CASCADE"
            )

        with pytest.raises(RuntimeError, match="definition is not equivalent"):
            command.upgrade(config, RECONCILIATION_REVISION)

        with engine.connect() as connection:
            _set_search_path(connection)
            assert connection.scalar(
                sa.text("SELECT version_num FROM portfolio.alembic_version")
            ) == RECONCILIATION_PARENT
            assert "ON DELETE CASCADE" in str(
                connection.scalar(
                    sa.text(
                        """
                        SELECT pg_get_constraintdef(oid)
                        FROM pg_constraint
                        WHERE conname = :constraint_name
                        """
                    ),
                    {"constraint_name": LEGACY_FOREIGN_KEY},
                )
            )

        with engine.begin() as connection:
            _make_foreign_key_canonical(connection)
            connection.exec_driver_sql(
                f"ALTER TABLE portfolio.transaction_record "
                f"RENAME CONSTRAINT {CURRENT_FOREIGN_KEY} TO {LEGACY_FOREIGN_KEY}"
            )
            _install_postgres_reconciliation_drift(connection)
            legacy_holding_oid = _object_oid(
                connection,
                object_name="ix_portfolio_daily_holding_asset_date",
            )
            legacy_transaction_oid = _object_oid(
                connection,
                object_name="ix_transaction_record_portfolio_asset_trade",
            )
            legacy_foreign_key_oid = _object_oid(
                connection,
                object_name=LEGACY_FOREIGN_KEY,
                constraint=True,
            )

        command.upgrade(config, RECONCILIATION_REVISION)
        with engine.connect() as connection:
            _assert_postgres_reconciled(connection)
            assert _object_oid(
                connection,
                object_name="ix_portfolio_daily_holding_instrument_date",
            ) == legacy_holding_oid
            assert _object_oid(
                connection,
                object_name="ix_transaction_record_portfolio_instrument_trade",
            ) == legacy_transaction_oid
            assert _object_oid(
                connection,
                object_name=CURRENT_FOREIGN_KEY,
                constraint=True,
            ) == legacy_foreign_key_oid

        command.downgrade(config, RECONCILIATION_PARENT)
        with engine.connect() as connection:
            _assert_postgres_reconciled(connection)
            assert connection.scalar(
                sa.text("SELECT version_num FROM portfolio.alembic_version")
            ) == RECONCILIATION_PARENT
    finally:
        with engine.begin() as connection:
            _make_foreign_key_canonical(connection)
            for table_name in (
                "taxonomy_record",
                "taxonomy_assignment_record",
                "target_set_record",
            ):
                columns = {
                    str(column["name"])
                    for column in sa.inspect(connection).get_columns(
                        table_name,
                        schema="portfolio",
                    )
                }
                for column_name in ("effective_from", "effective_to"):
                    if column_name in columns:
                        connection.exec_driver_sql(
                            f"UPDATE portfolio.{table_name} SET {column_name} = NULL"
                        )
        command.upgrade(config, "head")
        engine.dispose()


def test_postgres_screenshot_evidence_cascades_with_portfolio_deletion(
    postgres_reconciliation_database: str,
) -> None:
    database_url = postgres_reconciliation_database
    command.upgrade(_portfolio_config(database_url), "head")
    _run_registry_upgrade("head")

    from portfolio_app.services.portfolio_store import create_portfolio, delete_portfolio
    from portfolio_app.services.transaction_captures import (
        create_transaction_capture,
        create_transaction_capture_batch,
        get_transaction_capture,
    )

    create_portfolio(
        "Keep PostgreSQL fixture",
        base_currency="USD",
        inception_date=date(2026, 1, 1),
    )
    target = create_portfolio(
        "Delete PostgreSQL screenshot fixture",
        base_currency="USD",
        inception_date=date(2026, 1, 1),
    )
    portfolio_id = str(target["portfolio_id"])
    capture = create_transaction_capture(
        portfolio_id=portfolio_id,
        filename="fixture.png",
        content=b"\x89PNG\r\n\x1a\npostgres-delete-fixture",
    )
    create_transaction_capture_batch(
        portfolio_id=portfolio_id,
        capture_ids=[str(capture["capture_id"])],
        purpose="auto",
    )

    assert delete_portfolio(portfolio_id)
    assert get_transaction_capture(
        portfolio_id=portfolio_id,
        capture_id=str(capture["capture_id"]),
    ) is None

    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            definition = connection.scalar(
                sa.text(
                    """
                    SELECT pg_get_constraintdef(con.oid)
                    FROM pg_constraint AS con
                    JOIN pg_class AS cls ON cls.oid = con.conrelid
                    JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
                    WHERE ns.nspname = 'portfolio'
                      AND cls.relname = 'transaction_capture_batch_item'
                      AND con.contype = 'f'
                      AND pg_get_constraintdef(con.oid) LIKE '%capture_id%'
                    """
                )
            )
        assert definition is not None
        assert "ON DELETE CASCADE" in str(definition)
    finally:
        engine.dispose()


def test_postgres_holding_kind_identity_rebuilds_read_model_and_reconciles_head(
    postgres_reconciliation_database: str,
) -> None:
    database_url = postgres_reconciliation_database
    config = _portfolio_config(database_url)
    engine = sa.create_engine(database_url)
    command.upgrade(config, "20260804_0042")

    with engine.begin() as connection:
        _set_search_path(connection)
        connection.execute(
            sa.text(
                """
                INSERT INTO portfolio_record (
                    portfolio_id, portfolio_name, base_currency,
                    valuation_timezone, valuation_cutoff_policy, as_of_date,
                    nav, day_change_value, day_change_pct,
                    securities_count, sort_order
                ) VALUES (
                    'portfolio-holding-kind', 'Holding Kind Migration', 'USD',
                    'UTC', 'latest_complete_eod', '2026-08-06',
                    100, 0, 0, 1, 0
                )
                """
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO portfolio_calculation_state "
                "(portfolio_id, daily_snapshot_status) "
                "VALUES ('portfolio-holding-kind', 'current')"
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO portfolio_daily_holding_snapshot (
                    portfolio_id, as_of_date, account_id, instrument_id,
                    currency, quantity, cost_basis, cost_basis_base,
                    last_price, market_value, market_value_base,
                    portfolio_weight, holding_json, calculated_at
                ) VALUES (
                    'portfolio-holding-kind', '2026-08-06', 'broker',
                    'option-contract', 'USD', 1, 100, 100,
                    NULL, 100, 100, 1, '{}', '2026-08-06T16:00:00Z'
                )
                """
            )
        )
        old_table_oid = connection.scalar(
            sa.text("SELECT 'portfolio.portfolio_daily_holding_snapshot'::regclass::oid")
        )

    command.upgrade(config, "20260806_0043")
    inspector = sa.inspect(engine)
    upgraded_columns = {
        str(column["name"])
        for column in inspector.get_columns(
            "portfolio_daily_holding_snapshot",
            schema="portfolio",
        )
    }
    upgraded_primary_key = inspector.get_pk_constraint(
        "portfolio_daily_holding_snapshot",
        schema="portfolio",
    )
    upgraded_indexes = {
        str(index["name"]): tuple(index["column_names"])
        for index in inspector.get_indexes(
            "portfolio_daily_holding_snapshot",
            schema="portfolio",
        )
    }
    upgraded_foreign_keys = {
        str(item["name"]): item
        for item in inspector.get_foreign_keys(
            "portfolio_daily_holding_snapshot",
            schema="portfolio",
        )
    }
    assert "holding_kind" in upgraded_columns
    assert upgraded_primary_key["name"] == "pk_portfolio_daily_holding_snapshot"
    assert tuple(upgraded_primary_key["constrained_columns"]) == (
        "portfolio_id",
        "as_of_date",
        "account_id",
        "instrument_id",
        "holding_kind",
    )
    assert upgraded_indexes == {
        "ix_portfolio_daily_holding_account_date": (
            "portfolio_id",
            "account_id",
            "as_of_date",
        ),
        "ix_portfolio_daily_holding_instrument_date": (
            "portfolio_id",
            "instrument_id",
            "as_of_date",
        ),
        "ix_portfolio_daily_holding_portfolio_date": (
            "portfolio_id",
            "as_of_date",
        ),
    }
    holding_foreign_key = upgraded_foreign_keys[
        "fk_portfolio_holding_snapshot_portfolio"
    ]
    assert holding_foreign_key["referred_schema"] == "portfolio"
    assert holding_foreign_key["referred_table"] == "portfolio_record"
    assert tuple(holding_foreign_key["referred_columns"]) == ("portfolio_id",)

    insert_holding = sa.text(
        """
        INSERT INTO portfolio.portfolio_daily_holding_snapshot (
            portfolio_id, as_of_date, account_id, instrument_id,
            holding_kind, currency, quantity, cost_basis, cost_basis_base,
            last_price, market_value, market_value_base,
            portfolio_weight, holding_json, calculated_at
        ) VALUES (
            'portfolio-holding-kind', '2026-08-06', 'broker',
            'option-contract', :holding_kind, 'USD', :quantity,
            NULL, NULL, NULL, :market_value, :market_value,
            NULL, '{}', '2026-08-06T16:00:00Z'
        )
        """
    )
    with engine.begin() as connection:
        _set_search_path(connection)
        upgraded_table_oid = connection.scalar(
            sa.text("SELECT 'portfolio.portfolio_daily_holding_snapshot'::regclass::oid")
        )
        assert upgraded_table_oid != old_table_oid
        assert connection.scalar(
            sa.text(
                "SELECT count(*) FROM portfolio.portfolio_daily_holding_snapshot"
            )
        ) == 0
        assert connection.scalar(
            sa.text(
                "SELECT daily_snapshot_status "
                "FROM portfolio.portfolio_calculation_state "
                "WHERE portfolio_id = 'portfolio-holding-kind'"
            )
        ) == "stale"
        connection.execute(
            insert_holding,
            {
                "holding_kind": "position",
                "quantity": 1,
                "market_value": 500,
            },
        )
        connection.execute(
            insert_holding,
            {
                "holding_kind": "option_obligation",
                "quantity": 100,
                "market_value": -300,
            },
        )
        assert connection.scalar(
            sa.text(
                "SELECT count(*) FROM portfolio.portfolio_daily_holding_snapshot "
                "WHERE instrument_id = 'option-contract'"
            )
        ) == 2

    command.downgrade(config, "20260804_0042")
    downgraded_inspector = sa.inspect(engine)
    downgraded_columns = {
        str(column["name"])
        for column in downgraded_inspector.get_columns(
            "portfolio_daily_holding_snapshot",
            schema="portfolio",
        )
    }
    downgraded_primary_key = downgraded_inspector.get_pk_constraint(
        "portfolio_daily_holding_snapshot",
        schema="portfolio",
    )
    assert "holding_kind" not in downgraded_columns
    assert tuple(downgraded_primary_key["constrained_columns"]) == (
        "portfolio_id",
        "as_of_date",
        "account_id",
        "instrument_id",
    )
    with engine.connect() as connection:
        _set_search_path(connection)
        downgraded_table_oid = connection.scalar(
            sa.text("SELECT 'portfolio.portfolio_daily_holding_snapshot'::regclass::oid")
        )
        assert downgraded_table_oid != upgraded_table_oid
        assert connection.scalar(
            sa.text(
                "SELECT count(*) FROM portfolio.portfolio_daily_holding_snapshot"
            )
        ) == 0

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(
            sa.text("SELECT version_num FROM portfolio.alembic_version")
        ) == ScriptDirectory.from_config(config).get_current_head()
        assert connection.scalar(
            sa.text(
                "SELECT inception_date FROM portfolio.portfolio_record "
                "WHERE portfolio_id = 'portfolio-holding-kind'"
            )
        ) == date(2026, 8, 6)
    inception_column = next(
        column
        for column in sa.inspect(engine).get_columns(
            "portfolio_record",
            schema="portfolio",
        )
        if column["name"] == "inception_date"
    )
    assert inception_column["nullable"] is False
    engine.dispose()
