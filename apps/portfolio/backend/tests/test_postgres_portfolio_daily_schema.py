from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
from time import monotonic, sleep
from typing import Iterator
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import DateTime, Numeric, Text, create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from portfolio_app.calculations.numeric import (
    exact_decimal_product,
    exact_decimal_subtract,
    method_decimal_divide,
    method_decimal_subtract,
    quantize_decimal,
)
from portfolio_app.calculations.portfolio_daily.db_models import (
    ALL_TABLES,
    DEPENDENCY_TABLES,
    portfolio_daily_snapshot_output,
)
from portfolio_app.services.transaction_revisions import (
    TransactionFactPayload,
    transaction_payload_hash,
)
from portfolio_ops_calculation_core import (
    CalculationScope,
    build_recompute_intent_dedupe_key,
)
from portfolio_ops_instrument_core.canonical_fx import effective_fx_leg_rate


pytestmark = pytest.mark.postgresql_integration

BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
DEFAULT_POSTGRES_URL = "postgresql+psycopg://portfolio_ops_test:portfolio_ops_test@127.0.0.1:5432/portfolio_ops"


def _admin_url(database_url: str) -> str:
    return (
        make_url(database_url)
        .set(database="postgres")
        .render_as_string(hide_password=False)
    )


def _migration_configs(database_url: str) -> tuple[Config, Config, Config]:
    roots = (
        WORKSPACE_ROOT / "infra" / "instrument_registry",
        WORKSPACE_ROOT / "infra" / "calculation_registry",
        BACKEND_ROOT,
    )
    configs: list[Config] = []
    for root in roots:
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        config.set_main_option("sqlalchemy.url", database_url)
        configs.append(config)
    return configs[0], configs[1], configs[2]


def _dispose_portfolio_database_caches() -> None:
    """Dispose an already-created application engine without creating one."""

    from portfolio_app.core.settings import get_settings
    from portfolio_app.db.session import get_engine, get_session_factory

    cached_engine = get_engine() if get_engine.cache_info().currsize else None
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    get_settings.cache_clear()
    if cached_engine is not None:
        cached_engine.dispose()


def _drop_test_database(admin_engine: Engine, database_name: str) -> None:
    """Drop an isolated database after short-lived PostgreSQL backends exit.

    ``DROP DATABASE ... WITH (FORCE)`` asks the non-superuser test role to
    terminate every backend.  That intermittently fails when PostgreSQL starts
    an autovacuum worker under its service role.  All application engines are
    disposed first, so a plain drop retried only for SQLSTATE 55006 both avoids
    excessive privilege and exposes a real leaked application connection.
    """

    deadline = monotonic() + 10.0
    while True:
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
            return
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) != "55006":
                raise
            if monotonic() >= deadline:
                with admin_engine.connect() as connection:
                    active_pids = connection.scalars(
                        text(
                            """
                            SELECT pid FROM pg_stat_activity
                            WHERE datname = :database_name
                            ORDER BY pid
                            """
                        ),
                        {"database_name": database_name},
                    ).all()
                raise RuntimeError(
                    "temporary PostgreSQL database still has active backends "
                    f"after application engine disposal: database={database_name!r}, "
                    f"pids={active_pids!r}"
                ) from error
            sleep(0.05)


@contextmanager
def _postgres_database(
    monkeypatch: pytest.MonkeyPatch,
    *,
    instrument_target: str = "head",
    portfolio_target: str = "head",
) -> Iterator[tuple[Engine, Config]]:
    base_url = os.getenv("PORTFOLIO_OPS_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL)
    database_name = f"portfolio_ops_pd_{uuid4().hex[:10]}"
    database_url = (
        make_url(base_url)
        .set(database=database_name)
        .render_as_string(hide_password=False)
    )
    admin_engine = create_engine(_admin_url(base_url), isolation_level="AUTOCOMMIT")
    _dispose_portfolio_database_caches()
    try:
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        except OperationalError as error:  # pragma: no cover - host dependent
            pytest.skip(f"PostgreSQL server is unavailable: {error}")

        values = {
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
        }
        for name, value in values.items():
            monkeypatch.setenv(name, value)

        instrument_config, registry_config, portfolio_config = _migration_configs(
            database_url
        )
        command.upgrade(instrument_config, instrument_target)
        command.upgrade(registry_config, "head")
        command.upgrade(portfolio_config, portfolio_target)
        engine = create_engine(database_url)
        try:
            yield engine, portfolio_config
        finally:
            engine.dispose()
            _dispose_portfolio_database_caches()
    finally:
        try:
            _drop_test_database(admin_engine, database_name)
        finally:
            admin_engine.dispose()


def _assert_metadata_parity(
    engine: Engine,
    *,
    future_columns: dict[str, set[str]] | None = None,
    future_nullable_changes: set[tuple[str, str]] | None = None,
    require_column_order: bool = True,
) -> None:
    ignored = future_columns or {}
    ignored_nullable = future_nullable_changes or set()
    inspector = inspect(engine)
    for model_table in ALL_TABLES:
        database_columns = {
            column["name"]: column
            for column in inspector.get_columns(model_table.name, schema="portfolio")
        }
        model_columns = [
            column
            for column in model_table.c
            if column.name not in ignored.get(model_table.name, set())
        ]
        model_column_names = [column.name for column in model_columns]
        if require_column_order:
            assert list(database_columns) == model_column_names, model_table.name
        else:
            assert set(database_columns) == set(model_column_names), model_table.name
        for model_column in model_columns:
            database_column = database_columns[model_column.name]
            if (model_table.name, model_column.name) not in ignored_nullable:
                assert database_column["nullable"] == model_column.nullable, (
                    model_table.name,
                    model_column.name,
                )
            if isinstance(model_column.type, Numeric):
                assert isinstance(database_column["type"], Numeric)
                assert database_column["type"].precision == model_column.type.precision
                assert database_column["type"].scale == model_column.type.scale
            elif isinstance(model_column.type, DateTime):
                assert isinstance(database_column["type"], DateTime)
                assert database_column["type"].timezone == model_column.type.timezone
            elif isinstance(model_column.type, Text):
                assert isinstance(database_column["type"], Text)
        assert inspector.get_pk_constraint(model_table.name, schema="portfolio")[
            "constrained_columns"
        ] == [column.name for column in model_table.primary_key]


def test_head_metadata_matches_models_independent_of_physical_column_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        _assert_metadata_parity(engine, require_column_order=False)


def _execute_batch(connection, sql: str, parameters: dict[str, object]) -> None:
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(text(statement), parameters)


def test_0043_is_strict_noop_for_current_transaction_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="20260714_0042") as (
        engine,
        portfolio_config,
    ):
        contract_sql = text(
            """
            SELECT
                relation.relfilenode,
                pg_get_viewdef('portfolio.transaction_current'::regclass, true),
                ARRAY(
                    SELECT constraint_row.conname
                    FROM pg_constraint AS constraint_row
                    WHERE constraint_row.conrelid = relation.oid
                      AND constraint_row.contype = 'c'
                    ORDER BY constraint_row.conname
                ),
                ARRAY(
                    SELECT trigger_row.tgname
                    FROM pg_trigger AS trigger_row
                    WHERE trigger_row.tgrelid = relation.oid
                      AND NOT trigger_row.tgisinternal
                    ORDER BY trigger_row.tgname
                ),
                ARRAY(
                    SELECT pg_get_functiondef(procedure.oid)
                    FROM pg_proc AS procedure
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = 'portfolio'
                      AND procedure.proname IN (
                          'canonical_transaction_revision_payload_v1',
                          'transaction_revision_payload_hash_v1',
                          'validate_transaction_revision_payload_insert_v1',
                          'assert_internal_transfer_group_v1',
                          'validate_internal_transfer_revision_deferred_v1'
                      )
                    ORDER BY procedure.proname
                )
            FROM pg_class AS relation
            WHERE relation.oid =
                'portfolio.transaction_revision_record'::regclass
            """
        )
        with engine.connect() as connection:
            before = connection.execute(contract_sql).one()

        command.upgrade(portfolio_config, "20260714_0043")

        with engine.connect() as connection:
            after = connection.execute(contract_sql).one()
            assert after == before
            assert connection.scalar(
                text("SELECT version_num FROM portfolio.alembic_version")
            ) == "20260714_0043"


@pytest.mark.parametrize(
    ("tamper_sql", "error_pattern"),
    (
        (
            """
            CREATE OR REPLACE FUNCTION
                portfolio.transaction_revision_payload_hash_v1(
                    row_value portfolio.transaction_revision_record
                )
            RETURNS text
            LANGUAGE sql
            IMMUTABLE
            STRICT
            AS $function$
                SELECT 'sha256:' || repeat('0', 64)
            $function$
            """,
            "ledger function definitions",
        ),
        (
            """
            ALTER TABLE portfolio.transaction_revision_record
            DISABLE TRIGGER trg_transaction_revision_record_payload_v1
            """,
            "trigger definitions, enabled events, or deferred semantics",
        ),
    ),
    ids=("same-name-function", "disabled-trigger"),
)
def test_0043_current_shape_contract_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tamper_sql: str,
    error_pattern: str,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="20260714_0042") as (
        engine,
        portfolio_config,
    ):
        with engine.begin() as connection:
            connection.exec_driver_sql(tamper_sql)

        with pytest.raises(RuntimeError, match=error_pattern):
            command.upgrade(portfolio_config, "20260714_0043")

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM portfolio.alembic_version")
            ) == "20260714_0042"


@pytest.mark.parametrize(
    "captured_daily_transaction",
    (False, True),
    ids=("converges-without-capture", "captured-input-rolls-back"),
)
def test_0043_converges_or_atomically_refuses_deployed_legacy_transaction_ledger(
    monkeypatch: pytest.MonkeyPatch,
    captured_daily_transaction: bool,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="20260714_0042") as (
        engine,
        portfolio_config,
    ):
        trade_at = datetime(2026, 7, 14, 12, tzinfo=UTC)
        snapshot = {
            "instrument_id": "legacy-ledger-fund",
            "instrument_type": "fund",
            "currency": "USD",
        }
        current_facts = TransactionFactPayload(
            transaction_type="opening_balance",
            trade_date=trade_at.date(),
            trade_time=trade_at.timetz().replace(tzinfo=None),
            trade_at=trade_at,
            trade_timezone="UTC",
            trade_time_is_estimated=False,
            settlement_date=trade_at.date(),
            acquisition_date=trade_at.date(),
            account_id="legacy-ledger-account",
            instrument_id="legacy-ledger-fund",
            instrument_snapshot_json=snapshot,
            quantity=Decimal("1.2300"),
            gross_amount=Decimal("100.5000"),
            consideration_basis="source_reported",
            fees=Decimal("0.0000"),
            taxes=Decimal("0"),
            currency="USD",
        )
        with engine.begin() as connection:
            _execute_batch(
                connection,
                """
                INSERT INTO portfolio.portfolio_record (
                    portfolio_id, portfolio_name, base_currency,
                    valuation_timezone, valuation_cutoff_policy, sort_order,
                    operating_profile
                ) VALUES (
                    'legacy-ledger-portfolio', 'Legacy Ledger', 'USD',
                    'UTC', 'close', 0, 'standard_taxonomy'
                );
                INSERT INTO portfolio.account_record (
                    account_id, portfolio_id, account_name, account_type,
                    currency, status
                ) VALUES (
                    'legacy-ledger-account', 'legacy-ledger-portfolio',
                    'Legacy Cash', 'deposit_account', 'USD', 'active'
                );
                INSERT INTO instrument_registry.instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json
                ) VALUES (
                    'legacy-ledger-fund', 'Legacy Ledger Fund', 'fund', 'USD',
                    '{}'::json, '{}'::json, '{}'::json, '{}'::json
                );
                INSERT INTO portfolio.transaction_identity_record (
                    transaction_id, portfolio_id, created_at, created_by
                ) VALUES (
                    'legacy-ledger-transaction', 'legacy-ledger-portfolio',
                    :trade_at, 'schema-test'
                );
                INSERT INTO portfolio.transaction_revision_group_record (
                    revision_group_id, portfolio_id, source_kind,
                    change_reason, actor_type, actor_id, actor_display_name,
                    actor_source, recorded_at
                ) VALUES (
                    'legacy-ledger-group', 'legacy-ledger-portfolio', 'migration',
                    'legacy convergence fixture', 'migration', 'schema-test',
                    'Schema Test', 'migration', :trade_at
                );
                INSERT INTO portfolio.transaction_revision_record (
                    revision_id, portfolio_id, transaction_id, revision_number,
                    revision_group_id, revision_kind, is_tombstone,
                    payload_schema_version, payload_hash, transaction_type,
                    trade_date, trade_time, trade_at, trade_timezone,
                    trade_time_is_estimated, settlement_date, acquisition_date,
                    account_id, instrument_id, instrument_snapshot_json,
                    quantity, gross_amount, fees, taxes, consideration_basis,
                    numeric_scale_state, quantity_input_scale,
                    gross_amount_input_scale, fees_input_scale,
                    taxes_input_scale, currency
                ) VALUES (
                    'legacy-ledger-revision', 'legacy-ledger-portfolio',
                    'legacy-ledger-transaction', 1, 'legacy-ledger-group',
                    'baseline', false, 'transaction-revision.v1', :payload_hash,
                    'opening_balance', DATE '2026-07-14', :trade_time, :trade_at,
                    'UTC', false, DATE '2026-07-14', DATE '2026-07-14',
                    'legacy-ledger-account', 'legacy-ledger-fund',
                    CAST(:snapshot AS json), :quantity, :gross_amount, :fees,
                    :taxes, 'source_reported', 'declared', 4, 4, 4, 0, 'USD'
                )
                """,
                {
                    "trade_at": trade_at,
                    "trade_time": trade_at.timetz().replace(tzinfo=None),
                    "payload_hash": transaction_payload_hash(current_facts),
                    "snapshot": json.dumps(snapshot, separators=(",", ":")),
                    "quantity": Decimal("1.2300"),
                    "gross_amount": Decimal("100.5000"),
                    "fees": Decimal("0.0000"),
                    "taxes": Decimal("0"),
                },
            )

        if captured_daily_transaction:
            with engine.begin() as connection:
                _execute_batch(
                    connection,
                    """
                    INSERT INTO calculation_registry.calculation_scope_generation (
                        calculation_kind, scope_kind, scope_id, generation
                    ) VALUES (
                        'portfolio_daily', 'portfolio',
                        'legacy-ledger-portfolio', 0
                    ) ON CONFLICT DO NOTHING;
                    INSERT INTO calculation_registry.calculation_run (
                        run_id, calculation_kind, scope_kind, scope_id,
                        requested_as_of, effective_as_of, cutoff_at, timezone,
                        methodology_version, input_schema_version,
                        output_schema_version, captured_generation, dedupe_key,
                        requested_by
                    ) VALUES (
                        '00000000-0000-0000-0000-000000004300',
                        'portfolio_daily', 'portfolio',
                        'legacy-ledger-portfolio', DATE '2026-07-14',
                        DATE '2026-07-14', transaction_timestamp(), 'UTC',
                        'portfolio-daily.exact.v1',
                        'portfolio-daily-input.v1',
                        'portfolio-daily-output.v1',
                        (
                            SELECT generation
                            FROM calculation_registry.calculation_scope_generation
                            WHERE calculation_kind = 'portfolio_daily'
                              AND scope_kind = 'portfolio'
                              AND scope_id = 'legacy-ledger-portfolio'
                        ),
                        :dedupe_key, 'schema-test'
                    );
                    INSERT INTO calculation_registry.calculation_input_manifest (
                        manifest_id, run_id, captured_generation,
                        schema_version
                    ) VALUES (
                        '00000000-0000-0000-0000-000000004301',
                        '00000000-0000-0000-0000-000000004300',
                        (
                            SELECT generation
                            FROM calculation_registry.calculation_scope_generation
                            WHERE calculation_kind = 'portfolio_daily'
                              AND scope_kind = 'portfolio'
                              AND scope_id = 'legacy-ledger-portfolio'
                        ),
                        'portfolio-daily-input.v1'
                    );
                    INSERT INTO portfolio.portfolio_daily_transaction_input (
                        manifest_id, run_id, portfolio_id, transaction_id,
                        revision_id, revision_number, revision_group_id,
                        group_recorded_at, revision_kind, is_tombstone,
                        supersedes_revision_id, supersedes_revision_number,
                        payload_schema_version, payload_hash, transaction_type,
                        trade_date, trade_time, trade_at, trade_timezone,
                        trade_time_is_estimated, settlement_date,
                        entitlement_date, acquisition_date, account_id,
                        settlement_cash_account_id, instrument_id,
                        instrument_snapshot_json, quantity, price, gross_amount,
                        counter_amount, quoted_fx_rate, fees, taxes,
                        consideration_basis, numeric_scale_state,
                        quantity_input_scale, price_input_scale,
                        gross_amount_input_scale, counter_amount_input_scale,
                        quoted_fx_rate_input_scale, fees_input_scale,
                        taxes_input_scale, consideration_evidence_state,
                        consideration_evidence_reason_codes,
                        consideration_terms_difference_exact,
                        fx_evidence_state, fx_evidence_reason_codes,
                        effective_fx_rate_method50,
                        quoted_terms_difference_exact, currency,
                        transfer_scope, transfer_object_type, transfer_group_id,
                        counterparty_account_id, note, selected_reason_code
                    )
                    SELECT
                        '00000000-0000-0000-0000-000000004301',
                        '00000000-0000-0000-0000-000000004300',
                        revision.portfolio_id, revision.transaction_id,
                        revision.revision_id, revision.revision_number,
                        revision.revision_group_id, revision_group.recorded_at,
                        revision.revision_kind, revision.is_tombstone,
                        revision.supersedes_revision_id,
                        revision.supersedes_revision_number,
                        revision.payload_schema_version, revision.payload_hash,
                        revision.transaction_type, revision.trade_date,
                        revision.trade_time, revision.trade_at,
                        revision.trade_timezone,
                        revision.trade_time_is_estimated,
                        revision.settlement_date, revision.entitlement_date,
                        revision.acquisition_date, revision.account_id,
                        revision.settlement_cash_account_id,
                        revision.instrument_id,
                        revision.instrument_snapshot_json, revision.quantity,
                        revision.price, revision.gross_amount,
                        revision.counter_amount, revision.quoted_fx_rate,
                        revision.fees, revision.taxes,
                        revision.consideration_basis,
                        revision.numeric_scale_state,
                        revision.quantity_input_scale,
                        revision.price_input_scale,
                        revision.gross_amount_input_scale,
                        revision.counter_amount_input_scale,
                        revision.quoted_fx_rate_input_scale,
                        revision.fees_input_scale,
                        revision.taxes_input_scale,
                        'unavailable',
                        ARRAY['price_unavailable']::varchar(64)[], NULL,
                        'not_applicable', ARRAY[]::varchar(64)[], NULL, NULL,
                        revision.currency, revision.transfer_scope,
                        revision.transfer_object_type,
                        revision.transfer_group_id,
                        revision.counterparty_account_id, revision.note,
                        'latest_at_knowledge_cutoff'
                    FROM portfolio.transaction_revision_record AS revision
                    JOIN portfolio.transaction_revision_group_record
                        AS revision_group
                      ON revision_group.revision_group_id =
                            revision.revision_group_id
                     AND revision_group.portfolio_id = revision.portfolio_id
                    WHERE revision.revision_id = 'legacy-ledger-revision'
                    """,
                    {
                        "dedupe_key": "43" * 32,
                    },
                )

        # Reproduce the complete deployed 37-column shape.  The arbitrary old
        # check name proves 0043 removes stale hashed/name-drifted constraints.
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP VIEW portfolio.transaction_current"
            )
            for trigger_name in (
                "trg_transaction_revision_record_transfer_v1",
                "trg_transaction_revision_record_payload_v1",
                "trg_transaction_revision_record_append_only",
            ):
                connection.exec_driver_sql(
                    f"DROP TRIGGER {trigger_name} "
                    "ON portfolio.transaction_revision_record"
                )
            for signature in (
                "validate_internal_transfer_revision_deferred_v1()",
                "assert_internal_transfer_group_v1(text, text)",
                "validate_transaction_revision_payload_insert_v1()",
                "transaction_revision_payload_hash_v1(portfolio.transaction_revision_record)",
                "canonical_transaction_revision_payload_v1(portfolio.transaction_revision_record)",
                "canonical_transaction_json_v1(json)",
                "transaction_json_number_v1(text)",
            ):
                connection.exec_driver_sql(
                    f"DROP FUNCTION portfolio.{signature}"
                )
            connection.exec_driver_sql(
                """
                DO $block$
                DECLARE constraint_name text;
                BEGIN
                    FOR constraint_name IN
                        SELECT constraint_row.conname
                        FROM pg_constraint AS constraint_row
                        WHERE constraint_row.conrelid =
                            'portfolio.transaction_revision_record'::regclass
                          AND constraint_row.contype = 'c'
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE portfolio.transaction_revision_record '
                            'DROP CONSTRAINT %%I', constraint_name
                        );
                    END LOOP;
                END
                $block$
                """
            )
            connection.exec_driver_sql(
                """
                ALTER TABLE portfolio.transaction_revision_record
                    ALTER COLUMN quantity TYPE numeric(38, 12),
                    ALTER COLUMN price TYPE numeric(38, 12),
                    ALTER COLUMN gross_amount TYPE numeric(38, 8),
                    ALTER COLUMN counter_amount TYPE numeric(38, 8),
                    ALTER COLUMN quoted_fx_rate TYPE numeric(38, 18),
                    ALTER COLUMN fees TYPE numeric(38, 8),
                    ALTER COLUMN taxes TYPE numeric(38, 8)
                """
            )
            connection.exec_driver_sql(
                "ALTER TABLE portfolio.transaction_revision_record "
                "RENAME COLUMN quoted_fx_rate TO fx_rate"
            )
            connection.exec_driver_sql(
                """
                ALTER TABLE portfolio.transaction_revision_record
                    DROP COLUMN consideration_basis,
                    DROP COLUMN numeric_scale_state,
                    DROP COLUMN quantity_input_scale,
                    DROP COLUMN price_input_scale,
                    DROP COLUMN gross_amount_input_scale,
                    DROP COLUMN counter_amount_input_scale,
                    DROP COLUMN quoted_fx_rate_input_scale,
                    DROP COLUMN fees_input_scale,
                    DROP COLUMN taxes_input_scale
                """
            )
            connection.exec_driver_sql(
                "UPDATE portfolio.transaction_revision_record "
                "SET payload_hash = 'sha256:' || repeat('0', 64)"
            )
            # Install old-signature functions/triggers as they existed in the
            # deployed database.  The convergence must remove these row-type
            # dependencies before renaming/adding ledger columns.
            for function_ddl in (
                """
                CREATE FUNCTION portfolio.transaction_json_number_v1(text)
                RETURNS text LANGUAGE sql IMMUTABLE STRICT
                AS $function$ SELECT $1 $function$
                """,
                """
                CREATE FUNCTION portfolio.canonical_transaction_json_v1(json)
                RETURNS text LANGUAGE sql IMMUTABLE STRICT
                AS $function$ SELECT $1::text $function$
                """,
                """
                CREATE FUNCTION portfolio.canonical_transaction_revision_payload_v1(
                    portfolio.transaction_revision_record
                ) RETURNS text LANGUAGE sql IMMUTABLE STRICT
                AS $function$
                    SELECT coalesce(trim_scale(($1).fx_rate)::text, 'legacy')
                $function$
                """,
                """
                CREATE FUNCTION portfolio.transaction_revision_payload_hash_v1(
                    portfolio.transaction_revision_record
                ) RETURNS text LANGUAGE sql IMMUTABLE STRICT
                AS $function$ SELECT 'sha256:' || repeat('0', 64) $function$
                """,
                """
                CREATE FUNCTION portfolio.validate_transaction_revision_payload_insert_v1()
                RETURNS trigger LANGUAGE plpgsql
                AS $function$ BEGIN RETURN NEW; END $function$
                """,
                """
                CREATE FUNCTION portfolio.assert_internal_transfer_group_v1(text, text)
                RETURNS void LANGUAGE plpgsql
                AS $function$ BEGIN RETURN; END $function$
                """,
                """
                CREATE FUNCTION portfolio.validate_internal_transfer_revision_deferred_v1()
                RETURNS trigger LANGUAGE plpgsql
                AS $function$ BEGIN RETURN NEW; END $function$
                """,
            ):
                connection.exec_driver_sql(function_ddl)
            connection.exec_driver_sql(
                """
                CREATE TRIGGER trg_transaction_revision_record_payload_v1
                BEFORE INSERT ON portfolio.transaction_revision_record
                FOR EACH ROW EXECUTE FUNCTION
                    portfolio.validate_transaction_revision_payload_insert_v1()
                """
            )
            connection.exec_driver_sql(
                """
                CREATE CONSTRAINT TRIGGER trg_transaction_revision_record_transfer_v1
                AFTER INSERT ON portfolio.transaction_revision_record
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
                    portfolio.validate_internal_transfer_revision_deferred_v1()
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TRIGGER trg_transaction_revision_record_append_only
                BEFORE UPDATE OR DELETE ON portfolio.transaction_revision_record
                FOR EACH ROW EXECUTE FUNCTION
                    portfolio.reject_transaction_ledger_mutation()
                """
            )
            connection.exec_driver_sql(
                """
                ALTER TABLE portfolio.transaction_revision_record
                ADD CONSTRAINT ck_transaction_revision_record_legacy_deadbeef
                CHECK (fx_rate IS NULL OR fx_rate > 0)
                """
            )
            connection.exec_driver_sql(
                """
                CREATE VIEW portfolio.transaction_current AS
                SELECT revision_id, portfolio_id, transaction_id, fx_rate
                FROM portfolio.transaction_revision_record
                WHERE NOT is_tombstone
                """
            )

        assert len(
            inspect(engine).get_columns(
                "transaction_revision_record", schema="portfolio"
            )
        ) == 37
        if captured_daily_transaction:
            with pytest.raises(
                RuntimeError,
                match="cannot rewrite transaction payload hashes after exact",
            ):
                command.upgrade(portfolio_config, "20260714_0043")
            with engine.connect() as connection:
                assert connection.scalar(
                    text("SELECT version_num FROM portfolio.alembic_version")
                ) == "20260714_0042"
                assert connection.scalar(
                    text(
                        "SELECT count(*) FROM "
                        "portfolio.portfolio_daily_transaction_input"
                    )
                ) == 1
                assert connection.scalar(
                    text(
                        "SELECT payload_hash FROM "
                        "portfolio.transaction_revision_record "
                        "WHERE revision_id = 'legacy-ledger-revision'"
                    )
                ) == "sha256:" + ("0" * 64)
                columns_after_failure = {
                    column["name"]
                    for column in inspect(connection).get_columns(
                        "transaction_revision_record", schema="portfolio"
                    )
                }
                assert "fx_rate" in columns_after_failure
                assert "quoted_fx_rate" not in columns_after_failure
                assert "consideration_basis" not in columns_after_failure
            return

        command.upgrade(portfolio_config, "20260714_0043")

        expected_facts = TransactionFactPayload(
            transaction_type="opening_balance",
            trade_date=trade_at.date(),
            trade_time=trade_at.timetz().replace(tzinfo=None),
            trade_at=trade_at,
            trade_timezone="UTC",
            trade_time_is_estimated=False,
            settlement_date=trade_at.date(),
            acquisition_date=trade_at.date(),
            account_id="legacy-ledger-account",
            instrument_id="legacy-ledger-fund",
            instrument_snapshot_json=snapshot,
            quantity=Decimal("1.23"),
            gross_amount=Decimal("100.5"),
            consideration_basis="source_reported",
            numeric_scale_state="legacy_inferred",
            fees=Decimal("0"),
            taxes=Decimal("0"),
            currency="USD",
        )
        with engine.connect() as connection:
            repaired = connection.execute(
                text(
                    """
                    SELECT consideration_basis, numeric_scale_state,
                           quantity_input_scale, gross_amount_input_scale,
                           fees_input_scale, taxes_input_scale, payload_hash
                    FROM portfolio.transaction_revision_record
                    WHERE revision_id = 'legacy-ledger-revision'
                    """
                )
            ).mappings().one()
            assert repaired == {
                "consideration_basis": "source_reported",
                "numeric_scale_state": "legacy_inferred",
                "quantity_input_scale": 2,
                "gross_amount_input_scale": 1,
                "fees_input_scale": 0,
                "taxes_input_scale": 0,
                "payload_hash": transaction_payload_hash(expected_facts),
            }
            check_names = set(
                connection.scalars(
                    text(
                        """
                        SELECT constraint_row.conname
                        FROM pg_constraint AS constraint_row
                        WHERE constraint_row.conrelid =
                            'portfolio.transaction_revision_record'::regclass
                          AND constraint_row.contype = 'c'
                        """
                    )
                ).all()
            )
            assert len(check_names) == 42
            assert "ck_transaction_revision_record_legacy_deadbeef" not in check_names
            assert connection.scalar(
                text("SELECT count(*) FROM portfolio.transaction_current")
            ) == 1


