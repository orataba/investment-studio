from __future__ import annotations

import ast
from datetime import date, datetime, timezone
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text


BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = BACKEND_ROOT / "alembic" / "versions"
SNAPSHOTS_DIR = BACKEND_ROOT / "watchlist_migration_snapshots"
RUNTIME_PACKAGE_NAMES = ("app", "watchlist_app")


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _assert_no_runtime_imports(path: Path) -> None:
    imported_modules = _imported_modules(path.read_text(encoding="utf-8"))
    offenders = sorted(
        module
        for module in imported_modules
        if any(module == package or module.startswith(f"{package}.") for package in RUNTIME_PACKAGE_NAMES)
    )
    assert not offenders, f"{path.name} must remain independent of runtime packages: {offenders}"


def test_watchlist_migrations_do_not_import_runtime_modules() -> None:
    for migration_path in VERSIONS_DIR.glob("*.py"):
        _assert_no_runtime_imports(migration_path)


def test_watchlist_migration_snapshots_do_not_import_runtime_modules() -> None:
    snapshot_paths = sorted(SNAPSHOTS_DIR.glob("*.py"))
    assert snapshot_paths
    for snapshot_path in snapshot_paths:
        _assert_no_runtime_imports(snapshot_path)


def test_watchlist_migrations_upgrade_an_empty_database(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-migrations.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "head")
    get_settings.cache_clear()

    inspector = inspect(create_engine(database_url))
    recalc_columns = {column["name"] for column in inspector.get_columns("recalc_job")}
    assert {"heartbeat_at", "lease_token"}.issubset(recalc_columns)

    expected_unique_indexes = {
        "recalc_job": "uq_recalc_job_running_instrument",
        "performance_snapshot": "uq_performance_snapshot_current_instrument",
        "risk_snapshot": "uq_risk_snapshot_current_instrument",
        "exposure_analytics_snapshot": "uq_exposure_snapshot_current_instrument",
        "holding_snapshot": "uq_holding_snapshot_current_instrument",
    }
    for table_name, index_name in expected_unique_indexes.items():
        indexes = {index["name"]: index for index in inspector.get_indexes(table_name)}
        assert indexes[index_name]["unique"] == 1

    tables = set(inspector.get_table_names())
    assert "instrument_score_snapshot" not in tables
    assert "instrument_rating_read_model" not in tables
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE field_key = 'instrument_name' "
                "AND source_metric_code = "
                "'watchlist_row_read_model.instrument_name'"
            )
        ) == 1
        assert connection.scalar(
            text(
                "SELECT group_mode FROM field_registry "
                "WHERE field_key = 'instrument_name'"
            )
        ) == "none"
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE field_key = 'asset_name'"
            )
        ) == 0
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE field_key IN ('asset_type', 'asset_class', 'instrument_class')"
            )
        ) == 0
        groupable_fields = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT field_key FROM field_registry "
                    "WHERE group_mode = 'discrete'"
                )
            )
        }
        assert groupable_fields == {
            "currency",
            "attr.coverage_status",
            "attr.manual_rating",
        }
        assert connection.scalar(
            text(
                "SELECT label FROM field_registry "
                "WHERE field_key = 'attr.manual_rating'"
            )
        ) == "Research Rating"


