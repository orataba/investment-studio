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
    ("watchlist", "research_guidance"),
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
    *{("market_text", name) for name in ("document_version", "import_receipt", "entity", "document_entity", "document_event", "collection_receipt")},
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
        "instrument_data": "20261008_0037",
        "data_ingestion": "20260904_0009",
        "portfolio": "20261012_0073",
        "watchlist": "20261014_0072",
        "market_data": "studio_market_0003",
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


@pytest.mark.parametrize("case,expected_failures", [
    ("unknown_unavailable_with_observed_price", 0),
    ("confirmed_price_return", 0), ("confirmed_total_return", 0),
    ("adjusted_total_return", 0),
    ("missing_chart", 1), ("missing_payload_contract", 1),
    ("confirmed_price_return_missing_basis", 1), ("confirmed_total_return_missing_basis", 1),
    ("legacy_unknown_close", 1), ("unknown_chart_points", 1),
    ("unknown_return_intervals", 1), ("unknown_return_wealth", 1),
    ("unknown_research_points", 1), ("unknown_published_return_kind", 1),
    ("unknown_latest_total_return", 1), ("confirmed_kind_mismatch", 1),
])
def test_index_audit_distinguishes_unavailable_analysis_from_missing_or_mislabeled_data(
    audit_module: ModuleType, case: str, expected_failures: int,
) -> None:
    metadata = {"quote_basis": None, "return_kind": None, "basis_type": None,
                "return_series_status": "unavailable"}
    payload = {
        "selected_series": dict(metadata),
        "series": [{"points": []}],
        "latest_values": {"valuation": {"value": 110}, "total_return": None},
        "price_series": {"quote_basis": "close", "points": [{"date": "2026-10-01", "value": 110}]},
        "return_series": {"metadata": dict(metadata), "intervals": [], "wealth_points": []},
        "research_returns": {"metadata": dict(metadata), "points": [], "intervals": [], "wealth_points": []},
    }
    configured_kind = None
    if case in {"confirmed_price_return", "confirmed_total_return", "confirmed_kind_mismatch", "adjusted_total_return"}:
        configured_kind = "price_return" if case != "confirmed_total_return" else "total_return"
        published_kind = "total_return" if case in {"confirmed_total_return", "confirmed_kind_mismatch", "adjusted_total_return"} else "price_return"
        payload["selected_series"] = {
            "quote_basis": "adjusted_close" if case == "adjusted_total_return" else "close",
            "return_kind": published_kind,
            "basis_type": "nav_with_dividend" if published_kind == "total_return" else "nav",
        }
        payload["series"][0]["points"] = [{"date": "2026-10-01", "value": 110}]
        if case == "adjusted_total_return":
            configured_kind = None
    elif case == "confirmed_price_return_missing_basis":
        configured_kind = "price_return"
    elif case == "confirmed_total_return_missing_basis":
        configured_kind = "total_return"
    elif case == "missing_payload_contract":
        del payload["return_series"]
    elif case == "legacy_unknown_close":
        payload["selected_series"]["quote_basis"] = "close"
    elif case == "unknown_chart_points":
        payload["series"][0]["points"] = [{"date": "2026-10-01", "value": 110}]
    elif case == "unknown_return_intervals":
        payload["return_series"]["intervals"] = [{"value": 0.1, "status": "complete"}]
    elif case == "unknown_return_wealth":
        payload["return_series"]["wealth_points"] = [{"date": "2026-10-01", "value": 100}]
    elif case == "unknown_research_points":
        payload["research_returns"]["points"] = [{"date": "2026-10-01", "value": 100}]
    elif case == "unknown_published_return_kind":
        payload["return_series"]["metadata"]["return_kind"] = "price_return"
    elif case == "unknown_latest_total_return":
        payload["latest_values"]["total_return"] = {"value": 110}

    # JSON traversal and null semantics stay in the production query. SQLite's
    # corresponding JSON functions let the offline suite exercise its rows.
    query = audit_module._watchlist_index_return_semantics_contract_query()
    query = query.replace("::jsonb", "").replace("jsonb_typeof", "json_type").replace("jsonb_array_length", "json_array_length")
    with sqlite3.connect(":memory:") as database:
        database.execute("ATTACH DATABASE ':memory:' AS instrument_data")
        database.execute("ATTACH DATABASE ':memory:' AS watchlist")
        database.executescript("""
            CREATE TABLE instrument_data.instrument (
                instrument_id TEXT, instrument_type TEXT, source_settings_json TEXT,
                lifecycle_state_json TEXT
            );
            CREATE TABLE watchlist.instrument_chart_read_model (
                instrument_id TEXT, payload_json TEXT
            );
        """)
        database.execute("INSERT INTO instrument_data.instrument VALUES (?, ?, ?, ?)",
                         ("index-test", "index", json.dumps({"return_semantics": configured_kind}), '{"status":"active"}'))
        if case != "missing_chart":
            database.execute("INSERT INTO watchlist.instrument_chart_read_model VALUES (?, ?)",
                             ("index-test", json.dumps(payload)))
        assert database.execute(query).fetchone()[0] == expected_failures