@pytest.mark.parametrize(
    "start_revision",
    ("20260714_0042", "20260714_0043"),
    ids=("same-upgrade-after-0043", "already-stamped-0043"),
)
def test_0044_forward_converges_instrument_universe_invalidation(
    monkeypatch: pytest.MonkeyPatch,
    start_revision: str,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target=start_revision) as (
        engine,
        portfolio_config,
    ):
        with engine.begin() as connection:
            _execute_batch(
                connection,
                """
                INSERT INTO portfolio.portfolio_record (
                    portfolio_id, portfolio_name, base_currency,
                    valuation_timezone, valuation_cutoff_policy, sort_order,
                    operating_profile
                ) VALUES (
                    'migration-0044-portfolio', 'Migration 0044', 'USD',
                    'UTC', 'close', 0, 'standard_taxonomy'
                );
                INSERT INTO instrument_registry.instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json
                ) VALUES (
                    'migration-0044-active', 'Migration 0044 Active',
                    'fund', 'HKD', '{}'::json, '{}'::json, '{}'::json, '{}'::json
                )
                """,
                {},
            )
            for event in ("insert", "update", "delete"):
                connection.exec_driver_sql(
                    "ALTER TABLE portfolio.portfolio_instrument_universe_record "
                    f"DISABLE TRIGGER trg_40_pd_trg_instrument_universe_{event}"
                )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_instrument_universe_record (
                        portfolio_id, instrument_id, instrument_ref_json,
                        source, holding_state, first_transaction_date,
                        last_transaction_date, transaction_count, status,
                        created_at, updated_at
                    ) VALUES (
                        'migration-0044-portfolio', 'migration-0044-active',
                        '{"instrument_id":"migration-0044-active"}'::json,
                        'taxonomy', 'not_held', NULL, NULL, 0, 'active',
                        '2026-07-14T00:00:00Z', '2026-07-14T00:00:00Z'
                    ), (
                        'migration-0044-portfolio', 'migration-0044-archived-orphan',
                        '{"instrument_id":"migration-0044-archived-orphan"}'::json,
                        'taxonomy', 'not_held', NULL, NULL, 0, 'archived',
                        '2026-07-14T00:00:00Z', '2026-07-14T00:00:00Z'
                    )
                    """
                )
            )
            # This is the originally deployed 0040 behavior: every non-delete
            # row attempts to subscribe, including archived tombstones.
            connection.execute(
                text(
                    r"""
                    CREATE OR REPLACE FUNCTION
                        portfolio.pd_trg_instrument_universe()
                    RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
                    SET search_path = pg_catalog AS $$
                    DECLARE v_scope_ids varchar[];
                    BEGIN
                        PERFORM portfolio.pd_lock_dependency_invalidation_protocol();
                        IF TG_OP <> 'DELETE' THEN
                            INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                                scope_id, dependency_kind, dependency_key,
                                source_reason_code
                            ) SELECT DISTINCT portfolio_id, 'instrument',
                                              instrument_id, 'instrument_universe'
                              FROM new_rows ON CONFLICT DO NOTHING;
                            INSERT INTO portfolio.portfolio_daily_dependency_subscription (
                                scope_id, dependency_kind, dependency_key,
                                source_reason_code
                            ) SELECT DISTINCT n.portfolio_id, 'currency',
                                              i.currency, 'instrument_universe'
                              FROM new_rows AS n
                              JOIN instrument_registry.instrument AS i
                                USING (instrument_id)
                              ON CONFLICT DO NOTHING;
                        END IF;
                        IF TG_OP = 'INSERT' THEN
                            SELECT array_agg(DISTINCT portfolio_id)
                              INTO v_scope_ids FROM new_rows;
                        ELSIF TG_OP = 'DELETE' THEN
                            SELECT array_agg(DISTINCT portfolio_id)
                              INTO v_scope_ids FROM old_rows;
                        ELSE
                            SELECT array_agg(DISTINCT portfolio_id)
                              INTO v_scope_ids FROM (
                                SELECT portfolio_id FROM old_rows
                                UNION SELECT portfolio_id FROM new_rows
                              ) AS changed;
                        END IF;
                        PERFORM * FROM portfolio.pd_invalidate_scopes(
                            v_scope_ids,
                            'portfolio_daily_dependency_changed',
                            jsonb_build_object(
                                'source_relation',
                                'portfolio.portfolio_instrument_universe_record',
                                'operation', TG_OP
                            )
                        );
                        RETURN NULL;
                    END; $$
                    """
                )
            )

        command.upgrade(portfolio_config, "head")

        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM portfolio.alembic_version")
            ) == "20260714_0045"
            subscriptions = set(
                connection.execute(
                    text(
                        """
                        SELECT dependency_kind, dependency_key
                        FROM portfolio.portfolio_daily_dependency_subscription
                        WHERE scope_id = 'migration-0044-portfolio'
                        """
                    )
                ).all()
            )
            assert ("instrument", "migration-0044-active") in subscriptions
            assert ("currency", "HKD") in subscriptions
            assert (
                "instrument",
                "migration-0044-archived-orphan",
            ) not in subscriptions
            trigger_states = connection.execute(
                text(
                    """
                    SELECT trigger_row.tgname, trigger_row.tgenabled
                    FROM pg_trigger AS trigger_row
                    WHERE trigger_row.tgrelid =
                        'portfolio.portfolio_instrument_universe_record'::regclass
                      AND trigger_row.tgname LIKE
                          'trg_40_pd_trg_instrument_universe_%'
                    ORDER BY trigger_row.tgname
                    """
                )
            ).all()
            assert len(trigger_states) == 3
            assert {state for _, state in trigger_states} == {"O"}
            function_owner = connection.scalar(
                text(
                    """
                    SELECT pg_get_userbyid(procedure.proowner)
                    FROM pg_proc AS procedure
                    WHERE procedure.oid =
                        'portfolio.pd_trg_instrument_universe()'::regprocedure
                    """
                )
            )
            assert function_owner == connection.scalar(text("SELECT current_user"))
            assert not connection.scalar(
                text(
                    """
                    SELECT has_function_privilege(
                        'public',
                        'portfolio.pd_trg_instrument_universe()',
                        'EXECUTE'
                    )
                    """
                )
            )

        # A new archived orphan must remain valid historical metadata and must
        # not touch the Registry-backed subscription relation.
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_instrument_universe_record (
                        portfolio_id, instrument_id, instrument_ref_json,
                        source, holding_state, first_transaction_date,
                        last_transaction_date, transaction_count, status,
                        created_at, updated_at
                    ) VALUES (
                        'migration-0044-portfolio',
                        'migration-0044-post-archived-orphan',
                        '{"instrument_id":"migration-0044-post-archived-orphan"}'::json,
                        'taxonomy', 'not_held', NULL, NULL, 0, 'archived',
                        '2026-07-14T00:00:00Z', '2026-07-14T00:00:00Z'
                    )
                    """
                )
            )
        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM portfolio.portfolio_daily_dependency_subscription
                    WHERE scope_id = 'migration-0044-portfolio'
                      AND dependency_kind = 'instrument'
                      AND dependency_key =
                          'migration-0044-post-archived-orphan'
                    """
                )
            ) == 0


def test_0045_persists_and_enforces_ingestion_evidence_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="20260714_0044") as (
        engine,
        portfolio_config,
    ):
        # A later Registry stamp remains compatible when the live source
        # contract itself is unchanged; Portfolio must not pin an exact head.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE instrument_registry.alembic_version "
                    "SET version_num = '20260714_0099'"
                )
            )
        command.upgrade(portfolio_config, "20260714_0045")

        with engine.connect() as connection:
            columns = {
                (row["table_name"], row["column_name"]): row["is_nullable"]
                for row in connection.execute(
                    text(
                        """
                        SELECT table_name, column_name, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = 'portfolio'
                          AND (
                              (table_name = 'portfolio_daily_quote_candidate'
                               AND column_name IN (
                                   'ingested_at', 'ingestion_time_state'
                               ))
                              OR
                              (table_name = 'portfolio_daily_fx_leg'
                               AND column_name = 'ingestion_time_state')
                          )
                        """
                    )
                ).mappings()
            }
            assert columns == {
                ("portfolio_daily_quote_candidate", "ingested_at"): "NO",
                (
                    "portfolio_daily_quote_candidate",
                    "ingestion_time_state",
                ): "NO",
                ("portfolio_daily_fx_leg", "ingestion_time_state"): "YES",
            }
            function_definitions = list(
                connection.scalars(
                    text(
                        """
                        SELECT pg_get_functiondef(procedure.oid)
                        FROM pg_proc AS procedure
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = procedure.pronamespace
                        WHERE namespace.nspname = 'portfolio'
                          AND procedure.proname IN (
                              'pd_guard_quote_candidate_lineage',
                              'pd_guard_fx_leg_lineage'
                          )
                        ORDER BY procedure.proname
                        """
                    )
                )
            )
            assert len(function_definitions) == 2
            assert all(
                definition.count("ingestion_time_state") >= 3
                for definition in function_definitions
            )

        with engine.begin() as connection:
            run_id, manifest_id, portfolio_id, cutoff_at = (
                _seed_building_fx_manifest(connection)
            )
            quote_series_id = str(uuid4())
            observation_id = str(uuid4())
            revision_id = str(uuid4())
            quote_window_id = str(uuid4())
            payload_hash = "sha256:" + "a" * 64
            _execute_batch(
                connection,
                """
                INSERT INTO instrument_registry.instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json
                ) VALUES (
                    'migration-0045-quote', 'Migration 0045 Quote', 'fund',
                    'USD', '{}'::json, '{}'::json, '{}'::json, '{}'::json
                );
                INSERT INTO instrument_registry.quote_series (
                    quote_series_id, instrument_id, metric_family,
                    quote_basis, currency
                ) VALUES (
                    :quote_series_id, 'migration-0045-quote', 'nav',
                    'official_nav', 'USD'
                );
                INSERT INTO instrument_registry.quote_observation (
                    observation_id, quote_series_id, as_of_date
                ) VALUES (
                    :observation_id, :quote_series_id, DATE '2026-07-14'
                );
                INSERT INTO instrument_registry.quote_observation_revision (
                    revision_id, observation_id, revision_number, value,
                    value_input_scale, numeric_scale_state,
                    payload_schema_version, status, ingested_at,
                    ingestion_time_state, payload_hash, is_current,
                    superseded_at
                ) VALUES (
                    :revision_id, :observation_id, 1, 100, 0, 'declared', 2,
                    'complete', :cutoff_at, 'observed', :payload_hash,
                    true, NULL
                );
                INSERT INTO portfolio.portfolio_daily_quote_window (
                    manifest_id, run_id, portfolio_id, quote_window_id,
                    instrument_id, quote_role, valuation_date, quote_currency,
                    window_start_at, window_end_at, selection_policy_version,
                    selection_policy_revision, consumer_policy_version,
                    freshness_policy_version, freshness_mode,
                    freshness_max_age_days, resolver_strategy_version,
                    freshness_limit_seconds, candidate_count, adopted_count,
                    selection_status, coverage_state, reason_codes
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :quote_window_id,
                    'migration-0045-quote', 'valuation', DATE '2026-07-14',
                    'USD', :cutoff_at - INTERVAL '1 day', :cutoff_at,
                    'quote-v1', :selection_revision, 'valuation-v1',
                    'fresh-v1', 'calendar_days', 5, 'resolver-v1', 432000,
                    1, 0, 'unavailable', 'unavailable',
                    ARRAY['test_excluded']::varchar(64)[]
                )
                """,
                {
                    "run_id": run_id,
                    "manifest_id": manifest_id,
                    "portfolio_id": portfolio_id,
                    "cutoff_at": cutoff_at,
                    "quote_series_id": quote_series_id,
                    "observation_id": observation_id,
                    "revision_id": revision_id,
                    "quote_window_id": quote_window_id,
                    "payload_hash": payload_hash,
                    "selection_revision": "sha256:" + "b" * 64,
                },
            )

        candidate_sql = text(
            """
            INSERT INTO portfolio.portfolio_daily_quote_candidate (
                manifest_id, run_id, portfolio_id, quote_window_id,
                candidate_rank, quote_series_id, observation_id,
                revision_id, revision_number, observation_date,
                quote_value, quote_status, source_published_at, ingested_at,
                ingestion_time_state, payload_hash, decision,
                decision_reason_code
            ) VALUES (
                :manifest_id, :run_id, :portfolio_id, :quote_window_id,
                1, :quote_series_id, :observation_id, :revision_id, 1,
                DATE '2026-07-14', 100, 'complete', NULL, :cutoff_at,
                :ingestion_time_state, :payload_hash, 'excluded',
                'test_excluded'
            )
            """
        )
        candidate_params = {
            "run_id": run_id,
            "manifest_id": manifest_id,
            "portfolio_id": portfolio_id,
            "cutoff_at": cutoff_at,
            "quote_series_id": quote_series_id,
            "observation_id": observation_id,
            "revision_id": revision_id,
            "quote_window_id": quote_window_id,
            "payload_hash": payload_hash,
        }
        with pytest.raises(
            DBAPIError,
            match="portfolio_daily_quote_revision_mismatch",
        ):
            with engine.begin() as connection:
                connection.execute(
                    candidate_sql,
                    {
                        **candidate_params,
                        "ingestion_time_state": "legacy_series_upper_bound",
                    },
                )
        with engine.begin() as connection:
            connection.execute(
                candidate_sql,
                {**candidate_params, "ingestion_time_state": "observed"},
            )
        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    """
                    SELECT ingestion_time_state
                    FROM portfolio.portfolio_daily_quote_candidate
                    WHERE manifest_id = :manifest_id
                    """
                ),
                {"manifest_id": manifest_id},
            ) == "observed"

        fx_series_id = str(uuid4())
        fx_observation_id = str(uuid4())
        fx_revision_id = str(uuid4())
        fx_path_id = str(uuid4())
        fx_payload_hash = "sha256:" + "c" * 64
        with engine.begin() as connection:
            _execute_batch(
                connection,
                """
                INSERT INTO instrument_registry.instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json
                ) VALUES (
                    'migration-0045-fx', 'Migration 0045 FX', 'fx', 'CNY',
                    '{}'::json, '{}'::json, '{}'::json, '{}'::json
                );
                INSERT INTO instrument_registry.quote_series (
                    quote_series_id, instrument_id, metric_family,
                    quote_basis, currency
                ) VALUES (
                    :fx_series_id, 'migration-0045-fx', 'fx', 'spot', 'CNY'
                );
                INSERT INTO instrument_registry.quote_observation (
                    observation_id, quote_series_id, as_of_date
                ) VALUES (
                    :fx_observation_id, :fx_series_id, DATE '2026-07-14'
                );
                INSERT INTO instrument_registry.quote_observation_revision (
                    revision_id, observation_id, revision_number, value,
                    value_input_scale, numeric_scale_state,
                    payload_schema_version, status, ingested_at,
                    ingestion_time_state, payload_hash, is_current,
                    superseded_at
                ) VALUES (
                    :fx_revision_id, :fx_observation_id, 1, 7.1, 1,
                    'declared', 2, 'complete', :cutoff_at, 'observed',
                    :fx_payload_hash, true, NULL
                );
                INSERT INTO portfolio.portfolio_daily_fx_path (
                    manifest_id, run_id, portfolio_id, fx_path_id,
                    valuation_date, from_currency, to_currency, path_kind,
                    resolution_status, leg_count, resolved_rate,
                    rate_derivation_residual_exact,
                    selection_policy_version, consumer_policy_version,
                    freshness_policy_version, freshness_mode,
                    freshness_max_age_days, resolver_strategy_version,
                    rate_math_precision, rate_rounding_mode, coverage_state,
                    reason_codes
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :fx_path_id,
                    DATE '2026-07-14', 'USD', 'CNY', 'direct', 'resolved', 1,
                    7.1, 0, 'fx-select-v1', 'fx-v1', 'fresh-v1',
                    'calendar_days', 5, 'resolver-v1', 50,
                    'ROUND_HALF_EVEN', 'complete', ARRAY[]::varchar(64)[]
                )
                """,
                {
                    "run_id": run_id,
                    "manifest_id": manifest_id,
                    "portfolio_id": portfolio_id,
                    "cutoff_at": cutoff_at,
                    "fx_series_id": fx_series_id,
                    "fx_observation_id": fx_observation_id,
                    "fx_revision_id": fx_revision_id,
                    "fx_path_id": fx_path_id,
                    "fx_payload_hash": fx_payload_hash,
                },
            )

        fx_leg_sql = text(
            """
            INSERT INTO portfolio.portfolio_daily_fx_leg (
                manifest_id, run_id, portfolio_id, fx_path_id, leg_order,
                from_currency, to_currency, is_inverted,
                leg_resolution_status, reason_codes, quote_series_id,
                observation_id, revision_id, revision_number,
                observation_date, quoted_rate, effective_rate,
                rate_derivation_residual_exact, quote_status,
                source_published_at, ingested_at, ingestion_time_state,
                payload_hash, consumer_policy_version,
                freshness_policy_version, freshness_mode,
                freshness_max_age_days, resolver_strategy_version,
                rate_math_precision, rate_rounding_mode
            ) VALUES (
                :manifest_id, :run_id, :portfolio_id, :fx_path_id, 1,
                'USD', 'CNY', false, 'resolved', ARRAY[]::varchar(64)[],
                :fx_series_id, :fx_observation_id, :fx_revision_id, 1,
                DATE '2026-07-14', 7.1, 7.1, 0, 'complete', NULL,
                :cutoff_at, :ingestion_time_state, :fx_payload_hash,
                'fx-v1', 'fresh-v1', 'calendar_days', 5, 'resolver-v1', 50,
                'ROUND_HALF_EVEN'
            )
            """
        )
        fx_leg_params = {
            "run_id": run_id,
            "manifest_id": manifest_id,
            "portfolio_id": portfolio_id,
            "cutoff_at": cutoff_at,
            "fx_series_id": fx_series_id,
            "fx_observation_id": fx_observation_id,
            "fx_revision_id": fx_revision_id,
            "fx_path_id": fx_path_id,
            "fx_payload_hash": fx_payload_hash,
        }
        with pytest.raises(
            DBAPIError,
            match="portfolio_daily_fx_leg_revision_mismatch",
        ):
            with engine.begin() as connection:
                connection.execute(
                    fx_leg_sql,
                    {
                        **fx_leg_params,
                        "ingestion_time_state": "legacy_series_upper_bound",
                    },
                )
        with engine.begin() as connection:
            connection.execute(
                fx_leg_sql,
                {**fx_leg_params, "ingestion_time_state": "observed"},
            )
        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    """
                    SELECT ingestion_time_state
                    FROM portfolio.portfolio_daily_fx_leg
                    WHERE manifest_id = :manifest_id
                      AND fx_path_id = :fx_path_id
                    """
                ),
                {"manifest_id": manifest_id, "fx_path_id": fx_path_id},
            ) == "observed"


@pytest.mark.parametrize(
    ("instrument_target", "source_drift_sql"),
    [
        ("20260714_0013", None),
        (
            "head",
            "ALTER TABLE instrument_registry.quote_observation_revision "
            "DISABLE TRIGGER trg_quote_observation_revision_immutable",
        ),
    ],
    ids=("registry-before-0014", "immutable-guard-disabled"),
)
def test_0045_source_contract_failure_is_atomic(
    monkeypatch: pytest.MonkeyPatch,
    instrument_target: str,
    source_drift_sql: str | None,
) -> None:
    with _postgres_database(
        monkeypatch,
        instrument_target=instrument_target,
        portfolio_target="20260714_0044",
    ) as (engine, portfolio_config):
        if source_drift_sql is not None:
            with engine.begin() as connection:
                connection.exec_driver_sql(source_drift_sql)

        with pytest.raises(RuntimeError, match="requires the complete"):
            command.upgrade(portfolio_config, "20260714_0045")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM portfolio.alembic_version")
            ) == "20260714_0044"
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'portfolio'
                      AND table_name IN (
                          'portfolio_daily_quote_candidate',
                          'portfolio_daily_fx_leg'
                      )
                      AND column_name = 'ingestion_time_state'
                    """
                )
            ) == 0