def test_investment_research_migration_preserves_profile_rating_and_notes(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'research-migration.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "20260822_0043")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument_detail (
                    instrument_id, instrument_type, detail_view_type,
                    instrument_name, primary_identifier_type,
                    primary_identifier_value, is_active, metadata_json,
                    created_at, updated_at
                ) VALUES (
                    'legacy-research', 'private_fund', 'fund',
                    'Legacy Research Fund', 'ticker', 'LEGACY', 1, '{}',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_manual_profile (
                    instrument_id, people_payload_json, strategy_payload_json,
                    price_payload_json, documents_payload_json,
                    research_payload_json, nav_settings_json, updated_at, updated_by
                ) VALUES (
                    'legacy-research', '{}', '{}', '{}', '{}',
                    :research, '{}', CURRENT_TIMESTAMP, 'legacy-user'
                )
                """
            ),
            {
                "research": json.dumps(
                    {
                        "overview": {
                            "current_view": "Constructive",
                            "research_view": "Manager edge remains intact.",
                            "dd_status": "Complete",
                            "next_review_date": "2026-06-30",
                        },
                        "manual_rating": 4,
                        "timeline_notes": [
                            {
                                "note_id": "legacy-note",
                                "note_date": "2026-05-01",
                                "title": "Manager review",
                                "summary": "No style drift observed.",
                                "importance": "high",
                                "tags": ["manager", "style"],
                            }
                        ],
                    }
                )
            },
        )

    command.upgrade(config, "head")
    get_settings.cache_clear()

    inspector = inspect(engine)
    manual_columns = {
        column["name"]
        for column in inspector.get_columns("instrument_manual_profile")
    }
    assert "research_payload_json" not in manual_columns
    with engine.connect() as connection:
        profile = connection.execute(
            text(
                "SELECT thesis, current_view, dd_status, next_review_date, "
                "manual_rating, updated_by FROM instrument_research_profile "
                "WHERE instrument_id = 'legacy-research'"
            )
        ).mappings().one()
        assert profile["thesis"] == "Manager edge remains intact."
        assert profile["current_view"] == "Constructive"
        assert profile["dd_status"] == "Complete"
        assert str(profile["next_review_date"]) == "2026-06-30"
        assert profile["manual_rating"] == 4
        assert profile["updated_by"] == "legacy-user"

        note = connection.execute(
            text(
                "SELECT note_id, note_type, title, summary, importance, tags_json "
                "FROM instrument_research_note "
                "WHERE instrument_id = 'legacy-research'"
            )
        ).mappings().one()
        assert note["note_id"] == "legacy-note"
        assert note["note_type"] == "research_update"
        assert note["title"] == "Manager review"
        assert note["summary"] == "No style drift observed."
        assert note["importance"] == "high"
        assert json.loads(note["tags_json"]) == ["manager", "style"]


def test_cross_market_taxonomy_migration_rewrites_assignments_and_read_models(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'cross-market-taxonomy.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "20260823_0046")

    legacy_attributes = {
        "coverage_status": "Watch",
        "instrument_taxonomy_level_1": "私募证券基金",
        "instrument_taxonomy_level_2": "股票策略",
        "instrument_taxonomy_level_3": "量化多头",
        "instrument_taxonomy_level_4": "沪深 300 增强",
        "instrument_taxonomy_leaf": "沪深 300 增强",
        "instrument_taxonomy_path": "私募证券基金 / 股票策略 / 量化多头 / 沪深 300 增强",
    }
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument_detail (
                    instrument_id, instrument_type, detail_view_type,
                    instrument_name, is_active, metadata_json
                ) VALUES (
                    'legacy-taxonomy', 'private_fund', 'fund',
                    'Legacy Taxonomy Fund', 1, '{}'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist (
                    watchlist_id, name, owner_type, owner_id,
                    is_default, is_shared, sort_order
                ) VALUES (
                    'taxonomy-migration', 'Taxonomy Migration', 'user', 'test',
                    0, 0, 0
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_taxonomy_assignment (
                    instrument_id, taxonomy_code, node_id,
                    assigned_at, source_record_id
                ) VALUES (
                    'legacy-taxonomy', 'instrument_taxonomy',
                    'fund-private-equity-quant-long-300',
                    CURRENT_TIMESTAMP, 'legacy-assignment'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist_row_read_model (
                    watchlist_id, instrument_id, instrument_type,
                    instrument_name, attributes_json, data_freshness_status
                ) VALUES (
                    'taxonomy-migration', 'legacy-taxonomy', 'private_fund',
                    'Legacy Taxonomy Fund', :attributes, 'fresh'
                )
                """
            ),
            {"attributes": json.dumps(legacy_attributes)},
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_summary_read_model (
                    instrument_id, payload_json, data_freshness_status
                ) VALUES (
                    'legacy-taxonomy', :payload, 'fresh'
                )
                """
            ),
            {
                "payload": json.dumps(
                    {
                        "instrument_id": "legacy-taxonomy",
                        "instrument_attributes": legacy_attributes,
                        "taxonomy": {
                            "assigned_node_id": "fund-private-equity-quant-long-300"
                        },
                    }
                )
            },
        )

    command.upgrade(config, "20260823_0047")
    with engine.connect() as connection:
        assignment = connection.execute(
            text(
                "SELECT node_id, source_record_id "
                "FROM instrument_taxonomy_assignment "
                "WHERE instrument_id = 'legacy-taxonomy'"
            )
        ).mappings().one()
        assert assignment["node_id"] == "fund-private-equity-quant-index-enhanced"
        assert assignment["source_record_id"] == "migration:20260823_0047:cross-market-taxonomy"

        history = connection.execute(
            text(
                "SELECT node_id, path_labels_json FROM instrument_taxonomy_assignment_history "
                "WHERE instrument_id = 'legacy-taxonomy' ORDER BY assigned_at DESC"
            )
        ).mappings().first()
        assert history is not None
        assert history["node_id"] == "fund-private-equity-quant-index-enhanced"
        history_path = history["path_labels_json"]
        if isinstance(history_path, str):
            history_path = json.loads(history_path)
        assert history_path == ["股票策略", "量化多头", "指数增强"]

        row_attributes = connection.scalar(
            text(
                "SELECT attributes_json FROM watchlist_row_read_model "
                "WHERE instrument_id = 'legacy-taxonomy'"
            )
        )
        if isinstance(row_attributes, str):
            row_attributes = json.loads(row_attributes)
        assert row_attributes == {
            "coverage_status": "Watch",
            "instrument_taxonomy_leaf": "指数增强",
            "instrument_taxonomy_path": "股票策略 / 量化多头 / 指数增强",
            "instrument_taxonomy_level_1": "股票策略",
            "instrument_taxonomy_level_2": "量化多头",
            "instrument_taxonomy_level_3": "指数增强",
        }

        summary_payload = connection.scalar(
            text(
                "SELECT payload_json FROM instrument_summary_read_model "
                "WHERE instrument_id = 'legacy-taxonomy'"
            )
        )
        if isinstance(summary_payload, str):
            summary_payload = json.loads(summary_payload)
        assert summary_payload["taxonomy"]["assigned_node_id"] == (
            "fund-private-equity-quant-index-enhanced"
        )
        assert summary_payload["instrument_attributes"] == row_attributes

        assert connection.scalar(
            text(
                "SELECT count(*) FROM instrument_taxonomy_node "
                "WHERE node_id = 'fund-private-equity-quant-long-300'"
            )
        ) == 0
        assert connection.scalar(
            text(
                "SELECT count(*) FROM instrument_taxonomy_node "
                "WHERE node_id = 'fund-private-equity-quant-index-enhanced' "
                "AND is_leaf = 1"
            )
        ) == 1
        definition = connection.execute(
            text(
                "SELECT options_json, required_for_monitoring "
                "FROM instrument_attribute_definition "
                "WHERE attribute_key = 'primary_geographic_exposure'"
            )
        ).mappings().one()
        options = definition["options_json"]
        if isinstance(options, str):
            options = json.loads(options)
        assert {"中国 A 股", "香港", "美国", "全球"}.issubset(options)
        assert bool(definition["required_for_monitoring"]) is True
    get_settings.cache_clear()


def test_name_grouping_migration_updates_registry_and_saved_views(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'name-grouping.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "20260823_0047")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE field_registry SET group_mode = 'discrete' "
                "WHERE field_key = 'instrument_name'"
            )
        )
        connection.execute(
            text(
                "UPDATE watchlist_view SET default_group_by = 'instrument_name' "
                "WHERE watchlist_view_id = (SELECT watchlist_view_id FROM watchlist_view LIMIT 1)"
            )
        )

    command.upgrade(config, "20260823_0048")
    get_settings.cache_clear()

    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT group_mode FROM field_registry "
                "WHERE field_key = 'instrument_name'"
            )
        ) == "none"
        assert connection.scalar(
            text(
                "SELECT count(*) FROM watchlist_view "
                "WHERE default_group_by = 'instrument_name'"
            )
        ) == 0
    get_settings.cache_clear()


def test_multi_asset_watchlist_semantics_migration_cleans_saved_fund_fields(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'multi-asset-watchlist.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "20260823_0048")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO instrument_detail (
                    instrument_id, instrument_type, detail_view_type,
                    instrument_name, is_active, metadata_json
                ) VALUES
                    ('migration-equity', 'equity', 'equity', 'Migration Equity', 1, '{}'),
                    ('migration-fund', 'public_fund', 'fund', 'Migration Fund', 1, '{}')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist (
                    watchlist_id, name, owner_type, owner_id,
                    is_default, is_shared, sort_order
                ) VALUES (
                    'migration-mixed', 'Migration Mixed', 'user', 'test', 0, 0, 0
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist_item (
                    watchlist_id, instrument_id, added_at
                ) VALUES
                    ('migration-mixed', 'migration-equity', CURRENT_TIMESTAMP),
                    ('migration-mixed', 'migration-fund', CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist_view (
                    watchlist_view_id, watchlist_id, name, kind,
                    default_sort_json, default_filters_json,
                    default_advanced_filter_json, default_group_by,
                    is_default, created_at
                ) VALUES (
                    'migration-mixed::legacy', 'migration-mixed', 'Legacy', 'table',
                    :sort, :filters, :advanced, 'attr.investment_edge_quality',
                    1, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "sort": json.dumps([{"field": "last_nav_date", "direction": "desc"}]),
                "filters": json.dumps(
                    {
                        "attr.investment_edge_quality": ["清晰且可持续"],
                        "attr.volatility_bucket": ["低波"],
                    }
                ),
                "advanced": json.dumps(
                    {
                        "type": "rule",
                        "field": "attr.style_stability",
                        "operator": "exists",
                    }
                ),
            },
        )
        for display_order, field_key in enumerate(
            (
                "attr.investment_edge_quality",
                "attr.volatility_bucket",
                "last_nav_date",
            )
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO watchlist_view_column (
                        watchlist_view_id, field_key, display_order, is_visible
                    ) VALUES (
                        'migration-mixed::legacy', :field_key, :display_order, 1
                    )
                    """
                ),
                {"field_key": field_key, "display_order": display_order},
            )
        for attribute_key, value in (
            ("investment_edge_quality", "清晰且可持续"),
            ("volatility_bucket", "低波"),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO instrument_attribute_value (
                        instrument_id, attribute_key, value_json, adopted_at
                    ) VALUES (
                        'migration-equity', :attribute_key, :value, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"attribute_key": attribute_key, "value": json.dumps(value)},
            )
        legacy_attributes = {
            "coverage_status": "Watch",
            "investment_edge_quality": "清晰且可持续",
            "volatility_bucket": "低波",
        }
        connection.execute(
            text(
                """
                INSERT INTO watchlist_row_read_model (
                    watchlist_id, instrument_id, instrument_type,
                    instrument_name, attributes_json, data_freshness_status
                ) VALUES (
                    'migration-mixed', 'migration-equity', 'equity',
                    'Migration Equity', :attributes, 'fresh'
                )
                """
            ),
            {"attributes": json.dumps(legacy_attributes)},
        )
        connection.execute(
            text(
                """
                INSERT INTO instrument_summary_read_model (
                    instrument_id, payload_json, data_freshness_status
                ) VALUES (
                    'migration-equity', :payload, 'fresh'
                )
                """
            ),
            {
                "payload": json.dumps(
                    {
                        "instrument_id": "migration-equity",
                        "fund_name": "Migration Equity",
                        "nav_snapshot": {"latest_nav": 10.0},
                        "instrument_attributes": legacy_attributes,
                    }
                )
            },
        )

    command.upgrade(config, "20260824_0049")
    get_settings.cache_clear()

    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE field_key = 'metric_as_of_date'"
            )
        ) == 1
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE field_key IN ('last_nav_date', 'attr.volatility_bucket', "
                "'attr.drawdown_control', 'attr.style_stability')"
            )
        ) == 0
        definition = connection.execute(
            text(
                "SELECT instrument_scope_json, applicability_json, is_groupable, "
                "required_for_monitoring FROM instrument_attribute_definition "
                "WHERE attribute_key = 'investment_edge_quality'"
            )
        ).mappings().one()
        scope = definition["instrument_scope_json"]
        applicability = definition["applicability_json"]
        if isinstance(scope, str):
            scope = json.loads(scope)
        if isinstance(applicability, str):
            applicability = json.loads(applicability)
        assert scope == ["public_fund", "private_fund"]
        assert applicability == {"coverage_status": ["Proposed", "Invested", "Paused"]}
        assert bool(definition["is_groupable"]) is False
        assert bool(definition["required_for_monitoring"]) is True
        coverage_status = connection.execute(
            text(
                "SELECT label, required_for_monitoring "
                "FROM instrument_attribute_definition "
                "WHERE attribute_key = 'coverage_status'"
            )
        ).mappings().one()
        assert coverage_status["label"] == "Investment Status"
        assert bool(coverage_status["required_for_monitoring"]) is True
        assert connection.scalar(
            text(
                "SELECT label FROM field_registry "
                "WHERE field_key = 'attr.coverage_status'"
            )
        ) == "Investment Status"
        assert connection.scalar(
            text(
                "SELECT label FROM field_category "
                "WHERE category_code = 'research_framework'"
            )
        ) == "Investment Research"
        assert connection.scalar(
            text(
                "SELECT label FROM field_category "
                "WHERE category_code = 'instrument_taxonomy'"
            )
        ) == "Instrument Taxonomy"
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_category "
                "WHERE category_code = 'product_taxonomy'"
            )
        ) == 0
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE category_code = 'product_taxonomy'"
            )
        ) == 0

        assert connection.execute(
            text(
                "SELECT field_key FROM watchlist_view_column "
                "WHERE watchlist_view_id = 'migration-mixed::legacy' "
                "ORDER BY display_order"
            )
        ).scalars().all() == ["metric_as_of_date"]
        migrated_view = connection.execute(
            text(
                "SELECT default_sort_json, default_filters_json, "
                "default_advanced_filter_json, default_group_by "
                "FROM watchlist_view "
                "WHERE watchlist_view_id = 'migration-mixed::legacy'"
            )
        ).mappings().one()
        migrated_sort = migrated_view["default_sort_json"]
        migrated_filters = migrated_view["default_filters_json"]
        migrated_advanced = migrated_view["default_advanced_filter_json"]
        if isinstance(migrated_sort, str):
            migrated_sort = json.loads(migrated_sort)
        if isinstance(migrated_filters, str):
            migrated_filters = json.loads(migrated_filters)
        if isinstance(migrated_advanced, str):
            migrated_advanced = json.loads(migrated_advanced)
        assert migrated_sort == [{"field": "metric_as_of_date", "direction": "desc"}]
        assert migrated_filters == {}
        assert migrated_advanced == {}
        assert migrated_view["default_group_by"] == "none"

        assert connection.scalar(
            text(
                "SELECT count(*) FROM instrument_attribute_value "
                "WHERE instrument_id = 'migration-equity'"
            )
        ) == 0
        row_attributes = connection.scalar(
            text(
                "SELECT attributes_json FROM watchlist_row_read_model "
                "WHERE instrument_id = 'migration-equity'"
            )
        )
        summary_payload = connection.scalar(
            text(
                "SELECT payload_json FROM instrument_summary_read_model "
                "WHERE instrument_id = 'migration-equity'"
            )
        )
        if isinstance(row_attributes, str):
            row_attributes = json.loads(row_attributes)
        if isinstance(summary_payload, str):
            summary_payload = json.loads(summary_payload)
        assert row_attributes == {"coverage_status": "Watch"}
        assert summary_payload["instrument_name"] == "Migration Equity"
        assert summary_payload["series_snapshot"] == {"latest_nav": 10.0}
        assert summary_payload["instrument_attributes"] == row_attributes
        assert "fund_name" not in summary_payload
        assert "nav_snapshot" not in summary_payload
    get_settings.cache_clear()


