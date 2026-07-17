from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parents[2]
MIGRATIONS_ROOT = WORKSPACE_ROOT / "infra" / "instrument_registry"
FUND_ID = "canonical-nav-fund"
FUND_POLICY = {
    "trading": ["official_nav"],
    "valuation": ["official_nav"],
    "total_return": ["total_return_nav"],
    "chart": ["total_return_nav"],
    "reference": ["official_nav"],
}
LEGACY_POLICY = {
    **FUND_POLICY,
    "total_return": ["reinvested_nav", "official_nav"],
    "chart": ["cumulative_nav", "official_nav"],
}


def _config(
    database_url: str,
    *,
    monkeypatch: pytest.MonkeyPatch,
) -> Config:
    monkeypatch.setenv(
        "PORTFOLIO_OPS_INSTRUMENT_REGISTRY_ALEMBIC_DATABASE_URL",
        database_url,
    )
    monkeypatch.setenv("PORTFOLIO_OPS_INSTRUMENT_REGISTRY_SCHEMA", "")
    config = Config(str(MIGRATIONS_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _insert_fund(
    connection: sa.Connection,
    *,
    policy: dict[str, object] = FUND_POLICY,
) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO instrument (
                instrument_id, instrument_name, instrument_type, currency,
                quote_selection_policy_json, source_settings_json,
                refresh_status_json, lifecycle_state_json,
                market_data_updated_at
            ) VALUES (
                :instrument_id, 'Canonical NAV Fund', 'fund', 'CNY',
                :policy, '{}', '{}', '{"status": "active"}', NULL
            )
            """
        ),
        {"instrument_id": FUND_ID, "policy": json.dumps(policy)},
    )


def _insert_legacy_nav(
    connection: sa.Connection,
    *,
    quote_basis: str,
    value: str,
) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO instrument_market_data (
                instrument_id, metric_family, quote_basis, as_of_date,
                value, currency, price_unit, price_scale, provider, status
            ) VALUES (
                :instrument_id, 'nav', :quote_basis, '2026-06-30',
                :value, 'CNY', 'per_unit', 1, 'legacy-provider', 'complete'
            )
            """
        ),
        {
            "instrument_id": FUND_ID,
            "quote_basis": quote_basis,
            "value": value,
        },
    )


def _install_legacy_snapshot(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            CREATE TABLE platform_metadata (
                metadata_key VARCHAR(128) PRIMARY KEY,
                value_json JSON NOT NULL
            )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            CREATE TABLE fund_nav_raw_observation (
                fund_nav_raw_observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id VARCHAR NOT NULL,
                as_of_date DATE NOT NULL,
                currency VARCHAR(8) NOT NULL,
                unit_nav_value TEXT,
                cash_cumulative_nav_value TEXT,
                observed_total_return_nav_value TEXT,
                unit_nav_status VARCHAR(32),
                cash_cumulative_nav_status VARCHAR(32),
                observed_total_return_nav_status VARCHAR(32),
                total_return_semantics VARCHAR(64) NOT NULL,
                unit_nav_source_provider VARCHAR,
                cash_cumulative_source_provider VARCHAR,
                total_return_source_provider VARCHAR,
                source_kind VARCHAR(64) NOT NULL,
                source_ref VARCHAR(512) NOT NULL,
                evidence_json JSON NOT NULL
            )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO fund_nav_raw_observation (
                instrument_id, as_of_date, currency,
                unit_nav_value, cash_cumulative_nav_value,
                observed_total_return_nav_value,
                unit_nav_status, cash_cumulative_nav_status,
                observed_total_return_nav_status, total_return_semantics,
                unit_nav_source_provider, cash_cumulative_source_provider,
                total_return_source_provider, source_kind, source_ref,
                evidence_json
            ) VALUES (
                :instrument_id, '2026-06-30', 'CNY',
                '1.00', '1.05', '1.10',
                'complete', 'complete', 'complete', 'legacy_unverified',
                'legacy-provider', 'legacy-provider', 'legacy-provider',
                'legacy_registry_snapshot',
                'instrument_registry@20260715_0011', '{}'
            )
            """
        ),
        {"instrument_id": FUND_ID},
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO platform_metadata (metadata_key, value_json)
            VALUES ('legacy_nav_snapshot_v1', :manifest)
            """
        ),
        {
            "manifest": json.dumps(
                {
                    "status": "complete",
                    "source_registry_revision": "20260715_0011",
                    "source_row_count": 3,
                    "observation_count": 1,
                    "captured_at": "2026-07-15T00:00:00Z",
                }
            )
        },
    )


