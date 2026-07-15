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
    CASH_CUMULATIVE_NAV_BASES,
    FX_INSTRUMENT_IDENTITIES,
    QUOTE_BASIS_METRIC_FAMILY,
    QuoteSelectionPolicy,
    VALUATION_PROHIBITED_TOTAL_RETURN_BASES,
)


FINAL_FLAT_TABLE_HEAD_PAIR = ("20260715_0011", "20260715_0039")

AUDIT_CHECK_NAMES = (
    "market_data_invalid_values",
    "market_data_currency_mismatch",
    "instrument_quote_policy_contract",
    "market_data_price_contract",
    "market_data_fx_identity_contract",
    "market_data_logical_duplicates",
    "valuation_policy_total_return_basis",
    "cash_cumulative_nav_in_return_policy",
    "portfolio_nav_reconciliation",
    "portfolio_twr_geometric_link",
    "portfolio_drawdown_from_twr",
    "portfolio_valuation_quote_match",
    "taxonomy_target_sum",
    "taxonomy_negative_targets",
    "reserved_cash_taxonomy_nodes",
    "taxonomy_assignment_overlap",
    "current_unassigned_planning_holdings",
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
CASH_CUMULATIVE_NAV_BASES_SQL = ", ".join(
    _sql_text_literal(quote_basis)
    for quote_basis in sorted(CASH_CUMULATIVE_NAV_BASES)
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
    version_relation = f"{component}.alembic_version"
    cursor.execute("SELECT to_regclass(%s)", (version_relation,))
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return {"row_count": 0, "version": None, "table_present": False}
    cursor.execute(
        sql.SQL("SELECT count(*)::integer, min(version_num)::text FROM {}.alembic_version").format(
            sql.Identifier(component)
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
        for component in ("instrument_registry", "portfolio")
    }
    relation_specs = {
        "market_data": ("instrument_registry", "instrument_market_data"),
        "transaction": ("portfolio", "transaction_record"),
        "snapshot": ("portfolio", "portfolio_daily_snapshot"),
        "holding": ("portfolio", "portfolio_daily_holding_snapshot"),
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
        "instrument",
        "corporate_action",
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
    head_pair = (
        versions["instrument_registry"]["version"],
        versions["portfolio"]["version"],
    )
    flat_table_heads = (
        versions["instrument_registry"]["row_count"] == 1
        and versions["portfolio"]["row_count"] == 1
        and head_pair == FINAL_FLAT_TABLE_HEAD_PAIR
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
                    name="market_data_invalid_values",
                    query="""
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
                                           OR quote_basis.value = 'accrued_interest'
                                           OR (
                                                role.role_name = 'valuation'
                                                AND quote_basis.value IN (
                                                    {VALUATION_PROHIBITED_BASES_SQL}
                                                )
                                              )
                                           OR (
                                                role.role_name IN ('total_return', 'chart')
                                                AND quote_basis.value IN (
                                                    {CASH_CUMULATIVE_NAV_BASES_SQL}
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
                                      'last', 'close', 'adjusted_close', 'clean_price',
                                      'dirty_price', 'par', 'accrued_interest'
                                  ))
                                  OR (md.metric_family = 'nav' AND md.quote_basis IN (
                                      'official_nav', 'total_return_nav', 'cumulative_nav',
                                      'accumulated_nav', 'cum_nav', 'dividend_adjusted_nav',
                                      'reinvested_nav'
                                  ))
                                  OR (md.metric_family = 'fx' AND md.quote_basis = 'spot')
                              )
                           OR (md.quote_basis = 'accrued_interest' AND i.instrument_type <> 'bond')
                           OR ((i.instrument_type = 'fx') <>
                               (md.metric_family = 'fx' AND md.quote_basis = 'spot'))
                           OR md.price_unit <> CASE
                                  WHEN i.instrument_type = 'bond' AND md.metric_family = 'price'
                                      THEN 'percent_of_par'
                                  WHEN i.instrument_type = 'fx' THEN 'rate'
                                  ELSE 'per_unit'
                              END
                           OR md.price_scale <> CASE
                                  WHEN i.instrument_type = 'bond' AND md.metric_family = 'price'
                                      THEN 0.01
                                  ELSE 1
                              END
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
                    query="""
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
                                  'adjusted_close',
                                  'adjusted_nav',
                                  'adjusted_price',
                                  'accum_nav',
                                  'accumulated_nav',
                                  'cum_nav',
                                  'cumulative_nav',
                                  'dividend_adjusted_nav',
                                  'nav_with_dividend',
                                  'reinvested_nav',
                                  'split_adjusted_close',
                                  'total_return_nav',
                                  'total_return_price'
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
                    name="cash_cumulative_nav_in_return_policy",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument instrument
                        WHERE coalesce(instrument.lifecycle_state_json ->> 'status', 'active') = 'active'
                          AND EXISTS (
                              SELECT 1
                              FROM json_array_elements_text(
                                  CASE
                                      WHEN json_typeof(
                                          instrument.quote_selection_policy_json -> 'total_return'
                                      ) = 'array'
                                      THEN instrument.quote_selection_policy_json -> 'total_return'
                                      ELSE '[]'::json
                                  END
                              ) basis(value)
                              WHERE basis.value IN (
                                  'accum_nav',
                                  'accumulated_nav',
                                  'cum_nav',
                                  'cumulative_nav'
                              )
                          )
                    """,
                    detail=(
                        "Cash cumulative NAV is a disclosure value, not a dividend-reinvested "
                        "total-return series, and must not be selected for return/risk analytics."
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
                            sum(market_value_base)::numeric AS market_value
                        FROM portfolio.portfolio_daily_holding_snapshot
                        GROUP BY portfolio_id, as_of_date
                    )
                    SELECT greatest(
                        coalesce(max(abs(
                            coalesce(holdings.market_value, 0)
                            + (snapshot.snapshot_json ->> 'pending_settlement')::numeric
                            - snapshot.nav::numeric
                        )), 0),
                        CASE WHEN count(*) FILTER (
                            WHERE snapshot.nav IS NULL
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
                    detail="NAV must equal holding market value plus pending settlement.",
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
                    detail="Stored cumulative TWR must equal the geometric link of daily TWR.",
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
                            max(1 + cumulative_twr) OVER (
                                PARTITION BY portfolio_id
                                ORDER BY as_of_date
                                ROWS UNBOUNDED PRECEDING
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

            valuation_mismatches = int(
                _scalar(
                    cursor,
                    """
                    WITH holdings AS (
                        SELECT
                            snapshot.*,
                            snapshot.holding_json ->> 'quote_basis' AS quote_basis
                        FROM portfolio.portfolio_daily_holding_snapshot snapshot
                        WHERE snapshot.instrument_id NOT LIKE 'cash:%'
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