def test_0045_refuses_to_reinterpret_preexisting_manifest_evidence_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="20260714_0044") as (
        engine,
        portfolio_config,
    ):
        with engine.begin() as connection:
            run_id, manifest_id, portfolio_id, _ = _seed_building_fx_manifest(
                connection
            )
            fx_path_id = str(uuid4())
            _execute_batch(
                connection,
                """
                INSERT INTO portfolio.portfolio_daily_fx_path (
                    manifest_id, run_id, portfolio_id, fx_path_id,
                    valuation_date, from_currency, to_currency, path_kind,
                    resolution_status, leg_count, selection_policy_version,
                    consumer_policy_version, freshness_policy_version,
                    freshness_mode, freshness_max_age_days,
                    resolver_strategy_version, rate_math_precision,
                    rate_rounding_mode, coverage_state, reason_codes
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :fx_path_id,
                    DATE '2026-07-14', 'USD', 'CNY', 'direct', 'unavailable',
                    1, 'fx-select-v1', 'fx-v1', 'fresh-v1', 'calendar_days',
                    5, 'resolver-v1', 50, 'ROUND_HALF_EVEN', 'unavailable',
                    ARRAY['missing_fx_quote']::varchar(64)[]
                );
                INSERT INTO portfolio.portfolio_daily_fx_leg (
                    manifest_id, run_id, portfolio_id, fx_path_id, leg_order,
                    from_currency, to_currency, is_inverted,
                    leg_resolution_status, reason_codes,
                    consumer_policy_version, freshness_policy_version,
                    freshness_mode, freshness_max_age_days,
                    resolver_strategy_version, rate_math_precision,
                    rate_rounding_mode
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :fx_path_id, 1,
                    'USD', 'CNY', false, 'missing',
                    ARRAY['missing_fx_quote']::varchar(64)[], 'fx-v1',
                    'fresh-v1', 'calendar_days', 5, 'resolver-v1', 50,
                    'ROUND_HALF_EVEN'
                )
                """,
                {
                    "run_id": run_id,
                    "manifest_id": manifest_id,
                    "portfolio_id": portfolio_id,
                    "fx_path_id": fx_path_id,
                },
            )

        with pytest.raises(RuntimeError, match="cannot reinterpret"):
            command.upgrade(portfolio_config, "20260714_0045")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM portfolio.alembic_version")
            ) == "20260714_0044"
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'portfolio'
                      AND table_name IN (
                          'portfolio_daily_quote_candidate',
                          'portfolio_daily_fx_leg'
                      )
                      AND column_name = 'ingestion_time_state'
                    """
                )
            ) == 0


def test_upgrade_metadata_parity_breaking_cutover_and_structural_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="20260713_0038") as (
        engine,
        portfolio_config,
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        valuation_timezone, valuation_cutoff_policy,
                        nav, day_change_value, day_change_pct,
                        securities_count, sort_order
                    ) VALUES (
                        'pre-0039', 'Pre 0039', 'USD', 'UTC', 'close',
                        1, 0, 0, 0, 0
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_instrument_universe_record (
                        portfolio_id, instrument_id, instrument_ref_json,
                        source, holding_state, first_transaction_date,
                        last_transaction_date, transaction_count, status,
                        created_at, updated_at
                    ) VALUES (
                        'pre-0039', 'legacy-archived-orphan',
                        '{"instrument_id":"legacy-archived-orphan"}'::json,
                        'taxonomy', 'not_held', NULL, NULL, 0, 'archived',
                        '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'
                    ), (
                        'pre-0039', 'fx-usd-cny',
                        '{"instrument_id":"fx-usd-cny"}'::json,
                        'transaction', 'held', NULL, NULL, 0, 'active',
                        '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'
                    )
                    """
                )
            )

        # 0043 is intentionally irreversible.  Exercise the independently
        # reversible Portfolio Daily cutover only through its 0042 boundary.
        command.upgrade(portfolio_config, "20260714_0042")
        _assert_metadata_parity(
            engine,
            future_columns={
                "portfolio_daily_quote_candidate": {"ingestion_time_state"},
                "portfolio_daily_fx_leg": {"ingestion_time_state"},
            },
            future_nullable_changes={
                ("portfolio_daily_quote_candidate", "ingested_at")
            },
        )
        inspector = inspect(engine)
        lot_columns = {
            column["name"]: column
            for column in inspector.get_columns(
                "portfolio_daily_lot_output", schema="portfolio"
            )
        }
        assert not lot_columns["cost_basis_local_exact"]["nullable"]
        assert lot_columns["cost_basis_base_exact"]["nullable"]
        assert lot_columns["unit_cost_base_rounding_residual_exact"]["nullable"]
        disposition_foreign_keys = inspector.get_foreign_keys(
            "portfolio_daily_lot_disposition_output", schema="portfolio"
        )
        assert all(
            foreign_key["referred_table"] != "portfolio_daily_lot_output"
            for foreign_key in disposition_foreign_keys
        )
        assert {
            "acquisition_transaction_id",
            "acquisition_revision_id",
            "acquisition_revision_number",
        } <= {
            column["name"]
            for column in inspector.get_columns(
                "portfolio_daily_lot_disposition_output", schema="portfolio"
            )
        }
        config_columns = {
            column["name"]: column
            for column in inspector.get_columns(
                "portfolio_daily_config_input", schema="portfolio"
            )
        }
        assert config_columns["quote_selection_policy_revision"]["type"].length == 128
        corp_action_columns = {
            column["name"]: column
            for column in inspector.get_columns(
                "portfolio_daily_corp_action_input", schema="portfolio"
            )
        }
        assert isinstance(corp_action_columns["event_updated_at"]["type"], DateTime)
        assert corp_action_columns["event_updated_at"]["type"].timezone
        assert not {
            "portfolio_daily_snapshot",
            "portfolio_daily_holding_snapshot",
            "portfolio_daily_contribution_slice",
            "portfolio_calculation_state",
        } & set(inspector.get_table_names(schema="portfolio"))
        portfolio_columns = {
            column["name"]
            for column in inspector.get_columns("portfolio_record", schema="portfolio")
        }
        assert (
            not {
                "as_of_date",
                "nav",
                "day_change_value",
                "day_change_pct",
                "securities_count",
            }
            & portfolio_columns
        )
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        """
                    SELECT generation
                    FROM calculation_registry.calculation_scope_generation
                    WHERE calculation_kind = 'portfolio_daily'
                      AND scope_kind = 'portfolio' AND scope_id = 'pre-0039'
                    """
                    )
                )
                == 0
            )
            seeded_intent = (
                connection.execute(
                    text(
                        """
                    SELECT requested_generation, dedupe_key, reason_code,
                           reason_context, status
                    FROM calculation_registry.calculation_recompute_intent
                    WHERE calculation_kind = 'portfolio_daily'
                      AND scope_kind = 'portfolio' AND scope_id = 'pre-0039'
                    """
                    )
                )
                .mappings()
                .one()
            )
            assert seeded_intent["requested_generation"] == 0
            assert seeded_intent["dedupe_key"] == build_recompute_intent_dedupe_key(
                CalculationScope("portfolio_daily", "portfolio", "pre-0039"),
                0,
            )
            assert seeded_intent["reason_code"] == "portfolio_daily_migration_seed"
            assert seeded_intent["reason_context"] == {
                "migration_revision": "20260714_0040",
                "source_relation": "portfolio.portfolio_record",
            }
            assert seeded_intent["status"] == "pending"
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM portfolio.portfolio_daily_dependency_subscription
                    WHERE scope_id = 'pre-0039'
                      AND dependency_kind = 'instrument'
                      AND dependency_key = 'legacy-archived-orphan'
                    """
                )
            ) == 0
            assert connection.scalar(
                text(
                    """
                    SELECT status
                    FROM portfolio.portfolio_instrument_universe_record
                    WHERE portfolio_id = 'pre-0039'
                      AND instrument_id = 'legacy-archived-orphan'
                    """
                )
            ) == "archived"
            assert connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM portfolio.portfolio_daily_dependency_subscription
                    WHERE scope_id = 'pre-0039'
                      AND dependency_kind = 'instrument'
                      AND dependency_key = 'fx-usd-cny'
                    """
                )
            ) == 1

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_instrument_universe_record (
                        portfolio_id, instrument_id, instrument_ref_json,
                        source, holding_state, first_transaction_date,
                        last_transaction_date, transaction_count, status,
                        created_at, updated_at
                    ) VALUES (
                        'pre-0039', 'post-upgrade-archived-orphan',
                        '{"instrument_id":"post-upgrade-archived-orphan"}'::json,
                        'taxonomy', 'not_held', NULL, NULL, 0, 'archived',
                        '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'
                    )
                    """
                )
            )

        with pytest.raises(
            IntegrityError,
            match="portfolio_daily_instrument_subscription_target_missing",
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE portfolio.portfolio_instrument_universe_record
                        SET status = 'active'
                        WHERE portfolio_id = 'pre-0039'
                          AND instrument_id = 'post-upgrade-archived-orphan'
                        """
                    )
                )

        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    """
                    SELECT status
                    FROM portfolio.portfolio_instrument_universe_record
                    WHERE portfolio_id = 'pre-0039'
                      AND instrument_id = 'post-upgrade-archived-orphan'
                    """
                )
            ) == "archived"

        command.downgrade(portfolio_config, "20260713_0038")
        inspector = inspect(engine)
        legacy_tables = set(inspector.get_table_names(schema="portfolio"))
        assert {
            "portfolio_daily_snapshot",
            "portfolio_daily_holding_snapshot",
            "portfolio_daily_contribution_slice",
            "portfolio_calculation_state",
        } <= legacy_tables
        assert not {table.name for table in ALL_TABLES} & legacy_tables
        with engine.connect() as connection:
            for table_name in (
                "portfolio_daily_snapshot",
                "portfolio_daily_holding_snapshot",
                "portfolio_daily_contribution_slice",
                "portfolio_calculation_state",
            ):
                assert (
                    connection.scalar(
                        text(f"SELECT count(*) FROM portfolio.{table_name}")
                    )
                    == 0
                )
            # The downgrade restores shape only; it never invents a NAV.
            assert connection.execute(
                text(
                    "SELECT nav, day_change_value, day_change_pct, securities_count "
                    "FROM portfolio.portfolio_record WHERE portfolio_id = 'pre-0039'"
                )
            ).one() == (None, None, None, 0)