def _insert_event_revision(
    connection: sa.Connection,
    *,
    event_id: str,
    action_id: str = "stable-action",
    revision_number: int,
    revision_kind: str,
    supersedes: str | None,
    cash_per_unit: str,
) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO fund_nav_event (
                fund_nav_event_id, fund_nav_action_id, revision_number,
                revision_kind, supersedes_fund_nav_event_id, instrument_id,
                event_type, effective_date, sequence_order, cash_per_unit,
                evidence_kind, source, external_event_id, provenance_json,
                recorded_by, revision_reason, created_at, updated_at
            ) VALUES (
                :event_id, :action_id, :revision_number,
                :revision_kind, :supersedes, :instrument_id,
                'cash_distribution', '2026-06-30', NULL, :cash_per_unit,
                'provider_notice', 'fund-manager-notice', :event_id,
                '{"notice": "verified"}', 'migration-test',
                :revision_kind, '2026-07-15T00:00:00Z',
                '2026-07-15T00:00:00Z'
            )
            """
        ),
        {
            "event_id": event_id,
            "action_id": action_id,
            "revision_number": revision_number,
            "revision_kind": revision_kind,
            "supersedes": supersedes,
            "instrument_id": FUND_ID,
            "cash_per_unit": cash_per_unit,
        },
    )


def _insert_evidence_revision(
    connection: sa.Connection,
    *,
    evidence_id: str,
    event_id: str,
    revision_number: int,
    revision_kind: str,
    supersedes: str | None,
    reinvestment_nav: str,
) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO fund_nav_reinvestment_evidence (
                fund_nav_reinvestment_evidence_id, instrument_id,
                fund_nav_event_id, revision_number, revision_kind,
                supersedes_fund_nav_reinvestment_evidence_id,
                reinvestment_nav, evidence_kind, source,
                external_evidence_id, provenance_json, recorded_by,
                revision_reason, created_at, updated_at
            ) VALUES (
                :evidence_id, :instrument_id, :event_id,
                :revision_number, :revision_kind, :supersedes,
                :reinvestment_nav, 'provider_notice', 'fund-manager-notice',
                :evidence_id, '{"notice": "verified"}', 'migration-test',
                :revision_kind, '2026-07-15T00:00:00Z',
                '2026-07-15T00:00:00Z'
            )
            """
        ),
        {
            "evidence_id": evidence_id,
            "instrument_id": FUND_ID,
            "event_id": event_id,
            "revision_number": revision_number,
            "revision_kind": revision_kind,
            "supersedes": supersedes,
            "reinvestment_nav": reinvestment_nav,
        },
    )


def test_migration_snapshots_then_deletes_untrusted_return_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'legacy-cleanup.db'}"
    config = _config(database_url, monkeypatch=monkeypatch)
    command.upgrade(config, "20260715_0011")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        _insert_fund(connection, policy=LEGACY_POLICY)
        _insert_legacy_nav(connection, quote_basis="official_nav", value="1.00")
        _insert_legacy_nav(connection, quote_basis="cumulative_nav", value="1.05")
        _insert_legacy_nav(connection, quote_basis="total_return_nav", value="1.10")

    with pytest.raises(RuntimeError, match="before the Platform legacy NAV snapshot"):
        command.upgrade(config, "20260715_0012")

    with engine.begin() as connection:
        _install_legacy_snapshot(connection)
    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                """
                SELECT quote_basis, value, nav_lineage_kind,
                       nav_lineage_evidence_json,
                       fund_nav_adjustment_factor_id
                FROM instrument_market_data
                WHERE instrument_id = :instrument_id
                ORDER BY quote_basis
                """
            ),
            {"instrument_id": FUND_ID},
        ).mappings().all()
        assert [(row["quote_basis"], row["value"]) for row in rows] == [
            ("official_nav", "1.00")
        ]
        assert rows[0]["nav_lineage_kind"] == "provider_explicit"
        lineage = json.loads(rows[0]["nav_lineage_evidence_json"])
        assert lineage["raw_snapshot_ref"] == "instrument_registry@20260715_0011"
        assert rows[0]["fund_nav_adjustment_factor_id"] is None

        policy_json = connection.scalar(
            sa.text(
                "SELECT quote_selection_policy_json FROM instrument "
                "WHERE instrument_id = :instrument_id"
            ),
            {"instrument_id": FUND_ID},
        )
        policy = json.loads(policy_json)
        assert policy["total_return"] == ["total_return_nav"]
        assert policy["chart"] == ["total_return_nav"]


