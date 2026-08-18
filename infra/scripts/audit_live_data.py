#!/usr/bin/env python3
"""Read-only integrity audit for the local Portfolio Operations database."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from dataclasses import asdict, dataclass
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INSTRUMENT_CORE_PYTHON = REPOSITORY_ROOT / "packages" / "instrument-core" / "python"
if str(INSTRUMENT_CORE_PYTHON) not in sys.path:
    sys.path.insert(0, str(INSTRUMENT_CORE_PYTHON))

from portfolio_ops_instrument_core import (  # noqa: E402
    FX_INSTRUMENT_IDENTITIES,
    QUOTE_BASIS_METRIC_FAMILY,
    QuoteSelectionPolicy,
    VALUATION_PROHIBITED_TOTAL_RETURN_BASES,
)


FINAL_FLAT_TABLE_HEADS = {
    "instrument_registry": "20260818_0025",
    "platform": "20260818_0003",
    "portfolio": "20260818_0053",
    "watchlist": "20260818_0042",
}
VERSION_TABLES = {
    "instrument_registry": "alembic_version",
    "platform": "platform_alembic_version",
    "portfolio": "alembic_version",
    "watchlist": "alembic_version",
}
FUND_NAV_PROJECTION_METHOD_VERSION = "fund_nav_reinvestment_projection/v7"

SCHEMA_IDENTIFIER_RENAMES = (
    (
        "constraint",
        "instrument_registry",
        "fk_instrument_identifier_asset_id_instrument",
        "fk_instrument_identifier_instrument_id_instrument",
    ),
    (
        "constraint",
        "instrument_registry",
        "fk_instrument_market_data_asset_id_instrument",
        "fk_instrument_market_data_instrument_id_instrument",
    ),
    (
        "constraint",
        "instrument_registry",
        "uq_instrument_market_data_asset_metric_basis_date_currency",
        "uq_instrument_market_data_instrument_metric_basis_date_currency",
    ),
    (
        "constraint",
        "watchlist",
        "fk_exposure_analytics_snapshot_asset_id_asset_detail",
        "fk_exposure_analytics_snapshot_instrument_id_instrument_detail",
    ),
    (
        "constraint",
        "watchlist",
        "fk_holding_snapshot_asset_id_asset_detail",
        "fk_holding_snapshot_instrument_id_instrument_detail",
    ),
    (
        "constraint",
        "watchlist",
        "fk_instrument_attribute_value_asset_id_asset_detail",
        "fk_instrument_attribute_value_instrument_id_instrument_detail",
    ),
    (
        "constraint",
        "watchlist",
        "pk_asset_chart_read_model",
        "pk_instrument_chart_read_model",
    ),
    (
        "constraint",
        "watchlist",
        "fk_asset_chart_read_model_asset_id_asset_detail",
        "fk_instrument_chart_read_model_instrument_id_instrument_detail",
    ),
    ("constraint", "watchlist", "pk_asset_detail", "pk_instrument_detail"),
    (
        "constraint",
        "watchlist",
        "fk_asset_detail_asset_id_instrument",
        "fk_instrument_detail_instrument_id_instrument",
    ),
    (
        "constraint",
        "watchlist",
        "pk_asset_exposure_holdings_read_model",
        "pk_instrument_exposure_holdings_read_model",
    ),
    (
        "constraint",
        "watchlist",
        "fk_asset_exposure_holdings_read_model_asset_id_asset_detail",
        "fk_instrument_exposure_holdings_read_model_instrument_i_4381",
    ),
    (
        "constraint",
        "watchlist",
        "pk_asset_exposure_read_model",
        "pk_instrument_exposure_read_model",
    ),
    (
        "constraint",
        "watchlist",
        "fk_asset_exposure_read_model_asset_id_asset_detail",
        "fk_instrument_exposure_read_model_instrument_id_instrum_0346",
    ),
    (
        "constraint",
        "watchlist",
        "pk_asset_manual_profile",
        "pk_instrument_manual_profile",
    ),
    (
        "constraint",
        "watchlist",
        "fk_asset_manual_profile_asset_id_asset_detail",
        "fk_instrument_manual_profile_instrument_id_instrument_detail",
    ),
    (
        "constraint",
        "watchlist",
        "pk_asset_performance_read_model",
        "pk_instrument_performance_read_model",
    ),
    (
        "constraint",
        "watchlist",
        "fk_asset_performance_read_model_asset_id_asset_detail",
        "fk_instrument_performance_read_model_instrument_id_inst_9937",
    ),
    (
        "constraint",
        "watchlist",
        "pk_asset_risk_read_model",
        "pk_instrument_risk_read_model",
    ),
    (
        "constraint",
        "watchlist",
        "fk_asset_risk_read_model_asset_id_asset_detail",
        "fk_instrument_risk_read_model_instrument_id_instrument_detail",
    ),
    (
        "constraint",
        "watchlist",
        "pk_asset_summary_read_model",
        "pk_instrument_summary_read_model",
    ),
    (
        "constraint",
        "watchlist",
        "fk_asset_summary_read_model_asset_id_asset_detail",
        "fk_instrument_summary_read_model_instrument_id_instrume_1b8e",
    ),
    (
        "constraint",
        "watchlist",
        "fk_instrument_taxonomy_assignment_asset_id_asset_detail",
        "fk_instrument_taxonomy_assignment_instrument_id_instrum_983f",
    ),
    (
        "constraint",
        "watchlist",
        "fk_nav_fact_asset_id_asset_detail",
        "fk_nav_fact_instrument_id_instrument_detail",
    ),
    (
        "constraint",
        "watchlist",
        "uq_nav_fact_asset_date_type_currency",
        "uq_nav_fact_instrument_date_type_currency",
    ),
    (
        "constraint",
        "watchlist",
        "fk_performance_snapshot_asset_id_asset_detail",
        "fk_performance_snapshot_instrument_id_instrument_detail",
    ),
    (
        "constraint",
        "watchlist",
        "fk_recalc_job_asset_id_asset_detail",
        "fk_recalc_job_instrument_id_instrument_detail",
    ),
    (
        "constraint",
        "watchlist",
        "fk_risk_snapshot_asset_id_asset_detail",
        "fk_risk_snapshot_instrument_id_instrument_detail",
    ),
    (
        "constraint",
        "watchlist",
        "fk_watchlist_item_asset_id_instrument",
        "fk_watchlist_item_instrument_id_instrument",
    ),
    (
        "constraint",
        "watchlist",
        "uq_watchlist_item_watchlist_asset",
        "uq_watchlist_item_watchlist_instrument",
    ),
    (
        "index",
        "watchlist",
        "idx_instrument_attribute_value_asset_attribute",
        "idx_instrument_attribute_value_instrument_attribute",
    ),
    (
        "index",
        "watchlist",
        "idx_nav_fact_asset_date",
        "idx_nav_fact_instrument_date",
    ),
    (
        "index",
        "watchlist",
        "uq_watchlist_item_watchlist_asset",
        "uq_watchlist_item_watchlist_instrument",
    ),
)

AUDIT_CHECK_NAMES = (
    "schema_identifier_contract",
    "instrument_type_equity_identity_contract",
    "fmp_equity_catalog_contract",
    "watchlist_system_contract",
    "watchlist_taxonomy_type_contract",
    "watchlist_field_identity_contract",
    "watchlist_group_by_contract",
    "watchlist_saved_view_field_contract",
    "watchlist_taxonomy_history_contract",
    "portfolio_account_category_contract",
    "derivative_registry_boundary",
    "portfolio_derivative_contract_integrity",
    "market_data_invalid_values",
    "market_data_currency_mismatch",
    "instrument_quote_policy_contract",
    "market_data_price_contract",
    "market_data_fx_identity_contract",
    "market_data_logical_duplicates",
    "valuation_policy_total_return_basis",
    "watchlist_index_return_semantics_contract",
    "fund_nav_current_projection_contract",
    "held_fund_recent_total_return_coverage",
    "portfolio_nav_reconciliation",
    "portfolio_twr_geometric_link",
    "portfolio_drawdown_from_twr",
    "holding_valuation_basis_contract",
    "portfolio_valuation_quote_match",
    "taxonomy_target_sum",
    "taxonomy_negative_targets",
    "reserved_cash_taxonomy_nodes",
    "taxonomy_assignment_overlap",
    "current_unassigned_planning_holdings",
    "analytics_scope_missing_effective_selection",
    "analytics_scope_incomplete_configuration",
    "price_bar_contract",
    "corporate_action_event_integrity",
    "held_confirmed_share_split_events_covered",
    "held_detected_or_uncovered_share_adjustments",
    "listed_total_return_coverage",
)


def _sql_text_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


MAINTAINED_FX_IDENTITIES_SQL = ", ".join(
    "(" + ", ".join(
        (
            _sql_text_literal(identity.instrument_id),
            _sql_text_literal(identity.base_currency),
            _sql_text_literal(identity.quote_currency),
        )
    ) + ")"
    for identity in FX_INSTRUMENT_IDENTITIES
)
QUOTE_SELECTION_POLICY_ROLES = tuple(QuoteSelectionPolicy.model_fields)
QUOTE_SELECTION_POLICY_ROLES_SQL = ", ".join(
    "(" + _sql_text_literal(role) + ")" for role in QUOTE_SELECTION_POLICY_ROLES
)
VALID_QUOTE_BASES_SQL = ", ".join(
    _sql_text_literal(quote_basis)
    for quote_basis in sorted(QUOTE_BASIS_METRIC_FAMILY)
)
VALUATION_PROHIBITED_BASES_SQL = ", ".join(
    _sql_text_literal(quote_basis)
    for quote_basis in sorted(VALUATION_PROHIBITED_TOTAL_RETURN_BASES)
)
FUND_NAV_PROJECTION_METHOD_VERSION_SQL = _sql_text_literal(
    FUND_NAV_PROJECTION_METHOD_VERSION
)
@dataclass(frozen=True)
class AuditCheck:
    name: str
    status: str
    value: Any
    limit: Any
    detail: str


@dataclass(frozen=True)
class SchemaProfile:
    family: str
    status: str
    versions: dict[str, dict[str, Any]]
    reason: str
    capabilities: dict[str, str | None]


def _begin_read_only(cursor: psycopg.Cursor[Any]) -> None:
    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    cursor.execute("SET LOCAL statement_timeout = '60s'")
    cursor.execute("SET LOCAL lock_timeout = '3s'")
    cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '90s'")


def _relation_kind(
    cursor: psycopg.Cursor[Any], schema_name: str, relation_name: str
) -> str | None:
    cursor.execute(
        """
        SELECT class.relkind::text
        FROM pg_catalog.pg_class class
        JOIN pg_catalog.pg_namespace namespace
          ON namespace.oid = class.relnamespace
        WHERE namespace.nspname = %s
          AND class.relname = %s
        """,
        (schema_name, relation_name),
    )
    row = cursor.fetchone()
    return None if row is None else str(row[0])


def _column_exists(
    cursor: psycopg.Cursor[Any], schema_name: str, table_name: str, column_name: str
) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
        )
        """,
        (schema_name, table_name, column_name),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _version_state(
    cursor: psycopg.Cursor[Any], component: str
) -> dict[str, Any]:
    version_table = VERSION_TABLES[component]
    version_relation = f"{component}.{version_table}"
    cursor.execute("SELECT to_regclass(%s)", (version_relation,))
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return {"row_count": 0, "version": None, "table_present": False}
    cursor.execute(
        sql.SQL("SELECT count(*)::integer, min(version_num)::text FROM {}.{}").format(
            sql.Identifier(component),
            sql.Identifier(version_table),
        )
    )
    count, version = cursor.fetchone() or (0, None)
    return {
        "row_count": int(count),
        "version": None if version is None else str(version),
        "table_present": True,
    }


