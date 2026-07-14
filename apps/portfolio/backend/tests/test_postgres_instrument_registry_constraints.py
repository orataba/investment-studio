from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, time
import importlib.util
import json
import os
from pathlib import Path
import sys
from time import monotonic, sleep
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError


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

from portfolio_app.db.models import (
    AccountRecordModel,
    PortfolioRecordModel,
    AllocationResearchRunRecordModel,
)
from portfolio_app.services.transaction_revisions import (
    CreateTransactionRevision,
    TransactionFactPayload,
    TransactionRevisionContext,
    append_transaction_revision_batch_unchecked,
)
from portfolio_ops_instrument_core import instrument_store as shared_store


pytestmark = pytest.mark.postgresql_integration

DEFAULT_POSTGRES_URL = "postgresql+psycopg://portfolio_ops_test:portfolio_ops_test@127.0.0.1:5432/portfolio_ops"
AUDIT_LIVE_DATA_PATH = WORKSPACE_ROOT / "infra" / "scripts" / "audit_live_data.py"


def _run_instrument_registry_upgrade() -> None:
    config = Config(
        str(WORKSPACE_ROOT / "infra" / "instrument_registry" / "alembic.ini")
    )
    config.set_main_option(
        "script_location",
        str(WORKSPACE_ROOT / "infra" / "instrument_registry" / "alembic"),
    )
    command.upgrade(config, "head")


def _run_calculation_registry_upgrade(database_url: str) -> None:
    config = Config(
        str(WORKSPACE_ROOT / "infra" / "calculation_registry" / "alembic.ini")
    )
    config.set_main_option(
        "script_location",
        str(WORKSPACE_ROOT / "infra" / "calculation_registry" / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _run_portfolio_upgrade(
    database_url: str,
    target_revision: str = "head",
) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, target_revision)


def _admin_database_url(database_url: str) -> str:
    url = make_url(database_url)
    return url.set(database="postgres").render_as_string(hide_password=False)


def _is_postgres_server_unavailable(error: OperationalError) -> bool:
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate is not None:
        return str(sqlstate).startswith("08") or sqlstate == "57P03"
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "connection refused",
            "connection timed out",
            "timeout expired",
            "could not translate host name",
            "name or service not known",
            "nodename nor servname provided",
            "network is unreachable",
            "no route to host",
        )
    )


def _drop_test_database(cleanup_engine, database_name: str) -> None:
    deadline = monotonic() + 10.0
    while True:
        try:
            with cleanup_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
            return
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) != "55006":
                raise
            if monotonic() >= deadline:
                raise RuntimeError(
                    "temporary PostgreSQL database still has active backends after "
                    f"application engine disposal: database={database_name!r}"
                ) from error
            sleep(0.05)