def test_database_enforces_revision_snapshot_factor_and_formula_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'projection-contract.db'}"
    config = _config(database_url, monkeypatch=monkeypatch)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        _insert_fund(connection)
        _insert_event_revision(
            connection,
            event_id="event-v1",
            revision_number=1,
            revision_kind="original",
            supersedes=None,
            cash_per_unit="0.10",
        )
        _insert_event_revision(
            connection,
            event_id="event-v2",
            revision_number=2,
            revision_kind="correction",
            supersedes="event-v1",
            cash_per_unit="0.20",
        )
        _insert_evidence_revision(
            connection,
            evidence_id="evidence-v1",
            event_id="event-v2",
            revision_number=1,
            revision_kind="original",
            supersedes=None,
            reinvestment_nav="1.00",
        )
        _insert_evidence_revision(
            connection,
            evidence_id="evidence-v2",
            event_id="event-v2",
            revision_number=2,
            revision_kind="correction",
            supersedes="evidence-v1",
            reinvestment_nav="0.80",
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_event_revision(
                connection,
                event_id="event-branch",
                revision_number=2,
                revision_kind="correction",
                supersedes="event-v1",
                cash_per_unit="0.30",
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_event_revision(
                connection,
                event_id="event-bad-cancel",
                revision_number=3,
                revision_kind="cancellation",
                supersedes="event-v2",
                cash_per_unit="0.30",
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_evidence_revision(
                connection,
                evidence_id="evidence-branch",
                event_id="event-v2",
                revision_number=2,
                revision_kind="correction",
                supersedes="evidence-v1",
                reinvestment_nav="0.70",
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            _insert_event_revision(
                connection,
                event_id="malformed-decimal-event",
                action_id="malformed-decimal-action",
                revision_number=1,
                revision_kind="original",
                supersedes=None,
                cash_per_unit="0.10garbage",
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE fund_nav_event SET revision_reason = 'mutated' "
                    "WHERE fund_nav_event_id = 'event-v1'"
                )
            )

    incomplete_fingerprint = "0" * 64
    incomplete_run_id = f"fund-nav-projection-{incomplete_fingerprint}"
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO fund_nav_projection_run (
                    fund_nav_projection_run_id, instrument_id,
                    input_fingerprint, source_observation_fingerprint,
                    projection_kind, projection_status, method_version,
                    anchor_date, source_provider, evidence_json,
                    created_by, created_at
                ) VALUES (
                    :run_id, :instrument_id, :fingerprint, :source_fingerprint,
                    'provider_explicit', 'complete', 'provider_factor/v1',
                    '2026-06-30', 'fund-manager-sheet',
                    '{"source": "verified"}', 'migration-test',
                    '2026-07-15T00:00:00Z'
                )
                """
            ),
            {
                "run_id": incomplete_run_id,
                "instrument_id": FUND_ID,
                "fingerprint": incomplete_fingerprint,
                "source_fingerprint": "1" * 64,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO fund_nav_projection_run_event VALUES (:run_id, 'event-v2')"
            ),
            {"run_id": incomplete_run_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO fund_nav_projection_run_reinvestment_evidence "
                "VALUES (:run_id, 'evidence-v2')"
            ),
            {"run_id": incomplete_run_id},
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO fund_nav_current_projection (
                        instrument_id, fund_nav_projection_run_id,
                        updated_at, updated_by
                    ) VALUES (
                        :instrument_id, :run_id,
                        '2026-07-15T00:00:00Z', 'migration-test'
                    )
                    """
                ),
                {"instrument_id": FUND_ID, "run_id": incomplete_run_id},
            )

    fingerprint = "a" * 64
    run_id = f"fund-nav-projection-{fingerprint}"
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO fund_nav_projection_run (
                    fund_nav_projection_run_id, instrument_id,
                    input_fingerprint, source_observation_fingerprint,
                    projection_kind, projection_status, method_version,
                    anchor_date, source_provider, evidence_json,
                    created_by, created_at
                ) VALUES (
                    :run_id, :instrument_id, :fingerprint, :source_fingerprint,
                    'provider_explicit', 'complete', 'provider_factor/v1',
                    '2026-06-30', 'fund-manager-sheet',
                    '{"source": "verified"}', 'migration-test',
                    '2026-07-15T00:00:00Z'
                )
                """
            ),
            {
                "run_id": run_id,
                "instrument_id": FUND_ID,
                "fingerprint": fingerprint,
                "source_fingerprint": "b" * 64,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO fund_nav_projection_run_event VALUES (:run_id, 'event-v2')"
            ),
            {"run_id": run_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO fund_nav_projection_run_reinvestment_evidence "
                "VALUES (:run_id, 'evidence-v2')"
            ),
            {"run_id": run_id},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO fund_nav_adjustment_factor (
                    fund_nav_adjustment_factor_id, factor_logical_key,
                    instrument_id, fund_nav_projection_run_id, as_of_date,
                    factor_level, factor_kind, evidence_kind, method_version,
                    anchor_date, source_provider, evidence_json,
                    created_at, updated_at
                ) VALUES (
                    'provider-factor', 'provider:2026-06-30', :instrument_id,
                    :run_id, '2026-06-30', '1.25', 'provider_implied',
                    'provider_total_return', 'provider_factor/v1',
                    '2026-06-30', 'fund-manager-sheet',
                    '{"fields": ["unit_nav", "adjusted_nav"]}',
                    '2026-07-15T00:00:00Z', '2026-07-15T00:00:00Z'
                )
                """
            ),
            {"instrument_id": FUND_ID, "run_id": run_id},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO fund_nav_current_projection (
                    instrument_id, fund_nav_projection_run_id,
                    updated_at, updated_by
                ) VALUES (
                    :instrument_id, :run_id,
                    '2026-07-15T00:00:00Z', 'migration-test'
                )
                """
            ),
            {"instrument_id": FUND_ID, "run_id": run_id},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO instrument_market_data (
                    instrument_id, metric_family, quote_basis, as_of_date,
                    value, currency, price_unit, price_scale, provider, status,
                    nav_lineage_kind, nav_lineage_evidence_json
                ) VALUES (
                    :instrument_id, 'nav', 'official_nav', '2026-06-30',
                    '1.20', 'CNY', 'per_unit', 1, 'fund-manager-sheet',
                    'complete', 'provider_explicit',
                    '{"source_field": "unit_nav"}'
                )
                """
            ),
            {"instrument_id": FUND_ID},
        )

    def insert_total(value: str) -> None:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO instrument_market_data (
                        instrument_id, metric_family, quote_basis, as_of_date,
                        value, currency, price_unit, price_scale, provider,
                        status, nav_lineage_kind,
                        nav_lineage_evidence_json,
                        fund_nav_adjustment_factor_id
                    ) VALUES (
                        :instrument_id, 'nav', 'total_return_nav',
                        '2026-06-30', :value, 'CNY', 'per_unit', 1,
                        'fund-manager-sheet', 'complete', 'provider_explicit',
                        :lineage, 'provider-factor'
                    )
                    """
                ),
                {
                    "instrument_id": FUND_ID,
                    "value": value,
                    "lineage": json.dumps(
                        {
                            "factor_record_id": "provider-factor",
                            "factor_logical_key": "provider:2026-06-30",
                            "factor_level": "1.25",
                        }
                    ),
                },
            )

    with pytest.raises(IntegrityError, match="factor is not canonical"):
        insert_total("1.49")
    insert_total("1.50")

    with pytest.raises(IntegrityError, match="referenced by total-return NAV"):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "DELETE FROM instrument_market_data "
                    "WHERE instrument_id = :instrument_id "
                    "AND quote_basis = 'official_nav'"
                ),
                {"instrument_id": FUND_ID},
            )
    with pytest.raises(IntegrityError, match="immutable"):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE fund_nav_adjustment_factor "
                    "SET factor_level = '1.30' "
                    "WHERE fund_nav_adjustment_factor_id = 'provider-factor'"
                )
            )
    with pytest.raises(IntegrityError, match="cannot be deleted"):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "DELETE FROM fund_nav_adjustment_factor "
                    "WHERE fund_nav_adjustment_factor_id = 'provider-factor'"
                )
            )


def test_migration_is_intentionally_irreversible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'irreversible.db'}"
    config = _config(database_url, monkeypatch=monkeypatch)
    command.upgrade(config, "head")
    with pytest.raises(RuntimeError, match="intentionally irreversible"):
        command.downgrade(config, "20260715_0011")