def test_0039_normalizes_and_enforces_unique_active_portfolio_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="20260713_0038") as (
        engine,
        portfolio_config,
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        valuation_timezone, valuation_cutoff_policy,
                        nav, day_change_value, day_change_pct,
                        securities_count, sort_order
                    ) VALUES
                        ('order-b', 'Order B', 'USD', 'UTC', 'close',
                         1, 0, 0, 0, 5),
                        ('order-a', 'Order A', 'USD', 'UTC', 'close',
                         1, 0, 0, 0, 5),
                        ('order-c', 'Order C', 'USD', 'UTC', 'close',
                         1, 0, 0, 0, 100)
                    """
                )
            )

        command.upgrade(portfolio_config, "20260714_0039")

        with engine.begin() as connection:
            assert connection.execute(
                text(
                    """
                    SELECT portfolio_id, sort_order
                    FROM portfolio.portfolio_record
                    WHERE lifecycle_status = 'active'
                    ORDER BY sort_order
                    """
                )
            ).all() == [
                ("order-a", 0),
                ("order-b", 1),
                ("order-c", 2),
            ]
            index_definition = connection.scalar(
                text(
                    """
                    SELECT indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'portfolio'
                      AND indexname =
                          'uq_portfolio_record_active_sort_order'
                    """
                )
            )
            assert index_definition is not None
            assert "CREATE UNIQUE INDEX" in index_definition
            assert "(sort_order)" in index_definition
            assert "lifecycle_status" in index_definition

            with pytest.raises(IntegrityError) as exc_info:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            INSERT INTO portfolio.portfolio_record (
                                portfolio_id, portfolio_name, base_currency,
                                valuation_timezone, valuation_cutoff_policy,
                                sort_order, lifecycle_status
                            ) VALUES (
                                'duplicate-active-order', 'Duplicate Active',
                                'USD', 'UTC', 'close', 0, 'active'
                            )
                            """
                        )
                    )
            assert getattr(exc_info.value.orig, "sqlstate", None) == "23505"
            assert (
                getattr(exc_info.value.orig.diag, "constraint_name", None)
                == "uq_portfolio_record_active_sort_order"
            )

            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        valuation_timezone, valuation_cutoff_policy,
                        sort_order, lifecycle_status
                    ) VALUES (
                        'archived-order-zero', 'Archived Order Zero',
                        'USD', 'UTC', 'close', 0, 'archived'
                    )
                    """
                )
            )


def test_0041_backfills_and_enforces_explicit_portfolio_operating_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch, portfolio_target="20260714_0040") as (
        engine,
        portfolio_config,
    ):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        valuation_timezone, valuation_cutoff_policy,
                        sort_order, lifecycle_status
                    ) VALUES (
                        'pre-0041', 'Pre 0041', 'USD',
                        'UTC', 'close', 0, 'active'
                    )
                    """
                )
            )

        command.upgrade(portfolio_config, "20260714_0041")

        with engine.begin() as connection:
            assert connection.scalar(
                text(
                    """
                    SELECT operating_profile
                    FROM portfolio.portfolio_record
                    WHERE portfolio_id = 'pre-0041'
                    """
                )
            ) == "standard_taxonomy"

            column = connection.execute(
                text(
                    """
                    SELECT is_nullable, column_default, character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = 'portfolio'
                      AND table_name = 'portfolio_record'
                      AND column_name = 'operating_profile'
                    """
                )
            ).one()
            assert column == ("NO", None, 32)

            constraint_definition = connection.scalar(
                text(
                    """
                    SELECT pg_get_constraintdef(constraint_row.oid)
                    FROM pg_constraint AS constraint_row
                    JOIN pg_class AS relation
                      ON relation.oid = constraint_row.conrelid
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'portfolio'
                      AND relation.relname = 'portfolio_record'
                      AND constraint_row.conname =
                          'ck_portfolio_record_operating_profile'
                    """
                )
            )
            assert constraint_definition is not None
            assert "standard_taxonomy" in constraint_definition
            assert "external_etf_rotation" in constraint_definition

            for portfolio_id, sort_order, operating_profile, sqlstate in (
                ("null-profile", 1, None, "23502"),
                ("invalid-profile", 2, "allocation_managed", "23514"),
            ):
                with pytest.raises(IntegrityError) as exc_info:
                    with connection.begin_nested():
                        connection.execute(
                            text(
                                """
                                INSERT INTO portfolio.portfolio_record (
                                    portfolio_id, portfolio_name, base_currency,
                                    operating_profile, valuation_timezone,
                                    valuation_cutoff_policy, sort_order,
                                    lifecycle_status
                                ) VALUES (
                                    :portfolio_id, :portfolio_id, 'USD',
                                    :operating_profile, 'UTC', 'close',
                                    :sort_order, 'active'
                                )
                                """
                            ),
                            {
                                "portfolio_id": portfolio_id,
                                "operating_profile": operating_profile,
                                "sort_order": sort_order,
                            },
                        )
                assert getattr(exc_info.value.orig, "sqlstate", None) == sqlstate

            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_record (
                        portfolio_id, portfolio_name, base_currency,
                        operating_profile, valuation_timezone,
                        valuation_cutoff_policy, sort_order, lifecycle_status
                    ) VALUES (
                        'external-profile', 'External Profile', 'USD',
                        'external_etf_rotation', 'UTC', 'close', 3, 'active'
                    )
                    """
                )
            )

        command.downgrade(portfolio_config, "20260714_0040")
        assert "operating_profile" not in {
            column["name"]
            for column in inspect(engine).get_columns(
                "portfolio_record",
                schema="portfolio",
            )
        }


def _seed_building_fx_manifest(connection) -> tuple[str, str, str, datetime]:
    run_id = str(uuid4())
    manifest_id = str(uuid4())
    portfolio_id = f"fx-inverse-{uuid4()}"
    cutoff_at = connection.scalar(text("SELECT transaction_timestamp()"))
    assert isinstance(cutoff_at, datetime)
    _execute_batch(
        connection,
        """
            INSERT INTO portfolio.portfolio_record (
                portfolio_id, portfolio_name, base_currency,
                valuation_timezone, valuation_cutoff_policy, sort_order,
                operating_profile
            ) VALUES (
                :portfolio_id, 'FX Inverse Evidence', 'USD', 'UTC', 'close', 0,
                'standard_taxonomy'
            );
            INSERT INTO calculation_registry.calculation_scope_generation (
                calculation_kind, scope_kind, scope_id, generation
            ) VALUES ('portfolio_daily', 'portfolio', :portfolio_id, 0)
            ON CONFLICT DO NOTHING;
            INSERT INTO calculation_registry.calculation_run (
                run_id, calculation_kind, scope_kind, scope_id,
                requested_as_of, effective_as_of, cutoff_at, timezone,
                methodology_version, input_schema_version, output_schema_version,
                captured_generation, dedupe_key, requested_by
            ) VALUES (
                :run_id, 'portfolio_daily', 'portfolio', :portfolio_id,
                DATE '2026-07-14', DATE '2026-07-14', :cutoff_at, 'UTC',
                'portfolio-daily.exact.v1', 'portfolio-daily-input.v1',
                'portfolio-daily-output.v1', 0, :dedupe_key, 'schema-test'
            );
            INSERT INTO calculation_registry.calculation_input_manifest (
                manifest_id, run_id, captured_generation, schema_version
            ) VALUES (
                :manifest_id, :run_id, 0, 'portfolio-daily-input.v1'
            );
        """,
        {
            "run_id": run_id,
            "manifest_id": manifest_id,
            "portfolio_id": portfolio_id,
            "cutoff_at": cutoff_at,
            "dedupe_key": uuid4().hex + uuid4().hex,
        },
    )
    return run_id, manifest_id, portfolio_id, cutoff_at


def _insert_resolved_fx_path(
    connection,
    *,
    run_id: str,
    manifest_id: str,
    portfolio_id: str,
    cutoff_at: datetime,
    legs: tuple[tuple[str, str, Decimal, bool, Decimal | None], ...],
) -> tuple[str, Decimal]:
    effective_rates = tuple(
        override
        if override is not None
        else effective_fx_leg_rate(quoted_rate, inverted=is_inverted)
        for _, _, quoted_rate, is_inverted, override in legs
    )
    resolved_rate = exact_decimal_product(*effective_rates)
    path_id = str(uuid4())
    path_kind = (
        "cross"
        if len(legs) == 2
        else "inverse"
        if legs[0][3]
        else "direct"
    )
    connection.execute(
        text(
            """
            INSERT INTO portfolio.portfolio_daily_fx_path (
                manifest_id, run_id, portfolio_id, fx_path_id,
                valuation_date, from_currency, to_currency, path_kind,
                resolution_status, leg_count, resolved_rate,
                rate_derivation_residual_exact, selection_policy_version,
                consumer_policy_version, freshness_policy_version,
                freshness_mode, freshness_max_age_days,
                resolver_strategy_version, rate_math_precision,
                rate_rounding_mode, coverage_state, reason_codes
            ) VALUES (
                :manifest_id, :run_id, :portfolio_id, :path_id,
                DATE '2026-07-14', :from_currency, :to_currency, :path_kind,
                'resolved', :leg_count, :resolved_rate, 0, 'fx-select-v1',
                'fx-v1', 'fresh-v1', 'calendar_days', 5, 'resolver-v1', 50,
                'ROUND_HALF_EVEN', 'complete', ARRAY[]::varchar(64)[]
            )
            """
        ),
        {
            "manifest_id": manifest_id,
            "run_id": run_id,
            "portfolio_id": portfolio_id,
            "path_id": path_id,
            "from_currency": legs[0][0],
            "to_currency": legs[-1][1],
            "path_kind": path_kind,
            "leg_count": len(legs),
            "resolved_rate": resolved_rate,
        },
    )

    for leg_order, (
        from_currency,
        to_currency,
        quoted_rate,
        is_inverted,
        _,
    ) in enumerate(legs, start=1):
        instrument_id = f"fx-evidence-{uuid4()}"
        quote_series_id = str(uuid4())
        observation_id = str(uuid4())
        revision_id = str(uuid4())
        payload_hash = "sha256:" + uuid4().hex + uuid4().hex
        effective_rate = effective_rates[leg_order - 1]
        residual = (
            exact_decimal_subtract(
                exact_decimal_product(effective_rate, quoted_rate),
                Decimal("1"),
            )
            if is_inverted
            else Decimal("0")
        )
        _execute_batch(
            connection,
            """
                INSERT INTO instrument_registry.instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json
                ) VALUES (
                    :instrument_id, 'FX Evidence Source', 'fx', :to_currency,
                    '{}'::json, '{}'::json, '{}'::json, '{}'::json
                );
                INSERT INTO instrument_registry.quote_series (
                    quote_series_id, instrument_id, metric_family,
                    quote_basis, currency
                ) VALUES (
                    :quote_series_id, :instrument_id, 'fx', 'spot', :to_currency
                );
                INSERT INTO instrument_registry.quote_observation (
                    observation_id, quote_series_id, as_of_date
                ) VALUES (
                    :observation_id, :quote_series_id, DATE '2026-07-14'
                );
                INSERT INTO instrument_registry.quote_observation_revision (
                    revision_id, observation_id, revision_number, value,
                    value_input_scale, numeric_scale_state,
                    payload_schema_version,
                    status, ingested_at, ingestion_time_state, payload_hash,
                    is_current, superseded_at
                ) VALUES (
                    :revision_id, :observation_id, 1, :quoted_rate,
                    :value_input_scale, 'declared', 2, 'complete',
                    :cutoff_at, 'observed', :payload_hash, true, NULL
                );
                INSERT INTO portfolio.portfolio_daily_fx_leg (
                    manifest_id, run_id, portfolio_id, fx_path_id, leg_order,
                    from_currency, to_currency, is_inverted,
                    leg_resolution_status, reason_codes, quote_series_id,
                    observation_id, revision_id, revision_number,
                    observation_date, quoted_rate, effective_rate,
                    rate_derivation_residual_exact, quote_status,
                    source_published_at, ingested_at, ingestion_time_state,
                    payload_hash,
                    consumer_policy_version, freshness_policy_version,
                    freshness_mode, freshness_max_age_days,
                    resolver_strategy_version, rate_math_precision,
                    rate_rounding_mode
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :path_id, :leg_order,
                    :from_currency, :to_currency, :is_inverted, 'resolved',
                    ARRAY[]::varchar(64)[], :quote_series_id, :observation_id,
                    :revision_id, 1, DATE '2026-07-14', :quoted_rate,
                    :effective_rate, :residual, 'complete', NULL, :cutoff_at,
                    'observed', :payload_hash, 'fx-v1', 'fresh-v1',
                    'calendar_days', 5,
                    'resolver-v1', 50, 'ROUND_HALF_EVEN'
                );
            """,
            {
                "manifest_id": manifest_id,
                "run_id": run_id,
                "portfolio_id": portfolio_id,
                "path_id": path_id,
                "leg_order": leg_order,
                "from_currency": from_currency,
                "to_currency": to_currency,
                "is_inverted": is_inverted,
                "instrument_id": instrument_id,
                "quote_series_id": quote_series_id,
                "observation_id": observation_id,
                "revision_id": revision_id,
                "quoted_rate": quoted_rate,
                "value_input_scale": max(-quoted_rate.as_tuple().exponent, 0),
                "effective_rate": effective_rate,
                "residual": residual,
                "cutoff_at": cutoff_at,
                "payload_hash": payload_hash,
            },
        )
    return path_id, resolved_rate


def _seed_sealed_manifest(
    connection,
    *,
    operating_profile: str = "standard_taxonomy",
    include_exact_cross_fx_path: bool = False,
    cross_resolved_rate_override: Decimal | None = None,
    include_missing_fx_path: bool = False,
    include_withdrawn_fx_leg: bool = False,
    include_recurring_unit_cost_lot_source: bool = False,
    corporate_action_source_updated_offset: timedelta | None = None,
    corporate_action_input_updated_offset: timedelta | None = None,
    excluded_quote_value: Decimal | None = None,
    raw_quote_decision: str = "excluded",
) -> tuple[str, str, str]:
    run_id = str(uuid4())
    manifest_id = str(uuid4())
    job_id = str(uuid4())
    portfolio_id = "attempt-isolation"
    account_id = "attempt-isolation-cash"
    portfolio_insert = """
        INSERT INTO portfolio.portfolio_record (
            portfolio_id, portfolio_name, base_currency,
            valuation_timezone, valuation_cutoff_policy, sort_order,
            operating_profile
        ) VALUES (
            :portfolio_id, 'Attempt Isolation', 'USD', 'UTC', 'close', 0,
            :operating_profile
        );
    """
    _execute_batch(
        connection,
        portfolio_insert,
        {
            "portfolio_id": portfolio_id,
            "operating_profile": operating_profile,
        },
    )
    _execute_batch(
        connection,
        """
            INSERT INTO portfolio.account_record (
                account_id, portfolio_id, account_name, account_type,
                currency, status
            ) VALUES (
                :account_id, :portfolio_id, 'Cash', 'deposit_account', 'USD', 'active'
            );
            INSERT INTO calculation_registry.calculation_scope_generation (
                calculation_kind, scope_kind, scope_id, generation
            ) VALUES ('portfolio_daily', 'portfolio', :portfolio_id, 0)
            ON CONFLICT DO NOTHING;
            """,
        {"portfolio_id": portfolio_id, "account_id": account_id},
    )
    cutoff_at = connection.scalar(text("SELECT transaction_timestamp()"))
    lot_source: dict[str, object] | None = None
    if include_recurring_unit_cost_lot_source:
        instrument_id = "fund-recurring-unit-cost"
        transaction_id = "transaction-100-over-3"
        revision_id = "revision-100-over-3"
        revision_group_id = "group-100-over-3"
        trade_at = datetime(2026, 7, 14, 12, tzinfo=UTC)
        snapshot = {
            "instrument_id": instrument_id,
            "instrument_type": "fund",
            "currency": "USD",
        }
        facts = TransactionFactPayload(
            transaction_type="opening_balance",
            trade_date=date(2026, 7, 14),
            trade_time=trade_at.timetz().replace(tzinfo=None),
            trade_at=trade_at,
            trade_timezone="UTC",
            trade_time_is_estimated=False,
            settlement_date=date(2026, 7, 14),
            acquisition_date=date(2026, 7, 14),
            account_id=account_id,
            instrument_id=instrument_id,
            instrument_snapshot_json=snapshot,
            quantity=Decimal("3"),
            gross_amount=Decimal("100"),
            consideration_basis="source_reported",
            fees=Decimal("0"),
            taxes=Decimal("0"),
            currency="USD",
        )
        lot_source = {
            "instrument_id": instrument_id,
            "transaction_id": transaction_id,
            "revision_id": revision_id,
            "revision_group_id": revision_group_id,
            "trade_at": trade_at,
            "trade_time": trade_at.timetz().replace(tzinfo=None),
            "snapshot": json.dumps(snapshot, separators=(",", ":"), sort_keys=True),
            "payload_hash": transaction_payload_hash(facts),
        }
        _execute_batch(
            connection,
            """
                INSERT INTO instrument_registry.instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json
                ) VALUES (
                    :instrument_id, 'Recurring Unit Cost Fund', 'fund', 'USD',
                    '{}'::json, '{}'::json, '{}'::json, '{}'::json
                );
                INSERT INTO portfolio.transaction_identity_record (
                    transaction_id, portfolio_id, created_at, created_by
                ) VALUES (
                    :transaction_id, :portfolio_id, :cutoff_at, 'schema-test'
                );
                INSERT INTO portfolio.transaction_revision_group_record (
                    revision_group_id, portfolio_id, source_kind,
                    change_reason, actor_type, actor_id, actor_display_name,
                    actor_source, recorded_at
                ) VALUES (
                    :revision_group_id, :portfolio_id, 'system',
                    'schema test lot source', 'service', 'schema-test',
                    'Schema Test', 'trusted_service', :cutoff_at
                );
                INSERT INTO portfolio.transaction_revision_record (
                    revision_id, portfolio_id, transaction_id, revision_number,
                    revision_group_id, revision_kind, is_tombstone,
                    payload_schema_version, payload_hash,
                    transaction_type, trade_date, trade_time, trade_at,
                    trade_timezone, trade_time_is_estimated, settlement_date,
                    acquisition_date, account_id, instrument_id,
                    instrument_snapshot_json, quantity, gross_amount,
                    fees, taxes, consideration_basis, numeric_scale_state,
                    quantity_input_scale, gross_amount_input_scale,
                    fees_input_scale, taxes_input_scale, currency
                ) VALUES (
                    :revision_id, :portfolio_id, :transaction_id, 1,
                    :revision_group_id, 'create', false,
                    'transaction-revision.v1', :payload_hash,
                    'opening_balance', DATE '2026-07-14', :trade_time, :trade_at,
                    'UTC', false, DATE '2026-07-14', DATE '2026-07-14',
                    :account_id, :instrument_id, CAST(:snapshot AS jsonb),
                    3, 100, 0, 0, 'source_reported', 'declared',
                    0, 0, 0, 0, 'USD'
                );
                """,
            {
                **lot_source,
                "portfolio_id": portfolio_id,
                "account_id": account_id,
                "cutoff_at": cutoff_at,
            },
        )
    captured_generation = int(
        connection.scalar(
            text(
                """
                SELECT generation
                FROM calculation_registry.calculation_scope_generation
                WHERE calculation_kind = 'portfolio_daily'
                  AND scope_kind = 'portfolio' AND scope_id = :portfolio_id
                """
            ),
            {"portfolio_id": portfolio_id},
        )
    )
    _execute_batch(
        connection,
        """
            INSERT INTO calculation_registry.calculation_run (
                run_id, calculation_kind, scope_kind, scope_id,
                requested_as_of, effective_as_of, cutoff_at, timezone,
                methodology_version, input_schema_version, output_schema_version,
                captured_generation, dedupe_key, requested_by
            ) VALUES (
                :run_id, 'portfolio_daily', 'portfolio', :portfolio_id,
                DATE '2026-07-14', DATE '2026-07-14', :cutoff_at, 'UTC',
                'portfolio-daily.exact.v1', 'portfolio-daily-input.v1',
                'portfolio-daily-output.v1', :captured_generation,
                :dedupe_key, 'schema-test'
            );
            INSERT INTO calculation_registry.calculation_input_manifest (
                manifest_id, run_id, captured_generation, schema_version
            ) VALUES (
                :manifest_id, :run_id, :captured_generation,
                'portfolio-daily-input.v1'
            );
            """,
        {
            "run_id": run_id,
            "manifest_id": manifest_id,
            "portfolio_id": portfolio_id,
            "cutoff_at": cutoff_at,
            "captured_generation": captured_generation,
            "dedupe_key": "1" * 64,
        },
    )
    if lot_source is not None:
        _execute_batch(
            connection,
            """
                INSERT INTO portfolio.portfolio_daily_transaction_input (
                    manifest_id, run_id, portfolio_id, transaction_id,
                    revision_id, revision_number, revision_group_id,
                    group_recorded_at, revision_kind, is_tombstone,
                    supersedes_revision_id, supersedes_revision_number,
                    payload_schema_version, payload_hash, transaction_type,
                    trade_date, trade_time, trade_at, trade_timezone,
                    trade_time_is_estimated, settlement_date,
                    entitlement_date, acquisition_date, account_id,
                    settlement_cash_account_id, instrument_id,
                    instrument_snapshot_json, quantity, price, gross_amount,
                    counter_amount, quoted_fx_rate, fees, taxes,
                    consideration_basis, numeric_scale_state,
                    quantity_input_scale, price_input_scale,
                    gross_amount_input_scale, counter_amount_input_scale,
                    quoted_fx_rate_input_scale, fees_input_scale,
                    taxes_input_scale, consideration_evidence_state,
                    consideration_evidence_reason_codes,
                    consideration_terms_difference_exact, fx_evidence_state,
                    fx_evidence_reason_codes, effective_fx_rate_method50,
                    quoted_terms_difference_exact, currency,
                    transfer_scope, transfer_object_type, transfer_group_id,
                    counterparty_account_id, note, selected_reason_code
                )
                SELECT :manifest_id, :run_id, revision.portfolio_id,
                    revision.transaction_id, revision.revision_id,
                    revision.revision_number, revision.revision_group_id,
                    revision_group.recorded_at, revision.revision_kind,
                    revision.is_tombstone, revision.supersedes_revision_id,
                    revision.supersedes_revision_number,
                    revision.payload_schema_version, revision.payload_hash,
                    revision.transaction_type, revision.trade_date,
                    revision.trade_time, revision.trade_at,
                    revision.trade_timezone, revision.trade_time_is_estimated,
                    revision.settlement_date, revision.entitlement_date,
                    revision.acquisition_date, revision.account_id,
                    revision.settlement_cash_account_id,
                    revision.instrument_id, revision.instrument_snapshot_json,
                    revision.quantity, revision.price, revision.gross_amount,
                    revision.counter_amount, revision.quoted_fx_rate,
                    revision.fees, revision.taxes,
                    revision.consideration_basis, revision.numeric_scale_state,
                    revision.quantity_input_scale, revision.price_input_scale,
                    revision.gross_amount_input_scale,
                    revision.counter_amount_input_scale,
                    revision.quoted_fx_rate_input_scale,
                    revision.fees_input_scale, revision.taxes_input_scale,
                    'unavailable', ARRAY['price_unavailable']::varchar(64)[],
                    NULL, 'not_applicable', ARRAY[]::varchar(64)[],
                    NULL, NULL, revision.currency, revision.transfer_scope,
                    revision.transfer_object_type, revision.transfer_group_id,
                    revision.counterparty_account_id, revision.note,
                    'latest_at_knowledge_cutoff'
                FROM portfolio.transaction_revision_record AS revision
                JOIN portfolio.transaction_revision_group_record AS revision_group
                  ON revision_group.revision_group_id = revision.revision_group_id
                 AND revision_group.portfolio_id = revision.portfolio_id
                WHERE revision.revision_id = :revision_id
                """,
            {
                "manifest_id": manifest_id,
                "run_id": run_id,
                "revision_id": lot_source["revision_id"],
            },
        )
    if excluded_quote_value is not None:
        quote_instrument_id = f"excluded-quote-{uuid4()}"
        quote_series_id = str(uuid4())
        observation_id = str(uuid4())
        revision_id = str(uuid4())
        quote_window_id = str(uuid4())
        _execute_batch(
            connection,
            """
                INSERT INTO instrument_registry.instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json
                ) VALUES (
                    :instrument_id, 'Excluded Raw Quote', 'fund', 'USD',
                    '{}'::json, '{}'::json, '{}'::json, '{}'::json
                );
                INSERT INTO instrument_registry.quote_series (
                    quote_series_id, instrument_id, metric_family,
                    quote_basis, currency
                ) VALUES (
                    :quote_series_id, :instrument_id, 'price', 'close', 'USD'
                );
                INSERT INTO instrument_registry.quote_observation (
                    observation_id, quote_series_id, as_of_date
                ) VALUES (
                    :observation_id, :quote_series_id, DATE '2026-07-14'
                );
                INSERT INTO instrument_registry.quote_observation_revision (
                    revision_id, observation_id, revision_number, value,
                    value_input_scale, numeric_scale_state,
                    payload_schema_version,
                    status, ingested_at, ingestion_time_state, payload_hash,
                    is_current, superseded_at
                ) VALUES (
                    :revision_id, :observation_id, 1, :quote_value,
                    :value_input_scale, 'declared', 2, 'rejected',
                    :cutoff_at, 'observed', :payload_hash, true, NULL
                );
                INSERT INTO portfolio.portfolio_daily_instrument_input (
                    manifest_id, run_id, portfolio_id, instrument_id,
                    requires_valuation, instrument_name, instrument_type,
                    currency, price_unit, contract_multiplier, price_factor,
                    valuation_contract_state, valuation_contract_reason_codes,
                    valuation_factor_source, quote_policy_version,
                    quote_selection_policy_revision, freshness_policy_version,
                    freshness_mode, freshness_max_age_days,
                    resolver_strategy_version, instrument_schema_version,
                    instrument_hash, canonical_instrument
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :instrument_id,
                    true, 'Excluded Raw Quote', 'fund', 'USD', 'per_unit',
                    1, 1, 'available', ARRAY[]::varchar(64)[], 'methodology',
                    'quote-v1',
                    'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                    'fresh-v1', 'calendar_days', 5, 'resolver-v1',
                    'portfolio-daily-instrument.v1', :instrument_hash,
                    '{}'::jsonb
                );
                INSERT INTO portfolio.portfolio_daily_corp_action_window (
                    manifest_id, run_id, portfolio_id, instrument_id,
                    window_from, window_to, selection_policy_version,
                    selection_policy_revision, consumer_policy_version,
                    freshness_policy_version, freshness_mode,
                    freshness_max_age_days, resolver_strategy_version,
                    expected_event_count, captured_event_count,
                    coverage_state, reason_codes
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :instrument_id,
                    DATE '2026-07-14', DATE '2026-07-14', 'corp-v1',
                    'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
                    'corp-v1', 'corp-v1', 'transaction_snapshot', 0,
                    'corp-v1', 0, 0, 'complete', ARRAY[]::varchar(64)[]
                );
                INSERT INTO portfolio.portfolio_daily_quote_window (
                    manifest_id, run_id, portfolio_id, quote_window_id,
                    instrument_id, quote_role, valuation_date, quote_currency,
                    window_start_at, window_end_at, selection_policy_version,
                    selection_policy_revision, consumer_policy_version,
                    freshness_policy_version, freshness_mode,
                    freshness_max_age_days, resolver_strategy_version,
                    freshness_limit_seconds, candidate_count, adopted_count,
                    selection_status, coverage_state, reason_codes
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :quote_window_id,
                    :instrument_id, 'valuation', DATE '2026-07-14', 'USD',
                    :cutoff_at - INTERVAL '1 day', :cutoff_at, 'quote-v1',
                    'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                    'valuation-v1', 'fresh-v1', 'calendar_days', 5,
                    'resolver-v1', 432000, 1, 0, 'unavailable', 'unavailable',
                    ARRAY['provider_rejected']::varchar(64)[]
                );
                INSERT INTO portfolio.portfolio_daily_quote_candidate (
                    manifest_id, run_id, portfolio_id, quote_window_id,
                    candidate_rank, quote_series_id, observation_id,
                    revision_id, revision_number, observation_date,
                    quote_value, quote_status, source_published_at, ingested_at,
                    ingestion_time_state, payload_hash, decision,
                    decision_reason_code
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :quote_window_id,
                    1, :quote_series_id, :observation_id, :revision_id, 1,
                    DATE '2026-07-14', :quote_value, 'rejected', NULL,
                    :cutoff_at, 'observed', :payload_hash, :decision,
                    'provider_rejected'
                );
            """,
            {
                "manifest_id": manifest_id,
                "run_id": run_id,
                "portfolio_id": portfolio_id,
                "instrument_id": quote_instrument_id,
                "quote_series_id": quote_series_id,
                "observation_id": observation_id,
                "revision_id": revision_id,
                "quote_window_id": quote_window_id,
                "quote_value": excluded_quote_value,
                "value_input_scale": max(
                    -excluded_quote_value.as_tuple().exponent,
                    0,
                ),
                "decision": raw_quote_decision,
                "cutoff_at": cutoff_at,
                "payload_hash": "sha256:" + "9" * 64,
                "instrument_hash": "8" * 64,
            },
        )
    _execute_batch(
        connection,
        """
            INSERT INTO portfolio.portfolio_daily_config_input (
                manifest_id, run_id, portfolio_id, effective_as_of, range_start,
                knowledge_cutoff_at, base_currency, valuation_timezone,
                valuation_cutoff_local_time, valuation_cutoff_policy,
                valuation_calendar_id, valuation_calendar_version,
                quote_policy_version, quote_selection_policy_revision,
                freshness_policy_version, freshness_mode, freshness_max_age_days,
                quote_resolver_strategy_version, fx_policy_version,
                corporate_action_policy_version, operating_profile,
                config_schema_version, config_hash, canonical_config
            ) VALUES (
                :manifest_id, :run_id, :portfolio_id, DATE '2026-07-14',
                DATE '2026-07-14',
                :cutoff_at, 'USD', 'UTC', TIME '16:00', 'close',
                'weekday', 'v1', 'quote-v1',
                'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                'fresh-v1',
                'calendar_days', 5, 'resolver-v1', 'fx-v1', 'corp-v1',
                'portfolio_daily', 'portfolio-daily-config.v1', :hash, '{}'::jsonb
            );
            INSERT INTO portfolio.portfolio_daily_account_input (
                manifest_id, run_id, portfolio_id, account_id, account_name,
                account_type, currency, cost_basis_method, account_status,
                account_schema_version, account_hash, canonical_account
            ) VALUES (
                :manifest_id, :run_id, :portfolio_id, :account_id, 'Cash',
                'deposit_account', 'USD', NULL, 'active', 'account.v1',
                :hash, '{}'::jsonb
            );
            """,
        {
            "manifest_id": manifest_id,
            "run_id": run_id,
            "portfolio_id": portfolio_id,
            "account_id": account_id,
            "cutoff_at": cutoff_at,
            "hash": "a" * 64,
        },
    )
    if lot_source is not None:
        _execute_batch(
            connection,
            """
                INSERT INTO portfolio.portfolio_daily_instrument_input (
                    manifest_id, run_id, portfolio_id, instrument_id,
                    requires_valuation, instrument_name, instrument_type,
                    currency, price_unit, contract_multiplier, price_factor,
                    valuation_contract_state, valuation_contract_reason_codes,
                    valuation_factor_source, quote_policy_version,
                    quote_selection_policy_revision, freshness_policy_version,
                    freshness_mode, freshness_max_age_days,
                    resolver_strategy_version, instrument_schema_version,
                    instrument_hash, canonical_instrument
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :instrument_id,
                    false, 'Recurring Unit Cost Fund', 'fund', 'USD',
                    'per_unit', 1, 1, 'available', ARRAY[]::varchar(64)[],
                    'methodology', 'quote-v1',
                    'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                    'fresh-v1', 'calendar_days', 5, 'resolver-v1',
                    'portfolio-daily-instrument.v1', :hash, '{}'::jsonb
                );
                INSERT INTO portfolio.portfolio_daily_corp_action_window (
                    manifest_id, run_id, portfolio_id, instrument_id,
                    window_from, window_to, selection_policy_version,
                    selection_policy_revision, consumer_policy_version,
                    freshness_policy_version, freshness_mode,
                    freshness_max_age_days, resolver_strategy_version,
                    expected_event_count, captured_event_count,
                    coverage_state, reason_codes
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :instrument_id,
                    DATE '2026-07-14', DATE '2026-07-14', 'corp-v1',
                    'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
                    'corp-v1', 'corp-v1', 'transaction_snapshot', 0,
                    'corp-v1', 0, 0, 'complete', ARRAY[]::varchar(64)[]
                );
                """,
            {
                "manifest_id": manifest_id,
                "run_id": run_id,
                "portfolio_id": portfolio_id,
                "instrument_id": lot_source["instrument_id"],
                "hash": "e" * 64,
            },
        )
    identity_path_id = str(uuid4())
    _execute_batch(
        connection,
        """
            INSERT INTO portfolio.portfolio_daily_fx_path (
                manifest_id, run_id, portfolio_id, fx_path_id,
                valuation_date, from_currency, to_currency, path_kind,
                resolution_status, leg_count, resolved_rate,
                rate_derivation_residual_exact, selection_policy_version,
                consumer_policy_version, freshness_policy_version,
                freshness_mode, freshness_max_age_days,
                resolver_strategy_version, rate_math_precision,
                rate_rounding_mode, coverage_state, reason_codes
            ) VALUES (
                :manifest_id, :run_id, :portfolio_id, :fx_path_id,
                DATE '2026-07-14', 'USD', 'USD', 'identity', 'resolved',
                0, 1, 0, 'fx-select-v1', 'fx-v1', 'fresh-v1',
                'calendar_days', 5, 'resolver-v1', 50,
                'ROUND_HALF_EVEN', 'complete', ARRAY[]::varchar(64)[]
            );
            """,
        {
            "manifest_id": manifest_id,
            "run_id": run_id,
            "portfolio_id": portfolio_id,
            "fx_path_id": identity_path_id,
        },
    )
    if include_exact_cross_fx_path:
        cross_path_id, _ = _insert_resolved_fx_path(
            connection,
            run_id=run_id,
            manifest_id=manifest_id,
            portfolio_id=portfolio_id,
            cutoff_at=cutoff_at,
            legs=(
                (
                    "HKD",
                    "USD",
                    Decimal("7.000000000000000001"),
                    True,
                    None,
                ),
                (
                    "USD",
                    "CNY",
                    Decimal("1.234567890123456789"),
                    False,
                    None,
                ),
            ),
        )
        if cross_resolved_rate_override is not None:
            connection.execute(
                text(
                    """
                    UPDATE portfolio.portfolio_daily_fx_path
                    SET resolved_rate = :resolved_rate
                    WHERE manifest_id = :manifest_id
                      AND fx_path_id = :fx_path_id
                    """
                ),
                {
                    "resolved_rate": cross_resolved_rate_override,
                    "manifest_id": manifest_id,
                    "fx_path_id": cross_path_id,
                },
            )
    if include_missing_fx_path:
        fx_path_id = str(uuid4())
        _execute_batch(
            connection,
            """
                INSERT INTO portfolio.portfolio_daily_fx_path (
                    manifest_id, run_id, portfolio_id, fx_path_id,
                    valuation_date, from_currency, to_currency, path_kind,
                    resolution_status, leg_count, selection_policy_version,
                    consumer_policy_version, freshness_policy_version,
                    freshness_mode, freshness_max_age_days,
                    resolver_strategy_version, rate_math_precision,
                    rate_rounding_mode, coverage_state, reason_codes
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :fx_path_id,
                    DATE '2026-07-14', 'EUR', 'USD', 'direct',
                    'unavailable', 1, 'fx-select-v1', 'fx-v1', 'fresh-v1',
                    'calendar_days', 5, 'resolver-v1', 50,
                    'ROUND_HALF_EVEN', 'unavailable',
                    ARRAY['missing_fx_quote']::varchar(64)[]
                );
                INSERT INTO portfolio.portfolio_daily_fx_leg (
                    manifest_id, run_id, portfolio_id, fx_path_id, leg_order,
                    from_currency, to_currency, is_inverted,
                    leg_resolution_status, reason_codes,
                    consumer_policy_version, freshness_policy_version,
                    freshness_mode, freshness_max_age_days,
                    resolver_strategy_version, rate_math_precision,
                    rate_rounding_mode
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :fx_path_id, 1,
                    'EUR', 'USD', false, 'missing',
                    ARRAY['missing_fx_quote']::varchar(64)[],
                    'fx-v1', 'fresh-v1', 'calendar_days', 5,
                    'resolver-v1', 50, 'ROUND_HALF_EVEN'
                );
                """,
            {
                "manifest_id": manifest_id,
                "run_id": run_id,
                "portfolio_id": portfolio_id,
                "fx_path_id": fx_path_id,
            },
        )
    if include_withdrawn_fx_leg:
        fx_path_id = str(uuid4())
        quote_series_id = str(uuid4())
        observation_id = str(uuid4())
        revision_id = str(uuid4())
        _execute_batch(
            connection,
            """
                INSERT INTO instrument_registry.quote_series (
                    quote_series_id, instrument_id, metric_family,
                    quote_basis, currency
                ) VALUES (
                    :quote_series_id, 'fx-usd-cny', 'fx', 'spot', 'CNY'
                );
                INSERT INTO instrument_registry.quote_observation (
                    observation_id, quote_series_id, as_of_date
                ) VALUES (
                    :observation_id, :quote_series_id, DATE '2026-07-14'
                );
                INSERT INTO instrument_registry.quote_observation_revision (
                    revision_id, observation_id, revision_number, value,
                    value_input_scale, numeric_scale_state,
                    payload_schema_version,
                    status, ingested_at, ingestion_time_state, payload_hash,
                    is_current, superseded_at
                ) VALUES (
                    :revision_id, :observation_id, 1, NULL,
                    NULL, NULL, 2, 'withdrawn',
                    :cutoff_at, 'observed', :payload_hash, true, NULL
                );
                INSERT INTO portfolio.portfolio_daily_fx_path (
                    manifest_id, run_id, portfolio_id, fx_path_id,
                    valuation_date, from_currency, to_currency, path_kind,
                    resolution_status, leg_count, selection_policy_version,
                    consumer_policy_version, freshness_policy_version,
                    freshness_mode, freshness_max_age_days,
                    resolver_strategy_version, rate_math_precision,
                    rate_rounding_mode, coverage_state, reason_codes
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :fx_path_id,
                    DATE '2026-07-14', 'CNY', 'USD', 'inverse',
                    'unavailable', 1, 'fx-select-v1', 'fx-v1', 'fresh-v1',
                    'calendar_days', 5, 'resolver-v1', 50,
                    'ROUND_HALF_EVEN', 'unavailable',
                    ARRAY['withdrawn_fx_quote']::varchar(64)[]
                );
                INSERT INTO portfolio.portfolio_daily_fx_leg (
                    manifest_id, run_id, portfolio_id, fx_path_id, leg_order,
                    from_currency, to_currency, is_inverted,
                    leg_resolution_status, reason_codes, quote_series_id,
                    observation_id, revision_id, revision_number,
                    observation_date, quoted_rate, effective_rate,
                    rate_derivation_residual_exact, quote_status,
                    source_published_at, ingested_at, ingestion_time_state,
                    payload_hash,
                    consumer_policy_version, freshness_policy_version,
                    freshness_mode, freshness_max_age_days,
                    resolver_strategy_version, rate_math_precision,
                    rate_rounding_mode
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :fx_path_id, 1,
                    'CNY', 'USD', true, 'rejected',
                    ARRAY['withdrawn_fx_quote']::varchar(64)[],
                    :quote_series_id, :observation_id, :revision_id, 1,
                    DATE '2026-07-14', NULL, NULL, NULL, 'withdrawn', NULL,
                    :cutoff_at, 'observed', :payload_hash, 'fx-v1', 'fresh-v1',
                    'calendar_days', 5, 'resolver-v1', 50, 'ROUND_HALF_EVEN'
                );
                """,
            {
                "manifest_id": manifest_id,
                "run_id": run_id,
                "portfolio_id": portfolio_id,
                "fx_path_id": fx_path_id,
                "quote_series_id": quote_series_id,
                "observation_id": observation_id,
                "revision_id": revision_id,
                "cutoff_at": cutoff_at,
                "payload_hash": "sha256:" + "e" * 64,
            },
        )
    if corporate_action_source_updated_offset is not None:
        instrument_id = f"corp-instrument-{uuid4()}"
        event_id = f"corp-event-{uuid4()}"
        source_updated_at = cutoff_at + corporate_action_source_updated_offset
        input_updated_at = cutoff_at + (
            corporate_action_input_updated_offset
            if corporate_action_input_updated_offset is not None
            else corporate_action_source_updated_offset
        )
        source_updated_text = (
            source_updated_at.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        canonical_event = json.dumps(
            {
                "action_type": "share_split",
                "announcement_date": "2026-07-13",
                "corporate_action_event_id": event_id,
                "cost_basis_treatment": "carry",
                "created_at": source_updated_text,
                "effective_date": "2026-07-14",
                "external_event_id": "schema-test-event",
                "instrument_id": instrument_id,
                "new_units": "2",
                "old_units": "1",
                "payable_date": "2026-07-14",
                "provenance": {},
                "quantity_precision": 0,
                "quantity_rounding": "exact",
                "record_date": "2026-07-13",
                "source": "schema_test",
                "status": "confirmed",
                "updated_at": source_updated_text,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        _execute_batch(
            connection,
            """
                INSERT INTO instrument_registry.instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json
                ) VALUES (
                    :instrument_id, 'Corporate Action Instrument', 'fund', 'USD',
                    '{}'::json, '{}'::json, '{}'::json, '{}'::json
                );
                INSERT INTO instrument_registry.corporate_action_event (
                    corporate_action_event_id, instrument_id, action_type,
                    announcement_date, record_date, effective_date, payable_date,
                    new_units, old_units, quantity_rounding, quantity_precision,
                    cost_basis_treatment, source, external_event_id, status,
                    provenance_json, created_at, updated_at
                ) VALUES (
                    :event_id, :instrument_id, 'share_split', DATE '2026-07-13',
                    DATE '2026-07-13', DATE '2026-07-14', DATE '2026-07-14',
                    '2', '1', 'exact', 0, 'carry', 'schema_test',
                    'schema-test-event', 'confirmed', '{}'::json,
                    :source_updated_text, :source_updated_text
                );
                INSERT INTO portfolio.portfolio_daily_instrument_input (
                    manifest_id, run_id, portfolio_id, instrument_id,
                    requires_valuation, instrument_name, instrument_type,
                    currency, price_unit, contract_multiplier, price_factor,
                    valuation_contract_state, valuation_contract_reason_codes,
                    valuation_factor_source, quote_policy_version,
                    quote_selection_policy_revision, freshness_policy_version,
                    freshness_mode, freshness_max_age_days,
                    resolver_strategy_version, instrument_schema_version,
                    instrument_hash, canonical_instrument
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :instrument_id,
                    false, 'Corporate Action Instrument', 'fund', 'USD',
                    'per_unit', 1, 1,
                    'available', ARRAY[]::varchar(64)[], 'methodology',
                    'quote-v1',
                    'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                    'fresh-v1', 'calendar_days', 5, 'resolver-v1',
                    'instrument.v1', :hash, '{}'::jsonb
                );
                INSERT INTO portfolio.portfolio_daily_corp_action_window (
                    manifest_id, run_id, portfolio_id, instrument_id,
                    window_from, window_to, selection_policy_version,
                    selection_policy_revision, consumer_policy_version,
                    freshness_policy_version, freshness_mode,
                    freshness_max_age_days, resolver_strategy_version,
                    expected_event_count, captured_event_count,
                    coverage_state, reason_codes
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :instrument_id,
                    DATE '2026-07-14', DATE '2026-07-14', 'corp-v1',
                    'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
                    'corp-v1', 'corp-v1', 'transaction_snapshot', 0,
                    'corp-v1', 1, 1, 'complete', ARRAY[]::varchar(64)[]
                );
                INSERT INTO portfolio.portfolio_daily_corp_action_input (
                    manifest_id, run_id, portfolio_id,
                    corporate_action_event_id, instrument_id, action_type,
                    announcement_date, record_date, effective_date, payable_date,
                    new_units, old_units, quantity_rounding, quantity_precision,
                    cost_basis_treatment, source, external_event_id, event_status,
                    event_updated_at, event_schema_version, event_hash,
                    canonical_event
                ) VALUES (
                    :manifest_id, :run_id, :portfolio_id, :event_id,
                    :instrument_id, 'share_split', DATE '2026-07-13',
                    DATE '2026-07-13', DATE '2026-07-14', DATE '2026-07-14',
                    2, 1, 'exact', 0, 'carry', 'schema_test',
                    'schema-test-event', 'confirmed', :input_updated_at,
                    'portfolio-daily-corporate-action.v1', :hash,
                    CAST(:canonical_event AS jsonb)
                );
                """,
            {
                "manifest_id": manifest_id,
                "run_id": run_id,
                "portfolio_id": portfolio_id,
                "instrument_id": instrument_id,
                "event_id": event_id,
                "source_updated_text": source_updated_text,
                "input_updated_at": input_updated_at,
                "canonical_event": canonical_event,
                "hash": "d" * 64,
            },
        )
    includes_corporate_action = corporate_action_source_updated_offset is not None
    dependency_counts = json.dumps(
        {
            "portfolio_config": 1,
            "account": 1,
            "transaction_revision": int(include_recurring_unit_cost_lot_source),
            "instrument_snapshot": int(include_recurring_unit_cost_lot_source)
            + int(excluded_quote_value is not None)
            + int(includes_corporate_action),
            "corporate_action_window": int(include_recurring_unit_cost_lot_source)
            + int(excluded_quote_value is not None)
            + int(includes_corporate_action),
            "corporate_action_snapshot": int(includes_corporate_action),
            "quote_window": int(excluded_quote_value is not None),
            "quote_candidate": int(excluded_quote_value is not None),
            "fx_path": 1
            + int(include_exact_cross_fx_path)
            + int(include_missing_fx_path)
            + int(include_withdrawn_fx_leg),
            "fx_leg": 2 * int(include_exact_cross_fx_path)
            + int(include_missing_fx_path)
            + int(include_withdrawn_fx_leg),
            "prior_publication": 0,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    _execute_batch(
        connection,
        """
            UPDATE calculation_registry.calculation_input_manifest
            SET status = 'sealed', canonical_manifest_hash = :hash,
                dependency_counts = CAST(:counts AS jsonb),
                sealed_at = clock_timestamp()
            WHERE manifest_id = :manifest_id;
            UPDATE calculation_registry.calculation_run
            SET status = 'queued', manifest_id = :manifest_id
            WHERE run_id = :run_id;
            INSERT INTO calculation_registry.calculation_job (
                job_id, run_id, dedupe_key
            ) VALUES (:job_id, :run_id, :dedupe_key);
            """,
        {
            "hash": "b" * 64,
            "counts": dependency_counts,
            "manifest_id": manifest_id,
            "run_id": run_id,
            "job_id": job_id,
            "dedupe_key": "1" * 64,
        },
    )
    return run_id, manifest_id, job_id


def test_excluded_raw_quote_with_extreme_scale_can_be_sealed_losslessly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = Decimal("1." + "1234567890" * 30)
    with _postgres_database(monkeypatch) as (engine, _):
        with engine.begin() as connection:
            _, manifest_id, _ = _seed_sealed_manifest(
                connection,
                excluded_quote_value=raw,
            )
            stored = connection.execute(
                text(
                    """
                    SELECT candidate.quote_value, manifest.status
                    FROM portfolio.portfolio_daily_quote_candidate AS candidate
                    JOIN calculation_registry.calculation_input_manifest AS manifest
                      ON manifest.manifest_id = candidate.manifest_id
                    WHERE candidate.manifest_id = :manifest_id
                    """
                ),
                {"manifest_id": manifest_id},
            ).one()
            assert stored == (raw, "sealed")


def test_adopted_raw_quote_outside_source_domain_is_rejected_not_rounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = Decimal("1.0000000000001")
    with _postgres_database(monkeypatch) as (engine, _):
        with engine.begin() as connection:
            with pytest.raises(
                DBAPIError,
                match=("ck_portfolio_daily_quote_candidate_adopted_price_domain"),
            ):
                with connection.begin_nested():
                    _seed_sealed_manifest(
                        connection,
                        excluded_quote_value=raw,
                        raw_quote_decision="adopted",
                    )


def test_same_transaction_facts_can_seal_a_second_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prior manifest must not contaminate cutoff-selection validation."""

    with _postgres_database(monkeypatch) as (engine, _):
        with engine.begin() as connection:
            source_run_id, source_manifest_id, _ = _seed_sealed_manifest(
                connection,
                include_recurring_unit_cost_lot_source=True,
            )
            run_id = str(uuid4())
            manifest_id = str(uuid4())
            connection.execute(
                text(
                    """
                    INSERT INTO calculation_registry.calculation_run (
                        run_id, calculation_kind, scope_kind, scope_id,
                        requested_as_of, effective_as_of, cutoff_at, timezone,
                        methodology_version, input_schema_version,
                        output_schema_version, captured_generation, dedupe_key,
                        requested_by
                    )
                    SELECT :run_id, calculation_kind, scope_kind, scope_id,
                        requested_as_of, effective_as_of, cutoff_at, timezone,
                        methodology_version, input_schema_version,
                        output_schema_version, captured_generation, :dedupe_key,
                        'schema-test-republication'
                    FROM calculation_registry.calculation_run
                    WHERE run_id = :source_run_id
                    """
                ),
                {
                    "run_id": run_id,
                    "source_run_id": source_run_id,
                    "dedupe_key": "2" * 64,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO calculation_registry.calculation_input_manifest (
                        manifest_id, run_id, captured_generation, schema_version
                    )
                    SELECT :manifest_id, :run_id, captured_generation,
                        schema_version
                    FROM calculation_registry.calculation_input_manifest
                    WHERE manifest_id = :source_manifest_id
                    """
                ),
                {
                    "run_id": run_id,
                    "manifest_id": manifest_id,
                    "source_manifest_id": source_manifest_id,
                },
            )

            for table in DEPENDENCY_TABLES:
                column_names = tuple(column.name for column in table.columns)
                quoted_columns = ", ".join(f'"{name}"' for name in column_names)
                selected_columns = ", ".join(
                    (
                        ":manifest_id"
                        if name == "manifest_id"
                        else ":run_id"
                        if name == "run_id"
                        else f'"{name}"'
                    )
                    for name in column_names
                )
                connection.execute(
                    text(
                        f'INSERT INTO "{table.schema}"."{table.name}" '
                        f"({quoted_columns}) SELECT {selected_columns} "
                        f'FROM "{table.schema}"."{table.name}" '
                        'WHERE "manifest_id" = :source_manifest_id'
                    ),
                    {
                        "manifest_id": manifest_id,
                        "run_id": run_id,
                        "source_manifest_id": source_manifest_id,
                    },
                )

            sealed_manifest_id = connection.scalar(
                text(
                    """
                    UPDATE calculation_registry.calculation_input_manifest AS target
                    SET status = 'sealed',
                        canonical_manifest_hash = :hash,
                        dependency_counts = source.dependency_counts,
                        sealed_at = clock_timestamp()
                    FROM calculation_registry.calculation_input_manifest AS source
                    WHERE target.manifest_id = :manifest_id
                      AND source.manifest_id = :source_manifest_id
                    RETURNING target.manifest_id
                    """
                ),
                {
                    "hash": "c" * 64,
                    "manifest_id": manifest_id,
                    "source_manifest_id": source_manifest_id,
                },
            )

            assert str(sealed_manifest_id) == manifest_id


def _lease(connection, *, job_id: str, attempt: int, worker_id: str) -> None:
    _execute_batch(
        connection,
        """
            UPDATE calculation_registry.calculation_job
            SET status = 'leased', attempt = :attempt,
                fencing_token = :attempt, lease_owner = :worker_id,
                heartbeat_at = clock_timestamp(),
                lease_expires_at = clock_timestamp() + interval '5 minutes',
                failure_code = NULL, failure_diagnostic = NULL
            WHERE job_id = :job_id
            """,
        {"job_id": job_id, "attempt": attempt, "worker_id": worker_id},
    )


def _insert_unavailable_attempt(
    connection,
    *,
    run_id: str,
    token: int,
    worker_id: str,
    output_hash: str,
) -> None:
    _execute_batch(
        connection,
        """
            INSERT INTO portfolio.portfolio_daily_run_output (
                run_id, output_fencing_token, worker_id, portfolio_id,
                range_start, range_end, methodology_version,
                input_schema_version, output_schema_version, closure_status,
                canonical_output_hash, snapshot_count, measured_nav_count,
                holding_count, balance_count, lot_count,
                lot_disposition_count, contribution_count,
                unavailable_component_count, ledger_balance_residual_exact,
                nav_bridge_residual_exact, pnl_residual_exact,
                twr_residual_exact, lot_residual_exact,
                rounding_adjustment_base, coverage_state, reason_codes
            ) VALUES (
                :run_id, :token, :worker_id, 'attempt-isolation',
                DATE '2026-07-14', DATE '2026-07-14',
                'portfolio-daily.exact.v1', 'portfolio-daily-input.v1',
                'portfolio-daily-output.v1', 'passed', :output_hash,
                1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0,
                'unavailable', ARRAY['valuation_unavailable']::varchar(64)[]
            );
            INSERT INTO portfolio.portfolio_daily_snapshot_output (
                run_id, output_fencing_token, worker_id, portfolio_id,
                as_of_date, base_currency, measured_nav,
                measured_position_market_value, measured_book_pnl,
                measured_return, measured_external_flows,
                unavailable_component_count,
                external_flow_in, external_flow_out,
                pnl_component_rounding_adjustment,
                position_attribution_rounding_adjustment,
                calculation_status, return_chain_status,
                nav_rounding_adjustment, pnl_rounding_adjustment,
                nav_coverage_state, nav_reason_codes,
                book_pnl_coverage_state, book_pnl_reason_codes,
                return_coverage_state, return_reason_codes,
                flow_coverage_state, flow_reason_codes,
                position_attribution_coverage_state,
                position_attribution_reason_codes,
                valuation_endpoint_status, valuation_reason_codes
            ) VALUES (
                :run_id, :token, :worker_id, 'attempt-isolation',
                DATE '2026-07-14', 'USD', false, false, false, false, true, 1,
                0, 0, 0, 0, 'no_new_valuation', 'no_new_valuation', 0, 0,
                'unavailable', ARRAY['valuation_unavailable']::varchar(64)[],
                'unavailable', ARRAY['book_pnl_unavailable']::varchar(64)[],
                'unavailable', ARRAY['unavailable_coverage']::varchar(64)[],
                'complete', ARRAY[]::varchar(64)[],
                'unavailable', ARRAY['attribution_unavailable']::varchar(64)[],
                'unavailable', ARRAY['valuation_unavailable']::varchar(64)[]
            );
            """,
        {
            "run_id": run_id,
            "token": token,
            "worker_id": worker_id,
            "output_hash": output_hash,
        },
    )


def test_snapshot_flow_reason_codes_are_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        worker_id = "portfolio-daily-flow-reason-worker"
        with engine.begin() as connection:
            run_id, _, job_id = _seed_sealed_manifest(connection)
            _lease(connection, job_id=job_id, attempt=1, worker_id=worker_id)
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status = 'running', started_at = clock_timestamp()
                    WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            )
            with pytest.raises(
                DBAPIError,
                match="portfolio_daily_reason_codes_not_canonical",
            ):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            INSERT INTO portfolio.portfolio_daily_snapshot_output (
                                run_id, output_fencing_token, worker_id, portfolio_id,
                                as_of_date, base_currency, measured_nav,
                                measured_position_market_value, measured_book_pnl,
                                measured_return, measured_external_flows,
                                unavailable_component_count,
                                external_flow_in, external_flow_out,
                                pnl_component_rounding_adjustment,
                                position_attribution_rounding_adjustment,
                                calculation_status, return_chain_status,
                                nav_rounding_adjustment, pnl_rounding_adjustment,
                                nav_coverage_state, nav_reason_codes,
                                book_pnl_coverage_state, book_pnl_reason_codes,
                                return_coverage_state, return_reason_codes,
                                flow_coverage_state, flow_reason_codes,
                                position_attribution_coverage_state,
                                position_attribution_reason_codes,
                                valuation_endpoint_status, valuation_reason_codes
                            ) VALUES (
                                :run_id, 1, :worker_id, 'attempt-isolation',
                                DATE '2026-07-14', 'USD', false, false, false,
                                false, false, 2, 0, NULL, 0, 0,
                                'no_new_valuation', 'no_new_valuation', 0, 0,
                                'unavailable',
                                    ARRAY['valuation_unavailable']::varchar(64)[],
                                'unavailable',
                                    ARRAY['book_pnl_unavailable']::varchar(64)[],
                                'unavailable',
                                    ARRAY['unavailable_coverage']::varchar(64)[],
                                'partial',
                                    ARRAY['z_reason', 'a_reason']::varchar(64)[],
                                'unavailable',
                                    ARRAY['attribution_unavailable']::varchar(64)[],
                                'unavailable',
                                    ARRAY['valuation_unavailable']::varchar(64)[]
                            )
                            """
                        ),
                        {"run_id": run_id, "worker_id": worker_id},
                    )