def _detect_schema_profile(cursor: psycopg.Cursor[Any]) -> SchemaProfile:
    versions = {
        component: _version_state(cursor, component)
        for component in FINAL_FLAT_TABLE_HEADS
    }
    relation_specs = {
        "market_data": ("instrument_registry", "instrument_market_data"),
        "transaction": ("portfolio", "transaction_record"),
        "snapshot": ("portfolio", "portfolio_daily_snapshot"),
        "holding": ("portfolio", "portfolio_daily_holding_snapshot"),
        "derivative_contract": ("portfolio", "derivative_contract_record"),
        "unsupported_quote_series": ("instrument_registry", "quote_series"),
        "unsupported_quote_observation": ("instrument_registry", "quote_observation"),
        "unsupported_quote_revision": (
            "instrument_registry",
            "quote_observation_revision",
        ),
        "unsupported_transaction_current": ("portfolio", "transaction_current"),
        "unsupported_snapshot_output": (
            "portfolio",
            "portfolio_daily_snapshot_output",
        ),
        "unsupported_holding_output": (
            "portfolio",
            "portfolio_daily_holding_output",
        ),
        "unsupported_quote_window": ("portfolio", "portfolio_daily_quote_window"),
        "unsupported_quote_candidate": (
            "portfolio",
            "portfolio_daily_quote_candidate",
        ),
        "unsupported_current_publication": (
            "calculation_registry",
            "calculation_current_publication",
        ),
        "unsupported_publication": (
            "calculation_registry",
            "calculation_publication",
        ),
        "instrument": ("instrument_registry", "instrument"),
        "corporate_action": (
            "instrument_registry",
            "corporate_action_event",
        ),
        "watchlist_chart": ("watchlist", "instrument_chart_read_model"),
        "target_set": ("portfolio", "target_set_record"),
        "target_line": ("portfolio", "target_set_line_record"),
        "taxonomy": ("portfolio", "taxonomy_record"),
        "taxonomy_assignment": (
            "portfolio",
            "taxonomy_assignment_record",
        ),
    }
    capabilities = {
        name: _relation_kind(cursor, *relation)
        for name, relation in relation_specs.items()
    }
    required_flat_tables = {
        "market_data",
        "transaction",
        "snapshot",
        "holding",
        "derivative_contract",
        "instrument",
        "corporate_action",
        "watchlist_chart",
        "target_set",
        "target_line",
        "taxonomy",
        "taxonomy_assignment",
    }
    unsupported_overhaul_markers = {
        name for name in relation_specs if name.startswith("unsupported_")
    }

    flat_table_shape = all(
        capabilities[name] == "r" for name in required_flat_tables
    ) and not any(capabilities[name] for name in unsupported_overhaul_markers)
    flat_table_heads = all(
        versions[component]["row_count"] == 1
        and versions[component]["version"] == expected_head
        for component, expected_head in FINAL_FLAT_TABLE_HEADS.items()
    )
    if flat_table_shape and flat_table_heads:
        return SchemaProfile(
            family="flat-table",
            status="supported",
            versions=versions,
            reason="Known flat-table schema at the exact final heads.",
            capabilities=capabilities,
        )

    present_flat_tables = sorted(
        name for name in required_flat_tables if capabilities[name]
    )
    present_unsupported_markers = sorted(
        name for name in unsupported_overhaul_markers if capabilities[name]
    )
    return SchemaProfile(
        family="unsupported",
        status="unsupported",
        versions=versions,
        reason=(
            "Schema/head capability gate failed closed; no schema-dependent query was run. "
            f"flat_table_markers={present_flat_tables}; "
            f"unsupported_overhaul_markers={present_unsupported_markers}"
        ),
        capabilities=capabilities,
    )


def _unsupported_checks(profile: SchemaProfile) -> list[AuditCheck]:
    version_summary = {
        component: state.get("version") for component, state in profile.versions.items()
    }
    reason = f"{profile.reason}; versions={version_summary}"
    return [
        AuditCheck(
            name="database_schema_compatibility",
            status="fail",
            value={"family": profile.family, "versions": version_summary},
            limit={"family": ["flat-table"]},
            detail=reason,
        ),
        *(
            AuditCheck(
                name=name,
                status="warning",
                value={
                    "applicable": False,
                    "schema": profile.family,
                    "versions": version_summary,
                },
                limit="not_applicable",
                detail=f"Check not run because its required schema contract is unavailable: {reason}",
            )
            for name in AUDIT_CHECK_NAMES
        ),
    ]


def _environment_database_url() -> str | None:
    return os.getenv("PORTFOLIO_OPS_LOCAL_DATABASE_URL")


def _normalize_database_url(database_url: str) -> str:
    normalized = database_url.strip()
    if not normalized:
        raise ValueError("database URL must not be empty")
    return normalized.replace("postgresql+psycopg://", "postgresql://", 1)


def _database_name(database_url: str) -> str:
    database_name = conninfo_to_dict(_normalize_database_url(database_url)).get(
        "dbname"
    )
    if not database_name:
        raise ValueError("database URL must name a database")
    return database_name


def _scalar(cursor: psycopg.Cursor[Any], query: str) -> Any:
    cursor.execute(query)
    row = cursor.fetchone()
    return None if row is None else row[0]


def _count_check(
    cursor: psycopg.Cursor[Any],
    *,
    name: str,
    query: str,
    detail: str,
    warning_only: bool = False,
) -> AuditCheck:
    value = int(_scalar(cursor, query) or 0)
    return AuditCheck(
        name=name,
        status="pass" if value == 0 else ("warning" if warning_only else "fail"),
        value=value,
        limit=0,
        detail=detail,
    )


def _schema_identifier_contract_query() -> str:
    expected_rows = ",\n".join(
        "(" + ", ".join(_sql_text_literal(value) for value in row) + ")"
        for row in SCHEMA_IDENTIFIER_RENAMES
    )
    return f"""
        WITH expected(object_kind, schema_name, legacy_name, canonical_name) AS (
            VALUES {expected_rows}
        ),
        actual AS (
            SELECT 'constraint'::text AS object_kind,
                   namespace.nspname::text AS schema_name,
                   constraint_record.conname::text AS object_name
            FROM pg_catalog.pg_constraint constraint_record
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = constraint_record.connamespace
            UNION ALL
            SELECT 'index'::text AS object_kind,
                   namespace.nspname::text AS schema_name,
                   class.relname::text AS object_name
            FROM pg_catalog.pg_class class
            JOIN pg_catalog.pg_namespace namespace
              ON namespace.oid = class.relnamespace
            WHERE class.relkind = 'i'
        )
        SELECT count(*)
        FROM expected
        WHERE NOT EXISTS (
                  SELECT 1 FROM actual
                  WHERE actual.object_kind = expected.object_kind
                    AND actual.schema_name = expected.schema_name
                    AND actual.object_name = expected.canonical_name
              )
           OR EXISTS (
                  SELECT 1 FROM actual
                  WHERE actual.object_kind = expected.object_kind
                    AND actual.schema_name = expected.schema_name
                    AND actual.object_name = expected.legacy_name
              )
    """


