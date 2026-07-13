from __future__ import annotations

import ast
from datetime import date, timedelta
import json
from pathlib import Path
from time import monotonic

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import Uuid, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from portfolio_ops_instrument_core.quote_revisions import (
    make_quote_observation_id,
    make_quote_revision_id,
    make_quote_series_id,
    quote_revision_payload_hash,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
MIGRATIONS_ROOT = WORKSPACE_ROOT / "infra" / "instrument_registry"
VERSIONS_DIR = MIGRATIONS_ROOT / "alembic" / "versions"
RUNTIME_PACKAGE_NAMES = ("app", "platform_app", "portfolio_ops_instrument_core")
STRICT_FX_POLICY = {
    "trading": ["spot"],
    "valuation": ["spot"],
    "total_return": [],
    "chart": ["spot"],
    "reference": ["spot"],
}


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


def test_instrument_registry_migrations_upgrade_an_empty_database(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'instrument-registry-migrations.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")

    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        instruments = connection.execute(
            text(
                """
                SELECT instrument_id, instrument_name, instrument_type, currency,
                       quote_selection_policy_json, source_settings_json,
                       refresh_status_json, lifecycle_state_json
                FROM instrument
                ORDER BY instrument_id
                """
            )
        ).mappings().all()
        identifiers = connection.execute(
            text(
                """
                SELECT instrument_id, identifier_type, identifier_value, is_primary
                FROM instrument_identifier
                ORDER BY instrument_id
                """
            )
        ).mappings().all()
        fact_counts = connection.execute(
            text(
                """
                SELECT
                    (SELECT COUNT(*) FROM quote_series) AS series_count,
                    (SELECT COUNT(*) FROM quote_observation) AS observation_count,
                    (SELECT COUNT(*) FROM quote_observation_revision) AS revision_count
                """
            )
        ).mappings().one()

    assert revision == "20260713_0011"
    assert [row["instrument_id"] for row in instruments] == [
        "fx-usd-cny",
        "fx-usd-hkd",
    ]
    expected_identity = {
        "fx-usd-cny": ("USD/CNY Spot", "CNY"),
        "fx-usd-hkd": ("USD/HKD Spot", "HKD"),
    }
    for row in instruments:
        assert row["instrument_type"] == "fx"
        assert (row["instrument_name"], row["currency"]) == expected_identity[
            row["instrument_id"]
        ]
        assert json.loads(row["quote_selection_policy_json"]) == STRICT_FX_POLICY
        assert json.loads(row["source_settings_json"]) == {}
        assert json.loads(row["refresh_status_json"]) == {}
        assert json.loads(row["lifecycle_state_json"]) == {"status": "active"}
    assert [
        (
            row["instrument_id"],
            row["identifier_type"],
            row["identifier_value"],
            bool(row["is_primary"]),
        )
        for row in identifiers
    ] == [
        ("fx-usd-cny", "ticker", "USDCNY", True),
        ("fx-usd-hkd", "ticker", "USDHKD", True),
    ]
    assert dict(fact_counts) == {
        "series_count": 0,
        "observation_count": 0,
        "revision_count": 0,
    }
    inspector = inspect(engine)
    assert {
        item["name"] for item in inspector.get_check_constraints("instrument")
    } >= {"ck_instrument_currency_iso_code"}
    assert {
        item["name"] for item in inspector.get_check_constraints("quote_series")
    } >= {"ck_quote_series_currency_iso_code"}


def test_currency_constraint_migration_rejects_ambiguous_existing_facts(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'invalid-currency-migration.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    command.upgrade(config, "20260713_0010")

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
                    'ambiguous-currency', 'Ambiguous Currency', 'fund', 'US',
                    '{}', '{}', '{}', '{"status": "active"}', NULL
                )
                """
            )
        )

    with pytest.raises(RuntimeError, match="explicit three-letter uppercase facts"):
        command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260713_0010"


def test_corporate_action_migration_seeds_confirmed_semiconductor_etf_splits(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'instrument-registry-populated.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
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


def _migration_config(database_url: str) -> Config:
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _seed_legacy_instrument(connection, *, instrument_id: str = "migration-fund") -> None:
    connection.execute(
        text(
            """
            INSERT INTO instrument (
                instrument_id, instrument_name, instrument_type, currency,
                quote_selection_policy_json, source_settings_json,
                refresh_status_json, lifecycle_state_json, market_data_updated_at
            ) VALUES (
                :instrument_id, 'Migration Fund', 'fund', 'USD',
                '{}', '{}', '{}', '{"status": "active"}',
                '2026-07-13T00:00:00.000000Z'
            )
            """
        ),
        {"instrument_id": instrument_id},
    )


def test_quote_revision_migration_backfills_deterministic_identity_and_unknown_ingestion(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'quote-revision-backfill.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = _migration_config(database_url)
    command.upgrade(config, "20260712_0007")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        _seed_legacy_instrument(connection)
        connection.execute(
            text(
                """
                INSERT INTO instrument_market_data (
                    instrument_id, metric_family, quote_basis, as_of_date,
                    value, currency, provider, status
                ) VALUES (
                    'migration-fund', 'nav', 'official_nav', :as_of_date,
                    '100.0000', 'USD', ' legacy-provider ', 'complete'
                )
                """
            ),
            {"as_of_date": "2026-07-10"},
        )

    command.upgrade(config, "head")

    expected_series_id = make_quote_series_id(
        instrument_id="migration-fund",
        metric_family="nav",
        quote_basis="official_nav",
        currency="USD",
    )
    expected_observation_id = make_quote_observation_id(
        quote_series_id=expected_series_id,
        as_of_date=date(2026, 7, 10),
    )
    expected_revision_id = make_quote_revision_id(
        observation_id=expected_observation_id,
        revision_number=1,
    )
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT s.quote_series_id, s.data_updated_at,
                       o.observation_id, o.as_of_date,
                       r.revision_id, r.revision_number, r.value, r.source_ref,
                       r.status, r.source_published_at, r.ingested_at,
                       r.payload_hash, r.is_current, r.superseded_at
                FROM quote_series s
                JOIN quote_observation o USING (quote_series_id)
                JOIN quote_observation_revision r USING (observation_id)
                """
            ).columns(
                quote_series_id=Uuid(as_uuid=False),
                observation_id=Uuid(as_uuid=False),
                revision_id=Uuid(as_uuid=False),
            )
        ).mappings().one()

    assert row["quote_series_id"] == expected_series_id
    assert row["observation_id"] == expected_observation_id
    assert row["revision_id"] == expected_revision_id
    assert row["revision_number"] == 1
    assert row["value"] == "100"
    assert row["source_ref"] == "legacy-provider"
    assert row["status"] == "complete"
    assert row["ingested_at"] is None
    assert row["source_published_at"] is None
    assert row["superseded_at"] is None
    assert bool(row["is_current"]) is True
    assert row["data_updated_at"] == "2026-07-13T00:00:00.000000Z"
    assert row["payload_hash"] == quote_revision_payload_hash(
        value="100.0",
        source_ref="legacy-provider",
        status="complete",
    )
    assert "instrument_market_data" not in inspect(engine).get_table_names()