def test_snapshot_measured_pnl_and_return_require_exact_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        worker_id = "portfolio-daily-measurement-dependency-worker"
        with engine.begin() as connection:
            run_id, _, job_id = _seed_sealed_manifest(connection)
            _lease(connection, job_id=job_id, attempt=1, worker_id=worker_id)
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status = 'running', started_at = clock_timestamp()
                    WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            )
            snapshot_sql = text(
                """
                INSERT INTO portfolio.portfolio_daily_snapshot_output (
                    run_id, output_fencing_token, worker_id, portfolio_id,
                    as_of_date, base_currency, measured_nav,
                    measured_position_market_value, measured_book_pnl,
                    measured_return, measured_external_flows,
                    unavailable_component_count,
                    opening_nav, closing_nav, position_market_value,
                    settled_cash, pending_receivable, pending_payable,
                    accrual_receivable, accrual_payable,
                    external_flow_in, external_flow_out,
                    economic_pnl, realized_pnl_daily,
                    unrealized_pnl_beginning, unrealized_pnl_ending,
                    unrealized_pnl_change, gross_income_daily,
                    expensed_fee_daily, expensed_tax_daily,
                    cash_fx_effect_daily, pending_fx_effect_daily,
                    accrual_fx_effect_daily, fx_conversion_effect_daily,
                    pnl_component_rounding_adjustment,
                    position_attribution_rounding_adjustment,
                    subperiod_twr_method50, subperiod_twr_published,
                    cumulative_twr_method50, cumulative_twr_published,
                    wealth_index_method50, wealth_index_published,
                    peak_wealth_index_method50, peak_wealth_index_published,
                    drawdown_method50, drawdown_published,
                    wealth_chain_rounding_adjustment_exact,
                    return_period_start_date, return_period_end_date,
                    return_period_day_count, calculation_status,
                    return_chain_status, nav_rounding_adjustment,
                    pnl_rounding_adjustment, nav_coverage_state,
                    nav_reason_codes, book_pnl_coverage_state,
                    book_pnl_reason_codes, return_coverage_state,
                    return_reason_codes, flow_coverage_state,
                    flow_reason_codes,
                    position_attribution_coverage_state,
                    position_attribution_reason_codes,
                    valuation_endpoint_status, valuation_reason_codes
                ) VALUES (
                    :run_id, 1, :worker_id, 'attempt-isolation',
                    DATE '2026-07-14', 'USD', :measured_nav,
                    :measured_nav, :measured_book_pnl, :measured_return,
                    :measured_external_flows, 2,
                    CASE WHEN :measured_nav THEN 100 ELSE NULL END,
                    CASE WHEN :measured_nav THEN 100 ELSE NULL END,
                    CASE WHEN :measured_nav THEN 100 ELSE NULL END,
                    CASE WHEN :measured_nav THEN 0 ELSE NULL END,
                    CASE WHEN :measured_nav THEN 0 ELSE NULL END,
                    CASE WHEN :measured_nav THEN 0 ELSE NULL END,
                    CASE WHEN :measured_nav THEN 0 ELSE NULL END,
                    CASE WHEN :measured_nav THEN 0 ELSE NULL END,
                    0,
                    CASE WHEN :measured_external_flows THEN 0 ELSE NULL END,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    0, 0,
                    CASE WHEN :measured_return THEN 0 ELSE NULL END,
                    CASE WHEN :measured_return THEN 0 ELSE NULL END,
                    CASE WHEN :measured_return THEN 0 ELSE NULL END,
                    CASE WHEN :measured_return THEN 0 ELSE NULL END,
                    CASE WHEN :measured_return THEN 1 ELSE NULL END,
                    CASE WHEN :measured_return THEN 1 ELSE NULL END,
                    CASE WHEN :measured_return THEN 1 ELSE NULL END,
                    CASE WHEN :measured_return THEN 1 ELSE NULL END,
                    CASE WHEN :measured_return THEN 0 ELSE NULL END,
                    CASE WHEN :measured_return THEN 0 ELSE NULL END,
                    CASE WHEN :measured_return THEN 0 ELSE NULL END,
                    CASE WHEN :measured_return THEN DATE '2026-07-13' ELSE NULL END,
                    CASE WHEN :measured_return THEN DATE '2026-07-14' ELSE NULL END,
                    CASE WHEN :measured_return THEN 1 ELSE NULL END,
                    CASE WHEN :measured_return THEN 'calculated' ELSE 'no_new_valuation' END,
                    CASE WHEN :measured_return THEN 'active' ELSE 'no_new_valuation' END,
                    0, 0,
                    CASE WHEN :measured_nav THEN 'complete' ELSE 'unavailable' END,
                    CASE WHEN :measured_nav
                        THEN ARRAY[]::varchar(64)[]
                        ELSE ARRAY['valuation_unavailable']::varchar(64)[] END,
                    CASE WHEN :measured_book_pnl THEN 'complete' ELSE 'unavailable' END,
                    CASE WHEN :measured_book_pnl
                        THEN ARRAY[]::varchar(64)[]
                        ELSE ARRAY['book_pnl_unavailable']::varchar(64)[] END,
                    CASE WHEN :measured_return THEN 'complete' ELSE 'unavailable' END,
                    CASE WHEN :measured_return
                        THEN ARRAY[]::varchar(64)[]
                        ELSE ARRAY['unavailable_coverage']::varchar(64)[] END,
                    CASE WHEN :measured_external_flows THEN 'complete' ELSE 'partial' END,
                    CASE WHEN :measured_external_flows
                        THEN ARRAY[]::varchar(64)[]
                        ELSE ARRAY['external_flow_base_measurement_unavailable']::varchar(64)[] END,
                    'unavailable',
                        ARRAY['attribution_unavailable']::varchar(64)[],
                    CASE WHEN :measured_nav THEN 'fresh' ELSE 'unavailable' END,
                    CASE WHEN :measured_nav
                        THEN ARRAY[]::varchar(64)[]
                        ELSE ARRAY['valuation_unavailable']::varchar(64)[] END
                )
                """
            )
            cases = (
                (
                    "ck_pd_snapshot_book_pnl_dependencies",
                    True,
                    False,
                    True,
                    False,
                ),
                (
                    "ck_pd_snapshot_return_dependencies",
                    False,
                    True,
                    True,
                    False,
                ),
                (
                    "ck_pd_snapshot_return_dependencies",
                    False,
                    True,
                    False,
                    True,
                ),
            )
            for (
                constraint_name,
                measured_book_pnl,
                measured_return,
                measured_nav,
                measured_external_flows,
            ) in cases:
                with pytest.raises(DBAPIError, match=constraint_name):
                    with connection.begin_nested():
                        connection.execute(
                            snapshot_sql,
                            {
                                "run_id": run_id,
                                "worker_id": worker_id,
                                "measured_book_pnl": measured_book_pnl,
                                "measured_return": measured_return,
                                "measured_nav": measured_nav,
                                "measured_external_flows": measured_external_flows,
                            },
                        )