def _run_flat_table_audit(database_url: str) -> list[AuditCheck]:
    checks: list[AuditCheck] = []
    database_url = _normalize_database_url(database_url)
    with psycopg.connect(database_url, autocommit=False) as connection:
        with connection.cursor() as cursor:
            _begin_read_only(cursor)
            profile = _detect_schema_profile(cursor)
            if profile.family != "flat-table":
                return _unsupported_checks(profile)
            checks.append(
                _count_check(
                    cursor,
                    name="schema_identifier_contract",
                    query=_schema_identifier_contract_query(),
                    detail=(
                        "Registry and Watchlist constraints and indexes must use the "
                        "canonical instrument-era identifiers, with no rewritten-revision "
                        "legacy names remaining."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="instrument_type_equity_identity_contract",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument instrument
                        WHERE instrument.instrument_type NOT IN (
                                'public_fund', 'private_fund', 'etf', 'index',
                                'equity', 'cash', 'fx', 'other'
                              )
                           OR (
                                instrument.instrument_type = 'equity'
                                AND (
                                    instrument.exchange_code NOT IN (
                                        'XNAS', 'XNYS', 'XASE', 'XHKG', 'XSHG', 'XSHE'
                                    )
                                    OR coalesce(
                                        instrument.source_settings_json ->> 'source_mode',
                                        ''
                                    ) <> 'api'
                                    OR coalesce(
                                        instrument.source_settings_json ->> 'source_api_profile',
                                        ''
                                    ) <> 'fmp'
                                    OR NOT EXISTS (
                                        SELECT 1
                                        FROM instrument_registry.instrument_identifier identifier
                                        WHERE identifier.instrument_id = instrument.instrument_id
                                          AND identifier.identifier_type = 'exchange_ticker'
                                    )
                                    OR NOT EXISTS (
                                        SELECT 1
                                        FROM instrument_registry.instrument_identifier identifier
                                        WHERE identifier.instrument_id = instrument.instrument_id
                                          AND identifier.identifier_type = 'provider_symbol'
                                          AND identifier.identifier_value LIKE 'fmp:%'
                                    )
                                )
                              )
                           OR (
                                instrument.instrument_type <> 'equity'
                                AND instrument.exchange_code IS NOT NULL
                              )
                    """,
                    detail=(
                        "Registry instrument types must use the split public/private fund "
                        "contract; every equity must have a supported exchange and FMP "
                        "identity, while non-equities must not carry exchange identity."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="fmp_equity_catalog_contract",
                    query="""
                        WITH required_exchange(exchange_code) AS (
                            VALUES ('XNAS'), ('XNYS'), ('XASE'), ('XHKG'), ('XSHG'), ('XSHE')
                        )
                        SELECT
                            (
                                SELECT count(*)
                                FROM required_exchange required
                                WHERE NOT EXISTS (
                                    SELECT 1
                                    FROM platform.fmp_equity_catalog catalog
                                    WHERE catalog.exchange_code = required.exchange_code
                                )
                            )
                            + (
                                SELECT count(*)
                                FROM platform.fmp_equity_catalog catalog
                                WHERE trim(catalog.fmp_symbol) = ''
                                   OR trim(catalog.exchange_ticker) = ''
                                   OR trim(catalog.company_name) = ''
                                   OR catalog.exchange_code NOT IN (
                                        'XNAS', 'XNYS', 'XASE', 'XHKG', 'XSHG', 'XSHE'
                                   )
                                   OR (
                                        catalog.exchange_code = 'XSHG'
                                        AND catalog.fmp_symbol ~ '^900[0-9]{3}[.]SS$'
                                   )
                                   OR (
                                        catalog.exchange_code = 'XSHE'
                                        AND catalog.fmp_symbol ~ '^200[0-9]{3}[.]SZ$'
                                   )
                            )
                    """,
                    detail=(
                        "The local FMP search catalog must cover all six supported "
                        "exchanges and must not misclassify China B shares as CNY A shares."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_system_contract",
                    query="""
                        WITH specs(
                            watchlist_id,
                            watchlist_name,
                            instrument_type,
                            sort_order
                        ) AS (
                            VALUES
                                ('index', 'Index', 'index', 0),
                                ('all-public-funds', 'All 公募', 'public_fund', 1),
                                ('all-private-funds', 'All 私募', 'private_fund', 2)
                        ), metadata_errors AS (
                            SELECT specs.watchlist_id
                            FROM specs
                            LEFT JOIN watchlist.watchlist record
                              ON record.watchlist_id = specs.watchlist_id
                            WHERE record.watchlist_id IS NULL
                               OR record.name <> specs.watchlist_name
                               OR record.owner_type <> 'system'
                               OR record.owner_id <> 'watchlist'
                               OR record.is_default IS NOT TRUE
                               OR record.is_shared IS NOT TRUE
                               OR record.sort_order <> specs.sort_order
                        ), unexpected_system_lists AS (
                            SELECT record.watchlist_id
                            FROM watchlist.watchlist record
                            WHERE (
                                    record.owner_type = 'system'
                                    AND record.owner_id = 'watchlist'
                                  )
                              AND NOT EXISTS (
                                    SELECT 1
                                    FROM specs
                                    WHERE specs.watchlist_id = record.watchlist_id
                              )
                        ), invalid_memberships AS (
                            SELECT item.watchlist_id, item.instrument_id
                            FROM watchlist.watchlist_item item
                            JOIN specs ON specs.watchlist_id = item.watchlist_id
                            LEFT JOIN instrument_registry.instrument instrument
                              ON instrument.instrument_id = item.instrument_id
                            WHERE instrument.instrument_id IS NULL
                               OR instrument.instrument_type <> specs.instrument_type
                               OR coalesce(
                                    instrument.lifecycle_state_json ->> 'status',
                                    'active'
                                  ) <> 'active'
                        ), invalid_rows AS (
                            SELECT row.watchlist_id, row.instrument_id
                            FROM watchlist.watchlist_row_read_model row
                            JOIN specs ON specs.watchlist_id = row.watchlist_id
                            LEFT JOIN watchlist.watchlist_item item
                              ON item.watchlist_id = row.watchlist_id
                             AND item.instrument_id = row.instrument_id
                            WHERE item.instrument_id IS NULL
                               OR row.instrument_type <> specs.instrument_type
                        ), missing_rows AS (
                            SELECT item.watchlist_id, item.instrument_id
                            FROM watchlist.watchlist_item item
                            JOIN specs ON specs.watchlist_id = item.watchlist_id
                            LEFT JOIN watchlist.watchlist_row_read_model row
                              ON row.watchlist_id = item.watchlist_id
                             AND row.instrument_id = item.instrument_id
                            WHERE row.instrument_id IS NULL
                        )
                        SELECT
                            (SELECT count(*) FROM metadata_errors)
                            + (SELECT count(*) FROM unexpected_system_lists)
                            + (SELECT count(*) FROM invalid_memberships)
                            + (SELECT count(*) FROM invalid_rows)
                            + (SELECT count(*) FROM missing_rows)
                    """,
                    detail=(
                        "The only system Watchlists are Index, All 公募, and All 私募; "
                        "their existing memberships and read models must contain active "
                        "instruments of the matching type."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_taxonomy_type_contract",
                    query="""
                        WITH invalid_nodes AS (
                            SELECT node.node_id
                            FROM watchlist.instrument_taxonomy_node node
                            WHERE node.instrument_type NOT IN (
                                    'public_fund', 'private_fund', 'etf', 'equity', 'index'
                                  )
                               OR node.node_id IN (
                                    'fund-public', 'fund-private', 'equity', 'index'
                                  )
                               OR node.node_id LIKE 'equity-sector-%'
                        ), invalid_assignments AS (
                            SELECT detail.instrument_id
                            FROM watchlist.instrument_detail detail
                            LEFT JOIN watchlist.instrument_taxonomy_assignment assignment
                              ON assignment.instrument_id = detail.instrument_id
                             AND assignment.taxonomy_code = 'instrument_taxonomy'
                            LEFT JOIN watchlist.instrument_taxonomy_node node
                              ON node.node_id = assignment.node_id
                            WHERE (
                                    assignment.node_id IS NOT NULL
                                    AND (
                                        node.node_id IS NULL
                                        OR node.instrument_type <> detail.instrument_type
                                    )
                                  )
                               OR (
                                    detail.instrument_type = 'equity'
                                    AND (
                                        coalesce(detail.metadata_json ->> 'exchange_code', '')
                                            NOT IN (
                                                'XNAS', 'XNYS', 'XASE',
                                                'XHKG', 'XSHG', 'XSHE'
                                            )
                                        OR assignment.node_id IS DISTINCT FROM CASE
                                            detail.metadata_json ->> 'exchange_code'
                                            WHEN 'XNAS' THEN 'equity-exchange-xnas'
                                            WHEN 'XNYS' THEN 'equity-exchange-xnys'
                                            WHEN 'XASE' THEN 'equity-exchange-xase'
                                            WHEN 'XHKG' THEN 'equity-exchange-xhkg'
                                            WHEN 'XSHG' THEN 'equity-exchange-xshg'
                                            WHEN 'XSHE' THEN 'equity-exchange-xshe'
                                            ELSE NULL
                                        END
                                    )
                                  )
                        )
                        SELECT
                            (SELECT count(*) FROM invalid_nodes)
                            + (SELECT count(*) FROM invalid_assignments)
                    """,
                    detail=(
                        "Watchlist taxonomy remains Watchlist-local and type-specific; "
                        "stock taxonomy must match the Registry exchange identity."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_group_by_contract",
                    query="""
                        SELECT
                            CASE
                                WHEN (
                                    SELECT count(*)
                                    FROM watchlist.field_registry
                                    WHERE field_key = 'instrument_type'
                                      AND group_mode = 'discrete'
                                      AND source_metric_code =
                                          'watchlist_row_read_model.instrument_type'
                                ) = 1
                                THEN 0
                                ELSE 1
                            END
                            + (
                                SELECT count(*)
                                FROM watchlist.watchlist_view
                                WHERE coalesce(default_group_by, 'none') NOT IN (
                                    'none',
                                    'instrument_type',
                                    'taxonomy',
                                    'data_freshness_status'
                                )
                            )
                    """,
                    detail=(
                        "Every saved Watchlist view must use one of the four "
                        "supported universal Group By values."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_saved_view_field_contract",
                    query="""
                        WITH RECURSIVE advanced_nodes AS (
                            SELECT
                                watchlist_view_id,
                                coalesce(default_advanced_filter_json::jsonb, '{}'::jsonb)
                                    AS node
                            FROM watchlist.watchlist_view
                            UNION ALL
                            SELECT parent.watchlist_view_id, child.value
                            FROM advanced_nodes parent
                            CROSS JOIN LATERAL jsonb_array_elements(
                                CASE
                                    WHEN jsonb_typeof(parent.node -> 'conditions') = 'array'
                                    THEN parent.node -> 'conditions'
                                    ELSE '[]'::jsonb
                                END
                            ) child(value)
                        ), field_references AS (
                            SELECT
                                view_record.watchlist_view_id,
                                filter_key.field_key,
                                'filter'::text AS operation
                            FROM watchlist.watchlist_view view_record
                            CROSS JOIN LATERAL jsonb_object_keys(
                                coalesce(view_record.default_filters_json::jsonb, '{}'::jsonb)
                            ) filter_key(field_key)
                            UNION ALL
                            SELECT
                                view_record.watchlist_view_id,
                                sort_rule.value ->> 'field',
                                'sort'::text
                            FROM watchlist.watchlist_view view_record
                            CROSS JOIN LATERAL jsonb_array_elements(
                                CASE
                                    WHEN jsonb_typeof(view_record.default_sort_json::jsonb) = 'array'
                                    THEN view_record.default_sort_json::jsonb
                                    ELSE '[]'::jsonb
                                END
                            ) sort_rule(value)
                            UNION ALL
                            SELECT
                                node.watchlist_view_id,
                                node.node ->> 'field',
                                'filter'::text
                            FROM advanced_nodes node
                            WHERE node.node ->> 'type' = 'rule'
                            UNION ALL
                            SELECT
                                view_column.watchlist_view_id,
                                view_column.field_key,
                                'selected'::text
                            FROM watchlist.watchlist_view_column view_column
                        ), invalid_references AS (
                            SELECT reference.*
                            FROM field_references reference
                            LEFT JOIN watchlist.field_registry field
                              ON field.field_key = reference.field_key
                            WHERE coalesce(trim(reference.field_key), '') = ''
                               OR reference.field_key IN (
                                    'asset_type', 'asset_class', 'instrument_class'
                               )
                               OR (
                                    field.field_key IS NULL
                                    AND NOT (
                                        reference.operation = 'selected'
                                        AND reference.field_key IN (
                                            'instrument_id',
                                            'metric_as_of_date',
                                            'metric_return_kind',
                                            'metric_quote_basis',
                                            'metric_series_type'
                                        )
                                    )
                               )
                               OR (
                                    reference.operation = 'filter'
                                    AND coalesce(field.filter_mode, 'none') = 'none'
                               )
                               OR (
                                    reference.operation = 'sort'
                                    AND coalesce(field.sort_mode, 'none') = 'none'
                               )
                        )
                        SELECT
                            (
                                SELECT count(*)
                                FROM watchlist.field_registry
                                WHERE field_key IN (
                                    'asset_type', 'asset_class', 'instrument_class'
                                )
                            )
                            + (SELECT count(*) FROM invalid_references)
                    """,
                    detail=(
                        "Saved Watchlist filters, sorts, advanced filters, and columns "
                        "must reference canonical fields supported for that operation."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_taxonomy_history_contract",
                    query="""
                        SELECT count(*)
                        FROM watchlist.instrument_taxonomy_assignment assignment
                        LEFT JOIN LATERAL (
                            SELECT history.node_id
                            FROM watchlist.instrument_taxonomy_assignment_history history
                            WHERE history.instrument_id = assignment.instrument_id
                              AND history.taxonomy_code = assignment.taxonomy_code
                            ORDER BY history.assigned_at DESC, history.history_id DESC
                            LIMIT 1
                        ) latest_history ON true
                        WHERE latest_history.node_id IS DISTINCT FROM assignment.node_id
                    """,
                    detail=(
                        "Every current Watchlist taxonomy assignment must match its "
                        "latest append-only history record."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_snapshot_source_generation",
                    query="""
                        SELECT count(*)
                        FROM portfolio.portfolio_calculation_state state
                        WHERE state.daily_snapshot_status = 'current'
                          AND (
                            state.source_market_data_updated_at IS DISTINCT FROM (
                                SELECT max(instrument.market_data_updated_at)
                                FROM instrument_registry.instrument instrument
                                WHERE instrument.market_data_updated_at IS NOT NULL
                                  AND (
                                    instrument.instrument_type = 'fx'
                                    OR EXISTS (
                                        SELECT 1
                                        FROM portfolio.transaction_record transaction
                                        WHERE transaction.portfolio_id = state.portfolio_id
                                          AND transaction.instrument_id = instrument.instrument_id
                                    )
                                  )
                            )
                            OR state.source_calculation_inputs_updated_at IS DISTINCT FROM (
                                SELECT max(instrument.calculation_inputs_updated_at)
                                FROM instrument_registry.instrument instrument
                                WHERE instrument.calculation_inputs_updated_at IS NOT NULL
                                  AND (
                                    instrument.instrument_type = 'fx'
                                    OR EXISTS (
                                        SELECT 1
                                        FROM portfolio.transaction_record transaction
                                        WHERE transaction.portfolio_id = state.portfolio_id
                                          AND transaction.instrument_id = instrument.instrument_id
                                    )
                                  )
                            )
                          )
                    """,
                    detail=(
                        "Every current Portfolio snapshot generation must match both "
                        "market-data and calculation-input Registry watermarks."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_field_identity_contract",
                    query="""
                        SELECT
                            CASE
                                WHEN (
                                    SELECT count(*)
                                    FROM watchlist.field_registry
                                    WHERE field_key = 'instrument_name'
                                      AND source_metric_code =
                                          'watchlist_row_read_model.instrument_name'
                                ) = 1
                                THEN 0
                                ELSE 1
                            END
                            + (
                                SELECT count(*)
                                FROM watchlist.field_registry
                                WHERE field_key = 'asset_name'
                                   OR source_metric_code =
                                      'watchlist_row_read_model.asset_name'
                            )
                            + (
                                SELECT count(*)
                                FROM watchlist.watchlist_view_column
                                WHERE field_key = 'asset_name'
                            )
                            + (
                                SELECT count(*)
                                FROM watchlist.watchlist_view
                                WHERE default_sort_json::text LIKE '%asset_name%'
                                   OR default_filters_json::text LIKE '%asset_name%'
                                   OR default_advanced_filter_json::text
                                      LIKE '%asset_name%'
                                   OR default_group_by = 'asset_name'
                            )
                    """,
                    detail=(
                        "Watchlist field metadata and saved views must use only the "
                        "canonical instrument_name identity."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_account_category_contract",
                    query="""
                        WITH invalid_account AS (
                            SELECT account.portfolio_id, account.account_id
                            FROM portfolio.account_record account
                            LEFT JOIN portfolio.account_record cash
                              ON cash.portfolio_id = account.portfolio_id
                             AND cash.account_id =
                                 account.default_settlement_cash_account_id
                            WHERE account.account_category IS NULL
                               OR account.account_category NOT IN (
                                      'cash', 'security', 'fcn', 'option'
                                  )
                               OR (
                                    account.account_category = 'cash'
                                    AND (
                                        account.account_type <> 'deposit_account'
                                        OR account.default_settlement_cash_account_id
                                           IS NOT NULL
                                        OR account.cost_basis_method IS NOT NULL
                                    )
                                  )
                               OR (
                                    account.account_category IN (
                                        'security', 'fcn', 'option'
                                    )
                                    AND (
                                        account.account_type <> 'securities_account'
                                        OR account.cost_basis_method IS NULL
                                        OR account.cost_basis_method NOT IN (
                                               'fifo', 'moving_average'
                                           )
                                        OR cash.account_category IS DISTINCT FROM 'cash'
                                        OR upper(cash.currency)
                                           IS DISTINCT FROM upper(account.currency)
                                    )
                                  )
                        ), invalid_contract_account AS (
                            SELECT contract.portfolio_id, contract.account_id
                            FROM portfolio.derivative_contract_record contract
                            JOIN portfolio.account_record account
                              ON account.portfolio_id = contract.portfolio_id
                             AND account.account_id = contract.account_id
                            WHERE account.account_category <> contract.contract_type
                        ), invalid_transaction_account AS (
                            SELECT txn.portfolio_id, txn.account_id
                            FROM portfolio.transaction_record txn
                            JOIN portfolio.account_record account
                              ON account.portfolio_id = txn.portfolio_id
                             AND account.account_id = txn.account_id
                            LEFT JOIN portfolio.derivative_contract_record contract
                              ON contract.portfolio_id = txn.portfolio_id
                             AND contract.derivative_contract_id =
                                 txn.derivative_contract_id
                            WHERE (
                                    txn.instrument_id IS NOT NULL
                                    AND account.account_category <> 'security'
                                  )
                               OR (
                                    txn.derivative_contract_id IS NOT NULL
                                    AND account.account_category
                                        <> contract.contract_type
                                  )
                        )
                        SELECT count(*)
                        FROM (
                            SELECT * FROM invalid_account
                            UNION ALL
                            SELECT * FROM invalid_contract_account
                            UNION ALL
                            SELECT * FROM invalid_transaction_account
                        ) issue
                    """,
                    detail=(
                        "Every account must be exactly Cash, Security, FCN, or Option; "
                        "holding accounts must map to same-currency Cash, and every "
                        "asset fact must use the matching account category."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="derivative_registry_boundary",
                    query="""
                        SELECT
                            (
                                SELECT count(*)
                                FROM instrument_registry.instrument
                                WHERE instrument_type IN ('bond', 'fcn', 'option')
                            )
                            +
                            (
                                SELECT count(*)
                                FROM instrument_registry.instrument_broker_identifier
                                WHERE identifier_type = 'contract_id'
                            )
                    """,
                    detail=(
                        "Shared Registry must contain reusable tracked market instruments "
                        "only; direct bonds are excluded, while FCNs, options, and their "
                        "contract identities belong to Portfolio."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="portfolio_derivative_contract_integrity",
                    query="""
                        WITH malformed_contract AS (
                            SELECT contract.portfolio_id, contract.derivative_contract_id
                            FROM portfolio.derivative_contract_record contract
                            WHERE json_typeof(contract.terms_json) <> 'object'
                               OR (
                                    contract.contract_type = 'option'
                                    AND (
                                        trim(coalesce(
                                            contract.terms_json ->> 'underlying_instrument_id',
                                            ''
                                        )) = ''
                                        OR contract.terms_json ->> 'option_type'
                                           NOT IN ('call', 'put')
                                        OR NOT pg_input_is_valid(
                                            coalesce(contract.terms_json ->> 'expiry_date', ''),
                                            'date'
                                        )
                                        OR NOT pg_input_is_valid(
                                            coalesce(contract.terms_json ->> 'strike', ''),
                                            'numeric'
                                        )
                                        OR CASE
                                            WHEN pg_input_is_valid(
                                                coalesce(
                                                    contract.terms_json ->> 'strike',
                                                    ''
                                                ),
                                                'numeric'
                                            )
                                            THEN (contract.terms_json ->> 'strike')::numeric <= 0
                                            ELSE false
                                        END
                                        OR NOT pg_input_is_valid(
                                            coalesce(
                                                contract.terms_json
                                                    ->> 'contract_multiplier',
                                                ''
                                            ),
                                            'numeric'
                                        )
                                        OR CASE
                                            WHEN pg_input_is_valid(
                                                coalesce(
                                                    contract.terms_json
                                                        ->> 'contract_multiplier',
                                                    ''
                                                ),
                                                'numeric'
                                            )
                                            THEN (
                                                contract.terms_json
                                                    ->> 'contract_multiplier'
                                            )::numeric <= 0
                                            ELSE false
                                        END
                                        OR (
                                            contract.terms_json::jsonb
                                            ? 'settlement_type'
                                        )
                                    )
                               )
                               OR (
                                    contract.contract_type = 'fcn'
                                    AND (
                                        NOT pg_input_is_valid(
                                            coalesce(contract.terms_json ->> 'notional', ''),
                                            'numeric'
                                        )
                                        OR CASE
                                            WHEN pg_input_is_valid(
                                                coalesce(
                                                    contract.terms_json ->> 'notional',
                                                    ''
                                                ),
                                                'numeric'
                                            )
                                            THEN (
                                                contract.terms_json ->> 'notional'
                                            )::numeric <= 0
                                            ELSE false
                                        END
                                        OR CASE
                                            WHEN contract.terms_json
                                                    -> 'annual_coupon_rate_pct'
                                                IS NULL
                                              OR json_typeof(
                                                    contract.terms_json
                                                        -> 'annual_coupon_rate_pct'
                                                ) = 'null'
                                            THEN false
                                            WHEN NOT pg_input_is_valid(
                                                coalesce(
                                                    contract.terms_json
                                                        ->> 'annual_coupon_rate_pct',
                                                    ''
                                                ),
                                                'numeric'
                                            )
                                            THEN true
                                            ELSE (
                                                contract.terms_json
                                                    ->> 'annual_coupon_rate_pct'
                                            )::numeric < 0
                                              OR (
                                                contract.terms_json
                                                    ->> 'annual_coupon_rate_pct'
                                            )::numeric >= 1000000
                                        END
                                        OR NOT pg_input_is_valid(
                                            coalesce(
                                                contract.terms_json ->> 'issue_date',
                                                ''
                                            ),
                                            'date'
                                        )
                                        OR NOT pg_input_is_valid(
                                            coalesce(
                                                contract.terms_json ->> 'maturity_date',
                                                ''
                                            ),
                                            'date'
                                        )
                                        OR CASE
                                            WHEN pg_input_is_valid(
                                                coalesce(
                                                    contract.terms_json ->> 'issue_date',
                                                    ''
                                                ),
                                                'date'
                                            )
                                             AND pg_input_is_valid(
                                                coalesce(
                                                    contract.terms_json ->> 'maturity_date',
                                                    ''
                                                ),
                                                'date'
                                            )
                                            THEN (
                                                contract.terms_json ->> 'maturity_date'
                                            )::date < (
                                                contract.terms_json ->> 'issue_date'
                                            )::date
                                            ELSE false
                                        END
                                        OR CASE
                                            WHEN contract.terms_json
                                                    -> 'final_observation_date'
                                                IS NULL
                                              OR json_typeof(
                                                    contract.terms_json
                                                        -> 'final_observation_date'
                                                ) = 'null'
                                            THEN false
                                            WHEN NOT pg_input_is_valid(
                                                coalesce(
                                                    contract.terms_json
                                                        ->> 'final_observation_date',
                                                    ''
                                                ),
                                                'date'
                                            )
                                            THEN true
                                            WHEN pg_input_is_valid(
                                                coalesce(
                                                    contract.terms_json ->> 'issue_date',
                                                    ''
                                                ),
                                                'date'
                                            )
                                             AND pg_input_is_valid(
                                                coalesce(
                                                    contract.terms_json ->> 'maturity_date',
                                                    ''
                                                ),
                                                'date'
                                            )
                                            THEN (
                                                contract.terms_json
                                                    ->> 'final_observation_date'
                                            )::date < (
                                                contract.terms_json ->> 'issue_date'
                                            )::date
                                              OR (
                                                contract.terms_json
                                                    ->> 'final_observation_date'
                                            )::date > (
                                                contract.terms_json ->> 'maturity_date'
                                            )::date
                                            ELSE false
                                        END
                                        OR trim(coalesce(
                                            contract.terms_json ->> 'issuer',
                                            ''
                                        )) = ''
                                        OR trim(coalesce(
                                            contract.terms_json ->> 'counterparty',
                                            ''
                                        )) = ''
                                        OR json_typeof(
                                            contract.terms_json
                                                -> 'underlyings'
                                        ) <> 'array'
                                        OR json_array_length(
                                            CASE
                                                WHEN json_typeof(
                                                    contract.terms_json
                                                        -> 'underlyings'
                                                ) = 'array'
                                                THEN contract.terms_json
                                                    -> 'underlyings'
                                                ELSE '[]'::json
                                            END
                                        ) = 0
                                        OR EXISTS (
                                            SELECT 1
                                            FROM json_array_elements(
                                                CASE
                                                    WHEN json_typeof(
                                                        contract.terms_json
                                                            -> 'underlyings'
                                                    ) = 'array'
                                                    THEN contract.terms_json
                                                        -> 'underlyings'
                                                    ELSE '[]'::json
                                                END
                                            ) underlying(value)
                                            WHERE json_typeof(underlying.value) <> 'object'
                                               OR trim(coalesce(
                                                    underlying.value ->> 'instrument_id',
                                                    ''
                                               )) = ''
                                               OR CASE
                                                    WHEN underlying.value
                                                            -> 'initial_reference_price'
                                                        IS NULL
                                                      OR json_typeof(
                                                            underlying.value
                                                                -> 'initial_reference_price'
                                                        ) = 'null'
                                                    THEN false
                                                    WHEN NOT pg_input_is_valid(
                                                        coalesce(
                                                            underlying.value
                                                                ->> 'initial_reference_price',
                                                            ''
                                                        ),
                                                        'numeric'
                                                    )
                                                    THEN true
                                                    ELSE (
                                                        underlying.value
                                                            ->> 'initial_reference_price'
                                                    )::numeric <= 0
                                               END
                                               OR CASE
                                                    WHEN underlying.value
                                                            -> 'strike_level_pct'
                                                        IS NULL
                                                      OR json_typeof(
                                                            underlying.value
                                                                -> 'strike_level_pct'
                                                        ) = 'null'
                                                    THEN false
                                                    WHEN NOT pg_input_is_valid(
                                                        coalesce(
                                                            underlying.value
                                                                ->> 'strike_level_pct',
                                                            ''
                                                        ),
                                                        'numeric'
                                                    )
                                                    THEN true
                                                    ELSE (
                                                        underlying.value
                                                            ->> 'strike_level_pct'
                                                    )::numeric <= 0
                                               END
                                               OR CASE
                                                    WHEN underlying.value
                                                            -> 'knock_in_level_pct'
                                                        IS NULL
                                                      OR json_typeof(
                                                            underlying.value
                                                                -> 'knock_in_level_pct'
                                                        ) = 'null'
                                                    THEN false
                                                    WHEN NOT pg_input_is_valid(
                                                        coalesce(
                                                            underlying.value
                                                                ->> 'knock_in_level_pct',
                                                            ''
                                                        ),
                                                        'numeric'
                                                    )
                                                    THEN true
                                                    ELSE (
                                                        underlying.value
                                                            ->> 'knock_in_level_pct'
                                                    )::numeric <= 0
                                               END
                                               OR CASE
                                                    WHEN underlying.value
                                                            -> 'knock_out_level_pct'
                                                        IS NULL
                                                      OR json_typeof(
                                                            underlying.value
                                                                -> 'knock_out_level_pct'
                                                        ) = 'null'
                                                    THEN false
                                                    WHEN NOT pg_input_is_valid(
                                                        coalesce(
                                                            underlying.value
                                                                ->> 'knock_out_level_pct',
                                                            ''
                                                        ),
                                                        'numeric'
                                                    )
                                                    THEN true
                                                    ELSE (
                                                        underlying.value
                                                            ->> 'knock_out_level_pct'
                                                    )::numeric <= 0
                                               END
                                               OR (
                                                    underlying.value::jsonb ? 'deliverable'
                                                    AND json_typeof(
                                                        underlying.value -> 'deliverable'
                                                    ) <> 'boolean'
                                               )
                                        )
                                        OR (
                                            SELECT count(*)
                                            FROM json_array_elements(
                                                CASE
                                                    WHEN json_typeof(
                                                        contract.terms_json
                                                            -> 'underlyings'
                                                    ) = 'array'
                                                    THEN contract.terms_json
                                                        -> 'underlyings'
                                                    ELSE '[]'::json
                                                END
                                            ) underlying(value)
                                        ) <> (
                                            SELECT count(DISTINCT
                                                underlying.value ->> 'instrument_id'
                                            )
                                            FROM json_array_elements(
                                                CASE
                                                    WHEN json_typeof(
                                                        contract.terms_json
                                                            -> 'underlyings'
                                                    ) = 'array'
                                                    THEN contract.terms_json
                                                        -> 'underlyings'
                                                    ELSE '[]'::json
                                                END
                                            ) underlying(value)
                                        )
                                    )
                               )
                        ), contract_instrument_reference AS (
                            SELECT
                                contract.portfolio_id,
                                contract.derivative_contract_id,
                                contract.terms_json ->> 'underlying_instrument_id'
                                    AS instrument_id
                            FROM portfolio.derivative_contract_record contract
                            WHERE contract.contract_type = 'option'
                            UNION ALL
                            SELECT
                                contract.portfolio_id,
                                contract.derivative_contract_id,
                                reference.value ->> 'instrument_id'
                                    AS instrument_id
                            FROM portfolio.derivative_contract_record contract
                            CROSS JOIN LATERAL json_array_elements(
                                CASE
                                    WHEN json_typeof(
                                        contract.terms_json
                                            -> 'underlyings'
                                    ) = 'array'
                                    THEN contract.terms_json
                                        -> 'underlyings'
                                    ELSE '[]'::json
                                END
                            ) reference(value)
                            WHERE contract.contract_type = 'fcn'
                        ), invalid_instrument_reference AS (
                            SELECT reference.portfolio_id,
                                   reference.derivative_contract_id
                            FROM contract_instrument_reference reference
                            LEFT JOIN instrument_registry.instrument instrument
                              ON instrument.instrument_id = reference.instrument_id
                            WHERE trim(coalesce(reference.instrument_id, '')) = ''
                               OR instrument.instrument_id IS NULL
                        ), invalid_transaction_reference AS (
                            SELECT transaction.portfolio_id,
                                   transaction.derivative_contract_id
                            FROM portfolio.transaction_record transaction
                            JOIN portfolio.derivative_contract_record contract
                              ON contract.portfolio_id = transaction.portfolio_id
                             AND contract.derivative_contract_id =
                                 transaction.derivative_contract_id
                            WHERE transaction.derivative_contract_id IS NOT NULL
                              AND (
                                  transaction.account_id <> contract.account_id
                                  OR transaction.currency <> contract.currency
                              )
                        ), invalid_option_lifecycle AS (
                            SELECT txn.portfolio_id,
                                   txn.derivative_contract_id
                            FROM portfolio.transaction_record txn
                            JOIN portfolio.derivative_contract_record contract
                              ON contract.portfolio_id = txn.portfolio_id
                             AND contract.derivative_contract_id =
                                 txn.derivative_contract_id
                            WHERE contract.contract_type = 'option'
                              AND (
                                  (
                                      txn.transaction_type = 'maturity_redemption'
                                      AND coalesce(txn.lifecycle_event_type, '')
                                          NOT IN (
                                              'option_long_expiry',
                                              'option_long_cash_settlement'
                                          )
                                  )
                                  OR (
                                      txn.transaction_type = 'lifecycle_event'
                                      AND coalesce(txn.lifecycle_event_type, '')
                                          NOT IN (
                                              'option_writer_expiry',
                                              'option_writer_cash_settlement'
                                          )
                                  )
                                  OR (
                                      txn.lifecycle_event_type IN (
                                          'option_long_expiry',
                                          'option_long_cash_settlement'
                                      )
                                      AND txn.transaction_type <>
                                          'maturity_redemption'
                                  )
                                  OR (
                                      txn.lifecycle_event_type IN (
                                          'option_writer_expiry',
                                          'option_writer_cash_settlement'
                                      )
                                      AND txn.transaction_type <> 'lifecycle_event'
                                  )
                                  OR (
                                      txn.lifecycle_event_type IN (
                                          'option_long_expiry',
                                          'option_writer_expiry'
                                      )
                                      AND (
                                          coalesce(txn.quantity, 0) <= 0
                                          OR coalesce(txn.gross_amount, 0) <> 0
                                          OR coalesce(txn.fees, 0) <> 0
                                          OR coalesce(txn.taxes, 0) <> 0
                                          OR txn.settlement_cash_account_id IS NOT NULL
                                      )
                                  )
                                  OR (
                                      txn.lifecycle_event_type IN (
                                          'option_long_cash_settlement',
                                          'option_writer_cash_settlement'
                                      )
                                      AND (
                                          coalesce(txn.quantity, 0) <= 0
                                          OR coalesce(txn.gross_amount, 0) <= 0
                                          OR txn.settlement_cash_account_id IS NULL
                                      )
                                  )
                                  OR CASE
                                      WHEN pg_input_is_valid(
                                          coalesce(
                                              contract.terms_json ->> 'expiry_date',
                                              ''
                                          ),
                                          'date'
                                      )
                                      THEN (
                                          txn.lifecycle_event_type IN (
                                              'option_long_expiry',
                                              'option_writer_expiry'
                                          )
                                          AND coalesce(
                                              txn.position_effective_date,
                                              txn.trade_date
                                          ) < (
                                              contract.terms_json ->> 'expiry_date'
                                          )::date
                                      ) OR (
                                          txn.lifecycle_event_type IN (
                                              'option_long_cash_settlement',
                                              'option_writer_cash_settlement'
                                          )
                                          AND coalesce(
                                              txn.position_effective_date,
                                              txn.trade_date
                                          ) > (
                                              contract.terms_json ->> 'expiry_date'
                                          )::date
                                      )
                                      ELSE false
                                  END
                              )
                        ), invalid_holding_reference AS (
                            SELECT holding.portfolio_id,
                                   holding.derivative_contract_id
                            FROM portfolio.portfolio_daily_holding_snapshot holding
                            JOIN portfolio.derivative_contract_record contract
                              ON contract.portfolio_id = holding.portfolio_id
                             AND contract.derivative_contract_id =
                                 holding.derivative_contract_id
                            WHERE holding.derivative_contract_id IS NOT NULL
                              AND (
                                  holding.position_reference_id <>
                                      holding.derivative_contract_id
                                  OR holding.account_id <> contract.account_id
                                  OR holding.currency <> contract.currency
                              )
                        )
                        SELECT count(*)
                        FROM (
                            SELECT * FROM malformed_contract
                            UNION ALL
                            SELECT * FROM invalid_instrument_reference
                            UNION ALL
                            SELECT * FROM invalid_transaction_reference
                            UNION ALL
                            SELECT * FROM invalid_option_lifecycle
                            UNION ALL
                            SELECT * FROM invalid_holding_reference
                        ) issue
                    """,
                    detail=(
                        "Portfolio-local derivative terms must be complete, reference "
                        "existing Registry underlyings, keep explicit and cash-consistent "
                        "option outcomes, and remain account/currency consistent across "
                        "contracts, transactions, and holdings."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="market_data_invalid_values",
                    query=f"""
                        SELECT count(*)
                        FROM instrument_registry.instrument_market_data
                        WHERE status NOT IN ('complete', 'partial', 'unavailable')
                           OR trim(value) !~
                              '^[+]?(?:[0-9]+(?:[.][0-9]*)?|[.][0-9]+)(?:[eE][+-]?[0-9]+)?$'
                           OR NOT pg_input_is_valid(trim(value), 'numeric')
                           OR CASE
                                  WHEN trim(value) ~
                                       '^[+]?(?:[0-9]+(?:[.][0-9]*)?|[.][0-9]+)(?:[eE][+-]?[0-9]+)?$'
                                       AND pg_input_is_valid(trim(value), 'numeric')
                                  THEN trim(value)::numeric <= 0
                                  ELSE false
                              END
                    """,
                    detail=(
                        "Every observation must have a canonical status and a finite, "
                        "positive decimal value."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="market_data_currency_mismatch",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument_market_data md
                        JOIN instrument_registry.instrument i USING (instrument_id)
                        WHERE md.currency <> i.currency
                    """,
                    detail="Every observation must use its instrument's canonical currency.",
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="instrument_quote_policy_contract",
                    query=f"""
                        SELECT count(*)
                        FROM instrument_registry.instrument instrument
                        WHERE json_typeof(instrument.quote_selection_policy_json)
                                  IS DISTINCT FROM 'object'
                           OR (
                                SELECT count(*)
                                FROM json_object_keys(
                                    CASE
                                        WHEN json_typeof(instrument.quote_selection_policy_json)
                                             = 'object'
                                        THEN instrument.quote_selection_policy_json
                                        ELSE '{{}}'::json
                                    END
                                ) policy_key
                              ) <> {len(QUOTE_SELECTION_POLICY_ROLES)}
                           OR EXISTS (
                                SELECT 1
                                FROM json_object_keys(
                                    CASE
                                        WHEN json_typeof(instrument.quote_selection_policy_json)
                                             = 'object'
                                        THEN instrument.quote_selection_policy_json
                                        ELSE '{{}}'::json
                                    END
                                ) policy_key
                                WHERE policy_key NOT IN (
                                    {", ".join(_sql_text_literal(role) for role in QUOTE_SELECTION_POLICY_ROLES)}
                                )
                              )
                           OR EXISTS (
                                SELECT 1
                                FROM (VALUES {QUOTE_SELECTION_POLICY_ROLES_SQL}) role(role_name)
                                WHERE json_typeof(
                                          instrument.quote_selection_policy_json -> role.role_name
                                      ) IS DISTINCT FROM 'array'
                                   OR json_array_length(
                                          CASE
                                              WHEN json_typeof(
                                                  instrument.quote_selection_policy_json
                                                  -> role.role_name
                                              ) = 'array'
                                              THEN instrument.quote_selection_policy_json
                                                   -> role.role_name
                                              ELSE '[]'::json
                                          END
                                      ) = 0
                                   OR EXISTS (
                                        SELECT 1
                                        FROM json_array_elements_text(
                                            CASE
                                                WHEN json_typeof(
                                                    instrument.quote_selection_policy_json
                                                    -> role.role_name
                                                ) = 'array'
                                                THEN instrument.quote_selection_policy_json
                                                     -> role.role_name
                                                ELSE '[]'::json
                                            END
                                        ) quote_basis(value)
                                        WHERE quote_basis.value NOT IN ({VALID_QUOTE_BASES_SQL})
                                           OR (
                                                role.role_name = 'valuation'
                                                AND quote_basis.value IN (
                                                    {VALUATION_PROHIBITED_BASES_SQL}
                                                )
                                              )
                                      )
                                   OR (
                                        SELECT count(*)
                                        FROM json_array_elements_text(
                                            CASE
                                                WHEN json_typeof(
                                                    instrument.quote_selection_policy_json
                                                    -> role.role_name
                                                ) = 'array'
                                                THEN instrument.quote_selection_policy_json
                                                     -> role.role_name
                                                ELSE '[]'::json
                                            END
                                        ) quote_basis(value)
                                      ) <> (
                                        SELECT count(DISTINCT quote_basis.value)
                                        FROM json_array_elements_text(
                                            CASE
                                                WHEN json_typeof(
                                                    instrument.quote_selection_policy_json
                                                    -> role.role_name
                                                ) = 'array'
                                                THEN instrument.quote_selection_policy_json
                                                     -> role.role_name
                                                ELSE '[]'::json
                                            END
                                        ) quote_basis(value)
                                      )
                              )
                    """,
                    detail=(
                        "Every instrument must persist exactly five non-empty, unique, "
                        "canonical quote-selection roles."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="market_data_price_contract",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument_market_data md
                        JOIN instrument_registry.instrument i USING (instrument_id)
                        WHERE NOT (
                                  (md.metric_family = 'price' AND md.quote_basis IN (
                                      'last', 'close', 'adjusted_close', 'par'
                                  ))
                                  OR (md.metric_family = 'nav' AND md.quote_basis IN (
                                      'official_nav', 'total_return_nav'
                                  ))
                                  OR (md.metric_family = 'fx' AND md.quote_basis = 'spot')
                              )
                           OR ((i.instrument_type = 'fx') <>
                               (md.metric_family = 'fx' AND md.quote_basis = 'spot'))
                           OR md.price_unit <> CASE
                                  WHEN i.instrument_type = 'fx' THEN 'rate'
                                  ELSE 'per_unit'
                              END
                           OR md.price_scale <> 1
                    """,
                    detail=(
                        "Quote identity, instrument type, price unit, and price scale must "
                        "form one canonical contract."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="market_data_fx_identity_contract",
                    query=f"""
                        WITH maintained_fx(instrument_id, base_currency, quote_currency) AS (
                            VALUES {MAINTAINED_FX_IDENTITIES_SQL}
                        ), violations AS (
                            SELECT 'instrument:' || i.instrument_id AS violation_id
                            FROM instrument_registry.instrument i
                            LEFT JOIN maintained_fx maintained
                              ON maintained.instrument_id = i.instrument_id
                            WHERE (i.instrument_type = 'fx' AND (
                                      maintained.instrument_id IS NULL
                                      OR i.currency <> maintained.quote_currency
                                  ))
                               OR (i.instrument_type <> 'fx' AND maintained.instrument_id IS NOT NULL)
                            UNION ALL
                            SELECT 'market-data:' || md.instrument_market_data_id::text
                            FROM instrument_registry.instrument_market_data md
                            JOIN instrument_registry.instrument i USING (instrument_id)
                            LEFT JOIN maintained_fx maintained
                              ON maintained.instrument_id = md.instrument_id
                            WHERE (
                                      md.metric_family = 'fx'
                                      OR md.quote_basis = 'spot'
                                      OR i.instrument_type = 'fx'
                                  )
                              AND NOT (
                                  i.instrument_type = 'fx'
                                  AND md.metric_family = 'fx'
                                  AND md.quote_basis = 'spot'
                                  AND maintained.instrument_id IS NOT NULL
                                  AND i.currency = maintained.quote_currency
                                  AND md.currency = maintained.quote_currency
                              )
                        )
                        SELECT count(*) FROM violations
                    """,
                    detail=(
                        "FX instruments and observations must use a maintained pair identity, "
                        "fx/spot semantics, and the pair quote currency."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="market_data_logical_duplicates",
                    query="""
                        SELECT count(*)
                        FROM (
                            SELECT instrument_id, metric_family, quote_basis, as_of_date
                            FROM instrument_registry.instrument_market_data
                            GROUP BY instrument_id, metric_family, quote_basis, as_of_date
                            HAVING count(*) > 1
                        ) duplicates
                    """,
                    detail="A quote basis may have only one observation per instrument and date.",
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="valuation_policy_total_return_basis",
                    query=f"""
                        SELECT count(*)
                        FROM instrument_registry.instrument instrument
                        WHERE coalesce(instrument.lifecycle_state_json ->> 'status', 'active') = 'active'
                          AND EXISTS (
                              SELECT 1
                              FROM json_array_elements_text(
                                  CASE
                                      WHEN json_typeof(
                                          instrument.quote_selection_policy_json -> 'valuation'
                                      ) = 'array'
                                      THEN instrument.quote_selection_policy_json -> 'valuation'
                                      ELSE '[]'::json
                                  END
                              ) basis(value)
                              WHERE basis.value IN (
                                  {VALUATION_PROHIBITED_BASES_SQL}
                              )
                          )
                    """,
                    detail=(
                        "Ledger valuation policies must use unadjusted tradable/NAV bases; "
                        "total-return bases would double-count distributions or share actions."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="watchlist_index_return_semantics_contract",
                    query="""
                        WITH active_indices AS (
                            SELECT
                                instrument.instrument_id,
                                lower(trim(coalesce(
                                    instrument.source_settings_json::jsonb
                                        ->> 'return_semantics',
                                    ''
                                ))) AS configured_return_kind
                            FROM instrument_registry.instrument instrument
                            WHERE instrument.instrument_type = 'index'
                              AND coalesce(
                                    instrument.lifecycle_state_json::jsonb ->> 'status',
                                    'active'
                                  ) = 'active'
                        ), observed AS (
                            SELECT
                                active.instrument_id,
                                active.configured_return_kind,
                                chart.payload_json::jsonb
                                    #>> '{selected_series,quote_basis}'
                                    AS selected_quote_basis,
                                chart.payload_json::jsonb
                                    #>> '{selected_series,return_kind}'
                                    AS published_return_kind,
                                chart.payload_json::jsonb
                                    #>> '{selected_series,basis_type}'
                                    AS published_basis_type
                            FROM active_indices active
                            LEFT JOIN watchlist.instrument_chart_read_model chart
                              ON chart.instrument_id = active.instrument_id
                        ), expected AS (
                            SELECT
                                observed.*,
                                CASE
                                    WHEN selected_quote_basis IN (
                                        'adjusted_close', 'total_return_nav'
                                    ) THEN 'total_return'
                                    WHEN selected_quote_basis IN ('close', 'last')
                                      AND configured_return_kind IN (
                                          'price_return', 'total_return'
                                      ) THEN configured_return_kind
                                    ELSE NULL
                                END AS expected_return_kind
                            FROM observed
                        )
                        SELECT count(*)
                        FROM expected
                        WHERE selected_quote_basis IS NULL
                           OR published_return_kind
                              IS DISTINCT FROM expected_return_kind
                           OR (
                                expected_return_kind = 'total_return'
                                AND published_basis_type
                                    IS DISTINCT FROM 'nav_with_dividend'
                              )
                           OR (
                                expected_return_kind = 'price_return'
                                AND published_basis_type IS DISTINCT FROM 'nav'
                              )
                    """,
                    detail=(
                        "Every active index Watchlist chart must preserve the Registry "
                        "return semantics independently from its close/last field; confirmed "
                        "total return maps to nav_with_dividend, confirmed price return maps "
                        "to nav, and unknown semantics must remain unpublished so relative "
                        "metrics fail closed."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="fund_nav_current_projection_contract",
                    query=f"""
                        SELECT count(*)
                        FROM instrument_registry.fund_nav_current_projection current
                        JOIN instrument_registry.fund_nav_projection_run run
                          ON run.fund_nav_projection_run_id =
                             current.fund_nav_projection_run_id
                        JOIN instrument_registry.instrument instrument
                          ON instrument.instrument_id=current.instrument_id
                        WHERE coalesce(
                                instrument.lifecycle_state_json ->> 'status',
                                'active'
                              ) = 'active'
                          AND (
                            run.instrument_id <> current.instrument_id
                            OR run.method_version <>
                               {FUND_NAV_PROJECTION_METHOD_VERSION_SQL}
                            OR (
                                run.projection_kind = 'event_derived'
                                AND run.projection_status IN ('complete', 'partial')
                                AND (
                                    SELECT count(*)
                                    FROM instrument_registry.fund_nav_adjustment_factor anchor
                                    WHERE anchor.fund_nav_projection_run_id =
                                          run.fund_nav_projection_run_id
                                      AND anchor.evidence_kind IN (
                                          'zero_cash_anchor',
                                          'window_normalized_anchor'
                                      )
                                      AND anchor.factor_level::numeric = 1
                                      AND anchor.anchor_date = run.anchor_date
                                      AND anchor.as_of_date = run.anchor_date
                                      AND anchor.fund_nav_event_id IS NULL
                                      AND anchor.fund_nav_reinvestment_evidence_id IS NULL
                                      AND anchor.previous_fund_nav_adjustment_factor_id IS NULL
                                ) <> 1
                              )
                          )
                    """,
                    detail=(
                        "Every active fund current projection must use the deployed "
                        f"{FUND_NAV_PROJECTION_METHOD_VERSION} method; complete or "
                        "partial event-derived series require "
                        "one auditable unit-factor zero-cash or window-normalized anchor."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="held_fund_recent_total_return_coverage",
                    query="""
                        WITH latest_portfolio_dates AS (
                            SELECT portfolio_id, max(as_of_date) AS as_of_date
                            FROM portfolio.portfolio_daily_snapshot
                            GROUP BY portfolio_id
                        ), held_funds AS (
                            SELECT
                                holding.instrument_id,
                                max(latest.as_of_date) AS reference_date
                            FROM portfolio.portfolio_daily_holding_snapshot holding
                            JOIN latest_portfolio_dates latest
                              ON latest.portfolio_id = holding.portfolio_id
                             AND latest.as_of_date = holding.as_of_date
                            JOIN instrument_registry.instrument instrument
                              ON instrument.instrument_id = holding.instrument_id
                             AND instrument.instrument_type IN ('public_fund', 'private_fund')
                            WHERE holding.quantity <> 0
                            GROUP BY holding.instrument_id
                        ), coverage AS (
                            SELECT
                                held.instrument_id,
                                count(*) FILTER (
                                    WHERE quote.quote_basis = 'official_nav'
                                      AND quote.status = 'complete'
                                ) AS official_count,
                                count(*) FILTER (
                                    WHERE quote.quote_basis = 'total_return_nav'
                                      AND quote.status = 'complete'
                                ) AS total_return_count,
                                max(quote.as_of_date) FILTER (
                                    WHERE quote.quote_basis = 'official_nav'
                                      AND quote.status = 'complete'
                                ) AS latest_official_date,
                                max(quote.as_of_date) FILTER (
                                    WHERE quote.quote_basis = 'total_return_nav'
                                      AND quote.status = 'complete'
                                ) AS latest_total_return_date
                            FROM held_funds held
                            LEFT JOIN instrument_registry.instrument_market_data quote
                              ON quote.instrument_id = held.instrument_id
                             AND quote.metric_family = 'nav'
                             AND quote.as_of_date BETWEEN
                                 held.reference_date - 120 AND held.reference_date
                            GROUP BY held.instrument_id
                        )
                        SELECT count(*)
                        FROM coverage
                        WHERE official_count >= 10
                          AND (
                              total_return_count * 2 < official_count
                              OR latest_total_return_date IS NULL
                              OR latest_total_return_date
                                 < latest_official_date - 14
                          )
                    """,
                    detail=(
                        "Each currently held fund with at least ten recent official NAV "
                        "observations must retain at least half of that 120-day window as "
                        "current total return; this catches stalled projection chains."
                    ),
                )
            )

            max_nav_error = float(
                _scalar(
                    cursor,
                    """
                    WITH holdings AS (
                        SELECT
                            portfolio_id,
                            as_of_date,
                            sum(market_value_base)::numeric AS market_value,
                            sum(
                                CASE
                                    WHEN instrument_id LIKE 'pending:%'
                                    THEN market_value_base
                                    ELSE 0
                                END
                            )::numeric AS materialized_pending_settlement
                        FROM portfolio.portfolio_daily_holding_snapshot
                        GROUP BY portfolio_id, as_of_date
                    )
                    SELECT greatest(
                        coalesce(max(abs(
                            coalesce(holdings.market_value, 0)
                            - snapshot.nav::numeric
                        )) FILTER (
                            WHERE snapshot.valuation_coverage_state = 'complete'
                        ), 0),
                        coalesce(max(abs(
                            coalesce(holdings.materialized_pending_settlement, 0)
                            - (snapshot.snapshot_json ->> 'pending_settlement')::numeric
                        )), 0),
                        CASE WHEN count(*) FILTER (
                            WHERE (
                                snapshot.valuation_coverage_state = 'complete'
                                AND snapshot.nav IS NULL
                            )
                               OR snapshot.snapshot_json ->> 'pending_settlement' IS NULL
                        ) > 0 THEN 1 ELSE 0 END
                    )
                    FROM portfolio.portfolio_daily_snapshot snapshot
                    LEFT JOIN holdings USING (portfolio_id, as_of_date)
                    """,
                )
                or 0.0
            )
            checks.append(
                AuditCheck(
                    name="portfolio_nav_reconciliation",
                    status="pass" if max_nav_error <= 1e-6 else "fail",
                    value=max_nav_error,
                    limit=1e-6,
                    detail=(
                        "Every completely valued NAV must equal all materialized holding "
                        "value, and pending holding rows must reconcile to the snapshot "
                        "pending-settlement total."
                    ),
                )
            )

            max_twr_error = float(
                _scalar(
                    cursor,
                    """
                    WITH linked AS (
                        SELECT
                            portfolio_id,
                            daily_twr,
                            cumulative_twr,
                            exp(sum(ln(
                                CASE WHEN daily_twr > -1 THEN 1 + daily_twr ELSE NULL END
                            )) OVER (
                                PARTITION BY portfolio_id
                                ORDER BY as_of_date
                                ROWS UNBOUNDED PRECEDING
                            )) - 1 AS recomputed_twr
                        FROM portfolio.portfolio_daily_snapshot
                        WHERE daily_twr IS NOT NULL
                          AND coalesce(
                              (snapshot_json ->> 'return_chain_continuous')::boolean,
                              false
                          )
                    )
                    SELECT greatest(
                        coalesce(max(abs(cumulative_twr - recomputed_twr)), 0),
                        CASE WHEN count(*) FILTER (
                            WHERE daily_twr <= -1 OR cumulative_twr IS NULL
                        ) > 0 THEN 1 ELSE 0 END
                    )
                    FROM linked
                    """,
                )
                or 0.0
            )
            checks.append(
                AuditCheck(
                    name="portfolio_twr_geometric_link",
                    status="pass" if max_twr_error <= 1e-12 else "fail",
                    value=max_twr_error,
                    limit=1e-12,
                    detail=(
                        "Stored cumulative TWR on continuous return chains must equal the "
                        "geometric link of daily TWR."
                    ),
                )
            )

            max_drawdown_error = float(
                _scalar(
                    cursor,
                    """
                    WITH wealth AS (
                        SELECT
                            portfolio_id,
                            as_of_date,
                            drawdown,
                            1 + cumulative_twr AS wealth_index,
                            greatest(
                                1.0,
                                max(1 + cumulative_twr) OVER (
                                    PARTITION BY portfolio_id
                                    ORDER BY as_of_date
                                    ROWS UNBOUNDED PRECEDING
                                )
                            ) AS peak_index
                        FROM portfolio.portfolio_daily_snapshot
                        WHERE cumulative_twr IS NOT NULL
                    )
                    SELECT greatest(
                        coalesce(max(abs(
                            drawdown - (wealth_index / peak_index - 1)
                        )), 0),
                        CASE WHEN count(*) FILTER (
                            WHERE drawdown IS NULL OR peak_index IS NULL OR peak_index <= 0
                        ) > 0 THEN 1 ELSE 0 END
                    )
                    FROM wealth
                    """,
                )
                or 0.0
            )
            checks.append(
                AuditCheck(
                    name="portfolio_drawdown_from_twr",
                    status="pass" if max_drawdown_error <= 1e-12 else "fail",
                    value=max_drawdown_error,
                    limit=1e-12,
                    detail="Drawdown must be derived from the TWR wealth index, not asset NAV.",
                )
            )

            checks.append(
                _count_check(
                    cursor,
                    name="holding_valuation_basis_contract",
                    query="""
                        SELECT count(*)
                        FROM portfolio.portfolio_daily_holding_snapshot holding
                        WHERE (
                                holding.holding_kind = 'position'
                                AND coalesce(
                                    holding.holding_json ->> 'valuation_basis',
                                    ''
                                ) NOT IN ('market_quote', 'carried_cost')
                              )
                           OR (
                                holding.holding_kind = 'position'
                                AND holding.holding_json ->> 'valuation_basis'
                                    = 'market_quote'
                                AND holding.last_price IS NULL
                              )
                           OR (
                                holding.holding_kind = 'position'
                                AND holding.holding_json ->> 'valuation_basis'
                                    = 'carried_cost'
                                AND holding.last_price IS NOT NULL
                              )
                           OR (
                                holding.holding_kind = 'option_obligation'
                                AND (
                                    holding.holding_json ->> 'valuation_basis'
                                        IS DISTINCT FROM 'premium_liability'
                                    OR holding.last_price IS NOT NULL
                                )
                              )
                    """,
                    detail=(
                        "Market-valued positions require a quote; event-carried positions "
                        "and written-option liabilities must remain explicitly unquoted."
                    ),
                )
            )

            valuation_mismatches = int(
                _scalar(
                    cursor,
                    """
                    WITH holdings AS (
                        SELECT
                            snapshot.*,
                            snapshot.holding_json ->> 'quote_basis' AS quote_basis
                        FROM portfolio.portfolio_daily_holding_snapshot snapshot
                        WHERE snapshot.holding_kind = 'position'
                          AND snapshot.holding_json ->> 'valuation_basis'
                              = 'market_quote'
                          AND snapshot.instrument_id NOT LIKE 'cash:%'
                          AND snapshot.instrument_id NOT LIKE 'pending:%'
                    )
                    SELECT count(*)
                    FROM holdings
                    LEFT JOIN LATERAL (
                        SELECT value::double precision AS expected_price
                        FROM instrument_registry.instrument_market_data quote
                        WHERE quote.instrument_id = holdings.instrument_id
                          AND quote.quote_basis = holdings.quote_basis
                          AND quote.as_of_date <= holdings.as_of_date
                          AND quote.status = 'complete'
                        ORDER BY quote.as_of_date DESC
                        LIMIT 1
                    ) selected_quote ON true
                    WHERE holdings.last_price IS NULL
                       OR selected_quote.expected_price IS NULL
                       OR abs(holdings.last_price - selected_quote.expected_price) > 1e-10
                    """,
                )
                or 0
            )
            checks.append(
                AuditCheck(
                    name="portfolio_valuation_quote_match",
                    status="pass" if valuation_mismatches == 0 else "fail",
                    value=valuation_mismatches,
                    limit=0,
                    detail="Holding valuation must match its declared unadjusted valuation basis.",
                )
            )

            checks.append(
                _count_check(
                    cursor,
                    name="analytics_scope_missing_effective_selection",
                    query="""
                        SELECT count(*)
                        FROM portfolio.portfolio_record portfolio
                        WHERE portfolio.default_planning_taxonomy_id IS NOT NULL
                          AND NOT EXISTS (
                            SELECT 1
                            FROM portfolio.analytics_taxonomy_selection_record selection
                            WHERE selection.portfolio_id = portfolio.portfolio_id
                              AND selection.taxonomy_id IS NOT NULL
                              AND selection.superseded_by_selection_id IS NULL
                              AND selection.effective_from <=
                                  coalesce(portfolio.as_of_date, CURRENT_DATE)
                              AND (
                                  selection.effective_to IS NULL
                                  OR selection.effective_to >=
                                     coalesce(portfolio.as_of_date, CURRENT_DATE)
                              )
                        )
                    """,
                    detail=(
                        "Every portfolio that declares a default planning taxonomy needs an "
                        "effective, explicit analytics taxonomy selection before Risk or Risk "
                        "Budget can be declared ready; the runtime intentionally fails closed "
                        "without one."
                    ),
                    warning_only=True,
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="analytics_scope_incomplete_configuration",
                    query="""
                        WITH effective_selection AS (
                            SELECT DISTINCT ON (portfolio.portfolio_id)
                                   portfolio.portfolio_id,
                                   coalesce(portfolio.as_of_date, CURRENT_DATE) AS as_of_date,
                                   selection.taxonomy_id
                            FROM portfolio.portfolio_record portfolio
                            JOIN portfolio.analytics_taxonomy_selection_record selection
                              ON selection.portfolio_id = portfolio.portfolio_id
                             AND selection.taxonomy_id IS NOT NULL
                             AND selection.superseded_by_selection_id IS NULL
                             AND selection.effective_from <=
                                 coalesce(portfolio.as_of_date, CURRENT_DATE)
                             AND (
                                 selection.effective_to IS NULL
                                 OR selection.effective_to >=
                                    coalesce(portfolio.as_of_date, CURRENT_DATE)
                             )
                            ORDER BY portfolio.portfolio_id,
                                     selection.effective_from DESC,
                                     selection.selection_version DESC
                        )
                        SELECT count(*)
                        FROM effective_selection selection
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM portfolio.taxonomy_configuration_revision configuration
                            WHERE configuration.portfolio_id = selection.portfolio_id
                              AND configuration.taxonomy_id = selection.taxonomy_id
                              AND configuration.superseded_by_revision_id IS NULL
                              AND configuration.effective_from <= selection.as_of_date
                              AND (
                                  configuration.effective_to IS NULL
                                  OR configuration.effective_to >= selection.as_of_date
                              )
                        )
                           OR NOT EXISTS (
                            SELECT 1
                            FROM portfolio.analytics_scope_policy_record policy
                            WHERE policy.portfolio_id = selection.portfolio_id
                              AND policy.taxonomy_id = selection.taxonomy_id
                              AND policy.taxonomy_node_id = '__root__'
                              AND policy.superseded_by_policy_id IS NULL
                              AND policy.effective_from <= selection.as_of_date
                              AND (
                                  policy.effective_to IS NULL
                                  OR policy.effective_to >= selection.as_of_date
                              )
                        )
                           OR NOT EXISTS (
                            SELECT 1
                            FROM portfolio.analytics_scope_policy_record policy
                            WHERE policy.portfolio_id = selection.portfolio_id
                              AND policy.taxonomy_id = selection.taxonomy_id
                              AND policy.taxonomy_node_id = '__unassigned__'
                              AND policy.superseded_by_policy_id IS NULL
                              AND policy.effective_from <= selection.as_of_date
                              AND (
                                  policy.effective_to IS NULL
                                  OR policy.effective_to >= selection.as_of_date
                              )
                        )
                    """,
                    detail=(
                        "An effective analytics selection needs a point-in-time taxonomy "
                        "configuration plus effective root and unassigned scope policies."
                    ),
                    warning_only=True,
                )
            )

            checks.append(
                _count_check(
                    cursor,
                    name="taxonomy_target_sum",
                    query="""
                        WITH target_totals AS (
                            SELECT
                                target_set.target_set_id,
                                target_set.weight_enabled,
                                target_set.risk_budget_enabled,
                                sum(line.target_weight) AS weight_total,
                                sum(line.target_risk_share) AS risk_total
                            FROM portfolio.target_set_record target_set
                            LEFT JOIN portfolio.target_set_line_record line
                                USING (target_set_id)
                            WHERE target_set.status = 'active'
                            GROUP BY
                                target_set.target_set_id,
                                target_set.weight_enabled,
                                target_set.risk_budget_enabled
                        )
                        SELECT count(*)
                        FROM target_totals
                        WHERE (weight_enabled AND abs(coalesce(weight_total, 0) - 1) > 1e-8)
                           OR (risk_budget_enabled AND abs(coalesce(risk_total, 0) - 1) > 1e-8)
                    """,
                    detail="Each enabled target dimension must sum to 100% within its scope.",
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="taxonomy_negative_targets",
                    query="""
                        SELECT count(*)
                        FROM portfolio.target_set_line_record
                        WHERE target_weight < 0 OR target_risk_share < 0
                    """,
                    detail="Long-only planning targets may not contain negative shares.",
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="reserved_cash_taxonomy_nodes",
                    query="""
                        SELECT count(*)
                        FROM portfolio.taxonomy_node_record node
                        JOIN portfolio.taxonomy_record taxonomy USING (taxonomy_id)
                        WHERE taxonomy.primary_assignment_scope = 'instrument'
                          AND (
                              lower(trim(coalesce(node.node_code, ''))) = 'cash'
                              OR lower(trim(node.node_name)) IN ('cash', '现金')
                          )
                    """,
                    detail=(
                        "Instrument taxonomies use the canonical system cash member, "
                        "not reserved Cash-labelled nodes."
                    ),
                )
            )
            has_assignment_windows = _column_exists(
                cursor,
                "portfolio",
                "taxonomy_assignment_record",
                "effective_from",
            ) and _column_exists(
                cursor,
                "portfolio",
                "taxonomy_assignment_record",
                "effective_to",
            )
            if has_assignment_windows:
                checks.append(
                    _count_check(
                        cursor,
                        name="taxonomy_assignment_overlap",
                        query="""
                            WITH assignments AS (
                                SELECT
                                    assignment.*,
                                    coalesce(effective_from, '-infinity'::date) AS valid_from,
                                    coalesce(effective_to, 'infinity'::date) AS valid_to
                                FROM portfolio.taxonomy_assignment_record assignment
                                WHERE status = 'active'
                            )
                            SELECT count(*)
                            FROM assignments left_assignment
                            JOIN assignments right_assignment
                              ON left_assignment.assignment_id < right_assignment.assignment_id
                             AND left_assignment.taxonomy_id = right_assignment.taxonomy_id
                             AND left_assignment.target_scope = right_assignment.target_scope
                             AND left_assignment.target_entity_id = right_assignment.target_entity_id
                             AND daterange(
                                 left_assignment.valid_from,
                                 left_assignment.valid_to,
                                 '[]'
                             ) && daterange(
                                 right_assignment.valid_from,
                                 right_assignment.valid_to,
                                 '[]'
                             )
                        """,
                        detail=(
                            "One entity cannot have overlapping active assignments in one taxonomy."
                        ),
                    )
                )
            else:
                checks.append(
                    _count_check(
                        cursor,
                        name="taxonomy_assignment_overlap",
                        query="""
                            SELECT count(*)
                            FROM (
                                SELECT taxonomy_id, target_scope, target_entity_id
                                FROM portfolio.taxonomy_assignment_record
                                GROUP BY taxonomy_id, target_scope, target_entity_id
                                HAVING count(*) > 1
                            ) duplicates
                        """,
                        detail=(
                            "One entity may have only one assignment in a taxonomy's "
                            "current-state model."
                        ),
                    )
                )
            assignment_date_predicate = (
                """
                              AND (
                                  assignment.effective_from IS NULL
                                  OR assignment.effective_from <= holding.as_of_date
                              )
                              AND (
                                  assignment.effective_to IS NULL
                                  OR assignment.effective_to >= holding.as_of_date
                              )
                """
                if has_assignment_windows
                else ""
            )
            checks.append(
                _count_check(
                    cursor,
                    name="current_unassigned_planning_holdings",
                    query="""
                        WITH latest AS (
                            SELECT portfolio_id, max(as_of_date) AS as_of_date
                            FROM portfolio.portfolio_daily_snapshot
                            GROUP BY portfolio_id
                        ), current_holdings AS (
                            SELECT DISTINCT
                                holding.portfolio_id,
                                holding.instrument_id,
                                latest.as_of_date
                            FROM portfolio.portfolio_daily_holding_snapshot holding
                            JOIN latest
                              ON latest.portfolio_id = holding.portfolio_id
                             AND latest.as_of_date = holding.as_of_date
                            WHERE holding.instrument_id NOT LIKE 'cash:%'
                              AND holding.holding_kind = 'position'
                              AND holding.holding_json ->> 'valuation_basis'
                                  = 'market_quote'
                              AND holding.quantity <> 0
                        ), planning_taxonomies AS (
                            SELECT taxonomy_id, portfolio_id
                            FROM portfolio.taxonomy_record
                            WHERE planning_enabled = true AND status = 'active'
                        )
                        SELECT count(*)
                        FROM current_holdings holding
                        JOIN planning_taxonomies taxonomy USING (portfolio_id)
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM portfolio.taxonomy_assignment_record assignment
                            WHERE assignment.taxonomy_id = taxonomy.taxonomy_id
                              AND assignment.target_scope = 'instrument'
                              AND assignment.target_entity_id = holding.instrument_id
                              AND assignment.status = 'active'
                    """
                    + assignment_date_predicate
                    + """
                        )
                    """,
                    detail=(
                        "Unassigned non-cash holdings must block or explicitly qualify a "
                        "taxonomy risk-budget solve."
                    ),
                    warning_only=True,
                )
            )

            checks.append(
                _count_check(
                    cursor,
                    name="price_bar_contract",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument_price_bar bar
                        JOIN instrument_registry.instrument instrument
                          ON instrument.instrument_id = bar.instrument_id
                        LEFT JOIN instrument_registry.instrument_market_data close_quote
                          ON close_quote.instrument_id = bar.instrument_id
                         AND close_quote.metric_family = 'price'
                         AND close_quote.quote_basis = 'close'
                         AND close_quote.as_of_date = bar.as_of_date
                         AND close_quote.currency = bar.currency
                        WHERE instrument.instrument_type NOT IN ('etf', 'equity', 'index')
                           OR bar.currency <> instrument.currency
                           OR close_quote.instrument_market_data_id IS NULL
                           OR abs(
                               bar.close_price::numeric - close_quote.value::numeric
                           ) > 0.000000000001
                    """,
                    detail=(
                        "Every raw OHLCV bar must belong to a listed instrument, use its "
                        "canonical currency, and reconcile to the same-date raw close."
                    ),
                )
            )

            checks.append(
                _count_check(
                    cursor,
                    name="corporate_action_event_integrity",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.corporate_action_event event
                        WHERE event.action_type <> 'share_split'
                           OR event.status NOT IN ('detected', 'confirmed', 'cancelled')
                           OR event.new_units !~ '^[0-9]+([.][0-9]+)?$'
                           OR event.old_units !~ '^[0-9]+([.][0-9]+)?$'
                           OR event.new_units::numeric <= 0
                           OR event.old_units::numeric <= 0
                           OR event.new_units::numeric = event.old_units::numeric
                           OR event.record_date > event.effective_date
                           OR event.quantity_rounding NOT IN (
                               'exact', 'truncate', 'round_half_up', 'cash_in_lieu'
                           )
                    """,
                    detail=(
                        "Corporate-action ratios, dates, status, and fractional-unit treatment "
                        "must be valid before an event can reach a portfolio ledger."
                    ),
                )
            )

            candidate_cte = """
                WITH first_trade AS (
                    SELECT transaction.instrument_id, min(transaction.trade_date) AS first_trade_date
                    FROM portfolio.transaction_record transaction
                    JOIN instrument_registry.instrument instrument
                      ON instrument.instrument_id = transaction.instrument_id
                    WHERE instrument.instrument_type IN ('etf', 'equity')
                    GROUP BY transaction.instrument_id
                ), quote_pairs AS (
                    SELECT
                        close_quote.instrument_id,
                        close_quote.as_of_date,
                        close_quote.value::numeric AS close_value,
                        adjusted_quote.value::numeric AS adjusted_value
                    FROM instrument_registry.instrument_market_data close_quote
                    JOIN instrument_registry.instrument_market_data adjusted_quote
                      ON adjusted_quote.instrument_id = close_quote.instrument_id
                     AND adjusted_quote.as_of_date = close_quote.as_of_date
                     AND adjusted_quote.currency = close_quote.currency
                     AND adjusted_quote.quote_basis = 'adjusted_close'
                     AND adjusted_quote.status = 'complete'
                    WHERE close_quote.quote_basis = 'close'
                      AND close_quote.status = 'complete'
                ), changes AS (
                    SELECT
                        instrument_id,
                        as_of_date,
                        close_value,
                        adjusted_value,
                        lag(close_value) OVER (
                            PARTITION BY instrument_id ORDER BY as_of_date
                        ) AS previous_close,
                        lag(adjusted_value) OVER (
                            PARTITION BY instrument_id ORDER BY as_of_date
                        ) AS previous_adjusted,
                        lag(adjusted_value / nullif(close_value, 0)) OVER (
                            PARTITION BY instrument_id ORDER BY as_of_date
                        ) AS previous_adjustment_ratio
                    FROM quote_pairs
                ), candidates AS (
                    SELECT change.instrument_id, change.as_of_date
                    FROM changes change
                    JOIN first_trade USING (instrument_id)
                    WHERE change.as_of_date >= first_trade.first_trade_date
                      AND change.previous_close IS NOT NULL
                      AND change.previous_adjusted IS NOT NULL
                      AND change.previous_adjustment_ratio IS NOT NULL
                      AND abs(
                          (change.adjusted_value / nullif(change.close_value, 0))
                          / nullif(change.previous_adjustment_ratio, 0) - 1
                      ) >= 0.20
                      AND abs(change.close_value / nullif(change.previous_close, 0) - 1) >= 0.15
                      AND abs(change.adjusted_value / nullif(change.previous_adjusted, 0) - 1) <= 0.25
                )
            """
            confirmed_covered_count = int(
                _scalar(
                    cursor,
                    candidate_cte
                    + """
                        SELECT count(*)
                        FROM candidates candidate
                        WHERE EXISTS (
                            SELECT 1
                            FROM instrument_registry.corporate_action_event event
                            WHERE event.instrument_id = candidate.instrument_id
                              AND event.effective_date = candidate.as_of_date
                              AND event.action_type = 'share_split'
                              AND event.status = 'confirmed'
                        )
                    """,
                )
                or 0
            )
            checks.append(
                AuditCheck(
                    name="held_confirmed_share_split_events_covered",
                    status="pass",
                    value=confirmed_covered_count,
                    limit="informational",
                    detail=(
                        "Provider factor/price discontinuities matched to issuer/exchange/CSD-confirmed "
                        "share events are covered by effective-date quantity and carry-cost ledger logic."
                    ),
                )
            )
            checks.append(
                _count_check(
                    cursor,
                    name="held_detected_or_uncovered_share_adjustments",
                    query=candidate_cte
                    + """
                        SELECT count(*)
                        FROM candidates candidate
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM instrument_registry.corporate_action_event event
                            WHERE event.instrument_id = candidate.instrument_id
                              AND event.effective_date = candidate.as_of_date
                              AND event.action_type = 'share_split'
                              AND event.status = 'confirmed'
                        )
                    """,
                    detail=(
                        "A large adjusted/raw factor change plus an inverse raw-price move can be a "
                        "cash distribution, share split, consolidation, or another adjustment. It is "
                        "never posted automatically; issuer/exchange/CSD evidence must confirm ratio, "
                        "record/effective dates, and fractional-unit treatment."
                    ),
                    warning_only=True,
                )
            )

            checks.append(
                _count_check(
                    cursor,
                    name="listed_total_return_coverage",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument instrument
                        WHERE instrument.instrument_type IN ('etf', 'equity')
                          AND (instrument.lifecycle_state_json ->> 'status') = 'active'
                          AND EXISTS (
                              SELECT 1
                              FROM instrument_registry.instrument_market_data close_quote
                              WHERE close_quote.instrument_id = instrument.instrument_id
                                AND close_quote.metric_family = 'price'
                                AND close_quote.quote_basis = 'close'
                                AND close_quote.status = 'complete'
                                AND NOT EXISTS (
                                    SELECT 1
                                    FROM instrument_registry.instrument_market_data adjusted_quote
                                    WHERE adjusted_quote.instrument_id = close_quote.instrument_id
                                      AND adjusted_quote.metric_family = 'price'
                                      AND adjusted_quote.quote_basis = 'adjusted_close'
                                      AND adjusted_quote.as_of_date = close_quote.as_of_date
                                      AND adjusted_quote.currency = close_quote.currency
                                      AND adjusted_quote.status = 'complete'
                                )
                          )
                    """,
                    detail=(
                        "Every complete listed-security close needs a same-date adjusted_close "
                        "before total-return risk and performance are considered complete."
                    ),
                    warning_only=True,
                )
            )
    return checks