def test_quote_revision_migration_fails_before_ddl_on_unavailable_legacy_row(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'quote-revision-invalid-status.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = _migration_config(database_url)
    command.upgrade(config, "20260712_0007")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        _seed_legacy_instrument(connection)
        connection.execute(
            text(
                """
                INSERT INTO instrument_market_data (
                    instrument_id, metric_family, quote_basis, as_of_date,
                    value, currency, provider, status
                ) VALUES (
                    'migration-fund', 'nav', 'official_nav', '2026-07-10',
                    '100', 'USD', NULL, 'unavailable'
                )
                """
            )
        )

    with pytest.raises(RuntimeError, match="unavailable means no observation"):
        command.upgrade(config, "head")
    assert "quote_series" not in inspect(engine).get_table_names()
    assert "instrument_market_data" in inspect(engine).get_table_names()


def test_quote_revision_migration_fails_on_normalized_identity_collision(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'quote-revision-collision.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = _migration_config(database_url)
    command.upgrade(config, "20260712_0007")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        _seed_legacy_instrument(connection)
        connection.execute(
            text(
                """
                INSERT INTO instrument_market_data (
                    instrument_id, metric_family, quote_basis, as_of_date,
                    value, currency, provider, status
                ) VALUES
                    ('migration-fund', 'nav', 'official_nav', '2026-07-10',
                     '100', 'USD', 'a', 'complete'),
                    ('migration-fund', 'NAV', 'OFFICIAL_NAV', '2026-07-10',
                     '101', 'usd', 'b', 'complete')
                """
            )
        )

    with pytest.raises(RuntimeError, match="collides after canonical identity"):
        command.upgrade(config, "head")
    assert "quote_series" not in inspect(engine).get_table_names()


def test_quote_revision_migration_backfill_is_batched_at_order_of_magnitude(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'quote-revision-volume.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = _migration_config(database_url)
    command.upgrade(config, "20260712_0007")
    engine = create_engine(database_url)
    row_count = 10_000
    start_date = date(1990, 1, 1)
    with engine.begin() as connection:
        _seed_legacy_instrument(connection)
        connection.execute(
            text(
                """
                INSERT INTO instrument_market_data (
                    instrument_id, metric_family, quote_basis, as_of_date,
                    value, currency, provider, status
                ) VALUES (
                    'migration-fund', 'nav', 'official_nav', :as_of_date,
                    :value, 'USD', 'volume-fixture', 'complete'
                )
                """
            ),
            [
                {
                    "as_of_date": (start_date + timedelta(days=index)).isoformat(),
                    "value": f"{100 + index / 10_000:.4f}",
                }
                for index in range(row_count)
            ],
        )

    started_at = monotonic()
    command.upgrade(config, "head")
    elapsed_seconds = monotonic() - started_at
    print(
        f"quote revision migration benchmark: {row_count} rows in "
        f"{elapsed_seconds:.3f}s"
    )
    with engine.connect() as connection:
        counts = connection.execute(
            text(
                """
                SELECT
                    (SELECT COUNT(*) FROM quote_series) AS series_count,
                    (SELECT COUNT(*) FROM quote_observation) AS observation_count,
                    (SELECT COUNT(*) FROM quote_observation_revision) AS revision_count
                """
            )
        ).mappings().one()
    assert counts == {
        "series_count": 1,
        "observation_count": row_count,
        "revision_count": row_count,
    }
    assert elapsed_seconds < 15.0


@pytest.mark.parametrize(
    ("metric_family", "quote_basis", "currency", "value", "message"),
    [
        ("nav", "close", "USD", "100", "unsupported canonical quote identity"),
        ("nav", "official_nav", "", "100", "canonical quote identity"),
        ("nav", "official_nav", "TOO-LONG-CURRENCY", "100", "canonical quote identity"),
        ("nav", "official_nav", "USD", "0", "must be positive"),
    ],
)
def test_quote_revision_migration_preflight_rejects_invalid_identity_or_value(
    tmp_path,
    monkeypatch,
    metric_family: str,
    quote_basis: str,
    currency: str,
    value: str,
    message: str,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / f'preflight-{quote_basis}-{currency}.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = _migration_config(database_url)
    command.upgrade(config, "20260712_0007")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        _seed_legacy_instrument(connection)
        connection.execute(
            text(
                """
                INSERT INTO instrument_market_data (
                    instrument_id, metric_family, quote_basis, as_of_date,
                    value, currency, provider, status
                ) VALUES (
                    'migration-fund', :metric_family, :quote_basis, '2026-07-10',
                    :value, :currency, 'preflight', 'complete'
                )
                """
            ),
            {
                "metric_family": metric_family,
                "quote_basis": quote_basis,
                "value": value,
                "currency": currency,
            },
        )

    with pytest.raises(RuntimeError, match=message):
        command.upgrade(config, "head")
    assert "quote_series" not in inspect(engine).get_table_names()


def test_sqlite_quote_schema_enforces_exact_numeric_identity_and_immutability(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'quote-schema-guards.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = _migration_config(database_url)
    command.upgrade(config, "20260712_0007")
    engine = create_engine(database_url)
    exact_value = "12345678901234567890.12345678901234567890123456789"
    long_source_ref = "issuer-document:" + "x" * 5000
    with engine.begin() as connection:
        _seed_legacy_instrument(connection)
        connection.execute(
            text(
                """
                INSERT INTO instrument_market_data (
                    instrument_id, metric_family, quote_basis, as_of_date,
                    value, currency, provider, status
                ) VALUES (
                    'migration-fund', 'nav', 'official_nav', '2026-07-10',
                    :value, 'USD', :source_ref, 'complete'
                )
                """
            ),
            {"value": exact_value, "source_ref": long_source_ref},
        )

    command.upgrade(config, "head")
    schema = inspect(engine)
    revision_columns = {
        column["name"]: str(column["type"]).upper()
        for column in schema.get_columns("quote_observation_revision")
    }
    assert revision_columns["value"] == "TEXT"
    assert revision_columns["source_ref"] == "TEXT"
    assert {item["name"] for item in schema.get_indexes("quote_series")} == set()
    assert {item["name"] for item in schema.get_indexes("quote_observation")} == set()
    assert {
        item["name"] for item in schema.get_indexes("quote_observation_revision")
    } == {"uq_quote_observation_revision_current"}
    assert "ix_corporate_action_instrument_effective_date" in {
        item["name"] for item in schema.get_indexes("corporate_action_event")
    }

    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT revision_id, observation_id, value, source_ref, payload_hash
                FROM quote_observation_revision
                """
            )
        ).mappings().one()
    assert row["value"] == exact_value
    assert row["source_ref"] == long_source_ref

    with pytest.raises(DBAPIError, match="quote_revision_immutable_violation"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE quote_observation_revision SET value = '999' "
                    "WHERE revision_id = :revision_id"
                ),
                {"revision_id": row["revision_id"]},
            )
    with pytest.raises(DBAPIError, match="quote_revision_immutable_violation"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM quote_observation_revision "
                    "WHERE revision_id = :revision_id"
                ),
                {"revision_id": row["revision_id"]},
            )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE quote_observation_revision
                SET is_current = 0, superseded_at = '2026-07-13T00:00:00Z'
                WHERE revision_id = :revision_id
                """
            ),
            {"revision_id": row["revision_id"]},
        )
        connection.execute(
            text(
                """
                INSERT INTO quote_observation_revision (
                    revision_id, observation_id, revision_number, value,
                    source_ref, status, source_published_at, ingested_at,
                    payload_hash, is_current, superseded_at
                ) VALUES (
                    '22222222222222222222222222222222', :observation_id, 2,
                    :value, :source_ref, 'complete', NULL,
                    '2026-07-13T00:00:00Z', :payload_hash, 1, NULL
                )
                """
            ),
            {
                "observation_id": row["observation_id"],
                "value": exact_value + "123",
                "source_ref": long_source_ref + ":revision-2",
                "payload_hash": "sha256:revision-2",
            },
        )
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT value FROM quote_observation_revision "
                "WHERE revision_number = 2"
            )
        ) == exact_value + "123"

    with pytest.raises(DBAPIError, match="quote_revision_insert_violation"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO quote_observation_revision (
                        revision_id, observation_id, revision_number, value,
                        source_ref, status, payload_hash, is_current, superseded_at
                    ) VALUES (
                        '55555555555555555555555555555555', :observation_id, 3,
                        '125', 'unclosed', 'complete', 'sha256:unclosed', 1, NULL
                    )
                    """
                ),
                {"observation_id": row["observation_id"]},
            )
    with pytest.raises(DBAPIError, match="quote_revision_insert_violation"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE quote_observation_revision
                    SET is_current = 0, superseded_at = '2026-07-14T00:00:00Z'
                    WHERE revision_number = 2
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO quote_observation_revision (
                        revision_id, observation_id, revision_number, value,
                        source_ref, status, payload_hash, is_current, superseded_at
                    ) VALUES (
                        '66666666666666666666666666666666', :observation_id, 4,
                        '126', 'skipped', 'complete', 'sha256:skipped', 1, NULL
                    )
                    """
                ),
                {"observation_id": row["observation_id"]},
            )
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT COUNT(*) FROM quote_observation_revision "
                "WHERE is_current"
            )
        ) == 1

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO quote_observation_revision (
                        revision_id, observation_id, revision_number, value,
                        source_ref, status, payload_hash, is_current, superseded_at
                    ) VALUES (
                        '33333333333333333333333333333333', :observation_id, 3,
                        '0', 'zero', 'complete', 'sha256:zero', 0,
                        '2026-07-13T00:00:00Z'
                    )
                    """
                ),
                {"observation_id": row["observation_id"]},
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO quote_series (
                        quote_series_id, instrument_id, metric_family,
                        quote_basis, currency, data_updated_at
                    ) VALUES (
                        '44444444444444444444444444444444', 'migration-fund',
                        'nav', 'close', 'USD', NULL
                    )
                    """
                )
            )