@pytest.fixture
def postgres_portfolio_env(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    base_database_url = os.getenv(
        "PORTFOLIO_OPS_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL
    )
    target_revision = str(getattr(request, "param", "head"))
    database_name = f"portfolio_ops_portfolio_fk_{uuid4().hex[:8]}"
    database_url = (
        make_url(base_database_url)
        .set(database=database_name)
        .render_as_string(hide_password=False)
    )
    admin_engine = create_engine(
        _admin_database_url(base_database_url), isolation_level="AUTOCOMMIT"
    )
    database_created = False
    settings_module = None
    session_module = None
    try:
        try:
            with admin_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except OperationalError as exc:  # pragma: no cover - environment-dependent skip
            if _is_postgres_server_unavailable(exc):
                pytest.skip(
                    f"PostgreSQL server is unavailable for integration tests: {exc}"
                )
            pytest.fail(
                "PostgreSQL integration test connection was rejected. Verify the "
                "portfolio_ops_test credentials created by "
                f"infra/launchd/bootstrap_local_database.sh. Cause: {exc}"
            )

        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        except Exception as exc:
            pytest.fail(
                "PostgreSQL integration test database creation failed. "
                "Ensure the dedicated portfolio_ops_test role exists and has CREATEDB "
                "permission (run infra/launchd/bootstrap_local_database.sh), or set "
                f"PORTFOLIO_OPS_TEST_POSTGRES_URL explicitly. Cause: {exc}"
            )
        database_created = True

        monkeypatch.setenv("PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE", database_name)
        monkeypatch.setenv(
            "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url
        )
        monkeypatch.setenv(
            "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL",
            database_url,
        )
        monkeypatch.setenv(
            "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "instrument_registry"
        )
        monkeypatch.setenv(
            "PORTFOLIO_OPS_CALCULATION_REGISTRY_DATABASE_URL",
            database_url,
        )
        monkeypatch.setenv(
            "PORTFOLIO_OPS_CALCULATION_REGISTRY_ALEMBIC_DATABASE_URL",
            database_url,
        )
        monkeypatch.setenv(
            "PORTFOLIO_OPS_CALCULATION_REGISTRY_SCHEMA",
            "calculation_registry",
        )
        monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_DATABASE_URL", database_url)
        monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_ALEMBIC_DATABASE_URL", database_url)
        monkeypatch.setenv("PORTFOLIO_OPS_PORTFOLIO_DATABASE_SCHEMA", "portfolio")

        from portfolio_app.core import settings as portfolio_settings_module
        from portfolio_app.db import session as portfolio_session_module

        settings_module = portfolio_settings_module
        session_module = portfolio_session_module
        settings_module.get_settings.cache_clear()
        session_module.get_engine.cache_clear()
        session_module.get_session_factory.cache_clear()

        try:
            _run_instrument_registry_upgrade()
            _run_calculation_registry_upgrade(database_url)
            _run_portfolio_upgrade(database_url, target_revision)

            session_factory = session_module.get_session_factory()
            identifier_value = f"PORTFK{uuid4().hex[:8].upper()}"
            instrument = shared_store.create_instrument(
                session_factory,
                instrument_name="Portfolio FK Integration Asset",
                instrument_type="equity",
                currency="USD",
                identifiers=[
                    {
                        "identifier_type": "ticker",
                        "identifier_value": identifier_value,
                        "is_primary": True,
                    }
                ],
            )
        except Exception as exc:
            pytest.fail(
                "PostgreSQL integration database setup or migration failed; "
                f"the temporary database will be removed. Cause: {exc}"
            )

        yield {
            "database_url": database_url,
            "database_schema": "portfolio",
            "instrument_id": str(instrument["instrument_id"]),
            "target_revision": target_revision,
        }
    finally:
        admin_engine.dispose()
        if session_module is not None:
            try:
                session_module.get_engine().dispose()
            except Exception:
                # Database cleanup below is the stronger isolation guarantee.
                pass
            finally:
                session_module.get_engine.cache_clear()
                session_module.get_session_factory.cache_clear()
        if settings_module is not None:
            settings_module.get_settings.cache_clear()

        if database_created:
            cleanup_engine = create_engine(
                _admin_database_url(base_database_url),
                isolation_level="AUTOCOMMIT",
            )
            try:
                _drop_test_database(cleanup_engine, database_name)
            finally:
                cleanup_engine.dispose()


def _seed_0035_legacy_transaction_ledger(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        valuation_timezone, valuation_cutoff_policy,
                        securities_count, sort_order
                    ) VALUES (
                        'migration-lock-portfolio', 'Migration Lock Portfolio',
                        'USD', 'UTC', 'end_of_day', 0, 0
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.account_record (
                        account_id, portfolio_id, account_name, account_type,
                        currency, status
                    ) VALUES (
                        'migration-lock-cash', 'migration-lock-portfolio',
                        'Migration Lock Cash', 'deposit_account', 'USD', 'active'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_calculation_state (
                        portfolio_id, daily_snapshot_status, dirty_from,
                        refresh_request_id, refresh_started_at,
                        refresh_completed_at, error_message
                    ) VALUES (
                        'migration-lock-portfolio', 'current', '2026-04-20',
                        'pre-0036-state', NULL, '2026-04-20T01:00:00Z', NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.transaction_record (
                        transaction_id, portfolio_id, transaction_type,
                        trade_date, trade_time, trade_at, trade_timezone,
                        trade_time_is_estimated, settlement_date,
                        entitlement_date, acquisition_date, account_id,
                        settlement_cash_account_id, instrument_id,
                        instrument_ref_json, quantity, price, gross_amount,
                        counter_amount, fx_rate, fees, taxes, currency,
                        transfer_scope, transfer_object_type, transfer_group_id,
                        counterparty_account_id, note, created_at
                    ) VALUES (
                        'migration-lock-transaction', 'migration-lock-portfolio',
                        'opening_balance', '2026-04-01', '09:30:00',
                        '2026-04-01T09:30:00+00:00', 'UTC', false,
                        '2026-04-01', NULL, NULL, 'migration-lock-cash',
                        NULL, NULL, NULL, NULL, NULL, 1000.0, NULL, NULL,
                        0.0, 0.0, 'USD', NULL, NULL, NULL, NULL,
                        '0035 rollback sentinel', '2026-04-01T09:30:00+00:00'
                    )
                    """
                )
            )
    finally:
        engine.dispose()


def _assert_0035_legacy_transaction_ledger_is_intact(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT version_num FROM portfolio.alembic_version")
                )
                == "20260713_0035"
            )
            assert (
                connection.scalar(
                    text("SELECT to_regclass('portfolio.transaction_record')")
                )
                == "portfolio.transaction_record"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT to_regclass('portfolio.transaction_record_legacy_0036')"
                    )
                )
                is None
            )
            for relation_name in (
                "transaction_identity_record",
                "transaction_revision_group_record",
                "transaction_revision_record",
                "transaction_current",
            ):
                assert (
                    connection.scalar(
                        text(f"SELECT to_regclass('portfolio.{relation_name}')")
                    )
                    is None
                )
            legacy_row = (
                connection.execute(
                    text(
                        """
                    SELECT transaction_id, portfolio_id, note, gross_amount
                    FROM portfolio.transaction_record
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert legacy_row["transaction_id"] == "migration-lock-transaction"
            assert legacy_row["portfolio_id"] == "migration-lock-portfolio"
            assert legacy_row["note"] == "0035 rollback sentinel"
            assert float(legacy_row["gross_amount"]) == 1000.0
            calculation_state = (
                connection.execute(
                    text(
                        """
                    SELECT daily_snapshot_status, dirty_from, refresh_request_id,
                           refresh_started_at, refresh_completed_at, error_message
                    FROM portfolio.portfolio_calculation_state
                    WHERE portfolio_id = 'migration-lock-portfolio'
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert calculation_state["daily_snapshot_status"] == "current"
            assert str(calculation_state["dirty_from"]) == "2026-04-20"
            assert calculation_state["refresh_request_id"] == "pre-0036-state"
            assert calculation_state["refresh_started_at"] is None
            assert calculation_state["refresh_completed_at"] == "2026-04-20T01:00:00Z"
            assert calculation_state["error_message"] is None
            assert (
                connection.scalar(
                    text(
                        "SELECT to_regprocedure("
                        "'portfolio.reject_transaction_ledger_mutation()')"
                    )
                )
                is None
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT to_regprocedure("
                        "'portfolio.validate_transaction_revision_insert()')"
                    )
                )
                is None
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "postgres_portfolio_env",
    ["20260713_0035"],
    indirect=True,
)
def test_0036_postgres_lock_timeout_fails_closed_before_legacy_read(
    postgres_portfolio_env: dict[str, str],
) -> None:
    database_url = postgres_portfolio_env["database_url"]
    _seed_0035_legacy_transaction_ledger(database_url)
    engine = create_engine(database_url)
    blocker = engine.connect()
    blocker_transaction = blocker.begin()
    try:
        # UPDATE holds PostgreSQL ROW EXCLUSIVE until rollback.  The 0036
        # SHARE ROW EXCLUSIVE migration lock must conflict with it.
        blocker.execute(
            text(
                """
                UPDATE portfolio.transaction_record
                SET note = note
                WHERE transaction_id = 'migration-lock-transaction'
                """
            )
        )
        with pytest.raises(
            RuntimeError,
            match="could not acquire the PostgreSQL legacy-ledger write lock",
        ):
            _run_portfolio_upgrade(database_url)
    finally:
        blocker_transaction.rollback()
        blocker.close()
        engine.dispose()

    _assert_0035_legacy_transaction_ledger_is_intact(database_url)


@pytest.mark.parametrize(
    "postgres_portfolio_env",
    ["20260713_0035"],
    indirect=True,
)
def test_0036_postgres_failure_after_destructive_ddl_rolls_back_to_0035(
    postgres_portfolio_env: dict[str, str],
) -> None:
    database_url = postgres_portfolio_env["database_url"]
    _seed_0035_legacy_transaction_ledger(database_url)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE FUNCTION portfolio.inject_0036_post_ddl_failure()
                    RETURNS trigger
                    LANGUAGE plpgsql
                    AS $function$
                    BEGIN
                        IF NEW.refresh_request_id = 'ledger-contract:20260713_0036' THEN
                            RAISE EXCEPTION
                                'injected 0036 failure after destructive DDL';
                        END IF;
                        RETURN NEW;
                    END;
                    $function$
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TRIGGER inject_0036_post_ddl_failure
                    BEFORE UPDATE ON portfolio.portfolio_calculation_state
                    FOR EACH ROW
                    EXECUTE FUNCTION portfolio.inject_0036_post_ddl_failure()
                    """
                )
            )

        # Calculation-state invalidation occurs only after 0036 has renamed and
        # dropped the legacy table, created/backfilled the new ledger, built the
        # current view, and installed its PostgreSQL triggers.  The injected
        # failure therefore proves all preceding DDL/DML is transactional.
        with pytest.raises(
            DBAPIError,
            match="injected 0036 failure after destructive DDL",
        ):
            _run_portfolio_upgrade(database_url)
    finally:
        engine.dispose()

    _assert_0035_legacy_transaction_ledger_is_intact(database_url)


def test_transaction_revision_instrument_registry_fk_is_enforced(
    postgres_portfolio_env: dict[str, str],
) -> None:
    from portfolio_app.db import session as session_module

    engine = create_engine(postgres_portfolio_env["database_url"])
    try:
        with engine.connect() as connection:
            constraint = (
                connection.execute(
                    text(
                        """
                    SELECT
                        con.conname AS constraint_name,
                        ref_ns.nspname AS referred_schema,
                        ref_cls.relname AS referred_table
                    FROM pg_constraint con
                    JOIN pg_class cls
                        ON cls.oid = con.conrelid
                    JOIN pg_namespace cls_ns
                        ON cls_ns.oid = cls.relnamespace
                    JOIN pg_class ref_cls
                        ON ref_cls.oid = con.confrelid
                    JOIN pg_namespace ref_ns
                        ON ref_ns.oid = ref_cls.relnamespace
                    WHERE cls_ns.nspname = :schema
                      AND cls.relname = 'transaction_revision_record'
                      AND con.conname = 'fk_transaction_revision_record_instrument_id_instrument'
                    """
                    ),
                    {"schema": postgres_portfolio_env["database_schema"]},
                )
                .mappings()
                .first()
            )
    finally:
        engine.dispose()

    assert constraint is not None
    assert constraint["referred_schema"] == "instrument_registry"
    assert constraint["referred_table"] == "instrument"

    session_factory = session_module.get_session_factory()
    portfolio_id = f"portfolio-fk-{uuid4().hex[:8]}"
    valid_transaction_id = f"tx-valid-{uuid4().hex[:8]}"
    invalid_transaction_id = f"tx-invalid-{uuid4().hex[:8]}"
    account_id = f"account-fk-{uuid4().hex[:8]}"

    with session_factory() as session:
        session.add(
            PortfolioRecordModel(
                portfolio_id=portfolio_id,
                portfolio_name="Constraint Verification Portfolio",
                base_currency="USD",
                operating_profile="standard_taxonomy",
                valuation_timezone="UTC",
                valuation_cutoff_policy="close",
            )
        )
        session.add(
            AccountRecordModel(
                account_id=account_id,
                portfolio_id=portfolio_id,
                account_name="Constraint verification account",
                account_type="securities_account",
                currency="USD",
                status="active",
            )
        )
        session.commit()

    def facts(instrument_id: str) -> TransactionFactPayload:
        return TransactionFactPayload(
            transaction_type="buy",
            trade_date=date(2026, 4, 21),
            trade_time=time(9, 30),
            trade_at=datetime(2026, 4, 21, 9, 30, tzinfo=UTC),
            trade_timezone="UTC",
            trade_time_is_estimated=False,
            settlement_date=date(2026, 4, 23),
            account_id=account_id,
            instrument_id=instrument_id,
            instrument_snapshot_json={
                "instrument_id": instrument_id,
                "instrument_name": "Constraint verification instrument",
                "instrument_type": "equity",
                "currency": "USD",
                "identifiers": [],
            },
            quantity="10.000000000000",
            price="100.000000000000",
            gross_amount="1000.00000000",
            consideration_basis="exact_quantity_price",
            fees="0.00000000",
            taxes="0.00000000",
            currency="USD",
        )

    context = TransactionRevisionContext(
        source_kind="system",
        change_reason="Verify the registry foreign key",
        actor_type="service",
        actor_id="test:postgres-constraints",
        actor_display_name="PostgreSQL constraint test",
        actor_source="trusted_service",
    )
    with session_factory() as session:
        append_transaction_revision_batch_unchecked(
            session,
            portfolio_id=portfolio_id,
            context=context,
            mutations=(
                CreateTransactionRevision(
                    transaction_id=valid_transaction_id,
                    facts=facts(postgres_portfolio_env["instrument_id"]),
                    created_at=context.recorded_at,
                ),
            ),
        )
        session.commit()

    with session_factory() as session:
        with pytest.raises(IntegrityError):
            append_transaction_revision_batch_unchecked(
                session,
                portfolio_id=portfolio_id,
                context=TransactionRevisionContext(
                    source_kind="system",
                    change_reason="Verify a missing instrument is rejected",
                    actor_type="service",
                    actor_id="test:postgres-constraints",
                    actor_display_name="PostgreSQL constraint test",
                    actor_source="trusted_service",
                ),
                mutations=(
                    CreateTransactionRevision(
                        transaction_id=invalid_transaction_id,
                        facts=facts(f"missing-{uuid4().hex[:8]}"),
                        created_at=datetime.now(UTC),
                    ),
                ),
            )
        session.rollback()


def test_postgres_enforces_current_taxonomy_and_active_target_set_uniqueness(
    postgres_portfolio_env: dict[str, str],
) -> None:
    engine = create_engine(postgres_portfolio_env["database_url"])
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        operating_profile, valuation_timezone,
                        valuation_cutoff_policy, sort_order
                    ) VALUES (
                        'taxonomy-constraint-portfolio',
                        'Taxonomy Constraint Portfolio', 'USD',
                        'standard_taxonomy', 'UTC', 'end_of_day', 0
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.taxonomy_record (
                        taxonomy_id, portfolio_id, name, taxonomy_type,
                        primary_assignment_scope, planning_enabled,
                        budgeting_level, root_default_target_dimension, status
                    ) VALUES (
                        'taxonomy-constraint', 'taxonomy-constraint-portfolio',
                        'Taxonomy Constraint', 'allocation', 'instrument', true,
                        'top_level', 'weight', 'active'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.taxonomy_assignment_record (
                        assignment_id, taxonomy_id, target_scope,
                        target_entity_id, taxonomy_node_id, status
                    ) VALUES (
                        'assignment-current', 'taxonomy-constraint',
                        'instrument', :instrument_id, 'node-a', 'active'
                    )
                    """
                ),
                {"instrument_id": postgres_portfolio_env["instrument_id"]},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.target_set_record (
                        target_set_id, taxonomy_id,
                        comparator_taxonomy_node_id, target_set_type, name,
                        weight_enabled, risk_budget_enabled, status
                    ) VALUES (
                        'target-current', 'taxonomy-constraint', NULL,
                        'saa', 'Current Target', true, false, 'active'
                    )
                    """
                )
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO portfolio.taxonomy_assignment_record (
                            assignment_id, taxonomy_id, target_scope,
                            target_entity_id, taxonomy_node_id, status
                        ) VALUES (
                            'assignment-archived', 'taxonomy-constraint',
                            'instrument', :instrument_id, 'node-b', 'archived'
                        )
                        """
                    ),
                    {"instrument_id": postgres_portfolio_env["instrument_id"]},
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO portfolio.target_set_record (
                            target_set_id, taxonomy_id,
                            comparator_taxonomy_node_id, target_set_type, name,
                            weight_enabled, risk_budget_enabled, status
                        ) VALUES (
                            'target-duplicate', 'taxonomy-constraint', NULL,
                            'saa', 'Duplicate Target', true, false, 'active'
                        )
                        """
                    )
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.target_set_record (
                        target_set_id, taxonomy_id,
                        comparator_taxonomy_node_id, target_set_type, name,
                        weight_enabled, risk_budget_enabled, status
                    ) VALUES (
                        'target-archived', 'taxonomy-constraint', NULL,
                        'saa', 'Archived Target', true, false, 'archived'
                    )
                    """
                )
            )
            assignment_index = connection.scalar(
                text(
                    """
                    SELECT indexdef FROM pg_indexes
                    WHERE schemaname = 'portfolio'
                      AND indexname = 'uq_taxonomy_assignment_target'
                    """
                )
            )
            target_index = connection.scalar(
                text(
                    """
                    SELECT indexdef FROM pg_indexes
                    WHERE schemaname = 'portfolio'
                      AND indexname = 'uq_target_set_active_scope'
                    """
                )
            )
        assert "UNIQUE INDEX" in str(assignment_index)
        assert " WHERE " not in str(assignment_index)
        assert "UNIQUE INDEX" in str(target_index)
        assert "COALESCE(comparator_taxonomy_node_id" in str(target_index)
        assert "WHERE" in str(target_index)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "postgres_portfolio_env",
    ["20260714_0041"],
    indirect=True,
)
def test_0042_migrates_allocation_research_schema_and_policy_replay_payloads(
    postgres_portfolio_env: dict[str, str],
) -> None:
    database_url = postgres_portfolio_env["database_url"]
    engine = create_engine(database_url)
    legacy_method = "research-backtest-metrics.v2.history-gated-arithmetic-sharpe"
    current_method = (
        "allocation-policy-replay-metrics.v3.history-gated-arithmetic-sharpe"
    )
    legacy_detail = {
        "backtest": {
            "metrics": {"method_version": legacy_method},
            "consumer": "policy_backtest",
        },
        "backtest_benchmark": {
            "metrics": {"method_version": legacy_method},
        },
        "backtest_relative_metrics": {"method_version": legacy_method},
        "unrelated": {"preserved": True},
    }
    legacy_request = {
        "backtest_rebalance_frequency": "1m",
        "backtest_benchmark_instrument_id": postgres_portfolio_env["instrument_id"],
        "consumer": "policy_backtest",
        "method_version": legacy_method,
        "unrelated": "preserved",
    }

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        valuation_timezone, valuation_cutoff_policy,
                        sort_order, lifecycle_status, operating_profile
                    ) VALUES (
                        'allocation-migration-portfolio',
                        'Allocation Migration Portfolio',
                        'USD', 'UTC', 'end_of_day', 97, 'active',
                        'standard_taxonomy'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.research_settings_record (
                        portfolio_id, as_of_mode, lookback_days,
                        calculation_frequency, missing_return_policy,
                        target_dimension, capital_mode,
                        backtest_rebalance_frequency,
                        backtest_benchmark_instrument_id
                    ) VALUES (
                        'allocation-migration-portfolio', 'dynamic', 90,
                        'auto', 'strict', 'scope_default', 'unit_notional',
                        '1m', :instrument_id
                    )
                    """
                ),
                {"instrument_id": postgres_portfolio_env["instrument_id"]},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.research_run_record (
                        research_run_id, portfolio_id, job_type, status,
                        requested_at, lookback_days, detail_json,
                        artifacts_json, request_payload_json
                    ) VALUES (
                        'allocation-migration-run',
                        'allocation-migration-portfolio',
                        'taxonomy_backtest', 'completed',
                        '2026-07-14T00:00:00Z', 90,
                        CAST(:detail_json AS json), '[]'::json,
                        CAST(:request_json AS json)
                    )
                    """
                ),
                {
                    "detail_json": json.dumps(legacy_detail),
                    "request_json": json.dumps(legacy_request),
                },
            )

        _run_portfolio_upgrade(database_url, "20260714_0042")

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT version_num FROM portfolio.alembic_version")
                )
                == "20260714_0042"
            )

            current_tables = set(
                connection.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = 'portfolio'
                          AND table_name IN (
                              'allocation_research_settings_record',
                              'allocation_research_run_record'
                          )
                        """
                    )
                ).scalars()
            )
            assert current_tables == {
                "allocation_research_settings_record",
                "allocation_research_run_record",
            }

            old_contract_objects = list(
                connection.execute(
                    text(
                        """
                        SELECT 'table:' || table_name AS object_name
                        FROM information_schema.tables
                        WHERE table_schema = 'portfolio'
                          AND table_name IN (
                              'research_settings_record',
                              'research_run_record'
                          )

                        UNION ALL

                        SELECT 'column:' || table_name || '.' || column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'portfolio'
                          AND table_name IN (
                              'allocation_research_settings_record',
                              'allocation_research_run_record'
                          )
                          AND column_name IN (
                              'research_run_id',
                              'backtest_rebalance_frequency',
                              'backtest_benchmark_instrument_id'
                          )

                        UNION ALL

                        SELECT 'constraint:' || constraint_record.conname
                        FROM pg_constraint AS constraint_record
                        JOIN pg_class AS relation
                          ON relation.oid = constraint_record.conrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = 'portfolio'
                          AND relation.relname IN (
                              'allocation_research_settings_record',
                              'allocation_research_run_record'
                          )
                          AND constraint_record.conname
                              ~ '^(pk|fk|ck)_research_'

                        UNION ALL

                        SELECT 'index:' || indexname
                        FROM pg_indexes
                        WHERE schemaname = 'portfolio'
                          AND indexname ~ '^ix_research_'
                        """
                    )
                ).scalars()
            )
            assert old_contract_objects == []

            constraint_names = set(
                connection.execute(
                    text(
                        """
                        SELECT constraint_record.conname
                        FROM pg_constraint AS constraint_record
                        JOIN pg_class AS relation
                          ON relation.oid = constraint_record.conrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = relation.relnamespace
                        WHERE namespace.nspname = 'portfolio'
                          AND relation.relname IN (
                              'allocation_research_settings_record',
                              'allocation_research_run_record'
                          )
                        """
                    )
                ).scalars()
            )
            assert {
                "pk_allocation_research_settings_record",
                "fk_allocation_research_settings_record_portfolio_id_por_88a5",
                "ck_allocation_research_settings_record_ck_allocation_re_890c",
                "ck_allocation_research_settings_record_ck_allocation_re_948a",
                "pk_allocation_research_run_record",
                "fk_allocation_research_run_record_portfolio_id_portfolio_record",
                "ck_allocation_research_run_record_ck_allocation_researc_5f70",
            }.issubset(constraint_names)

            settings = (
                connection.execute(
                    text(
                        """
                    SELECT policy_replay_rebalance_frequency,
                           policy_replay_benchmark_instrument_id
                    FROM portfolio.allocation_research_settings_record
                    WHERE portfolio_id = 'allocation-migration-portfolio'
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert settings["policy_replay_rebalance_frequency"] == "1m"
            assert (
                settings["policy_replay_benchmark_instrument_id"]
                == postgres_portfolio_env["instrument_id"]
            )

            run = (
                connection.execute(
                    text(
                        """
                    SELECT allocation_research_run_id, job_type,
                           detail_json, request_payload_json
                    FROM portfolio.allocation_research_run_record
                    WHERE allocation_research_run_id =
                        'allocation-migration-run'
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert run["job_type"] == "target_weight_solve"
            detail = run["detail_json"]
            request_payload = run["request_payload_json"]
            assert (
                detail["policy_replay"]["metrics"]["method_version"] == current_method
            )
            assert detail["policy_replay"]["consumer"] == "policy_replay"
            assert (
                detail["policy_replay_benchmark"]["metrics"]["method_version"]
                == current_method
            )
            assert (
                detail["policy_replay_relative_metrics"]["method_version"]
                == current_method
            )
            assert detail["unrelated"] == {"preserved": True}
            assert request_payload == {
                "policy_replay_rebalance_frequency": "1m",
                "policy_replay_benchmark_instrument_id": (
                    postgres_portfolio_env["instrument_id"]
                ),
                "consumer": "policy_replay",
                "method_version": current_method,
                "unrelated": "preserved",
            }
            assert "backtest" not in json.dumps(detail, sort_keys=True)
            assert "backtest" not in json.dumps(request_payload, sort_keys=True)
    finally:
        engine.dispose()


def test_live_audit_enforces_allocation_policy_replay_metrics_contract(
    postgres_portfolio_env: dict[str, str],
) -> None:
    from portfolio_app.db import session as session_module

    module_name = f"portfolio_ops_live_audit_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, AUDIT_LIVE_DATA_PATH)
    assert spec is not None and spec.loader is not None
    audit_module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = audit_module
    try:
        spec.loader.exec_module(audit_module)
    finally:
        sys.modules.pop(module_name, None)

    method_version = (
        "allocation-policy-replay-metrics.v3.history-gated-arithmetic-sharpe"
    )

    def metric_payload(
        *,
        start_date: str | None,
        end_date: str | None,
        elapsed_days: int | None,
    ) -> dict[str, object]:
        if start_date is None or end_date is None:
            calendar_span_days = None
            eligible = False
            reasons = ["performance_history_window_unavailable"]
            message = "Observed performance history is unavailable."
        else:
            assert elapsed_days is not None
            calendar_span_days = elapsed_days + 1
            eligible = elapsed_days >= 365
            reasons = [] if eligible else ["annualized_return_history_below_minimum"]
            message = None if eligible else "Insufficient performance history."
        return {
            "method_version": method_version,
            "history_reliability": {
                "start_date": start_date,
                "end_date": end_date,
                "elapsed_days": elapsed_days,
                "calendar_span_days": calendar_span_days,
                "minimum_history_days": 365,
                "annualized_return_eligible": eligible,
                "annualized_return_reason_codes": reasons,
                "sample_label": "Canonical persisted Allocation Research history",
                "annualization_message": message,
            },
            "start_date": start_date,
            "end_date": end_date,
            "annualized_return": 0.08 if eligible else None,
            "calmar_ratio": 0.8 if eligible else None,
        }

    short_metrics = metric_payload(
        start_date="2026-01-01",
        end_date="2026-12-31",
        elapsed_days=364,
    )
    eligible_metrics = metric_payload(
        start_date="2025-01-01",
        end_date="2026-01-01",
        elapsed_days=365,
    )
    unavailable_metrics = metric_payload(
        start_date=None,
        end_date=None,
        elapsed_days=None,
    )
    valid_detail = {
        "policy_replay": {"metrics": short_metrics},
        "policy_replay_benchmark": {"metrics": eligible_metrics},
        "policy_replay_relative_metrics": unavailable_metrics,
    }

    portfolio_id = f"allocation-research-audit-portfolio-{uuid4().hex[:8]}"
    allocation_research_run_id = f"allocation-research-audit-run-{uuid4().hex[:8]}"
    session_factory = session_module.get_session_factory()
    with session_factory() as session:
        session.add(
            PortfolioRecordModel(
                portfolio_id=portfolio_id,
                portfolio_name="Allocation Research Metrics Audit Portfolio",
                base_currency="USD",
                operating_profile="standard_taxonomy",
                valuation_timezone="UTC",
                valuation_cutoff_policy="end_of_day",
            )
        )
        session.add(
            AllocationResearchRunRecordModel(
                allocation_research_run_id=allocation_research_run_id,
                portfolio_id=portfolio_id,
                job_type="target_weight_solve",
                status="completed",
                lookback_days=90,
                detail_json=deepcopy(valid_detail),
            )
        )
        session.commit()

    engine = create_engine(postgres_portfolio_env["database_url"])

    def violation_count() -> int:
        with engine.connect() as connection:
            return int(
                connection.scalar(
                    text(
                        audit_module.PORTFOLIO_ALLOCATION_POLICY_REPLAY_METRICS_CONTRACT_QUERY
                    )
                )
                or 0
            )

    def detail_with_invalid_metrics(
        metric_path: str,
        invalid_metrics: object,
    ) -> dict[str, object]:
        detail = deepcopy(valid_detail)
        if metric_path == "policy_replay.metrics":
            detail["policy_replay"]["metrics"] = invalid_metrics
        elif metric_path == "policy_replay_benchmark.metrics":
            detail["policy_replay_benchmark"]["metrics"] = invalid_metrics
        else:
            assert metric_path == "policy_replay_relative_metrics"
            detail["policy_replay_relative_metrics"] = invalid_metrics
        return detail

    legacy_metrics = deepcopy(eligible_metrics)
    legacy_metrics.pop("method_version")
    wrong_method_metrics = deepcopy(eligible_metrics)
    wrong_method_metrics["method_version"] = "allocation-policy-replay-metrics.v2"
    missing_history_field = deepcopy(short_metrics)
    missing_history_field["history_reliability"].pop("sample_label")
    wrong_minimum = deepcopy(eligible_metrics)
    wrong_minimum["history_reliability"]["minimum_history_days"] = 366
    inconsistent_elapsed_days = deepcopy(eligible_metrics)
    inconsistent_elapsed_days["history_reliability"]["elapsed_days"] = 364
    inconsistent_metric_boundary = deepcopy(short_metrics)
    inconsistent_metric_boundary["end_date"] = "2026-12-30"
    invalid_iso_boundary = deepcopy(short_metrics)
    invalid_iso_boundary["start_date"] = "2026-02-31"
    invalid_iso_boundary["history_reliability"]["start_date"] = "2026-02-31"
    leaked_short_history_metrics = deepcopy(short_metrics)
    leaked_short_history_metrics["annualized_return"] = 0.08
    leaked_short_history_metrics["calmar_ratio"] = 0.8

    invalid_cases = [
        ("non_object", "policy_replay.metrics", []),
        ("unversioned", "policy_replay_benchmark.metrics", legacy_metrics),
        (
            "wrong_method_version",
            "policy_replay_relative_metrics",
            wrong_method_metrics,
        ),
        ("missing_history_field", "policy_replay.metrics", missing_history_field),
        ("wrong_minimum", "policy_replay_benchmark.metrics", wrong_minimum),
        (
            "inconsistent_elapsed_days",
            "policy_replay_relative_metrics",
            inconsistent_elapsed_days,
        ),
        (
            "inconsistent_metric_boundary",
            "policy_replay.metrics",
            inconsistent_metric_boundary,
        ),
        (
            "invalid_iso_boundary",
            "policy_replay_benchmark.metrics",
            invalid_iso_boundary,
        ),
        (
            "leaked_short_history_metrics",
            "policy_replay_relative_metrics",
            leaked_short_history_metrics,
        ),
    ]

    try:
        assert violation_count() == 0
        for case_name, metric_path, invalid_metrics in invalid_cases:
            with session_factory() as session:
                row = session.get(
                    AllocationResearchRunRecordModel, allocation_research_run_id
                )
                assert row is not None
                row.detail_json = detail_with_invalid_metrics(
                    metric_path,
                    invalid_metrics,
                )
                session.commit()
            assert violation_count() == 1, case_name

        with session_factory() as session:
            row = session.get(
                AllocationResearchRunRecordModel, allocation_research_run_id
            )
            assert row is not None
            row.detail_json = {
                "policy_replay": {"metrics": None},
                "policy_replay_benchmark": {"metrics": None},
                "policy_replay_relative_metrics": None,
            }
            session.commit()
        assert violation_count() == 0
    finally:
        engine.dispose()