def test_watchlist_view_contract_cleanup_rewrites_legacy_fields(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-view-contract.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "20260813_0040")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        for legacy_key in ("asset_type", "asset_class"):
            connection.execute(
                text(
                    """
                    INSERT INTO field_registry (
                        field_key, label, description, category_code, data_type,
                        formatter_code, sort_mode, filter_mode, group_mode,
                        instrument_scope_json, product_scope_json,
                        availability_rule_json, source_domain, source_metric_code,
                        default_width, default_visible
                    )
                    SELECT
                        :legacy_key, :legacy_key, description, category_code, data_type,
                        formatter_code, sort_mode, filter_mode, group_mode,
                        instrument_scope_json, product_scope_json,
                        availability_rule_json, source_domain, source_metric_code,
                        default_width, default_visible
                    FROM field_registry
                    WHERE field_key = 'instrument_type'
                    """
                ),
                {"legacy_key": legacy_key},
            )
        connection.execute(
            text(
                """
                INSERT INTO watchlist (
                    watchlist_id, name, description, owner_type, owner_id,
                    is_default, is_shared, sort_order, created_at, updated_at
                ) VALUES (
                    'all-coverage', 'All Covered', NULL, 'system', 'watchlist',
                    1, 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO watchlist_view (
                    watchlist_view_id, watchlist_id, name, description, kind,
                    default_sort_json, default_filters_json,
                    default_advanced_filter_json, default_group_by, density,
                    is_default, created_at
                ) VALUES (
                    'all-coverage::overview', 'all-coverage', 'Overview', NULL,
                    'system', :sort_json, :filters_json, :advanced_json,
                    'taxonomy', 'standard', 1, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "sort_json": json.dumps(
                    [
                        {"field": "asset_type", "direction": "asc"},
                        {"field": "asset_class", "direction": "desc"},
                    ]
                ),
                "filters_json": json.dumps(
                    {"asset_type": ["fund"], "asset_class": ["legacy"]}
                ),
                "advanced_json": json.dumps(
                    {
                        "type": "group",
                        "operator": "and",
                        "conditions": [
                            {
                                "type": "rule",
                                "field": "asset_type",
                                "operator": "in",
                                "value": ["fund"],
                            },
                            {
                                "type": "rule",
                                "field": "instrument_class",
                                "operator": "exists",
                                "value": None,
                            },
                        ],
                    }
                ),
            },
        )
        for display_order, field_key in enumerate(
            ("instrument_type", "asset_type", "asset_class", "instrument_class")
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO watchlist_view_column (
                        watchlist_view_id, field_key, display_order, width,
                        is_visible, pin_side
                    ) VALUES (
                        'all-coverage::overview', :field_key, :display_order,
                        140, 1, NULL
                    )
                    """
                ),
                {"field_key": field_key, "display_order": display_order},
            )

    command.upgrade(config, "20260813_0041")
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE field_key IN ('asset_type', 'asset_class', 'instrument_class')"
            )
        ) == 0
        assert connection.execute(
            text(
                "SELECT field_key FROM watchlist_view_column "
                "WHERE watchlist_view_id = 'all-coverage::overview' "
                "ORDER BY display_order"
            )
        ).scalars().all() == ["instrument_type"]
        migrated_view = connection.execute(
            text(
                "SELECT default_sort_json, default_filters_json, "
                "default_advanced_filter_json, default_group_by "
                "FROM watchlist_view "
                "WHERE watchlist_view_id = 'all-coverage::overview'"
            )
        ).mappings().one()

        default_sort = migrated_view["default_sort_json"]
        default_filters = migrated_view["default_filters_json"]
        advanced_filter = migrated_view["default_advanced_filter_json"]
        if isinstance(default_sort, str):
            default_sort = json.loads(default_sort)
        if isinstance(default_filters, str):
            default_filters = json.loads(default_filters)
        if isinstance(advanced_filter, str):
            advanced_filter = json.loads(advanced_filter)
        assert default_sort == [{"field": "instrument_type", "direction": "asc"}]
        assert default_filters == {"instrument_type": ["fund"]}
        assert advanced_filter["conditions"] == [
            {
                "type": "rule",
                "field": "instrument_type",
                "operator": "in",
                "value": ["fund"],
            }
        ]
        assert migrated_view["default_group_by"] == "instrument_type"
    get_settings.cache_clear()