def test_strict_total_return_policy_migration_is_atomic_and_advances_watermark(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'strict-policy.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = _migration_config(database_url)
    command.upgrade(config, "20260713_0008")
    engine = create_engine(database_url)
    legacy_watermark = "2026-07-01T00:00:00.000000Z"
    base_policy = {
        "trading": ["last", "close"],
        "valuation": ["close", "last"],
        "total_return": ["adjusted_close", "close", "last"],
        "chart": ["adjusted_close", "close", "last"],
        "reference": ["close", "last"],
    }
    fund_policy = {
        "trading": ["official_nav"],
        "valuation": ["official_nav"],
        "total_return": ["total_return_nav", "official_nav", "close"],
        "chart": ["total_return_nav", "official_nav"],
        "reference": ["official_nav"],
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO registry_metadata (
                    registry_key, registry_name, market_data_updated_at
                ) VALUES ('shared', 'Test Registry', :watermark)
                """
            ),
            {"watermark": legacy_watermark},
        )
        for instrument_id, instrument_type, policy in (
            ("legacy-etf", "etf", base_policy),
            ("legacy-index", "index", base_policy),
            ("legacy-fund", "fund", fund_policy),
            (
                "already-strict",
                "etf",
                {**base_policy, "total_return": ["adjusted_close"]},
            ),
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
                        :policy, '{}', '{}', '{"status": "active"}', :watermark
                    )
                    """
                ),
                {
                    "instrument_id": instrument_id,
                    "instrument_type": instrument_type,
                    "policy": json.dumps(policy),
                    "watermark": legacy_watermark,
                },
            )

    command.upgrade(config, "20260713_0009")
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT instrument_id, quote_selection_policy_json,
                       market_data_updated_at
                FROM instrument
                ORDER BY instrument_id
                """
            )
        ).mappings().all()
        global_watermark = connection.scalar(
            text(
                "SELECT market_data_updated_at FROM registry_metadata "
                "WHERE registry_key = 'shared'"
            )
        )
    by_id = {row["instrument_id"]: row for row in rows}
    assert json.loads(by_id["legacy-etf"]["quote_selection_policy_json"])[
        "total_return"
    ] == ["adjusted_close"]
    assert json.loads(by_id["legacy-index"]["quote_selection_policy_json"])[
        "total_return"
    ] == ["adjusted_close"]
    assert json.loads(by_id["legacy-fund"]["quote_selection_policy_json"])[
        "total_return"
    ] == ["total_return_nav"]
    assert by_id["already-strict"]["market_data_updated_at"] == legacy_watermark
    assert by_id["legacy-etf"]["market_data_updated_at"] > legacy_watermark
    assert global_watermark == by_id["legacy-etf"]["market_data_updated_at"]


def test_strict_policy_migration_validates_full_policy_before_any_update(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'strict-policy-invalid.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = _migration_config(database_url)
    command.upgrade(config, "20260713_0008")
    engine = create_engine(database_url)
    valid_needs_update = {
        "trading": ["close"],
        "valuation": ["close"],
        "total_return": ["adjusted_close", "close"],
        "chart": ["adjusted_close", "close"],
        "reference": ["close"],
    }
    invalid_valuation = {
        **valid_needs_update,
        "valuation": ["adjusted_close"],
    }
    with engine.begin() as connection:
        for instrument_id, policy in (
            ("a-valid-needs-update", valid_needs_update),
            ("z-invalid-valuation", invalid_valuation),
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
                        :instrument_id, :instrument_id, 'etf', 'USD', :policy,
                        '{}', '{}', '{"status": "active"}',
                        '2026-07-01T00:00:00.000000Z'
                    )
                    """
                ),
                {
                    "instrument_id": instrument_id,
                    "policy": json.dumps(policy),
                },
            )

    with pytest.raises(RuntimeError, match="Valuation policy"):
        command.upgrade(config, "head")
    with engine.connect() as connection:
        stored = connection.scalar(
            text(
                "SELECT quote_selection_policy_json FROM instrument "
                "WHERE instrument_id = 'a-valid-needs-update'"
            )
        )
        current_revision = connection.scalar(
            text(
                "SELECT version_num FROM alembic_version"
            )
        )
    assert json.loads(stored) == valid_needs_update
    assert current_revision == "20260713_0008"