@pytest.mark.parametrize("case,expected_failures", [
    ("hk_etf", 0), ("hk_usd_counter", 0), ("hk_cny_counter", 0),
    ("us_etf", 0), ("us_equity", 0), ("us_bats_equity", 0),
    ("fmp_equity", 0), ("tushare_etf", 0), ("fmp_equity_without_fmp_identity", 1),
    ("missing_pin", 1), ("invalid_pin", 1), ("currency_mismatch", 1),
    ("missing_provider_currency", 1), ("scaled_price", 1), ("invalid_history_date", 1),
    ("wrong_market_calendar", 1), ("wrong_frequency", 1), ("wrong_source_mode", 1),
    ("wrong_return_semantics", 1), ("raw_total_return_policy", 1),
    ("wrong_ticker", 1), ("malformed_provider_symbol", 1), ("duplicate_provider_symbol", 1),
    ("missing_provider_symbol", 1), ("symbol_market_mismatch", 1), ("missing_exchange", 1),
    ("nonlisted_futu", 1), ("mixed_raw_provider", 1), ("mixed_adjusted_provider", 1), ("mixed_bar_provider", 1),
])
def test_listed_identity_audit_accepts_only_the_configured_futu_identity_and_price_contract(
    audit_module: ModuleType, case: str, expected_failures: int,
) -> None:
    from datetime import date

    kind, exchange, currency = "etf", "XHKG", "HKD"
    provider, ticker = "futu:HK.02822", "2822.HK"
    source = {"source_mode": "api", "source_api_profile": "futu", "expected_frequency": "daily",
              "market_calendar": exchange, "return_semantics": "price_return", "source_provider_currency": currency,
              "source_provider_security_id": "100", "source_price_multiplier": "1", "history_start_date": "2004-11-18"}
    policy = {role: ["adjusted_close"] if role == "total_return" else ["close"]
              for role in ("trading", "valuation", "total_return", "chart", "reference")}
    if case == "hk_usd_counter":
        provider, ticker, currency = "futu:HK.09822", "9822.HK", "USD"
        source["source_provider_currency"] = currency
    elif case == "hk_cny_counter":
        provider, ticker, currency = "futu:HK.82822", "82822.HK", "CNY"
        source["source_provider_currency"] = currency
    elif case in {"us_etf", "us_equity", "us_bats_equity"}:
        kind = "etf" if case == "us_etf" else "equity"
        exchange = {"us_etf": "ARCX", "us_equity": "XNAS", "us_bats_equity": "BATS"}[case]
        provider, ticker, currency = "futu:US.SPY", "SPY", "USD"
        source.update(market_calendar=exchange, source_provider_currency=currency)
    elif case in {"fmp_equity", "fmp_equity_without_fmp_identity"}:
        kind, exchange, currency = "equity", "XNYS", "USD"
        provider, ticker = "fmp:IBM", "IBM"
        source.update(source_api_profile="fmp", market_calendar=exchange)
        if case == "fmp_equity_without_fmp_identity":
            provider = "futu:US.IBM"
    elif case == "tushare_etf":
        exchange, currency, provider, ticker = "XSHG", "CNY", "tushare:510300.SH", "510300.SH"
        source.update(source_api_profile="tushare", market_calendar=exchange)
    elif case == "missing_pin":
        del source["source_provider_security_id"]
    elif case == "invalid_pin":
        source["source_provider_security_id"] = "0"
    elif case == "currency_mismatch":
        source["source_provider_currency"] = "USD"
    elif case == "missing_provider_currency":
        del source["source_provider_currency"]
    elif case == "scaled_price":
        source["source_price_multiplier"] = "0.01"
    elif case == "invalid_history_date":
        source["history_start_date"] = "2004-02-30"
    elif case == "wrong_market_calendar":
        source["market_calendar"] = "XNYS"
    elif case == "wrong_frequency":
        source["expected_frequency"] = "weekly"
    elif case == "wrong_source_mode":
        source["source_mode"] = "manual"
    elif case == "wrong_return_semantics":
        source["return_semantics"] = "total_return"
    elif case == "raw_total_return_policy":
        policy["total_return"] = ["adjusted_close", "close"]
    elif case == "wrong_ticker":
        ticker = "2800.HK"
    elif case == "malformed_provider_symbol":
        provider = "futu:HK.2822"
    elif case == "symbol_market_mismatch":
        provider, ticker = "futu:US.SPY", "SPY"
    elif case == "missing_exchange":
        exchange = None
    elif case == "nonlisted_futu":
        kind, exchange = "index", None

    def valid_date(value, _type):
        try:
            date.fromisoformat(value)
            return True
        except ValueError:
            return False

    query = audit_module._instrument_type_listed_security_identity_contract_query()
    query = re.sub(r"instrument.quote_selection_policy_json::jsonb IS DISTINCT FROM\s*('[^']+')::jsonb",
                   r"NOT json_equal(instrument.quote_selection_policy_json, \1)", query)
    with sqlite3.connect(":memory:") as database:
        database.create_function("regexp_like", 2, lambda value, pattern: bool(re.search(pattern, value or "")))
        database.create_function("pg_input_is_valid", 2, valid_date)
        database.create_function("lpad", 3, lambda value, width, fill: value.rjust(width, fill))
        database.create_function("greatest", -1, max)
        database.create_function("json_equal", 2, lambda left, right: json.loads(left) == json.loads(right))
        database.executescript("""
            ATTACH ':memory:' AS instrument_data;
            CREATE TABLE instrument_data.instrument (
                instrument_id TEXT, instrument_type TEXT, exchange_code TEXT, currency TEXT,
                source_settings_json TEXT, quote_selection_policy_json TEXT);
            CREATE TABLE instrument_data.instrument_identifier (
                instrument_id TEXT, identifier_type TEXT, identifier_value TEXT);
            CREATE TABLE instrument_data.instrument_market_data (
                instrument_id TEXT, metric_family TEXT, quote_basis TEXT, provider TEXT);
            CREATE TABLE instrument_data.instrument_price_bar (instrument_id TEXT, provider TEXT);
        """)
        database.execute("INSERT INTO instrument_data.instrument VALUES (?, ?, ?, ?, ?, ?)",
                         ("security", kind, exchange, currency, json.dumps(source), json.dumps(policy)))
        database.execute("INSERT INTO instrument_data.instrument_identifier VALUES (?, ?, ?)",
                         ("security", "exchange_ticker", ticker))
        if case != "missing_provider_symbol":
            database.execute("INSERT INTO instrument_data.instrument_identifier VALUES (?, ?, ?)",
                             ("security", "provider_symbol", provider))
        if case == "duplicate_provider_symbol":
            database.execute("INSERT INTO instrument_data.instrument_identifier VALUES ('security', 'provider_symbol', 'futu:HK.02800')")
        if source["source_api_profile"] == "futu":
            database.executemany("INSERT INTO instrument_data.instrument_market_data VALUES (?, ?, ?, ?)", [
                ("security", "price", "close", "fmp:daily" if case == "mixed_raw_provider" else "futu:history-kline:unadjusted"),
                ("security", "price", "adjusted_close", "fmp:daily" if case == "mixed_adjusted_provider" else "futu:declared-cash-reinvestment:v1"),
            ])
            database.execute("INSERT INTO instrument_data.instrument_price_bar VALUES (?, ?)",
                             ("security", "fmp:daily" if case == "mixed_bar_provider" else "futu:history-kline:unadjusted"))
        assert database.execute(query).fetchone()[0] == expected_failures


