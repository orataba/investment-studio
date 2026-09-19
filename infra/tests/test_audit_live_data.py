from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
from types import ModuleType

import pytest
from alembic.script import ScriptDirectory


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = REPOSITORY_ROOT / "infra" / "scripts" / "audit_live_data.py"
FLAT_TABLE_RELATIONS = {
    ("instrument_data", "instrument_market_data"),
    ("instrument_data", "instrument"),
    ("instrument_data", "corporate_action_event"),
    ("watchlist", "instrument_chart_read_model"),
    ("portfolio", "transaction_record"),
    ("portfolio", "portfolio_daily_snapshot"),
    ("portfolio", "portfolio_daily_holding_snapshot"),
    ("portfolio", "derivative_contract_record"),
    ("portfolio", "option_delivery_link"),
    ("portfolio", "target_set_record"),
    ("portfolio", "target_set_line_record"),
    ("portfolio", "taxonomy_record"),
    ("portfolio", "taxonomy_assignment_record"),
    *{("market_data", name) for name in ("datasets", "batches", "files", "current", "snapshots")},
    *{("market_text", name) for name in ("document_version", "import_receipt", "entity", "document_entity", "document_event")},
    ("briefing", "report"),
}


@pytest.fixture()
def audit_module() -> ModuleType:
    module_name = "investment_studio_test_audit_live_data"
    spec = importlib.util.spec_from_file_location(module_name, AUDIT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_flat_table_profile_accepts_only_final_heads(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_heads = {
        "identity": "20260919_0002",
        "instrument_data": "20260908_0035",
        "data_ingestion": "20260904_0009",
        "portfolio": "20260908_0063",
        "watchlist": "20260914_0058",
        "market_data": "studio_market_0002",
        "briefing": "20260908_0003",
    }
    monkeypatch.setattr(
        audit_module,
        "_version_state",
        lambda _cursor, component: {
            "row_count": 1,
            "version": expected_heads[component],
            "table_present": True,
        },
    )
    monkeypatch.setattr(
        audit_module,
        "_relation_kind",
        lambda _cursor, schema, relation: (
            "r" if (schema, relation) in FLAT_TABLE_RELATIONS else None
        ),
    )

    profile = audit_module._detect_schema_profile(object())

    assert profile.family == "flat-table"
    assert profile.status == "supported"
    assert "final" in profile.reason
    assert audit_module.FINAL_FLAT_TABLE_HEADS == expected_heads
    assert not hasattr(audit_module, "TEMPORARY_MIGRATION_SOURCE_HEAD_PAIR")
    assert not hasattr(audit_module, "SUPPORTED_FLAT_TABLE_HEAD_PAIRS")
    assert not hasattr(audit_module, "OVERHAUL_HEADS")
    assert not hasattr(audit_module, "_run_overhaul_audit")


def test_audit_heads_match_migration_sources(audit_module: ModuleType) -> None:
    migration_roots = {
        "identity": "home/backend/alembic",
        "instrument_data": "shared-data/instruments/alembic",
        "data_ingestion": "shared-data/alembic",
        "portfolio": "apps/portfolio/backend/alembic",
        "watchlist": "apps/watchlist/backend/alembic",
        "market_data": "shared-data/market/alembic",
        "briefing": "apps/briefing/backend/alembic",
    }
    for component, path in migration_roots.items():
        migrations = ScriptDirectory(str(REPOSITORY_ROOT / path))
        assert migrations.get_heads() == [audit_module.FINAL_FLAT_TABLE_HEADS[component]]


def test_audit_contract_names_cover_registry_0019(
    audit_module: ModuleType,
) -> None:
    assert len(audit_module.AUDIT_CHECK_NAMES) == 55
    assert {"numeric_publication_catalog_contract", "numeric_complete_snapshot_contract",
            "numeric_dataset_clock_contract", "market_text_version_contract",
            "briefing_frozen_source_contract"} <= set(audit_module.AUDIT_CHECK_NAMES)
    assert "schema_identifier_contract" in audit_module.AUDIT_CHECK_NAMES
    assert (
        "instrument_type_listed_security_identity_contract"
        in audit_module.AUDIT_CHECK_NAMES
    )
    assert "fmp_equity_catalog_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "fmp_etf_catalog_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "watchlist_system_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "watchlist_taxonomy_type_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "instrument_crypto_spot_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "watchlist_crypto_scope_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "watchlist_field_identity_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "watchlist_group_by_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "watchlist_saved_view_field_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "watchlist_taxonomy_history_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "portfolio_account_category_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "portfolio_inception_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "portfolio_instrument_reference_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "derivative_registry_boundary" in audit_module.AUDIT_CHECK_NAMES
    assert (
        "portfolio_derivative_contract_integrity"
        in audit_module.AUDIT_CHECK_NAMES
    )
    assert (
        "portfolio_option_delivery_link_integrity"
        in audit_module.AUDIT_CHECK_NAMES
    )
    assert "instrument_quote_policy_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "market_data_price_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "market_data_fx_identity_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "market_data_fx_source_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "market_data_fx_freshness_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "fund_nav_current_projection_contract" in audit_module.AUDIT_CHECK_NAMES
    assert (
        "watchlist_index_return_semantics_contract"
        in audit_module.AUDIT_CHECK_NAMES
    )
    assert "held_fund_recent_total_return_coverage" in audit_module.AUDIT_CHECK_NAMES
    assert "price_bar_contract" in audit_module.AUDIT_CHECK_NAMES
    assert "holding_valuation_basis_contract" in audit_module.AUDIT_CHECK_NAMES
    assert (
        "analytics_scope_missing_effective_selection"
        in audit_module.AUDIT_CHECK_NAMES
    )
    assert "analytics_scope_incomplete_configuration" in audit_module.AUDIT_CHECK_NAMES
    assert "cash_cumulative_nav_in_return_policy" not in audit_module.AUDIT_CHECK_NAMES
    assert not hasattr(audit_module, "CASH_CUMULATIVE_NAV_BASES_SQL")
    assert "fx-usd-hkd" in audit_module.MAINTAINED_FX_IDENTITIES_SQL
    assert "fx-usd-cny" in audit_module.MAINTAINED_FX_IDENTITIES_SQL
    assert audit_module.QUOTE_SELECTION_POLICY_ROLES == (
        "trading",
        "valuation",
        "total_return",
        "chart",
        "reference",
    )
    assert (
        audit_module.FUND_NAV_PROJECTION_METHOD_VERSION
        == "fund_nav_reinvestment_projection/v7"
    )
    assert (
        "constraint",
        "watchlist",
        "uq_watchlist_item_watchlist_asset",
        "uq_watchlist_item_watchlist_instrument",
    ) in audit_module.SCHEMA_IDENTIFIER_RENAMES
    assert (
        "index",
        "watchlist",
        "uq_watchlist_item_watchlist_asset",
        "uq_watchlist_item_watchlist_instrument",
    ) in audit_module.SCHEMA_IDENTIFIER_RENAMES
    assert audit_module.FUND_NAV_PROJECTION_METHOD_VERSION_SQL == (
        "'fund_nav_reinvestment_projection/v7'"
    )


def test_migration_source_heads_are_unsupported_after_cutover(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions = {
        "instrument_data": "20260712_0007",
        "data_ingestion": "20260716_0002",
        "portfolio": "20260711_0032",
        "watchlist": "20260728_0030",
    }
    monkeypatch.setattr(
        audit_module,
        "_version_state",
        lambda _cursor, component: {
            "row_count": 1,
            "version": versions.get(component, audit_module.FINAL_FLAT_TABLE_HEADS[component]),
            "table_present": True,
        },
    )
    monkeypatch.setattr(
        audit_module,
        "_relation_kind",
        lambda _cursor, schema, relation: (
            "r" if (schema, relation) in FLAT_TABLE_RELATIONS else None
        ),
    )

    profile = audit_module._detect_schema_profile(object())

    assert profile.family == "unsupported"
    assert profile.status == "unsupported"


def test_failed_overhaul_head_and_shape_are_unsupported(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_heads = {
        "instrument_data": "20260714_0014",
        "data_ingestion": "20260716_0002",
        "portfolio": "20260714_0045",
        "watchlist": "20260728_0030",
    }
    monkeypatch.setattr(
        audit_module,
        "_version_state",
        lambda _cursor, component: {
            "row_count": 1,
            "version": failed_heads.get(component, audit_module.FINAL_FLAT_TABLE_HEADS[component]),
            "table_present": True,
        },
    )
    monkeypatch.setattr(
        audit_module,
        "_relation_kind",
        lambda _cursor, schema, relation: (
            "r"
            if (schema, relation) in FLAT_TABLE_RELATIONS
            or (schema, relation) == ("instrument_data", "quote_series")
            else None
        ),
    )

    profile = audit_module._detect_schema_profile(object())

    assert profile.family == "unsupported"
    assert profile.status == "unsupported"


def test_twr_audit_cte_projects_daily_twr(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = audit_module.SchemaProfile(
        family="flat-table",
        status="supported",
        versions={
            "portfolio": {
                "row_count": 1,
                "version": "20260716_0040",
                "table_present": True,
            }
        },
        reason="test",
        capabilities={},
    )
    queries: list[str] = []

    class FakeContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self

    monkeypatch.setattr(audit_module.psycopg, "connect", lambda *_args, **_kwargs: FakeContext())
    monkeypatch.setattr(audit_module, "_begin_read_only", lambda _cursor: None)
    monkeypatch.setattr(audit_module, "_detect_schema_profile", lambda _cursor: profile)
    monkeypatch.setattr(audit_module, "_column_exists", lambda *_args, **_kwargs: False)

    def capture_scalar(_cursor, query: str):
        queries.append(query)
        return 0

    monkeypatch.setattr(audit_module, "_scalar", capture_scalar)

    database_url = "postgresql+psycopg://" + "user:" + "secret" + "@localhost/audit_db"
    audit_module._run_flat_table_audit(database_url)

    nav_query = next(
        query
        for query in queries
        if "materialized_pending_settlement" in query
    )
    assert "coalesce(holdings.market_value, 0)" in nav_query
    assert "snapshot.valuation_coverage_state = 'complete'" in nav_query
    assert "+ (snapshot.snapshot_json ->> 'pending_settlement')" not in nav_query
    assert (
        "coalesce(holdings.materialized_pending_settlement, 0)"
        in nav_query
    )
    valuation_query = next(
        query
        for query in queries
        if "selected_quote.expected_price" in query
    )
    assert "snapshot.instrument_id NOT LIKE 'pending:%'" in valuation_query
    assert "snapshot.holding_kind = 'position'" in valuation_query
    assert "= 'market_quote'" in valuation_query
    assert "quote.metric_family = holdings.holding_json ->> 'quote_metric_family'" in valuation_query
    assert "quote.currency = holdings.currency" in valuation_query
    holding_basis_query = next(
        query
        for query in queries
        if "premium_liability" in query and "carried_cost" in query
    )
    assert "holding.last_price IS NULL" in holding_basis_query
    assert "holding.last_price IS NOT NULL" in holding_basis_query

    derivative_contract_query = next(
        query
        for query in queries
        if "portfolio.derivative_contract_record contract" in query
        and "contract.terms_json" in query
    )
    assert "contract.terms_json::jsonb" in derivative_contract_query
    assert "? 'settlement_type'" in derivative_contract_query
    assert "invalid_option_lifecycle" in derivative_contract_query
    assert "option_long_cash_settlement" in derivative_contract_query
    assert "option_writer_cash_settlement" in derivative_contract_query
    assert "annual_coupon_rate_pct" in derivative_contract_query
    assert "final_observation_date" in derivative_contract_query
    assert "-> 'underlyings'" in derivative_contract_query
    assert "underlying_instrument_ids" not in derivative_contract_query
    assert "deliverable_instrument_ids" not in derivative_contract_query
    assert "barrier_type" not in derivative_contract_query

    option_delivery_query = next(
        query
        for query in queries
        if "portfolio.option_delivery_link link" in query
    )
    assert "FULL OUTER JOIN physical_outcome option_txn" in option_delivery_query
    assert "option_long_exercise" in option_delivery_query
    assert "option_writer_assignment" in option_delivery_query
    assert "stock_source_quantity IS DISTINCT FROM" in option_delivery_query
    assert "contract_multiplier" in option_delivery_query
    assert "stock_source_price IS DISTINCT FROM" in option_delivery_query
    assert "stock_settlement_cash_account_id IS NULL" in option_delivery_query

    analytics_selection_query = next(
        query
        for query in queries
        if "analytics_taxonomy_selection_record" in query
        and "default_planning_taxonomy_id" in query
    )
    assert "portfolio.default_planning_taxonomy_id IS NOT NULL" in (
        analytics_selection_query
    )

    twr_query = next(query for query in queries if "AS recomputed_twr" in query)
    linked_projection = re.search(
        r"WITH linked AS \(\s*SELECT(?P<projection>.*?)"
        r"FROM portfolio\.portfolio_daily_snapshot",
        twr_query,
        flags=re.DOTALL,
    )
    assert linked_projection is not None
    assert re.search(
        r"^\s*daily_twr,\s*$",
        linked_projection.group("projection"),
        flags=re.MULTILINE,
    )
    assert "snapshot_json ->> 'return_chain_continuous'" in twr_query
    drawdown_query = next(
        query
        for query in queries
        if "AS peak_index" in query and "drawdown" in query
    )
    assert re.search(
        r"greatest\(\s*1\.0,\s*max\(1 \+ cumulative_twr\)",
        drawdown_query,
        flags=re.DOTALL,
    )
    fund_nav_projection_query = next(
        query
        for query in queries
        if "instrument_data.fund_nav_current_projection" in query
    )
    assert "fund_nav_reinvestment_projection/v7" in fund_nav_projection_query
    assert "lifecycle_state_json" in fund_nav_projection_query
    assert "{FUND_NAV_PROJECTION_METHOD_VERSION_SQL}" not in (
        fund_nav_projection_query
    )
    held_fund_coverage_query = next(
        query
        for query in queries
        if "held_fund_recent_total_return_coverage" in query
        or (
            "latest_portfolio_dates" in query
            and "total_return_count * 2 < official_count" in query
        )
    )
    assert "held.reference_date - 120" in held_fund_coverage_query
    assert "latest_official_date - 14" in held_fund_coverage_query
    index_semantics_query = next(
        query
        for query in queries
        if "watchlist.instrument_chart_read_model" in query
    )
    assert "configured_return_kind" in index_semantics_query
    assert "published_return_kind" in index_semantics_query
    assert "IS DISTINCT FROM expected_return_kind" in index_semantics_query
    assert "IS DISTINCT FROM 'nav_with_dividend'" in index_semantics_query
    saved_view_query = next(
        query for query in queries if "WITH RECURSIVE advanced_nodes" in query
    )
    assert "watchlist.watchlist_view_column" in saved_view_query
    assert "asset_type" in saved_view_query
    assert "asset_class" in saved_view_query
    assert "instrument_class" in saved_view_query
    assert "field.filter_mode" in saved_view_query
    assert "field.sort_mode" in saved_view_query
    taxonomy_type_query = next(
        query for query in queries if "invalid_nodes AS" in query
    )
    assert "node.path_node_ids_json ->> 0" in taxonomy_type_query
    assert "WHEN 'XNAS' THEN 'equity-market-us'" in taxonomy_type_query
    assert "WHEN 'XHKG' THEN 'equity-market-hk'" in taxonomy_type_query
    assert "WHEN 'XSHG' THEN 'equity-market-cn-a'" in taxonomy_type_query
    assert "WHEN 'XLON' THEN 'equity-market-eu'" in taxonomy_type_query
    assert "equity-exchange-xnas" not in taxonomy_type_query
    listed_identity_query = next(
        query
        for query in queries
        if "instrument.instrument_type = 'etf'" in query
        and "identifier.identifier_type = 'provider_symbol'" in query
    )
    assert "'XASE', 'ARCX', 'BATS'" in listed_identity_query
    etf_catalog_query = next(
        query
        for query in queries
        if "data_ingestion.fmp_etf_catalog catalog" in query
        and "required_exchange" in query
    )
    assert "catalog.exchange_code IN ('XASE', 'ARCX')" in etf_catalog_query
    assert "'XASE', 'ARCX', 'BATS'" in etf_catalog_query
    missing_selection_query = next(
        query
        for query in queries
        if "analytics_taxonomy_selection_record" in query
        and "taxonomy_configuration_revision" not in query
    )
    assert "superseded_by_selection_id IS NULL" in missing_selection_query
    incomplete_configuration_query = next(
        query for query in queries if "taxonomy_configuration_revision" in query
    )
    assert "taxonomy_node_id = '__root__'" in incomplete_configuration_query
    assert "taxonomy_node_id = '__unassigned__'" in incomplete_configuration_query


@pytest.mark.parametrize(
    ("nav", "coverage", "payload", "holdings", "expected_error"),
    [
        (100, "complete", {"pending_settlement": 0}, [("equity", 100)], 0),
        (100, "complete", {"pending_settlement": 10}, [("equity", 90), ("pending:cash", 10)], 0),
        (None, "unavailable", {"valuation_blocked_reason": "Required market data missing: fund valuation price"}, [], 0),
        (100, "complete", {}, [("equity", 100)], 1),
        (100, "complete", {"pending_settlement": 0}, [("equity", 90)], 10),
        (100, "complete", {"pending_settlement": 10}, [("equity", 100)], 10),
        (None, "complete", {"pending_settlement": 0}, [], 1),
        (None, "unavailable", {"pending_settlement": 0}, [], 1),
        (None, "unavailable", {"valuation_blocked_reason": "Required market data missing: fund valuation price"}, [("equity", 0)], 1),
    ],
    ids=[
        "valued", "pending-reconciled", "terminal-marker", "missing-pending",
        "nav-imbalance", "pending-imbalance", "complete-null-nav",
        "unexplained-null-nav", "terminal-with-holdings",
    ],
)
def test_nav_audit_preserves_reconciliation_around_terminal_markers(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    nav: float | None,
    coverage: str,
    payload: dict[str, object],
    holdings: list[tuple[str, float]],
    expected_error: float,
) -> None:
    profile = audit_module.SchemaProfile(
        family="flat-table", status="supported", versions={}, reason="test", capabilities={}
    )

    class FakeContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self

    monkeypatch.setattr(audit_module.psycopg, "connect", lambda *_args, **_kwargs: FakeContext())
    monkeypatch.setattr(audit_module, "_begin_read_only", lambda _cursor: None)
    monkeypatch.setattr(audit_module, "_detect_schema_profile", lambda _cursor: profile)
    monkeypatch.setattr(audit_module, "_column_exists", lambda *_args, **_kwargs: False)

    # Execute the production reconciliation query over a reliable prefix and
    # its following row. SQLite supports the SQL/JSON used here; only GREATEST
    # needs registration, leaving the accounting predicates unchanged.
    with sqlite3.connect(":memory:") as database:
        database.create_function("greatest", -1, max)
        database.execute("ATTACH DATABASE ':memory:' AS portfolio")
        database.executescript("""
            CREATE TABLE portfolio.portfolio_daily_snapshot (
                portfolio_id TEXT, as_of_date TEXT, nav NUMERIC,
                valuation_coverage_state TEXT, snapshot_json TEXT
            );
            CREATE TABLE portfolio.portfolio_daily_holding_snapshot (
                portfolio_id TEXT, as_of_date TEXT, instrument_id TEXT,
                market_value_base NUMERIC
            );
        """)
        database.executemany(
            "INSERT INTO portfolio.portfolio_daily_snapshot VALUES (?, ?, ?, ?, ?)",
            [
                ("p", "2026-08-03", 100, "complete", '{"pending_settlement":0}'),
                ("p", "2026-08-04", nav, coverage, json.dumps(payload)),
            ],
        )
        database.executemany(
            "INSERT INTO portfolio.portfolio_daily_holding_snapshot VALUES (?, ?, ?, ?)",
            [("p", "2026-08-03", "equity", 100)]
            + [("p", "2026-08-04", instrument_id, value) for instrument_id, value in holdings],
        )

        def run_nav_query(_cursor, query: str):
            if "materialized_pending_settlement" in query:
                return database.execute(query).fetchone()[0]
            return 0

        monkeypatch.setattr(audit_module, "_scalar", run_nav_query)
        checks = audit_module._run_flat_table_audit("postgresql://localhost/audit_test")

    reconciliation = next(check for check in checks if check.name == "portfolio_nav_reconciliation")
    assert reconciliation.value == expected_error
    assert reconciliation.status == ("pass" if expected_error == 0 else "fail")


def test_cli_requires_explicit_database_configuration(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("INVESTMENT_STUDIO_LOCAL_DATABASE_URL", raising=False)
    monkeypatch.delenv("INVESTMENT_STUDIO_DATA_DATABASE_URL", raising=False)

    with pytest.raises(SystemExit) as error:
        audit_module.main([])

    assert error.value.code == 2
    assert "a database URL is required" in capsys.readouterr().err


def test_environment_database_url_requires_canonical_local_target(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_url = "postgresql+psycopg://platform/database"
    local_url = "postgresql+psycopg://local/database"
    monkeypatch.delenv("INVESTMENT_STUDIO_LOCAL_DATABASE_URL", raising=False)
    monkeypatch.setenv("INVESTMENT_STUDIO_DATA_DATABASE_URL", platform_url)
    assert audit_module._environment_database_url() is None

    monkeypatch.setenv("INVESTMENT_STUDIO_LOCAL_DATABASE_URL", local_url)
    assert audit_module._environment_database_url() == local_url


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_normalizes_sqlalchemy_url_and_reports_database_name(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    as_json: bool,
) -> None:
    profile = audit_module.SchemaProfile(
        family="flat-table",
        status="supported",
        versions={},
        reason="test",
        capabilities={},
    )
    checks = [
        audit_module.AuditCheck(
            name="fixture",
            status="pass",
            value=0,
            limit=0,
            detail="fixture",
        )
    ]
    captured_urls: list[str] = []

    def fake_run_audit_report(database_url: str):
        captured_urls.append(database_url)
        return profile, checks

    monkeypatch.setattr(audit_module, "run_audit_report", fake_run_audit_report)
    database_url = "postgresql+psycopg://" + "user:" + "secret" + "@localhost/audit_fixture"
    monkeypatch.setenv("INVESTMENT_STUDIO_LOCAL_DATABASE_URL", database_url)
    arguments = []
    if as_json:
        arguments.append("--json")

    assert audit_module.main(arguments) == 0

    output = capsys.readouterr().out
    assert captured_urls == [database_url.replace("postgresql+psycopg://", "postgresql://", 1)]
    if as_json:
        assert json.loads(output)["database_name"] == "audit_fixture"
    else:
        assert "database_name: audit_fixture" in output


@pytest.mark.parametrize('other_status,expected_exit', [(None, 0), ('warning', 1), ('fail', 1)])
def test_unassigned_readiness_is_disclosed_without_bypassing_integrity_gate(
    audit_module, monkeypatch, capsys, other_status, expected_exit,
):
    monkeypatch.setattr(audit_module, '_scalar', lambda _cursor, _query: 7)
    readiness = audit_module._planning_holdings_readiness_check(object())
    assert readiness.status == 'info'
    assert readiness.value == 7
    assert readiness.limit == 'root_solve_ready_requires_zero'
    assert 'not ready' in readiness.detail
    checks = [readiness]
    if other_status:
        checks.append(audit_module.AuditCheck(name='analytics_scope_incomplete_configuration',
            status=other_status, value=1, limit=0, detail='Missing required scope policy.'))
    profile = audit_module.SchemaProfile(family='flat-table', status='supported',
        versions={}, reason='fixture', capabilities={})
    monkeypatch.setattr(audit_module, 'run_audit_report', lambda _url: (profile, checks))
    monkeypatch.setenv('INVESTMENT_STUDIO_LOCAL_DATABASE_URL', 'postgresql://localhost/readiness_fixture')
    assert audit_module.main(['--json', '--fail-on-warning']) == expected_exit
    report = json.loads(capsys.readouterr().out)
    assert report['informational_count'] == 1
    assert report['checks'][0]['value'] == 7


def test_system_directory_audit_detects_missing_instruments_and_preserves_type_boundaries(audit_module):
    with sqlite3.connect(':memory:') as db:
        db.executescript("""
            ATTACH ':memory:' AS instrument_data;
            ATTACH ':memory:' AS watchlist;
            CREATE TABLE instrument_data.instrument (instrument_id TEXT, instrument_type TEXT, lifecycle_state_json TEXT);
            CREATE TABLE watchlist.watchlist (watchlist_id TEXT, name TEXT, owner_type TEXT, owner_id TEXT, is_default BOOL, is_shared BOOL, sort_order INT);
            CREATE TABLE watchlist.watchlist_item (watchlist_id TEXT, instrument_id TEXT);
            CREATE TABLE watchlist.watchlist_row_read_model (watchlist_id TEXT, instrument_id TEXT, instrument_type TEXT);
        """)
        for wid, name, order in [('all-instruments','All Instruments',0),('index','Index',1),
                                  ('all-public-funds','All 公募',2),('all-private-funds','All 私募',3)]:
            db.execute('INSERT INTO watchlist.watchlist VALUES (?, ?, ?, ?, ?, ?, ?)', (wid,name,'system','watchlist',True,True,order))
        db.executemany('INSERT INTO instrument_data.instrument VALUES (?, ?, ?)', [
            ('btc','crypto','{"status":"active"}'), ('stock','equity','{}'),
            ('archived','equity','{"status":"archived"}'), ('cash','cash','{}')])
        for iid,kind in [('btc','crypto'),('stock','equity')]:
            db.execute('INSERT INTO watchlist.watchlist_item VALUES (?, ?)', ('all-instruments',iid))
            db.execute('INSERT INTO watchlist.watchlist_row_read_model VALUES (?, ?, ?)', ('all-instruments',iid,kind))
        count = lambda: db.execute(audit_module._watchlist_system_contract_query()).fetchone()[0]
        assert count() == 0
        db.execute("DELETE FROM watchlist.watchlist_item WHERE instrument_id='stock'")
        db.execute("DELETE FROM watchlist.watchlist_row_read_model WHERE instrument_id='stock'")
        assert count() == 1  # A missing registered asset must not disappear silently.
        db.execute("INSERT INTO watchlist.watchlist_item VALUES ('all-instruments','stock')")
        assert count() == 1  # Membership without its read model is also incomplete.
        db.execute("INSERT INTO watchlist.watchlist_row_read_model VALUES ('all-instruments','stock','equity')")
        db.execute("INSERT INTO watchlist.watchlist_item VALUES ('all-instruments','archived')")
        db.execute("INSERT INTO watchlist.watchlist_row_read_model VALUES ('all-instruments','archived','equity')")
        assert count() == 1
        db.execute("DELETE FROM watchlist.watchlist_item WHERE instrument_id='archived'")
        db.execute("DELETE FROM watchlist.watchlist_row_read_model WHERE instrument_id='archived'")
        db.execute("UPDATE watchlist.watchlist_row_read_model SET instrument_type='etf' WHERE instrument_id='btc'")
        assert count() == 1  # Mixed directories still reconcile every row's actual type.
        db.execute("UPDATE watchlist.watchlist_row_read_model SET instrument_type='crypto' WHERE instrument_id='btc'")
        db.execute("UPDATE watchlist.watchlist SET sort_order=0 WHERE watchlist_id='index'")
        assert count() == 1


def _sqlite_crypto_audit_query(audit_module):
    # Translate only PostgreSQL JSON containment and the clock to SQLite fixture
    # functions; execute the production joins and financial predicates unchanged.
    query = audit_module._instrument_crypto_spot_contract_query()
    query = query.replace("CAST(CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS DATE)", "'2026-09-08'")
    query = query.replace("(VALUES ('trading'), ('valuation'), ('total_return'), ('chart'), ('reference')) roles(role_name)",
                          "(SELECT 'trading' AS role_name UNION ALL SELECT 'valuation' UNION ALL SELECT 'total_return' UNION ALL SELECT 'chart' UNION ALL SELECT 'reference') roles")
    return re.sub(r"\(i.quote_selection_policy_json -> roles.role_name\)::jsonb\s+<@ '\[\"close\", \"last\"\]'::jsonb",
                  "json_subset(i.quote_selection_policy_json -> roles.role_name, '[\"close\", \"last\"]')", query)


@pytest.mark.parametrize('defect', ['currency','type','calendar','return_basis','provider','utc_day','volume','adjustment'])
def test_crypto_audit_rejects_wrong_vehicle_clocks_and_undocumented_bar_semantics(audit_module, defect):
    with sqlite3.connect(':memory:') as db:
        db.create_function('json_subset',2,lambda a,b: set(json.loads(a)) <= set(json.loads(b)))
        db.executescript("""
            ATTACH ':memory:' AS instrument_data;
            CREATE TABLE instrument_data.instrument (instrument_id TEXT, instrument_type TEXT, currency TEXT, exchange_code TEXT, source_settings_json TEXT, quote_selection_policy_json TEXT);
            CREATE TABLE instrument_data.instrument_identifier (instrument_id TEXT, identifier_type TEXT, identifier_value TEXT);
            CREATE TABLE instrument_data.instrument_market_data (instrument_id TEXT, metric_family TEXT, quote_basis TEXT, currency TEXT, provider TEXT, as_of_date TEXT);
            CREATE TABLE instrument_data.instrument_price_bar (instrument_id TEXT, provider TEXT, as_of_date TEXT, adjustment_factor TEXT, volume TEXT, volume_unit TEXT, turnover TEXT, turnover_unit TEXT);
        """)
        source = {'source_mode':'api','source_api_profile':'fmp','market_calendar':'24/7','expected_frequency':'daily','return_semantics':'price_return'}
        policy = {role:['close','last'] for role in ('trading','valuation','total_return','chart','reference')}
        db.execute('INSERT INTO instrument_data.instrument VALUES (?, ?, ?, ?, ?, ?)', ('btcusd','crypto','USD',None,json.dumps(source),json.dumps(policy)))
        db.execute("INSERT INTO instrument_data.instrument_identifier VALUES ('btcusd','provider_symbol','fmp:BTCUSD')")
        db.execute("INSERT INTO instrument_data.instrument_market_data VALUES ('btcusd','price','close','USD','fmp:market_series_daily:BTCUSD','2026-09-07')")
        db.execute("INSERT INTO instrument_data.instrument_price_bar VALUES ('btcusd','fmp:market_series_daily:BTCUSD','2026-09-07',NULL,NULL,NULL,NULL,NULL)")
        query = _sqlite_crypto_audit_query(audit_module)
        assert db.execute(query).fetchone()[0] == 0
        if defect == 'currency': db.execute("UPDATE instrument_data.instrument SET currency='USDT'")
        elif defect == 'type': db.execute("UPDATE instrument_data.instrument SET instrument_type='etf'")
        elif defect == 'calendar':
            source['market_calendar']='XNYS'
            db.execute('UPDATE instrument_data.instrument SET source_settings_json=?', (json.dumps(source),))
        elif defect == 'return_basis':
            policy['total_return']=['adjusted_close']
            db.execute('UPDATE instrument_data.instrument SET quote_selection_policy_json=?', (json.dumps(policy),))
        elif defect == 'provider': db.execute("UPDATE instrument_data.instrument_identifier SET identifier_value='fmp:BTC'")
        elif defect == 'utc_day': db.execute("UPDATE instrument_data.instrument_market_data SET as_of_date='2026-09-08'")
        elif defect == 'volume': db.execute("UPDATE instrument_data.instrument_price_bar SET volume='1000',volume_unit='shares'")
        elif defect == 'adjustment': db.execute("UPDATE instrument_data.instrument_price_bar SET adjustment_factor='1'")
        assert db.execute(query).fetchone()[0] > 0