@pytest.mark.parametrize(
    "conflict_kind",
    [
        "instrument_type",
        "currency",
        "lifecycle",
        "valuation_policy",
        "other_policy_role",
        "unexpected_policy_key",
    ],
)
def test_canonical_fx_identity_migration_fails_closed_before_any_insert(
    tmp_path,
    monkeypatch,
    conflict_kind: str,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / f'fx-conflict-{conflict_kind}.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = _migration_config(database_url)
    command.upgrade(config, "20260713_0009")
    engine = create_engine(database_url)

    instrument_type = "fx"
    currency = "HKD"
    lifecycle = {"status": "active"}
    policy = json.loads(json.dumps(STRICT_FX_POLICY))
    if conflict_kind == "instrument_type":
        instrument_type = "equity"
    elif conflict_kind == "currency":
        currency = "CNY"
    elif conflict_kind == "lifecycle":
        lifecycle = {"status": "archived"}
    elif conflict_kind == "valuation_policy":
        policy["valuation"] = ["close"]
    elif conflict_kind == "other_policy_role":
        policy["chart"] = []
    else:
        policy["business_owner"] = "treasury"

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json,
                    market_data_updated_at
                ) VALUES (
                    'fx-usd-hkd', 'Existing Business Label', :instrument_type,
                    :currency, :policy, '{"owner": "treasury"}',
                    '{"status": "verified"}', :lifecycle,
                    '2026-07-01T00:00:00.000000Z'
                )
                """
            ),
            {
                "instrument_type": instrument_type,
                "currency": currency,
                "policy": json.dumps(policy),
                "lifecycle": json.dumps(lifecycle),
            },
        )

    with pytest.raises(RuntimeError, match="Canonical FX identity conflict"):
        command.upgrade(config, "head")

    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        existing = connection.execute(
            text(
                """
                SELECT instrument_type, currency, quote_selection_policy_json,
                       lifecycle_state_json, source_settings_json,
                       refresh_status_json, market_data_updated_at
                FROM instrument
                WHERE instrument_id = 'fx-usd-hkd'
                """
            )
        ).mappings().one()
        missing_leg_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM instrument "
                "WHERE instrument_id = 'fx-usd-cny'"
            )
        )
    assert revision == "20260713_0009"
    assert existing["instrument_type"] == instrument_type
    assert existing["currency"] == currency
    assert json.loads(existing["quote_selection_policy_json"]) == policy
    assert json.loads(existing["lifecycle_state_json"]) == lifecycle
    assert json.loads(existing["source_settings_json"]) == {"owner": "treasury"}
    assert json.loads(existing["refresh_status_json"]) == {"status": "verified"}
    assert existing["market_data_updated_at"] == "2026-07-01T00:00:00.000000Z"
    assert missing_leg_count == 0


def test_canonical_fx_identity_migration_is_idempotent_and_preserves_business_data(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'fx-idempotent.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = _migration_config(database_url)
    command.upgrade(config, "20260713_0009")
    engine = create_engine(database_url)
    original_watermark = "2026-07-01T00:00:00.000000Z"
    strict_policy = json.loads(json.dumps(STRICT_FX_POLICY))
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO registry_metadata (
                    registry_key, registry_name, market_data_updated_at
                ) VALUES ('shared', 'Existing Registry Name', :watermark)
                """
            ),
            {"watermark": original_watermark},
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json,
                    market_data_updated_at
                ) VALUES (
                    'fx-usd-hkd', 'Treasury USD/HKD Reference', 'fx', 'HKD',
                    :policy, '{"vendor": "licensed-feed"}',
                    '{"last_job": "verified"}',
                    '{"status": "active", "owner": "treasury"}', :watermark
                )
                """
            ),
            {
                "policy": json.dumps(strict_policy),
                "watermark": original_watermark,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_identifier (
                    instrument_identifier_id, instrument_id, identifier_type,
                    identifier_value, is_primary
                ) VALUES (777, 'fx-usd-hkd', 'ticker', 'USDHKD', 1)
                """
            )
        )

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        rows = connection.execute(
            text(
                """
                SELECT instrument_id, instrument_name, quote_selection_policy_json,
                       source_settings_json, refresh_status_json,
                       lifecycle_state_json, market_data_updated_at
                FROM instrument
                WHERE instrument_id IN ('fx-usd-hkd', 'fx-usd-cny')
                ORDER BY instrument_id
                """
            )
        ).mappings().all()
        identifiers = connection.execute(
            text(
                """
                SELECT instrument_identifier_id, instrument_id, identifier_value,
                       is_primary
                FROM instrument_identifier
                WHERE instrument_id IN ('fx-usd-hkd', 'fx-usd-cny')
                ORDER BY instrument_id
                """
            )
        ).mappings().all()
        registry = connection.execute(
            text(
                """
                SELECT registry_name, market_data_updated_at
                FROM registry_metadata WHERE registry_key = 'shared'
                """
            )
        ).mappings().one()
        fx_series_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM quote_series "
                "WHERE instrument_id IN ('fx-usd-hkd', 'fx-usd-cny')"
            )
        )

    assert revision == "20260713_0011"
    assert [row["instrument_id"] for row in rows] == ["fx-usd-cny", "fx-usd-hkd"]
    hkd = rows[1]
    assert hkd["instrument_name"] == "Treasury USD/HKD Reference"
    assert json.loads(hkd["quote_selection_policy_json"]) == strict_policy
    assert json.loads(hkd["source_settings_json"]) == {"vendor": "licensed-feed"}
    assert json.loads(hkd["refresh_status_json"]) == {"last_job": "verified"}
    assert json.loads(hkd["lifecycle_state_json"]) == {
        "status": "active",
        "owner": "treasury",
    }
    assert hkd["market_data_updated_at"] == original_watermark
    assert len(identifiers) == 2
    assert [row["identifier_value"] for row in identifiers] == ["USDCNY", "USDHKD"]
    assert identifiers[1]["instrument_identifier_id"] == 777
    assert all(bool(row["is_primary"]) for row in identifiers)
    assert registry["registry_name"] == "Existing Registry Name"
    assert registry["market_data_updated_at"] > original_watermark
    assert fx_series_count == 0