def test_lot_unit_cost_is_published_with_an_exact_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legal 100/3 lot must not invent a finite "exact" unit cost."""

    with _postgres_database(monkeypatch) as (engine, _):
        worker_id = "portfolio-daily-worker"
        with engine.begin() as connection:
            run_id, _, job_id = _seed_sealed_manifest(
                connection,
                include_recurring_unit_cost_lot_source=True,
            )
            _lease(connection, job_id=job_id, attempt=1, worker_id=worker_id)
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status = 'running', started_at = clock_timestamp()
                    WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            )
            parameters = {
                "run_id": run_id,
                "worker_id": worker_id,
                "local_residual": "0.000000000001",
                "base_residual": "-0.000000000001",
            }
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_daily_lot_output (
                        run_id, output_fencing_token, worker_id, portfolio_id,
                        as_of_date, account_id, instrument_id, lot_id,
                        source_transaction_id, source_revision_id,
                        source_revision_number, custody_transaction_id,
                        custody_revision_id, custody_revision_number,
                        acquisition_date, currency,
                        open_quantity_exact, open_quantity, measured_base_cost,
                        cost_basis_local_exact, cost_basis_local,
                        unit_cost_local,
                        unit_cost_local_rounding_residual_exact,
                        cost_basis_base_exact, cost_basis_base, unit_cost_base,
                        unit_cost_base_rounding_residual_exact,
                        local_cost_rounding_adjustment,
                        base_cost_rounding_adjustment,
                        base_cost_coverage_state, base_cost_reason_codes
                    ) VALUES (
                        :run_id, 1, :worker_id, 'attempt-isolation',
                        DATE '2026-07-14', 'attempt-isolation-cash',
                        'fund-recurring-unit-cost', 'lot-100-over-3',
                        'transaction-100-over-3', 'revision-100-over-3', 1,
                        'transaction-100-over-3', 'revision-100-over-3', 1,
                        DATE '2026-07-14', 'USD',
                        3, 3, true, 100, 100, 33.333333333333,
                        CAST(:local_residual AS numeric),
                        200, 200, 66.666666666667,
                        CAST(:base_residual AS numeric),
                        0, 0, 'complete', ARRAY[]::varchar(64)[]
                    )
                    """
                ),
                parameters,
            )
            row = connection.execute(
                text(
                    """
                    SELECT acquisition_fx_rate_exact,
                           unit_cost_local_rounding_residual_exact,
                           unit_cost_base_rounding_residual_exact
                    FROM portfolio.portfolio_daily_lot_output
                    WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            ).one()
            assert row == (None, Decimal("0.000000000001"), Decimal("-0.000000000001"))

            with pytest.raises(DBAPIError, match="ck_pd_lot_unit_cost_local"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            INSERT INTO portfolio.portfolio_daily_lot_output (
                                run_id, output_fencing_token, worker_id,
                                portfolio_id, as_of_date, account_id,
                                instrument_id, lot_id, source_transaction_id,
                                source_revision_id, source_revision_number,
                                custody_transaction_id, custody_revision_id,
                                custody_revision_number,
                                acquisition_date, currency,
                                open_quantity_exact, open_quantity,
                                measured_base_cost, cost_basis_local_exact,
                                cost_basis_local, unit_cost_local,
                                unit_cost_local_rounding_residual_exact,
                                local_cost_rounding_adjustment,
                                base_cost_coverage_state, base_cost_reason_codes
                            ) VALUES (
                                :run_id, 1, :worker_id, 'attempt-isolation',
                                DATE '2026-07-14', 'attempt-isolation-cash',
                                'fund-recurring-unit-cost', 'bad-lot',
                                'transaction-100-over-3',
                                'revision-100-over-3', 1,
                                'transaction-100-over-3',
                                'revision-100-over-3', 1,
                                DATE '2026-07-14', 'USD',
                                3, 3, false, 100, 100, 33.333333333333,
                                0, 0, 'unavailable',
                                ARRAY['historical_base_cost_unavailable']::varchar(64)[]
                            )
                            """
                        ),
                        parameters,
                    )


def test_lot_cost_bridge_uses_half_even_at_the_postgres_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        worker_id = "portfolio-daily-worker"
        with engine.begin() as connection:
            run_id, _, job_id = _seed_sealed_manifest(
                connection,
                include_recurring_unit_cost_lot_source=True,
            )
            _lease(connection, job_id=job_id, attempt=1, worker_id=worker_id)
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status = 'running', started_at = clock_timestamp()
                    WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            )

            statement = text(
                """
                INSERT INTO portfolio.portfolio_daily_lot_output (
                    run_id, output_fencing_token, worker_id, portfolio_id,
                    as_of_date, account_id, instrument_id, lot_id,
                    source_transaction_id, source_revision_id,
                    source_revision_number, custody_transaction_id,
                    custody_revision_id, custody_revision_number,
                    acquisition_date, currency, open_quantity_exact,
                    open_quantity, measured_base_cost,
                    cost_basis_local_exact, cost_basis_local,
                    unit_cost_local,
                    unit_cost_local_rounding_residual_exact,
                    local_cost_rounding_adjustment,
                    base_cost_coverage_state, base_cost_reason_codes
                ) VALUES (
                    :run_id, 1, :worker_id, 'attempt-isolation',
                    DATE '2026-07-14', 'attempt-isolation-cash',
                    'fund-recurring-unit-cost', :lot_id,
                    'transaction-100-over-3', 'revision-100-over-3', 1,
                    'transaction-100-over-3', 'revision-100-over-3', 1,
                    DATE '2026-07-14', 'USD', 1, 1, false,
                    1.000000005, :published_cost, 1.000000000000,
                    0.000000005, 0, 'unavailable',
                    ARRAY['historical_base_cost_unavailable']::varchar(64)[]
                )
                """
            )
            connection.execute(
                statement,
                {
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "lot_id": "half-even-cost",
                    "published_cost": Decimal("1.00000000"),
                },
            )
            with pytest.raises(DBAPIError, match="ck_pd_lot_cost_bridge_local"):
                with connection.begin_nested():
                    connection.execute(
                        statement,
                        {
                            "run_id": run_id,
                            "worker_id": worker_id,
                            "lot_id": "away-from-zero-cost",
                            "published_cost": Decimal("1.00000001"),
                        },
                    )