def detect_schema_profile(database_url: str) -> SchemaProfile:
    database_url = _normalize_database_url(database_url)
    with psycopg.connect(database_url, autocommit=False) as connection:
        with connection.cursor() as cursor:
            _begin_read_only(cursor)
            return _detect_schema_profile(cursor)


def run_audit_report(database_url: str) -> tuple[SchemaProfile, list[AuditCheck]]:
    database_url = _normalize_database_url(database_url)
    profile = detect_schema_profile(database_url)
    if profile.family == "flat-table":
        return profile, _run_flat_table_audit(database_url)
    return profile, _unsupported_checks(profile)


def run_audit(database_url: str) -> list[AuditCheck]:
    """Return only the check list for callers that do not need schema metadata."""
    return run_audit_report(database_url)[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Return non-zero for warnings as well as failed integrity checks.",
    )
    args = parser.parse_args(argv)

    configured_database_url = _environment_database_url()
    if not configured_database_url:
        parser.error(
            "a database URL is required via PORTFOLIO_OPS_LOCAL_DATABASE_URL"
        )
    try:
        database_url = _normalize_database_url(configured_database_url)
        database_name = _database_name(database_url)
    except (ValueError, psycopg.Error):
        parser.error("the database URL must be a valid PostgreSQL URL naming a database")

    try:
        profile, checks = run_audit_report(database_url)
    except psycopg.Error as error:
        profile = SchemaProfile(
            family="unavailable",
            status="unsupported",
            versions={},
            reason="Database audit execution failed before completion.",
            capabilities={},
        )
        checks = [
            AuditCheck(
                name="database_audit_execution",
                status="fail",
                value={
                    "error_type": type(error).__name__,
                    "sqlstate": getattr(error, "sqlstate", None),
                    "message": str(error),
                },
                limit={"error": None},
                detail=(
                    "The read-only audit could not complete; no missing relation or "
                    "query error is treated as a pass."
                ),
            )
        ]
    failed = [check for check in checks if check.status == "fail"]
    warnings = [check for check in checks if check.status == "warning"]
    payload = {
        "status": "failed" if failed else ("warning" if warnings else "passed"),
        "database_name": database_name,
        "schema": asdict(profile),
        "failed_count": len(failed),
        "warning_count": len(warnings),
        "checks": [asdict(check) for check in checks],
    }
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"database_name: {database_name}")
        for check in checks:
            print(
                f"{check.status.upper():7} {check.name}: "
                f"value={check.value!r} limit={check.limit!r} — {check.detail}"
            )
        print(
            f"Result: {payload['status']} "
            f"({len(checks)} checks, {len(failed)} failed, {len(warnings)} warnings)"
        )

    if failed or (args.fail_on_warning and warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