def test_canonical_fx_identity_migration_rejects_primary_ticker_hijacking_atomically(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'fx-ticker-conflict.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = _migration_config(database_url)
    command.upgrade(config, "20260713_0009")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json,
                    market_data_updated_at
                ) VALUES (
                    'unrelated-fx-owner', 'Unrelated FX Owner', 'fx', 'HKD',
                    :policy, '{}', '{}', '{"status": "active"}', NULL
                )
                """
            ),
            {"policy": json.dumps(STRICT_FX_POLICY)},
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_identifier (
                    instrument_id, identifier_type, identifier_value, is_primary
                ) VALUES ('unrelated-fx-owner', 'ticker', 'USDHKD', 1)
                """
            )
        )

    with pytest.raises(RuntimeError, match="Canonical FX identifier conflict"):
        command.upgrade(config, "head")
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        canonical_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM instrument "
                "WHERE instrument_id IN ('fx-usd-hkd', 'fx-usd-cny')"
            )
        )
    assert revision == "20260713_0009"
    assert canonical_count == 0


def test_canonical_fx_identity_migration_rejects_second_primary_identifier(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'fx-second-primary.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = _migration_config(database_url)
    command.upgrade(config, "20260713_0009")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument (
                    instrument_id, instrument_name, instrument_type, currency,
                    quote_selection_policy_json, source_settings_json,
                    refresh_status_json, lifecycle_state_json,
                    market_data_updated_at
                ) VALUES (
                    'fx-usd-hkd', 'Existing USD/HKD', 'fx', 'HKD',
                    :policy, '{}', '{}', '{"status": "active"}', NULL
                )
                """
            ),
            {"policy": json.dumps(STRICT_FX_POLICY)},
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_identifier (
                    instrument_id, identifier_type, identifier_value, is_primary
                ) VALUES ('fx-usd-hkd', 'internal', 'legacy-primary', 1)
                """
            )
        )

    with pytest.raises(RuntimeError, match="different primary identifier"):
        command.upgrade(config, "head")
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        cny_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM instrument "
                "WHERE instrument_id = 'fx-usd-cny'"
            )
        )
        ticker_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM instrument_identifier "
                "WHERE instrument_id = 'fx-usd-hkd' "
                "AND identifier_type = 'ticker'"
            )
        )
    assert revision == "20260713_0009"
    assert cny_count == 0
    assert ticker_count == 0