def test_manifest_sealing_attempt_isolation_and_publication_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        worker_id = "portfolio-daily-worker"
        with engine.begin() as connection:
            run_id, manifest_id, job_id = _seed_sealed_manifest(connection)
            with pytest.raises(DBAPIError, match="calculation_manifest_not_building"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "DELETE FROM portfolio.portfolio_daily_account_input "
                            "WHERE manifest_id = :manifest_id"
                        ),
                        {"manifest_id": manifest_id},
                    )

            _lease(connection, job_id=job_id, attempt=1, worker_id=worker_id)
            _execute_batch(
                connection,
                """
                    UPDATE calculation_registry.calculation_run
                    SET status = 'running', started_at = clock_timestamp()
                    WHERE run_id = :run_id
                    """,
                {"run_id": run_id},
            )
            _insert_unavailable_attempt(
                connection,
                run_id=run_id,
                token=1,
                worker_id=worker_id,
                output_hash="c" * 64,
            )
            _execute_batch(
                connection,
                """
                    UPDATE calculation_registry.calculation_job
                    SET status = 'retry_wait', available_at = clock_timestamp(),
                        lease_owner = NULL, lease_expires_at = NULL,
                        heartbeat_at = NULL, failure_code = 'retryable_test',
                        failure_diagnostic = 'attempt one abandoned'
                    WHERE job_id = :job_id
                    """,
                {"job_id": job_id},
            )
            _lease(connection, job_id=job_id, attempt=2, worker_id=worker_id)

            with pytest.raises(DBAPIError, match="calculation_output_stale_fence"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            INSERT INTO portfolio.portfolio_daily_balance_output (
                                run_id, output_fencing_token, worker_id,
                                portfolio_id, as_of_date, account_id,
                                component_type, component_key, currency,
                                measured_base_amount, local_amount,
                                base_rounding_adjustment, coverage_state,
                                reason_codes
                            ) VALUES (
                                :run_id, 1, :worker_id, 'attempt-isolation',
                                DATE '2026-07-14', 'attempt-isolation-cash',
                                'settled_cash', 'USD', 'USD', false, 0, 0,
                                'unavailable', ARRAY['fx_unavailable']::varchar(64)[]
                            )
                            """
                        ),
                        {"run_id": run_id, "worker_id": worker_id},
                    )

            _insert_unavailable_attempt(
                connection,
                run_id=run_id,
                token=2,
                worker_id=worker_id,
                output_hash="d" * 64,
            )
            _execute_batch(
                connection,
                """
                    UPDATE calculation_registry.calculation_job
                    SET status = 'succeeded', lease_owner = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL,
                        failure_code = NULL, failure_diagnostic = NULL,
                        completed_at = clock_timestamp()
                    WHERE job_id = :job_id;
                    UPDATE calculation_registry.calculation_run
                    SET status = 'succeeded', completed_at = clock_timestamp()
                    WHERE run_id = :run_id;
                    """,
                {"job_id": job_id, "run_id": run_id},
            )

            publication_sql = text(
                """
                INSERT INTO calculation_registry.calculation_publication (
                    publication_id, run_id, manifest_id, calculation_kind,
                    scope_kind, scope_id, output_schema_version,
                    published_fencing_token, canonical_output_hash
                ) VALUES (
                    :publication_id, :run_id, :manifest_id, 'portfolio_daily',
                    'portfolio', 'attempt-isolation', 'portfolio-daily-output.v1',
                    :token, :output_hash
                )
                """
            )
            with pytest.raises(
                DBAPIError, match="calculation_publication_requires_succeeded_job"
            ):
                with connection.begin_nested():
                    connection.execute(
                        publication_sql,
                        {
                            "publication_id": str(uuid4()),
                            "run_id": run_id,
                            "manifest_id": manifest_id,
                            "token": 1,
                            "output_hash": "c" * 64,
                        },
                    )
            connection.execute(
                publication_sql,
                {
                    "publication_id": str(uuid4()),
                    "run_id": run_id,
                    "manifest_id": manifest_id,
                    "token": 2,
                    "output_hash": "d" * 64,
                },
            )

            with pytest.raises(DBAPIError, match="portfolio_daily_output_immutable"):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            """
                            UPDATE portfolio.portfolio_daily_run_output
                            SET rounding_adjustment_base = 0
                            WHERE run_id = :run_id AND output_fencing_token = 2
                            """
                        ),
                        {"run_id": run_id},
                    )


def test_configured_fx_path_preserves_typed_missing_leg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        with engine.begin() as connection:
            _, manifest_id, _ = _seed_sealed_manifest(
                connection,
                include_missing_fx_path=True,
                include_withdrawn_fx_leg=True,
            )
            path = connection.execute(
                text(
                    """
                    SELECT path_kind, resolution_status, leg_count,
                           resolved_rate, rate_derivation_residual_exact,
                           coverage_state
                    FROM portfolio.portfolio_daily_fx_path
                    WHERE manifest_id = :manifest_id
                      AND from_currency = 'EUR'
                    """
                ),
                {"manifest_id": manifest_id},
            ).one()
            assert path == ("direct", "unavailable", 1, None, None, "unavailable")
            leg = connection.execute(
                text(
                    """
                    SELECT leg_resolution_status, quote_series_id, revision_id,
                           quoted_rate, effective_rate, reason_codes
                    FROM portfolio.portfolio_daily_fx_leg
                    WHERE manifest_id = :manifest_id
                      AND from_currency = 'EUR'
                    """
                ),
                {"manifest_id": manifest_id},
            ).one()
            assert leg[:5] == ("missing", None, None, None, None)
            assert leg.reason_codes == ["missing_fx_quote"]
            withdrawn_leg = connection.execute(
                text(
                    """
                    SELECT fl.leg_resolution_status, fl.revision_id,
                           fl.quote_status, fl.quoted_rate, fl.effective_rate,
                           fp.path_kind, fp.resolution_status
                    FROM portfolio.portfolio_daily_fx_leg AS fl
                    JOIN portfolio.portfolio_daily_fx_path AS fp
                      ON fp.manifest_id = fl.manifest_id
                     AND fp.fx_path_id = fl.fx_path_id
                    WHERE fl.manifest_id = :manifest_id
                      AND fl.from_currency = 'CNY'
                    """
                ),
                {"manifest_id": manifest_id},
            ).one()
            assert withdrawn_leg[0] == "rejected"
            assert withdrawn_leg.revision_id is not None
            assert withdrawn_leg[2:] == (
                "withdrawn",
                None,
                None,
                "inverse",
                "unavailable",
            )


def test_inverted_fx_leg_replays_decimal50_half_even_in_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        with engine.begin() as connection:
            # 0.15 * 7 - 1 = 0.05 is internally self-consistent, but it is not
            # the versioned Decimal50/HALF_EVEN inverse.  Residual validation
            # alone must therefore not admit it as sealed calculation input.
            with pytest.raises(
                DBAPIError,
                match=(
                    "ck_pd_fx_leg_derivation|"
                    "portfolio_daily_fx_leg_effective_rate_mismatch"
                ),
            ):
                with connection.begin_nested():
                    run_id, manifest_id, portfolio_id, cutoff_at = (
                        _seed_building_fx_manifest(connection)
                    )
                    _insert_resolved_fx_path(
                        connection,
                        run_id=run_id,
                        manifest_id=manifest_id,
                        portfolio_id=portfolio_id,
                        cutoff_at=cutoff_at,
                        legs=(("CNY", "USD", Decimal("7"), True, Decimal("0.15")),),
                    )

            canonical_inverse = effective_fx_leg_rate(
                Decimal("7"), inverted=True
            )
            overprecise_inverse = Decimal(f"{canonical_inverse}1")
            assert len(overprecise_inverse.as_tuple().digits) == 51
            precision_constraint = connection.scalar(
                text(
                    """
                    SELECT pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conrelid =
                        'portfolio.portfolio_daily_fx_leg'::regclass
                      AND conname =
                        'ck_portfolio_daily_fx_leg_effective_rate_precision'
                    """
                )
            )
            assert precision_constraint is not None
            assert (
                "round_significant_half_even(effective_rate, 50)"
                in precision_constraint
            )
            with pytest.raises(
                DBAPIError,
                match=(
                    "ck_portfolio_daily_fx_leg_effective_rate_precision|"
                    "ck_pd_fx_leg_derivation|"
                    "portfolio_daily_fx_leg_effective_rate_mismatch"
                ),
            ):
                with connection.begin_nested():
                    run_id, manifest_id, portfolio_id, cutoff_at = (
                        _seed_building_fx_manifest(connection)
                    )
                    _insert_resolved_fx_path(
                        connection,
                        run_id=run_id,
                        manifest_id=manifest_id,
                        portfolio_id=portfolio_id,
                        cutoff_at=cutoff_at,
                        legs=(
                            (
                                "CNY",
                                "USD",
                                Decimal("7"),
                                True,
                                overprecise_inverse,
                            ),
                        ),
                    )

            run_id, manifest_id, portfolio_id, cutoff_at = (
                _seed_building_fx_manifest(connection)
            )
            path_id, expected_rate = _insert_resolved_fx_path(
                connection,
                run_id=run_id,
                manifest_id=manifest_id,
                portfolio_id=portfolio_id,
                cutoff_at=cutoff_at,
                legs=(("CNY", "USD", Decimal("7"), True, None),),
            )
            stored = connection.execute(
                text(
                    """
                    SELECT effective_rate, rate_derivation_residual_exact
                    FROM portfolio.portfolio_daily_fx_leg
                    WHERE manifest_id = :manifest_id
                      AND fx_path_id = :path_id
                    """
                ),
                {"manifest_id": manifest_id, "path_id": path_id},
            ).one()

            assert expected_rate == canonical_inverse
            assert stored.effective_rate == expected_rate
            assert stored.rate_derivation_residual_exact == exact_decimal_subtract(
                exact_decimal_product(expected_rate, Decimal("7")), Decimal("1")
            )


def test_cross_fx_path_preserves_exact_product_beyond_decimal50(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_rate = exact_decimal_product(
        effective_fx_leg_rate(
            Decimal("7.000000000000000001"),
            inverted=True,
        ),
        Decimal("1.234567890123456789"),
    )
    sign, digits, exponent = expected_rate.as_tuple()
    assert len(digits) > 50
    assert digits[-1] < 9
    tampered_rate = Decimal(
        (sign, (*digits[:-1], digits[-1] + 1), exponent)
    )

    with _postgres_database(monkeypatch) as (engine, _):
        with engine.begin() as connection:
            with pytest.raises(
                DBAPIError,
                match="portfolio_daily_fx_path_rate_mismatch",
            ):
                with connection.begin_nested():
                    _seed_sealed_manifest(
                        connection,
                        include_exact_cross_fx_path=True,
                        cross_resolved_rate_override=tampered_rate,
                    )

            _, manifest_id, _ = _seed_sealed_manifest(
                connection,
                include_exact_cross_fx_path=True,
            )
            sealed_cross = connection.execute(
                text(
                    """
                    SELECT manifest.status, path.resolved_rate,
                           path.rate_derivation_residual_exact,
                           count(leg.leg_order) AS leg_count
                    FROM calculation_registry.calculation_input_manifest AS manifest
                    JOIN portfolio.portfolio_daily_fx_path AS path
                      ON path.manifest_id = manifest.manifest_id
                    JOIN portfolio.portfolio_daily_fx_leg AS leg
                      ON leg.manifest_id = path.manifest_id
                     AND leg.fx_path_id = path.fx_path_id
                    WHERE manifest.manifest_id = :manifest_id
                      AND path.path_kind = 'cross'
                    GROUP BY manifest.status, path.resolved_rate,
                             path.rate_derivation_residual_exact
                    """
                ),
                {"manifest_id": manifest_id},
            ).one()

            assert sealed_cross == ("sealed", expected_rate, Decimal("0"), 2)


def test_corporate_action_lineage_and_knowledge_cutoff_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        with engine.begin() as connection:
            with pytest.raises(
                DBAPIError, match="portfolio_daily_corporate_action_mismatch"
            ):
                with connection.begin_nested():
                    _seed_sealed_manifest(
                        connection,
                        corporate_action_source_updated_offset=timedelta(0),
                        corporate_action_input_updated_offset=timedelta(seconds=1),
                    )

            with pytest.raises(
                DBAPIError,
                match="portfolio_daily_corporate_action_after_knowledge_cutoff",
            ):
                with connection.begin_nested():
                    _seed_sealed_manifest(
                        connection,
                        corporate_action_source_updated_offset=timedelta(seconds=1),
                    )

            _, manifest_id, _ = _seed_sealed_manifest(
                connection,
                corporate_action_source_updated_offset=timedelta(0),
            )
            stored_updated_at = connection.scalar(
                text(
                    """
                    SELECT event_updated_at
                    FROM portfolio.portfolio_daily_corp_action_input
                    WHERE manifest_id = :manifest_id
                    """
                ),
                {"manifest_id": manifest_id},
            )
            cutoff_at = connection.scalar(
                text(
                    """
                    SELECT cr.cutoff_at
                    FROM calculation_registry.calculation_run AS cr
                    JOIN calculation_registry.calculation_input_manifest AS cim
                      ON cim.run_id = cr.run_id
                    WHERE cim.manifest_id = :manifest_id
                    """
                ),
                {"manifest_id": manifest_id},
            )
            assert isinstance(stored_updated_at, datetime)
            assert stored_updated_at == cutoff_at


def test_holding_exact_factor_identity_and_total_loss_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        worker_id = "portfolio-daily-worker"
        with engine.begin() as connection:
            run_id, _, job_id = _seed_sealed_manifest(connection)
            _lease(connection, job_id=job_id, attempt=1, worker_id=worker_id)
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status = 'running', started_at = clock_timestamp()
                    WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            )
            holding_sql = """
                INSERT INTO portfolio.portfolio_daily_holding_output (
                    run_id, output_fencing_token, worker_id, portfolio_id,
                    as_of_date, account_id, instrument_id, currency,
                    quantity_exact, quantity, measured_price,
                    measured_market_value, measured_book_pnl,
                    unavailable_component_count, adopted_price_exact, price,
                    contract_multiplier_exact, contract_multiplier,
                    price_factor_exact, price_factor, adopted_fx_rate_exact,
                    fx_rate_to_base, market_value_local_exact,
                    market_value_base_exact, market_value_local,
                    market_value_base, pnl_component_rounding_adjustment_base,
                    position_attribution_rounding_adjustment_base,
                    local_rounding_adjustment, base_rounding_adjustment,
                    valuation_coverage_state, valuation_coverage_reason_codes,
                    book_pnl_coverage_state, book_pnl_reason_codes,
                    position_attribution_coverage_state,
                    position_attribution_reason_codes,
                    valuation_endpoint_status, valuation_reason_codes
                ) VALUES (
                    :run_id, 1, :worker_id, 'attempt-isolation',
                    DATE '2026-07-14', 'attempt-isolation-cash', :instrument_id,
                    'USD', 2, 2, true, true, false, 2, 3.1, 3.1,
                    10, 10, 0.01, 0.01, 7, 7, :local_exact,
                    :base_exact, :local_exact, :base_exact, 0, 0, 0, 0,
                    'complete', ARRAY[]::varchar(64)[],
                    'unavailable', ARRAY['book_pnl_unavailable']::varchar(64)[],
                    'unavailable', ARRAY['attribution_unavailable']::varchar(64)[],
                    'fresh', ARRAY[]::varchar(64)[]
                )
            """
            connection.execute(
                text(holding_sql),
                {
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "instrument_id": "exact-holding",
                    "local_exact": "0.62",
                    "base_exact": "4.34",
                },
            )
            with pytest.raises(DBAPIError, match="ck_pd_holding_measured_value"):
                with connection.begin_nested():
                    connection.execute(
                        text(holding_sql),
                        {
                            "run_id": run_id,
                            "worker_id": worker_id,
                            "instrument_id": "bad-exact-holding",
                            "local_exact": "0.63",
                            "base_exact": "4.41",
                        },
                    )

            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_daily_snapshot_output (
                        run_id, output_fencing_token, worker_id, portfolio_id,
                        as_of_date, base_currency, measured_nav,
                        measured_position_market_value, measured_book_pnl,
                        measured_return, measured_external_flows,
                        unavailable_component_count,
                        opening_nav, closing_nav, position_market_value,
                        settled_cash, pending_receivable, pending_payable,
                        accrual_receivable, accrual_payable,
                        external_flow_in, external_flow_out,
                        economic_pnl, realized_pnl_daily,
                        unrealized_pnl_beginning, unrealized_pnl_ending,
                        unrealized_pnl_change, gross_income_daily,
                        expensed_fee_daily, expensed_tax_daily,
                        cash_fx_effect_daily, pending_fx_effect_daily,
                        accrual_fx_effect_daily, fx_conversion_effect_daily,
                        pnl_component_rounding_adjustment,
                        position_attribution_rounding_adjustment,
                        subperiod_twr_method50, subperiod_twr_published,
                        cumulative_twr_method50, cumulative_twr_published,
                        wealth_index_method50, wealth_index_published,
                        peak_wealth_index_method50, peak_wealth_index_published,
                        drawdown_method50, drawdown_published,
                        wealth_chain_rounding_adjustment_exact,
                        reliable_anchor_date, reliable_anchor_nav_exact,
                        reliable_anchor_nav,
                        return_period_start_date, return_period_end_date,
                        return_period_day_count, calculation_status,
                        return_chain_status, nav_rounding_adjustment,
                        pnl_rounding_adjustment, nav_coverage_state,
                        nav_reason_codes, book_pnl_coverage_state,
                        book_pnl_reason_codes, return_coverage_state,
                        return_reason_codes, flow_coverage_state,
                        flow_reason_codes,
                        position_attribution_coverage_state,
                        position_attribution_reason_codes,
                        valuation_endpoint_status, valuation_reason_codes
                    ) VALUES (
                        :run_id, 1, :worker_id, 'attempt-isolation',
                        DATE '2026-07-14', 'USD', true, true, true, true,
                        true, 1,
                        100, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, -100, 0, 100, 0, -100, 0, 0, 0, 0, 0, 0, 0,
                        0, 0, -1, -1, -1, -1, 0, 0, 1, 1, -1, -1, 0,
                        DATE '2026-07-14', 0, 0,
                        DATE '2026-07-13', DATE '2026-07-14', 1,
                        'calculated', 'active', 0, 0,
                        'complete', ARRAY[]::varchar(64)[],
                        'complete', ARRAY[]::varchar(64)[],
                        'complete', ARRAY[]::varchar(64)[],
                        'complete', ARRAY[]::varchar(64)[],
                        'unavailable', ARRAY['attribution_unavailable']::varchar(64)[],
                        'fresh', ARRAY[]::varchar(64)[]
                    )
                    """
                ),
                {"run_id": run_id, "worker_id": worker_id},
            )


