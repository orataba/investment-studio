from __future__ import annotations

import ast
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text

from studio_data.core.settings import get_settings


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parent
MIGRATIONS_ROOT = WORKSPACE_ROOT / "shared-data" / "instruments"
VERSIONS_DIR = MIGRATIONS_ROOT / "alembic" / "versions"
PLATFORM_MIGRATIONS_ROOT = BACKEND_ROOT
PLATFORM_VERSIONS_DIR = PLATFORM_MIGRATIONS_ROOT / "alembic" / "versions"
RUNTIME_PACKAGE_NAMES = ("app", "studio_data", "investment_studio_instrument_core")


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_instrument_registry_revisions_do_not_import_runtime_modules() -> None:
    for migration_path in VERSIONS_DIR.glob("*.py"):
        imported_modules = _imported_modules(migration_path.read_text(encoding="utf-8"))
        offenders = sorted(
            module
            for module in imported_modules
            if any(
                module == package or module.startswith(f"{package}.")
                for package in RUNTIME_PACKAGE_NAMES
            )
        )
        assert not offenders, f"{migration_path.name} imports runtime packages: {offenders}"


def test_platform_revisions_do_not_import_runtime_modules() -> None:
    for migration_path in PLATFORM_VERSIONS_DIR.glob("*.py"):
        imported_modules = _imported_modules(migration_path.read_text(encoding="utf-8"))
        offenders = sorted(
            module
            for module in imported_modules
            if any(
                module == package or module.startswith(f"{package}.")
                for package in RUNTIME_PACKAGE_NAMES
            )
        )
        assert not offenders, f"{migration_path.name} imports runtime packages: {offenders}"


def test_instrument_registry_migrations_upgrade_an_empty_database(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'instrument-registry-migrations.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "")

    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "head")


def test_crypto_type_migration_preserves_triggers_and_guards_existing_assets(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from investment_studio_instrument_core import instrument_store

    database_url = f"sqlite+pysqlite:///{tmp_path / 'crypto-type.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "")
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "20260907_0034")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        before = dict(connection.execute(text("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")).all())
    command.upgrade(config, "head")
    factory = sessionmaker(bind=engine)
    instrument = instrument_store.create_instrument(factory, instrument_name="Bitcoin", instrument_type="crypto", currency="USD",
        identifiers=[{"identifier_type": "ticker", "identifier_value": "BTCUSD", "is_primary": True}])
    assert instrument["instrument_type"] == "crypto"
    assert instrument["exchange_code"] is None
    assert instrument["source_settings"]["market_calendar"] == "24/7"
    assert instrument["source_settings"]["return_semantics"] == "price_return"
    assert instrument["quote_selection_policy"]["chart"] == ["close", "last"]
    with pytest.raises(RuntimeError, match="crypto facts exist"):
        command.downgrade(config, "20260907_0034")
    assert instrument_store.get_instrument(factory, instrument["instrument_id"])["instrument_type"] == "crypto"
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM instrument_identifier WHERE instrument_id = :iid"), {"iid": instrument["instrument_id"]})
        connection.execute(text("DELETE FROM instrument WHERE instrument_id = :iid"), {"iid": instrument["instrument_id"]})
    command.downgrade(config, "20260907_0034")
    with engine.connect() as connection:
        after = dict(connection.execute(text("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")).all())
        assert after == before