def test_holding_revision_migration_rejects_duplicate_input_hashes(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'duplicate-holdings.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "20260809_0031")

    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for snapshot_id in ("holding-revision-1", "holding-revision-2"):
            connection.execute(
                text(
                    """
                    INSERT INTO holding_snapshot (
                        holding_snapshot_id,
                        instrument_id,
                        as_of_date,
                        source_cutoff_at,
                        methodology_version,
                        input_hash,
                        calculated_at,
                        superseded_at,
                        is_current,
                        source_record_id
                    ) VALUES (
                        :snapshot_id,
                        'fund-duplicate-holding',
                        :as_of_date,
                        :source_cutoff_at,
                        'holding-v1',
                        'same-input',
                        :calculated_at,
                        :superseded_at,
                        0,
                        NULL
                    )
                    """
                ),
                {
                    "snapshot_id": snapshot_id,
                    "as_of_date": date(2026, 8, 1),
                    "source_cutoff_at": now,
                    "calculated_at": now,
                    "superseded_at": now,
                },
            )

    with pytest.raises(RuntimeError, match="duplicate .*instrument_id, input_hash"):
        command.upgrade(config, "20260809_0032")
    get_settings.cache_clear()


def test_primary_display_field_migration_reconciles_upgraded_seed_data(
    tmp_path,
    monkeypatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'primary-display-field.db'}"
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_URL", database_url)
    monkeypatch.setenv("PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA", "")

    from watchlist_app.core.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "20260809_0035")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO watchlist ("
                "watchlist_id, name, description, owner_type, owner_id, "
                "is_default, is_shared, sort_order, created_at, updated_at"
                ") VALUES ("
                "'migration-watchlist', 'Migration Watchlist', NULL, "
                "'user', 'migration-test', 1, 0, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO watchlist_view ("
                "watchlist_view_id, watchlist_id, name, description, kind, "
                "default_sort_json, default_filters_json, "
                "default_advanced_filter_json, default_group_by, density, "
                "is_default, created_at"
                ") VALUES ("
                "'migration-view', 'migration-watchlist', 'Migration View', "
                "NULL, 'table', '[]', '{}', '{}', NULL, NULL, 1, "
                "CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO watchlist_view_column ("
                "watchlist_view_id, field_key, display_order, width, "
                "is_visible, pin_side"
                ") VALUES ("
                "'migration-view', 'instrument_name', 0, 320, 1, NULL)"
            )
        )
        connection.execute(
            text(
                "UPDATE field_registry "
                "SET field_key = 'asset_name', "
                "source_metric_code = 'watchlist_row_read_model.asset_name' "
                "WHERE field_key = 'instrument_name'"
            )
        )
        connection.execute(
            text(
                "UPDATE watchlist_view_column SET field_key = 'asset_name' "
                "WHERE field_key = 'instrument_name'"
            )
        )
        view_id = "migration-view"
        connection.execute(
            text(
                "UPDATE watchlist_view "
                "SET default_sort_json = :sort_json, "
                "default_filters_json = :filters_json, "
                "default_group_by = 'asset_name' "
                "WHERE watchlist_view_id = :view_id"
            ),
            {
                "sort_json": '[{"field":"asset_name","direction":"asc"}]',
                "filters_json": '{"asset_name":"Audit"}',
                "view_id": view_id,
            },
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE field_key = 'instrument_name' "
                "AND source_metric_code = "
                "'watchlist_row_read_model.instrument_name'"
            )
        ) == 1
        assert connection.scalar(
            text(
                "SELECT count(*) FROM field_registry "
                "WHERE field_key = 'asset_name'"
            )
        ) == 0
        assert connection.scalar(
            text(
                "SELECT count(*) FROM watchlist_view_column "
                "WHERE field_key = 'asset_name'"
            )
        ) == 0
        migrated_view = connection.execute(
            text(
                "SELECT default_sort_json, default_filters_json, default_group_by "
                "FROM watchlist_view WHERE watchlist_view_id = :view_id"
            ),
            {"view_id": view_id},
        ).mappings().one()
        default_sort = migrated_view["default_sort_json"]
        default_filters = migrated_view["default_filters_json"]
        if isinstance(default_sort, str):
            default_sort = json.loads(default_sort)
        if isinstance(default_filters, str):
            default_filters = json.loads(default_filters)
        assert default_sort == [
            {"field": "instrument_name", "direction": "asc"}
        ]
        assert default_filters == {"instrument_name": "Audit"}
        assert migrated_view["default_group_by"] == "none"
    get_settings.cache_clear()
