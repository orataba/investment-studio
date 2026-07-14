#!/usr/bin/env python3
"""Read-only integrity audit for the local Portfolio Operations database."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any

import psycopg


DEFAULT_DATABASE_URL = (
    "postgresql://portfolio_ops:portfolio_ops@127.0.0.1:5432/portfolio_ops"
)


@dataclass(frozen=True)
class AuditCheck:
    name: str
    status: str
    value: Any
    limit: Any
    detail: str


def _database_url() -> str:
    raw_url = (
        os.getenv("PORTFOLIO_OPS_LOCAL_DATABASE_URL")
        or os.getenv("PORTFOLIO_OPS_PLATFORM_DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )
    return raw_url.replace("postgresql+psycopg://", "postgresql://", 1)


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


def run_audit(database_url: str) -> list[AuditCheck]:
    checks: list[AuditCheck] = []
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            checks.append(
                _count_check(
                    cursor,
                    name="market_data_invalid_values",
                    query="""
                        SELECT count(*)
                        FROM instrument_registry.instrument_market_data
                        WHERE CASE
                            WHEN value ~ '^-?([0-9]+([.][0-9]*)?|[.][0-9]+)$'
                            THEN metric_family IN ('price', 'nav', 'fx')
                                 AND value::numeric <= 0
                            ELSE true
                        END
                    """,
                    detail="Price, NAV, and FX observations must be numeric and positive.",
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
                        WHERE md.metric_family <> 'fx'
                          AND md.currency <> i.currency
                    """,
                    detail="Non-FX observations must use the instrument currency.",
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
                                  coalesce(
                                      instrument.quote_selection_policy_json -> 'valuation',
                                      '[]'::json
                                  )
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
                                  coalesce(
                                      instrument.quote_selection_policy_json -> 'total_return',
                                      '[]'::json
                                  )
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
                    SELECT coalesce(max(abs(
                        coalesce(holdings.market_value, 0)
                        + (snapshot.snapshot_json ->> 'pending_settlement')::numeric
                        - snapshot.nav::numeric
                    )), 0)
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
                            cumulative_twr,
                            exp(sum(ln(1 + daily_twr)) OVER (
                                PARTITION BY portfolio_id
                                ORDER BY as_of_date
                                ROWS UNBOUNDED PRECEDING
                            )) - 1 AS recomputed_twr
                        FROM portfolio.portfolio_daily_snapshot
                        WHERE daily_twr IS NOT NULL
                    )
                    SELECT coalesce(max(abs(cumulative_twr - recomputed_twr)), 0)
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
                    SELECT coalesce(max(abs(
                        drawdown - (wealth_index / peak_index - 1)
                    )), 0)
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
                    WHERE selected_quote.expected_price IS NULL
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
                    detail="One entity cannot have overlapping active assignments in one taxonomy.",
                )
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
                              AND (
                                  assignment.effective_from IS NULL
                                  OR assignment.effective_from <= holding.as_of_date
                              )
                              AND (
                                  assignment.effective_to IS NULL
                                  OR assignment.effective_to >= holding.as_of_date
                              )
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=_database_url())
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Return non-zero for warnings as well as failed integrity checks.",
    )
    args = parser.parse_args()

    checks = run_audit(args.database_url)
    failed = [check for check in checks if check.status == "fail"]
    warnings = [check for check in checks if check.status == "warning"]
    payload = {
        "status": "failed" if failed else ("warning" if warnings else "passed"),
        "failed_count": len(failed),
        "warning_count": len(warnings),
        "checks": [asdict(check) for check in checks],
    }
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
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