def test_audit_contract_names_cover_registry_0019(
    audit_module: ModuleType,
) -> None:
    assert len(audit_module.AUDIT_CHECK_NAMES) == 56
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
    assert "watchlist_screener_projection_ready" in audit_module.AUDIT_CHECK_NAMES
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
        "taxonomy_default_planning_invalid"
        not in audit_module.AUDIT_CHECK_NAMES
    )
    assert "taxonomy_configuration_state_incomplete" in audit_module.AUDIT_CHECK_NAMES
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


@pytest.mark.parametrize("versions", [
    {"instrument_data": "20260712_0007", "data_ingestion": "20260716_0002",
     "portfolio": "20260711_0032", "watchlist": "20260728_0030"},
    {"watchlist": "20260923_0062"},
], ids=["legacy-schema", "missing-retained-portfolio-scope"])
def test_migration_source_heads_are_unsupported_after_cutover(
    audit_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    versions: dict[str, str],
) -> None:
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
    checks = audit_module._run_flat_table_audit(database_url)
    assert {check.name for check in checks} == set(audit_module.AUDIT_CHECK_NAMES)

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
    assert "NOT IN ('physical', 'cash')" in derivative_contract_query
    assert "? 'settlement_type'" not in derivative_contract_query
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

    assert not any("default_planning_taxonomy_id" in query for query in queries)
    target_query = next(query for query in queries if "WITH target_totals AS" in query)
    assert "line.target_value" in target_query
    assert "cash_reserve" in target_query
    assert "target_set.weight_enabled" not in target_query

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
    list_projection_query = next(query for query in queries if "screener_payload_json IS NULL" in query)
    assert "watchlist.instrument_chart_read_model" in list_projection_query
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
    configuration_state_query = next(
        query for query in queries
        if "portfolio.portfolio_taxonomy_state state" in query
    )
    assert "state.current_version < revision.configuration_version" in configuration_state_query
    assert not any("analytics_scope_policy_record" in query or "analytics_taxonomy_selection_record" in query
        for query in queries)


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
        checks.append(audit_module.AuditCheck(name='taxonomy_configuration_state_incomplete',
            status=other_status, value=1, limit=0, detail='Missing taxonomy configuration version.'))
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