def test_nonterminating_drawdown_uses_decimal50_in_storage_and_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        worker_id = "portfolio-daily-drawdown-worker"
        output_hash = "f" * 64
        with engine.begin() as connection:
            run_id, manifest_id, job_id = _seed_sealed_manifest(connection)
            _lease(connection, job_id=job_id, attempt=1, worker_id=worker_id)
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status = 'running', started_at = clock_timestamp()
                    WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            )

            wealth_method50 = Decimal("6")
            peak_method50 = Decimal("7")
            drawdown_method50 = method_decimal_divide(
                method_decimal_subtract(wealth_method50, peak_method50),
                peak_method50,
            )
            assert drawdown_method50 == Decimal(
                "-0.14285714285714285714285714285714285714285714285714"
            )
            return_chain_constraint = connection.scalar(
                text(
                    """
                    SELECT pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conrelid =
                            'portfolio.portfolio_daily_snapshot_output'::regclass
                      AND conname = 'ck_pd_snapshot_return_method_chain'
                    """
                )
            )
            assert return_chain_constraint is not None
            assert (
                "calculation_registry.divide_significant_half_even"
                in return_chain_constraint
            )
            row = {
                "run_id": UUID(run_id),
                "output_fencing_token": 1,
                "worker_id": worker_id,
                "portfolio_id": "attempt-isolation",
                "as_of_date": date(2026, 7, 14),
                "base_currency": "USD",
                "measured_nav": True,
                "measured_position_market_value": True,
                "measured_book_pnl": False,
                "measured_return": False,
                "measured_external_flows": True,
                "unavailable_component_count": 2,
                "opening_nav": Decimal("1"),
                "closing_nav": Decimal("1"),
                "position_market_value": Decimal("1"),
                "settled_cash": Decimal("0"),
                "pending_receivable": Decimal("0"),
                "pending_payable": Decimal("0"),
                "accrual_receivable": Decimal("0"),
                "accrual_payable": Decimal("0"),
                "external_flow_in": Decimal("0"),
                "external_flow_out": Decimal("0"),
                "position_attribution_rounding_adjustment": Decimal("0"),
                "pnl_component_rounding_adjustment": Decimal("0"),
                "cumulative_twr_method50": method_decimal_subtract(
                    wealth_method50,
                    Decimal("1"),
                ),
                "cumulative_twr_published": Decimal("5"),
                "wealth_index_method50": wealth_method50,
                "wealth_index_published": wealth_method50,
                "peak_wealth_index_method50": peak_method50,
                "peak_wealth_index_published": peak_method50,
                "drawdown_method50": drawdown_method50,
                "drawdown_published": quantize_decimal(
                    drawdown_method50,
                    scale=18,
                ),
                "reliable_anchor_date": date(2026, 7, 14),
                "reliable_anchor_nav_exact": Decimal("1"),
                "reliable_anchor_nav": Decimal("1"),
                "calculation_status": "no_new_valuation",
                "return_chain_status": "no_new_valuation",
                "nav_rounding_adjustment": Decimal("0"),
                "pnl_rounding_adjustment": Decimal("0"),
                "nav_coverage_state": "complete",
                "nav_reason_codes": [],
                "book_pnl_coverage_state": "unavailable",
                "book_pnl_reason_codes": ["book_pnl_unavailable"],
                "return_coverage_state": "unavailable",
                "return_reason_codes": ["carry_forward_valuation"],
                "flow_coverage_state": "complete",
                "flow_reason_codes": [],
                "position_attribution_coverage_state": "unavailable",
                "position_attribution_reason_codes": ["attribution_unavailable"],
                "valuation_endpoint_status": "carry_forward",
                "valuation_reason_codes": ["carry_forward_valuation"],
            }

            postgres_default_division = dict(row)
            postgres_default_division["drawdown_method50"] = Decimal(
                "-0.14285714285714285714"
            )
            with pytest.raises(
                DBAPIError,
                match="ck_pd_snapshot_return_method_chain",
            ):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        postgres_default_division,
                    )

            connection.execute(portfolio_daily_snapshot_output.insert(), row)
            connection.execute(
                text(
                    """
                    INSERT INTO portfolio.portfolio_daily_run_output (
                        run_id, output_fencing_token, worker_id, portfolio_id,
                        range_start, range_end, methodology_version,
                        input_schema_version, output_schema_version, closure_status,
                        canonical_output_hash, snapshot_count, measured_nav_count,
                        holding_count, balance_count, lot_count,
                        lot_disposition_count, contribution_count,
                        unavailable_component_count, ledger_balance_residual_exact,
                        nav_bridge_residual_exact, pnl_residual_exact,
                        twr_residual_exact, lot_residual_exact,
                        rounding_adjustment_base, coverage_state, reason_codes
                    ) VALUES (
                        :run_id, 1, :worker_id, 'attempt-isolation',
                        DATE '2026-07-14', DATE '2026-07-14',
                        'portfolio-daily.exact.v1', 'portfolio-daily-input.v1',
                        'portfolio-daily-output.v1', 'passed', :output_hash,
                        1, 1, 0, 0, 0, 0, 0, 2, 0, 0, 0, 0, 0, 0,
                        'partial',
                        ARRAY['attribution_unavailable', 'book_pnl_unavailable']::varchar(64)[]
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "output_hash": output_hash,
                },
            )
            _execute_batch(
                connection,
                """
                    UPDATE calculation_registry.calculation_job
                    SET status = 'succeeded', lease_owner = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL,
                        failure_code = NULL, failure_diagnostic = NULL,
                        completed_at = clock_timestamp()
                    WHERE job_id = :job_id;
                    UPDATE calculation_registry.calculation_run
                    SET status = 'succeeded', completed_at = clock_timestamp()
                    WHERE run_id = :run_id;
                    """,
                {"job_id": job_id, "run_id": run_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO calculation_registry.calculation_publication (
                        publication_id, run_id, manifest_id, calculation_kind,
                        scope_kind, scope_id, output_schema_version,
                        published_fencing_token, canonical_output_hash
                    ) VALUES (
                        :publication_id, :run_id, :manifest_id, 'portfolio_daily',
                        'portfolio', 'attempt-isolation', 'portfolio-daily-output.v1',
                        1, :output_hash
                    )
                    """
                ),
                {
                    "publication_id": str(uuid4()),
                    "run_id": run_id,
                    "manifest_id": manifest_id,
                    "output_hash": output_hash,
                },
            )

            stored = connection.execute(
                text(
                    """
                    SELECT snapshot.wealth_index_method50,
                           snapshot.peak_wealth_index_method50,
                           snapshot.drawdown_method50,
                           snapshot.drawdown_published,
                           manifest.status
                    FROM portfolio.portfolio_daily_snapshot_output AS snapshot
                    JOIN calculation_registry.calculation_input_manifest AS manifest
                      ON manifest.run_id = snapshot.run_id
                    WHERE snapshot.run_id = :run_id
                      AND snapshot.output_fencing_token = 1
                    """
                ),
                {"run_id": run_id},
            ).one()
            assert stored == (
                wealth_method50,
                peak_method50,
                drawdown_method50,
                quantize_decimal(drawdown_method50, scale=18),
                "sealed",
            )


def test_bounded_method50_twr_chain_round_trips_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        worker_id = "portfolio-daily-bounded-twr-worker"
        with engine.begin() as connection:
            run_id, _, job_id = _seed_sealed_manifest(connection)
            _lease(connection, job_id=job_id, attempt=1, worker_id=worker_id)
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status = 'running', started_at = clock_timestamp()
                    WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            )
            wealth_method50 = Decimal(
                "1.0333333333333333333333333333333333333333333333333"
            )
            cumulative_method50 = method_decimal_subtract(
                wealth_method50,
                Decimal("1"),
            )
            row = {
                "run_id": UUID(run_id),
                "output_fencing_token": 1,
                "worker_id": worker_id,
                "portfolio_id": "attempt-isolation",
                "as_of_date": date(2026, 7, 14),
                "base_currency": "USD",
                "measured_nav": True,
                "measured_position_market_value": True,
                "measured_book_pnl": False,
                "measured_return": False,
                "measured_external_flows": True,
                "unavailable_component_count": 2,
                "opening_nav": Decimal("1"),
                "closing_nav": Decimal("1"),
                "position_market_value": Decimal("1"),
                "settled_cash": Decimal("0"),
                "pending_receivable": Decimal("0"),
                "pending_payable": Decimal("0"),
                "accrual_receivable": Decimal("0"),
                "accrual_payable": Decimal("0"),
                "external_flow_in": Decimal("0"),
                "external_flow_out": Decimal("0"),
                "position_attribution_rounding_adjustment": Decimal("0"),
                "pnl_component_rounding_adjustment": Decimal("0"),
                "cumulative_twr_method50": cumulative_method50,
                "cumulative_twr_published": Decimal("0.033333333333333333"),
                "wealth_index_method50": wealth_method50,
                "wealth_index_published": Decimal("1.033333333333333333"),
                "peak_wealth_index_method50": wealth_method50,
                "peak_wealth_index_published": Decimal("1.033333333333333333"),
                "drawdown_method50": Decimal("0"),
                "drawdown_published": Decimal("0"),
                "reliable_anchor_date": date(2026, 7, 14),
                "reliable_anchor_nav_exact": Decimal("1"),
                "reliable_anchor_nav": Decimal("1"),
                "calculation_status": "no_new_valuation",
                "return_chain_status": "no_new_valuation",
                "nav_rounding_adjustment": Decimal("0"),
                "pnl_rounding_adjustment": Decimal("0"),
                "nav_coverage_state": "complete",
                "nav_reason_codes": [],
                "book_pnl_coverage_state": "unavailable",
                "book_pnl_reason_codes": ["book_pnl_unavailable"],
                "return_coverage_state": "unavailable",
                "return_reason_codes": ["carry_forward_valuation"],
                "flow_coverage_state": "complete",
                "flow_reason_codes": [],
                "position_attribution_coverage_state": "unavailable",
                "position_attribution_reason_codes": ["attribution_unavailable"],
                "valuation_endpoint_status": "carry_forward",
                "valuation_reason_codes": ["carry_forward_valuation"],
            }
            connection.execute(portfolio_daily_snapshot_output.insert(), row)

            stored = connection.execute(
                text(
                    """
                    SELECT cumulative_twr_method50, wealth_index_method50,
                           peak_wealth_index_method50
                    FROM portfolio.portfolio_daily_snapshot_output
                    WHERE run_id = :run_id AND output_fencing_token = 1
                    """
                ),
                {"run_id": run_id},
            ).one()
            assert stored == (
                cumulative_method50,
                wealth_method50,
                wealth_method50,
            )

            method_columns = {
                column["name"]: column["type"]
                for column in inspect(engine).get_columns(
                    "portfolio_daily_snapshot_output",
                    schema="portfolio",
                )
                if column["name"].endswith("_method50")
                or column["name"] == "wealth_chain_rounding_adjustment_exact"
            }
            assert method_columns
            assert all(
                isinstance(column_type, Numeric)
                and column_type.precision is None
                and column_type.scale is None
                for column_type in method_columns.values()
            )

            cancellation = dict(row)
            cancellation.update(
                {
                    "as_of_date": date(2026, 7, 15),
                    "cumulative_twr_method50": Decimal("-1"),
                    "cumulative_twr_published": Decimal("-1"),
                    "wealth_index_method50": Decimal("1E-51"),
                    "wealth_index_published": Decimal("0"),
                    "peak_wealth_index_method50": Decimal("1"),
                    "peak_wealth_index_published": Decimal("1"),
                    "drawdown_method50": Decimal("-1"),
                    "drawdown_published": Decimal("-1"),
                }
            )
            connection.execute(portfolio_daily_snapshot_output.insert(), cancellation)

            scale_100_boundary = dict(row)
            scale_100_boundary.update(
                {
                    "as_of_date": date(2026, 7, 26),
                    "cumulative_twr_method50": Decimal("-1"),
                    "cumulative_twr_published": Decimal("-1"),
                    "wealth_index_method50": Decimal("1E-100"),
                    "wealth_index_published": Decimal("0"),
                    "peak_wealth_index_method50": Decimal("1"),
                    "peak_wealth_index_published": Decimal("1"),
                    "drawdown_method50": Decimal("-1"),
                    "drawdown_published": Decimal("-1"),
                }
            )
            connection.execute(
                portfolio_daily_snapshot_output.insert(),
                scale_100_boundary,
            )

            accounting_scale_boundary = dict(row)
            accounting_scale_boundary.update(
                {
                    "as_of_date": date(2026, 7, 25),
                    "reliable_anchor_nav_exact": Decimal("1E-200"),
                    "reliable_anchor_nav": Decimal("0"),
                }
            )
            connection.execute(
                portfolio_daily_snapshot_output.insert(),
                accounting_scale_boundary,
            )
            assert connection.scalar(
                text(
                    """
                    SELECT reliable_anchor_nav_exact
                    FROM portfolio.portfolio_daily_snapshot_output
                    WHERE run_id = :run_id AND output_fencing_token = 1
                      AND as_of_date = DATE '2026-07-25'
                    """
                ),
                {"run_id": run_id},
            ) == Decimal("1E-200")

            excess_accounting_scale = dict(row)
            excess_accounting_scale.update(
                {
                    "as_of_date": date(2026, 7, 30),
                    "reliable_anchor_nav_exact": Decimal("1E-201"),
                    "reliable_anchor_nav": Decimal("0"),
                }
            )
            with pytest.raises(
                DBAPIError,
                match="ck_portfolio_daily_snapshot_output_acct_ev_domain",
            ):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        excess_accounting_scale,
                    )

            unit_chain = dict(row)
            unit_chain.update(
                {
                    "cumulative_twr_method50": Decimal("0"),
                    "cumulative_twr_published": Decimal("0"),
                    "wealth_index_method50": Decimal("1"),
                    "wealth_index_published": Decimal("1"),
                    "peak_wealth_index_method50": Decimal("1"),
                    "peak_wealth_index_published": Decimal("1"),
                    "drawdown_method50": Decimal("0"),
                    "drawdown_published": Decimal("0"),
                }
            )
            calculated_unit_chain = dict(unit_chain)
            calculated_unit_chain.update(
                {
                    "as_of_date": date(2026, 7, 28),
                    "measured_return": True,
                    "subperiod_twr_method50": Decimal("0"),
                    "subperiod_twr_published": Decimal("0"),
                    "wealth_chain_rounding_adjustment_exact": Decimal("1E-200"),
                    "return_period_start_date": date(2026, 7, 27),
                    "return_period_end_date": date(2026, 7, 28),
                    "return_period_day_count": 1,
                    "calculation_status": "calculated",
                    "return_chain_status": "active",
                    "return_coverage_state": "complete",
                    "return_reason_codes": [],
                    "valuation_endpoint_status": "fresh",
                    "valuation_reason_codes": [],
                }
            )

            excess_method_scale = dict(calculated_unit_chain)
            excess_method_scale.update(
                {
                    "as_of_date": date(2026, 7, 27),
                    "subperiod_twr_method50": Decimal("1E-101"),
                    "wealth_chain_rounding_adjustment_exact": Decimal("0"),
                    "return_period_start_date": date(2026, 7, 26),
                    "return_period_end_date": date(2026, 7, 27),
                }
            )
            with pytest.raises(
                DBAPIError,
                match="ck_pd_snapshot_return_method_storage_domain",
            ):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        excess_method_scale,
                    )

            connection.execute(
                portfolio_daily_snapshot_output.insert(),
                calculated_unit_chain,
            )

            excess_adjustment_scale = dict(calculated_unit_chain)
            excess_adjustment_scale.update(
                {
                    "as_of_date": date(2026, 7, 29),
                    "wealth_chain_rounding_adjustment_exact": Decimal("1E-201"),
                    "return_period_start_date": date(2026, 7, 28),
                    "return_period_end_date": date(2026, 7, 29),
                }
            )
            with pytest.raises(
                DBAPIError,
                match="ck_pd_snapshot_return_adjustment_storage_domain",
            ):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        excess_adjustment_scale,
                    )

            pseudo_method = dict(row)
            pseudo_method_value = Decimal(
                "1.12345678901234567890123456789012345678901234567891"
            )
            pseudo_method.update(
                {
                    "as_of_date": date(2026, 7, 16),
                    "cumulative_twr_method50": method_decimal_subtract(
                        pseudo_method_value,
                        Decimal("1"),
                    ),
                    "cumulative_twr_published": Decimal("0.123456789012345679"),
                    "wealth_index_method50": pseudo_method_value,
                    "wealth_index_published": Decimal("1.123456789012345679"),
                    "peak_wealth_index_method50": pseudo_method_value,
                    "peak_wealth_index_published": Decimal("1.123456789012345679"),
                }
            )
            with pytest.raises(
                DBAPIError, match="ck_pd_snapshot_return_method_precision"
            ):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        pseudo_method,
                    )

            invalid = dict(row)
            invalid["as_of_date"] = date(2026, 7, 17)
            invalid["cumulative_twr_published"] = Decimal("0.04")
            with pytest.raises(DBAPIError, match="ck_pd_snapshot_cumulative_published"):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        invalid,
                    )

            overflow = dict(row)
            overflow["as_of_date"] = date(2026, 7, 18)
            overflow["wealth_index_method50"] = Decimal("1E+32")
            with pytest.raises(
                DBAPIError,
                match=("ck_pd_snapshot_return_(method_storage_domain|method_chain)"),
            ):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        overflow,
                    )

            impossible_return = dict(row)
            impossible_return.update(
                {
                    "as_of_date": date(2026, 7, 19),
                    "measured_return": True,
                    "subperiod_twr_method50": Decimal("-1.1"),
                    "subperiod_twr_published": Decimal("-1.1"),
                    "wealth_chain_rounding_adjustment_exact": Decimal("0"),
                    "return_period_start_date": date(2026, 7, 18),
                    "return_period_end_date": date(2026, 7, 19),
                    "return_period_day_count": 1,
                    "calculation_status": "calculated",
                    "return_chain_status": "active",
                    "return_coverage_state": "complete",
                    "return_reason_codes": [],
                    "valuation_endpoint_status": "fresh",
                    "valuation_reason_codes": [],
                }
            )
            with pytest.raises(
                DBAPIError,
                match="ck_pd_snapshot_subperiod_method50",
            ):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        impossible_return,
                    )

            invalid_drawdown = dict(row)
            invalid_drawdown.update(
                {
                    "as_of_date": date(2026, 7, 20),
                    "cumulative_twr_method50": Decimal("0"),
                    "cumulative_twr_published": Decimal("0"),
                    "wealth_index_method50": Decimal("1"),
                    "wealth_index_published": Decimal("1"),
                    "peak_wealth_index_method50": Decimal("2"),
                    "peak_wealth_index_published": Decimal("2"),
                    "drawdown_method50": Decimal("-0.4"),
                    "drawdown_published": Decimal("-0.4"),
                }
            )
            with pytest.raises(
                DBAPIError,
                match="ck_pd_snapshot_return_method_chain",
            ):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        invalid_drawdown,
                    )

            null_reanchor = dict(row)
            null_reanchor.update(
                {
                    "as_of_date": date(2026, 7, 21),
                    "cumulative_twr_method50": None,
                    "cumulative_twr_published": None,
                    "wealth_index_method50": None,
                    "wealth_index_published": None,
                    "peak_wealth_index_method50": None,
                    "peak_wealth_index_published": None,
                    "drawdown_method50": None,
                    "drawdown_published": None,
                    "reliable_anchor_date": None,
                    "reliable_anchor_nav_exact": None,
                    "reliable_anchor_nav": None,
                    "calculation_status": "reanchored",
                    "return_chain_status": "reanchor",
                }
            )
            with pytest.raises(
                DBAPIError,
                match="ck_pd_snapshot_reanchor_values",
            ):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        null_reanchor,
                    )

            zero_active_no_new = dict(row)
            zero_active_no_new.update(
                {
                    "as_of_date": date(2026, 7, 22),
                    "cumulative_twr_method50": Decimal("-1"),
                    "cumulative_twr_published": Decimal("-1"),
                    "wealth_index_method50": Decimal("0"),
                    "wealth_index_published": Decimal("0"),
                    "peak_wealth_index_method50": Decimal("1"),
                    "peak_wealth_index_published": Decimal("1"),
                    "drawdown_method50": Decimal("-1"),
                    "drawdown_published": Decimal("-1"),
                }
            )
            with pytest.raises(
                DBAPIError,
                match="ck_pd_snapshot_no_new_values",
            ):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        zero_active_no_new,
                    )

            no_new_with_period = dict(row)
            no_new_with_period.update(
                {
                    "as_of_date": date(2026, 7, 23),
                    "return_period_start_date": date(2026, 7, 22),
                    "return_period_end_date": date(2026, 7, 23),
                    "return_period_day_count": 1,
                }
            )
            with pytest.raises(
                DBAPIError,
                match="ck_pd_snapshot_(no_new_values|return_period)",
            ):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        no_new_with_period,
                    )

            obsolete_awaiting_status = dict(row)
            obsolete_awaiting_status.update(
                {
                    "as_of_date": date(2026, 7, 24),
                    "calculation_status": "awaiting_anchor",
                }
            )
            with pytest.raises(
                DBAPIError,
                match="ck_pd_snapshot_calc_status",
            ):
                with connection.begin_nested():
                    connection.execute(
                        portfolio_daily_snapshot_output.insert(),
                        obsolete_awaiting_status,
                    )


def test_group_contribution_method50_and_published_bridges_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _postgres_database(monkeypatch) as (engine, _):
        worker_id = "portfolio-daily-group-closure-worker"
        with engine.begin() as connection:
            run_id, _, job_id = _seed_sealed_manifest(connection)
            _lease(connection, job_id=job_id, attempt=1, worker_id=worker_id)
            connection.execute(
                text(
                    """
                    UPDATE calculation_registry.calculation_run
                    SET status = 'running', started_at = clock_timestamp()
                    WHERE run_id = :run_id
                    """
                ),
                {"run_id": run_id},
            )
            statement = text(
                """
                INSERT INTO portfolio.portfolio_daily_contribution_output (
                    run_id, output_fencing_token, worker_id, portfolio_id,
                    as_of_date, axis, group_key, group_label, measured,
                    opening_nav_exact, opening_nav,
                    opening_nav_rounding_adjustment,
                    closing_nav_exact, closing_nav,
                    closing_nav_rounding_adjustment,
                    external_flow_in_exact, external_flow_in,
                    external_flow_in_rounding_adjustment,
                    external_flow_out_exact, external_flow_out,
                    external_flow_out_rounding_adjustment,
                    internal_flow_in_exact, internal_flow_in,
                    internal_flow_in_rounding_adjustment,
                    internal_flow_out_exact, internal_flow_out,
                    internal_flow_out_rounding_adjustment,
                    economic_pnl_exact, economic_pnl,
                    economic_pnl_rounding_adjustment,
                    contribution_method50, contribution_published,
                    contribution_division_adjustment_exact,
                    contribution_rounding_adjustment,
                    closure_residual_exact, rounding_adjustment_base,
                    coverage_state, reason_codes
                ) VALUES (
                    :run_id, 1, :worker_id, 'attempt-isolation',
                    DATE '2026-07-14', 'instrument', :group_key, 'Exact group', true,
                    2, 2, 0,
                    :closing_exact, :closing_published, 0,
                    0, 0, 0,
                    0, 0, 0,
                    0, 0, 0,
                    0, 0, 0,
                    1, 1, 0,
                    :contribution_method50, :contribution_published, 0, 0,
                    0, 0, 'complete', ARRAY[]::varchar(64)[]
                )
                """
            )
            connection.execute(
                statement,
                {
                    "run_id": run_id,
                    "worker_id": worker_id,
                    "group_key": "valid-group",
                    "closing_exact": "3",
                    "closing_published": "3",
                    "contribution_method50": Decimal("0.5"),
                    "contribution_published": Decimal("0.5"),
                },
            )
            with pytest.raises(DBAPIError, match="ck_pd_contrib_bridge"):
                with connection.begin_nested():
                    connection.execute(
                        statement,
                        {
                            "run_id": run_id,
                            "worker_id": worker_id,
                            "group_key": "bad-exact-group",
                            "closing_exact": "3.1",
                            "closing_published": "3.1",
                            "contribution_method50": Decimal("0.5"),
                            "contribution_published": Decimal("0.5"),
                        },
                    )
            with pytest.raises(DBAPIError, match="ck_pd_contrib_bridge"):
                with connection.begin_nested():
                    connection.execute(
                        statement,
                        {
                            "run_id": run_id,
                            "worker_id": worker_id,
                            "group_key": "bad-published-group",
                            "closing_exact": "3",
                            "closing_published": "3.00000001",
                            "contribution_method50": Decimal("0.5"),
                            "contribution_published": Decimal("0.5"),
                        },
                    )
            with pytest.raises(
                DBAPIError,
                match="ck_pd_contrib_method_storage_domain",
            ):
                with connection.begin_nested():
                    connection.execute(
                        statement,
                        {
                            "run_id": run_id,
                            "worker_id": worker_id,
                            "group_key": "excess-method-scale-group",
                            "closing_exact": "3",
                            "closing_published": "3",
                            "contribution_method50": Decimal("0.5" + ("0" * 99) + "1"),
                            "contribution_published": Decimal("0.5"),
                        },
                    )