def test_reference_observation_migration_preserves_real_collection_clock_and_is_reversible(tmp_path, monkeypatch):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'reference-observations.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL", database_url)
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "20260904_0032")
    engine = create_engine(database_url)
    valid = {"fetched_at": "2026-09-07T13:00:00+08:00", "source": {"provider_updated_at": "2026-06-30"},
             "sections": {"holdings": [{"symbol": "AAA", "weightPercentage": 10}]}}
    snapshots = {"dated": valid, "naive": {**valid, "fetched_at": "2026-09-07T13:00:00"},
                 "invalid": {**valid, "fetched_at": "unavailable"}, "empty": {**valid, "sections": {}}}
    with engine.begin() as connection:
        metadata = sa.MetaData()
        instruments = sa.Table("instrument", metadata, autoload_with=connection)
        current = sa.Table("instrument_reference_snapshot", metadata, autoload_with=connection)
        for iid, record in snapshots.items():
            connection.execute(instruments.insert().values(instrument_id=iid, instrument_name=iid, instrument_type="other", currency="USD",
                quote_selection_policy_json={}, source_settings_json={}, refresh_status_json={}, lifecycle_state_json={}))
            connection.execute(current.insert().values(instrument_id=iid, value_json=record))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        observations = sa.Table("instrument_reference_observation", sa.MetaData(), autoload_with=connection)
        rows = connection.execute(sa.select(observations)).mappings().all()
        assert len(rows) == 1 and rows[0]["instrument_id"] == "dated"
        assert rows[0]["value_json"] == valid
        assert rows[0]["collected_at"].isoformat() == "2026-09-07T05:00:00"
        assert inspect(connection).get_indexes("instrument_reference_observation")[0]["column_names"] == ["instrument_id", "collected_at", "observation_id"]
    command.downgrade(config, "20260904_0032")
    with engine.connect() as connection:
        assert "instrument_reference_observation" not in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT count(*) FROM instrument_reference_snapshot")) == 4


def test_registry_metadata_has_no_derivative_identity() -> None:
    from investment_studio_instrument_core.db_models import Instrument

    assert "ix_instrument_option_underlying" not in {
        index.name for index in Instrument.__table__.indexes
    }
    assert not {
        "option_underlying_instrument_id",
        "option_type",
        "option_expiry_date",
        "option_strike",
        "option_contract_multiplier",
        "option_settlement_type",
        "option_contract_currency",
        "fcn_contract_json",
        "derivative_adjustment_policy_json",
    }.intersection(Instrument.__table__.columns.keys())


def test_bond_registry_removal_is_guarded_and_enforced(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'bond-registry-removal.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "")
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "20260809_0022")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json,
                    market_data_updated_at, calculation_inputs_updated_at
                ) VALUES (
                    'legacy-bond', 'Legacy Bond', 'bond', 'USD',
                    '{}', '{}', '{}', '{}', NULL, NULL
                )
                """
            )
        )
    with pytest.raises(RuntimeError, match="zero bond instruments"):
        command.upgrade(config, "20260810_0023")

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM instrument WHERE instrument_id = 'legacy-bond'"))
    command.upgrade(config, "head")

    with engine.begin() as connection, pytest.raises(sa.exc.IntegrityError):
        connection.execute(
            text(
                """
                INSERT INTO instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json,
                    market_data_updated_at, calculation_inputs_updated_at
                ) VALUES (
                    'bond-after-removal', 'Bond After Removal', 'bond', 'USD',
                    '{}', '{}', '{}', '{}', NULL, NULL
                )
                """
            )
        )


def test_event_valued_instrument_type_migration_is_guarded_and_reversible(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'event-valued-types.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "")
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "20260731_0016")

    engine = create_engine(database_url)
    command.upgrade(config, "20260804_0017")
    with engine.begin() as connection:
        for instrument_id, instrument_type in (
            ("fcn-migration-test", "fcn"),
            ("option-migration-test", "option"),
        ):
            connection.execute(
                text(
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
                ),
                {
                    "instrument_id": instrument_id,
                    "instrument_type": instrument_type,
                },
            )

    with pytest.raises(RuntimeError, match="FCN or option instruments exist"):
        command.downgrade(config, "20260731_0016")

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM instrument WHERE instrument_type IN ('fcn', 'option')")
        )
    command.downgrade(config, "20260731_0016")
    try:
        with engine.begin() as connection, pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                text(
                    """
                    INSERT INTO instrument (
                        instrument_id, instrument_name, instrument_type, currency,
                        quote_selection_policy_json, source_settings_json,
                        refresh_status_json, lifecycle_state_json,
                        market_data_updated_at
                    ) VALUES (
                        'option-after-downgrade', 'option-after-downgrade',
                        'option', 'USD', '{}', '{}', '{}', '{}', NULL
                    )
                    """
                )
            )
    finally:
        command.upgrade(config, "head")


def test_option_contract_identity_migration_requires_backfill_and_complete_identity(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'option-contract-identity.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "")
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "20260804_0017")

    engine = create_engine(database_url)
    base_insert = text(
        """
        INSERT INTO instrument (
            instrument_id, instrument_name, instrument_type, currency,
            quote_selection_policy_json, source_settings_json,
            refresh_status_json, lifecycle_state_json,
            market_data_updated_at
        ) VALUES (
            :instrument_id, :instrument_id, :instrument_type, :currency,
            '{}', '{}', '{}', '{}', NULL
        )
        """
    )
    with engine.begin() as connection:
        connection.execute(
            base_insert,
            {
                "instrument_id": "option-underlying",
                "instrument_type": "equity",
                "currency": "USD",
            },
        )
        connection.execute(
            base_insert,
            {
                "instrument_id": "legacy-option",
                "instrument_type": "option",
                "currency": "USD",
            },
        )

    with pytest.raises(RuntimeError, match="identity-less option instruments exist"):
        command.upgrade(config, "20260806_0018")

    preflight_column_names = {
        column["name"] for column in inspect(engine).get_columns("instrument")
    }
    identity_columns = {
        "option_underlying_instrument_id",
        "option_type",
        "option_expiry_date",
        "option_strike",
        "option_contract_multiplier",
        "option_settlement_type",
        "option_contract_currency",
    }
    assert identity_columns.isdisjoint(preflight_column_names)

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM instrument WHERE instrument_id = 'legacy-option'")
        )

    command.upgrade(config, "20260806_0018")
    column_names = {
        column["name"] for column in inspect(engine).get_columns("instrument")
    }
    assert identity_columns.issubset(column_names)

    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            base_insert,
            {
                "instrument_id": "identity-less-option",
                "instrument_type": "option",
                "currency": "USD",
            },
        )

    identity_insert = text(
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
        connection.execute(identity_insert, complete_identity)

    invalid_identities = [
        {
            **complete_identity,
            "instrument_id": "partial-option",
            "contract_currency": None,
        },
        {
            **complete_identity,
            "instrument_id": "non-option-identity",
            "instrument_type": "equity",
        },
        {
            **complete_identity,
            "instrument_id": "self-option",
            "underlying_id": "self-option",
        },
    ]
    for invalid_identity in invalid_identities:
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
            connection.execute(identity_insert, invalid_identity)

    with pytest.raises(RuntimeError, match="option contract identity exists"):
        command.downgrade(config, "20260804_0017")

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM instrument WHERE instrument_id = 'complete-option'")
        )
    command.downgrade(config, "20260804_0017")
    downgraded_columns = {
        column["name"] for column in inspect(engine).get_columns("instrument")
    }
    assert identity_columns.isdisjoint(downgraded_columns)


def test_derivative_contract_reconciliation_migration_is_strict_and_guarded(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'derivative-reconciliation.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "")
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "20260806_0018")

    engine = create_engine(database_url)
    base_insert = text(
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
        connection.execute(
            base_insert,
            {"instrument_id": "contract-underlying", "instrument_type": "equity"},
        )
        connection.execute(
            base_insert,
            {"instrument_id": "ungoverned-fcn", "instrument_type": "fcn"},
        )

    with pytest.raises(RuntimeError, match="derivative instruments lack"):
        command.upgrade(config, "20260807_0019")
    assert {
        column["name"] for column in inspect(engine).get_columns("instrument")
    }.isdisjoint({"fcn_contract_json", "derivative_adjustment_policy_json"})
    assert "instrument_broker_identifier" not in inspect(engine).get_table_names()

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM instrument WHERE instrument_id = 'ungoverned-fcn'")
        )
    command.upgrade(config, "20260807_0019")

    inspector = inspect(engine)
    assert {
        "fcn_contract_json",
        "derivative_adjustment_policy_json",
    }.issubset({column["name"] for column in inspector.get_columns("instrument")})
    assert "instrument_broker_identifier" in inspector.get_table_names()
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("instrument")
    }.issuperset(
        {
            "ck_instrument_fcn_contract_metadata",
            "ck_instrument_derivative_adjustment_policy",
        }
    )
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints(
            "instrument_broker_identifier"
        )
    } == {"ck_instrument_broker_identifier_broker_identifier_type"}
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "instrument_broker_identifier"
        )
    } == {"uq_instrument_broker_identifier_identity"}

    derivative_insert = text(
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
        "underlying_id": "contract-underlying",
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
        connection.execute(derivative_insert, complete_option)
        connection.execute(derivative_insert, complete_fcn)

    invalid_derivatives = [
        {**complete_option, "instrument_id": "option-without-policy", "adjustment_policy": None},
        {
            **complete_option,
            "instrument_id": "option-with-json-null-policy",
            "adjustment_policy": "null",
        },
        {**complete_fcn, "instrument_id": "fcn-without-contract", "fcn_contract": None},
        {**complete_fcn, "instrument_id": "fcn-without-policy", "adjustment_policy": None},
        {
            **complete_fcn,
            "instrument_id": "equity-with-derivative-metadata",
            "instrument_type": "equity",
        },
    ]
    for invalid_derivative in invalid_derivatives:
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
            connection.execute(derivative_insert, invalid_derivative)

    broker_insert = text(
        """
        INSERT INTO instrument_broker_identifier (
            instrument_id, broker, identifier_type, identifier_value, is_primary
        ) VALUES (
            :instrument_id, :broker, :identifier_type, :identifier_value, 1
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
        connection.execute(broker_insert, broker_identity)
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            broker_insert,
            {**broker_identity, "instrument_id": "governed-fcn"},
        )
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as connection:
        connection.execute(
            broker_insert,
            {
                **broker_identity,
                "identifier_type": "unsupported",
                "identifier_value": "other",
            },
        )

    with pytest.raises(RuntimeError, match="broker reconciliation identity"):
        command.downgrade(config, "20260806_0018")

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM instrument WHERE instrument_type IN ('fcn', 'option')")
        )
        connection.execute(
            broker_insert,
            {
                "instrument_id": "contract-underlying",
                "broker": "custodian",
                "identifier_type": "symbol",
                "identifier_value": "UNDERLYING",
            },
        )
    with pytest.raises(RuntimeError, match="broker reconciliation identity"):
        command.downgrade(config, "20260806_0018")

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM instrument_broker_identifier"))
    command.downgrade(config, "20260806_0018")
    downgraded_inspector = inspect(engine)
    assert "instrument_broker_identifier" not in downgraded_inspector.get_table_names()
    assert {
        column["name"] for column in downgraded_inspector.get_columns("instrument")
    }.isdisjoint({"fcn_contract_json", "derivative_adjustment_policy_json"})


def test_platform_migrations_upgrade_an_empty_database(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'platform-migrations.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_DATABASE_URL", database_url)
    monkeypatch.delenv("INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL", raising=False)
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA", "")
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA", "")
    get_settings.cache_clear()
    try:
        config = Config(str(PLATFORM_MIGRATIONS_ROOT / "alembic.ini"))
        config.set_main_option(
            "script_location",
            str(PLATFORM_MIGRATIONS_ROOT / "alembic"),
        )
        command.upgrade(config, "head")
        command.check(config)
    finally:
        get_settings.cache_clear()

    tables = set(inspect(create_engine(database_url)).get_table_names())
    assert {
        "email_mailbox_ingestion_lease",
        "email_folder_cursor",
        "email_message_occurrence",
        "email_attachment_artifact",
        "email_message_attachment",
        "email_attachment_parse",
        "email_nav_candidate",
    }.issubset(tables)


def test_platform_mailbox_lease_migrates_from_email_foundation_head(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'platform-lease-upgrade.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_DATABASE_URL", database_url)
    monkeypatch.delenv("INVESTMENT_STUDIO_DATA_ALEMBIC_DATABASE_URL", raising=False)
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_DATABASE_SCHEMA", "")
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_OPERATIONS_DATABASE_SCHEMA", "")
    get_settings.cache_clear()
    try:
        config = Config(str(PLATFORM_MIGRATIONS_ROOT / "alembic.ini"))
        config.set_main_option(
            "script_location",
            str(PLATFORM_MIGRATIONS_ROOT / "alembic"),
        )
        command.upgrade(config, "20260715_0001")
        assert "email_mailbox_ingestion_lease" not in set(
            inspect(create_engine(database_url)).get_table_names()
        )
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()

    assert "email_mailbox_ingestion_lease" in set(
        inspect(create_engine(database_url)).get_table_names()
    )


def test_daily_api_source_metadata_migration_excludes_manual_instruments(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'daily-api-source-metadata.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "")
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "20260717_0015")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO registry_metadata (
                    registry_key, registry_name, market_data_updated_at
                ) VALUES ('shared', 'Shared Registry', NULL)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json, market_data_updated_at
                ) VALUES
                (
                    '510300-sh', '沪深300ETF', 'etf', 'CNY',
                    '{}',
                    '{"source_mode":"api","source_api_profile":"tushare","source_location":"Tushare"}',
                    '{}', '{"status":"active"}', NULL
                ),
                (
                    'manual-etf', '手工ETF', 'etf', 'CNY',
                    '{}',
                    '{"source_mode":"manual","source_location":"CSV import","market_calendar":"XSHG"}',
                    '{}', '{"status":"active"}', NULL
                ),
                (
                    'weekly-api-etf', '显式周频ETF', 'etf', 'CNY',
                    '{}',
                    '{"source_mode":"api","source_api_profile":"tushare","expected_frequency":"weekly","market_calendar":"XSHG"}',
                    '{}', '{"status":"active"}', NULL
                ),
                (
                    'h11001-csi', '中证全债指数', 'index', 'CNY',
                    '{}',
                    '{"source_mode":"api","source_api_profile":"tushare","expected_frequency":"daily"}',
                    '{}', '{"status":"active"}', NULL
                )
                """
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = {
            row["instrument_id"]: row
            for row in connection.execute(
                text(
                    """
                    SELECT instrument_id, source_settings_json,
                           market_data_updated_at, calculation_inputs_updated_at
                    FROM instrument
                    WHERE instrument_id IN (
                        '510300-sh', 'manual-etf', 'weekly-api-etf', 'h11001-csi'
                    )
                    """
                )
            ).mappings()
        }
    def source_settings(instrument_id: str) -> dict[str, object]:
        value = rows[instrument_id]["source_settings_json"]
        return json.loads(value) if isinstance(value, str) else dict(value)

    daily_source = source_settings("510300-sh")
    assert daily_source["expected_frequency"] == "daily"
    assert daily_source["market_calendar"] == "XSHG"
    assert daily_source["release_lag_days"] == 0
    assert rows["510300-sh"]["market_data_updated_at"]

    manual_source = source_settings("manual-etf")
    assert "expected_frequency" not in manual_source
    assert manual_source["market_calendar"] == "XSHG"
    assert rows["manual-etf"]["market_data_updated_at"] is None

    weekly_source = source_settings("weekly-api-etf")
    assert weekly_source["expected_frequency"] == "daily"
    assert weekly_source["market_calendar"] == "XSHG"
    assert rows["weekly-api-etf"]["market_data_updated_at"]
    assert rows["weekly-api-etf"]["calculation_inputs_updated_at"]

    h11001_source = source_settings("h11001-csi")
    assert not any(key.startswith("source_api_fallback_") for key in h11001_source)


def test_single_primary_source_migration_routes_only_mainland_etfs(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'single-primary-source.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "")
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "20260822_0026")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    exchange_code, quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json
                ) VALUES
                (
                    'mainland-etf', 'Mainland ETF', 'etf', 'CNY', 'XSHG', '{}',
                    '{"source_mode":"api","source_api_profile":"fmp","source_location":"FMP API"}',
                    '{}', '{"status":"active"}'
                ),
                (
                    'mainland-equity', 'Mainland Equity', 'equity', 'CNY', 'XSHG', '{}',
                    '{"source_mode":"api","source_api_profile":"fmp","source_location":"FMP API"}',
                    '{}', '{"status":"active"}'
                ),
                (
                    'a-share-index', 'A-share Index', 'index', 'CNY', NULL, '{}',
                    '{"source_mode":"api","source_api_profile":"tushare","source_api_fallback_profile":"csindex","source_api_fallback_code":"H11001"}',
                    '{}', '{"status":"active"}'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_identifier (
                    instrument_id, identifier_type, identifier_value, is_primary
                ) VALUES
                ('mainland-etf', 'exchange_ticker', '510300.SH', true),
                ('mainland-etf', 'provider_symbol', 'fmp:510300.SS', false)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_market_data (
                    instrument_id, metric_family, quote_basis, as_of_date, value,
                    currency, price_unit, price_scale, provider, status
                ) VALUES
                (
                    'mainland-etf', 'price', 'close', '2026-08-22', '4.20',
                    'CNY', 'per_unit', 1, 'fmp:historical-price-eod:non-split-adjusted',
                    'complete'
                ),
                (
                    'a-share-index', 'price', 'close', '2026-08-22', '267.16',
                    'CNY', 'per_unit', 1, 'csindex:index-perf', 'complete'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_price_bar (
                    instrument_id, as_of_date, open_price, high_price, low_price,
                    close_price, currency, provider, status
                ) VALUES (
                    'mainland-etf', '2026-08-22', '4.10', '4.30', '4.00',
                    '4.20', 'CNY', 'fmp:historical-price-eod:non-split-adjusted',
                    'complete'
                )
                """
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = {
            row["instrument_id"]: json.loads(row["source_settings_json"])
            for row in connection.execute(
                text(
                    "SELECT instrument_id, source_settings_json FROM instrument "
                    "WHERE instrument_id IN ('mainland-etf', 'mainland-equity', 'a-share-index')"
                )
            ).mappings()
        }
        remaining_market_data = connection.execute(
            text(
                "SELECT instrument_id, provider FROM instrument_market_data "
                "WHERE instrument_id IN ('mainland-etf', 'a-share-index')"
            )
        ).mappings().all()
        remaining_price_bars = connection.execute(
            text(
                "SELECT instrument_id, provider FROM instrument_price_bar "
                "WHERE instrument_id = 'mainland-etf'"
            )
        ).mappings().all()
        tushare_identifier = connection.execute(
            text(
                "SELECT instrument_id FROM instrument_identifier "
                "WHERE identifier_type = 'provider_symbol' "
                "AND identifier_value = 'tushare:510300.SH'"
            )
        ).scalar_one_or_none()

    assert rows["mainland-etf"]["source_api_profile"] == "tushare"
    assert rows["mainland-etf"]["source_location"] == "DataHub Tushare"
    assert tushare_identifier == "mainland-etf"
    assert remaining_market_data == []
    assert remaining_price_bars == []
    assert rows["mainland-equity"]["source_api_profile"] == "fmp"
    assert not any(
        key.startswith("source_api_fallback_") for key in rows["a-share-index"]
    )


def test_corporate_action_migration_seeds_confirmed_semiconductor_etf_splits(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'instrument-registry-populated.db'}"
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_DATABASE_URL", database_url)
    monkeypatch.setenv("INVESTMENT_STUDIO_INSTRUMENT_DATA_SCHEMA", "")
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "20260712_0006")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json, market_data_updated_at
                ) VALUES (
                    '159516-sz', '半导体设备ETF国泰', 'etf', 'CNY',
                    '{}', '{}', '{}', '{\"status\": \"active\"}', NULL
                )
                """
            )
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT effective_date, record_date, new_units, old_units,
                       quantity_rounding, status, source
                FROM corporate_action_event
                WHERE instrument_id = '159516-sz'
                ORDER BY effective_date
                """
            )
        ).mappings().all()

    assert [str(row["effective_date"]) for row in rows] == ["2026-03-30", "2026-07-10"]
    assert [str(row["record_date"]) for row in rows] == ["2026-03-27", "2026-07-09"]
    assert all(row["new_units"] == "2" and row["old_units"] == "1" for row in rows)
    assert [row["quantity_rounding"] for row in rows] == ["exact", "truncate"]
    assert all(row["status"] == "confirmed" for row in rows)
    assert [row["source"] for row in rows] == [
        "fund_manager_announcement",
        "distributor_mirror",
    ]
