from __future__ import annotations

import csv
import json
import mimetypes
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from portfolio_app.core.settings import get_settings
from portfolio_app.db.models import (
    ResearchRunRecordModel,
    ResearchSettingsRecordModel,
    TaxonomyNodeRecordModel,
    TaxonomyRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.asset_charts import build_asset_sparkline
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.ledger import build_account_workspace
from portfolio_app.services.performance import build_statement_of_assets_report
from portfolio_app.services.research_backtest import build_research_scope_options, run_taxonomy_backtest
from portfolio_app.services.portfolio_store import (
    get_portfolio,
    list_target_sets,
    list_taxonomies,
    list_taxonomy_assignments,
    list_taxonomy_nodes,
    list_accounts,
    list_transactions,
)

TEXT_SUFFIXES = {".csv", ".json", ".md", ".txt", ".yaml", ".yml"}
HTML_SUFFIXES = {".html"}
BACKTEST_METRIC_LABELS = {
    "cumulative_return": "Cumulative Return",
    "annualized_return": "Annualized Return",
    "annualized_volatility": "Annualized Volatility",
    "sharpe_ratio": "Sharpe Ratio",
    "sortino_ratio": "Sortino Ratio",
    "max_drawdown": "Max Drawdown",
    "calmar_ratio": "Calmar Ratio",
    "observations": "Observations",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_now_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if isinstance(value, date) else None


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}%"


def _format_number(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _format_dimension(value: object) -> str:
    normalized = str(value or "").strip().replace("_", " ")
    if not normalized:
        return "—"
    return normalized.title()


def _format_solver_kind(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return "—"
    if normalized == "weight":
        return "Weight"
    if normalized == "risk-budget":
        return "Risk Budget"
    if normalized == "single-member":
        return "Single Member"
    if normalized == "fallback-insufficient-history":
        return "Fallback: Insufficient History"
    if normalized == "fallback-solver":
        return "Fallback: Solver"
    return normalized.replace("-", " ").title()


def _research_outputs_root() -> Path:
    root = get_settings().research_outputs_root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _artifact_media_type(path: Path) -> str:
    if path.suffix.lower() == ".md":
        return "text/markdown"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _artifact_preview_kind(path: Path) -> str:
    if path.suffix.lower() in HTML_SUFFIXES:
        return "html"
    if path.suffix.lower() in TEXT_SUFFIXES:
        return "text"
    return "binary"


def _relative_artifact_path(path: Path) -> str:
    return path.resolve().relative_to(_research_outputs_root().resolve()).as_posix()


def _resolve_artifact_path(portfolio_id: str, path: str) -> Path:
    candidate = (_research_outputs_root() / path).resolve()
    root = _research_outputs_root().resolve()
    portfolio_root = (root / portfolio_id).resolve()
    if root not in candidate.parents and candidate != root:
        raise ValueError("Artifact path must stay within research outputs root.")
    if portfolio_root not in candidate.parents and candidate != portfolio_root:
        raise ValueError("Artifact path must stay within the selected portfolio research outputs.")
    if not candidate.exists() or not candidate.is_file():
        raise ValueError("Research artifact not found.")
    return candidate


def _next_research_run_id(portfolio_id: str) -> str:
    timestamp = _utc_now().strftime("%Y%m%d__%H%M%SZ")
    return f"{portfolio_id}__research__{timestamp}__{uuid4().hex[:8]}"


def _default_as_of_date(portfolio: dict[str, object]) -> date:
    if portfolio.get("as_of_date"):
        return date.fromisoformat(str(portfolio["as_of_date"]))
    return date.today()


def _ensure_research_settings_record(
    session,
    portfolio_id: str,
    *,
    default_planning_taxonomy_id: str | None,
    default_as_of_date: date,
) -> ResearchSettingsRecordModel:
    record = session.get(ResearchSettingsRecordModel, portfolio_id)
    if record is not None:
        changed = False
        if not record.start_date:
            record.start_date = default_as_of_date - timedelta(days=180)
            changed = True
        if not record.run_template:
            record.run_template = "taxonomy_backtest"
            changed = True
        if not record.target_set_mode:
            record.target_set_mode = "taa_over_saa"
            changed = True
        if not record.target_dimension:
            record.target_dimension = "scope_default"
            changed = True
        if not record.rebalance_frequency:
            record.rebalance_frequency = "monthly"
            changed = True
        if changed:
            record.updated_at = _utc_now_iso()
            session.commit()
        return record

    record = ResearchSettingsRecordModel(
        portfolio_id=portfolio_id,
        planning_taxonomy_id=default_planning_taxonomy_id,
        comparator_taxonomy_node_id=None,
        as_of_date=default_as_of_date,
        start_date=default_as_of_date - timedelta(days=180),
        lookback_days=90,
        benchmark_mode="none",
        run_template="taxonomy_backtest",
        target_set_mode="taa_over_saa",
        target_dimension="scope_default",
        rebalance_frequency="monthly",
        notes=None,
        updated_at=_utc_now_iso(),
    )
    session.add(record)
    session.commit()
    return record


def _validate_planning_taxonomy(
    session,
    portfolio_id: str,
    taxonomy_id: str | None,
) -> TaxonomyRecordModel | None:
    resolved_taxonomy_id = str(taxonomy_id or "").strip() or None
    if resolved_taxonomy_id is None:
        return None

    taxonomy = session.scalar(
        select(TaxonomyRecordModel).where(
            TaxonomyRecordModel.portfolio_id == portfolio_id,
            TaxonomyRecordModel.taxonomy_id == resolved_taxonomy_id,
        )
    )
    if taxonomy is None:
        raise ValueError("Planning taxonomy not found.")
    if not taxonomy.planning_enabled:
        raise ValueError("Research planning taxonomy must be planning-enabled.")
    if taxonomy.primary_assignment_scope != "instrument":
        raise ValueError("Research planning taxonomy must use instrument assignment scope.")
    return taxonomy


def _validate_research_scope(
    session,
    *,
    taxonomy: TaxonomyRecordModel | None,
    comparator_taxonomy_node_id: str | None,
) -> str | None:
    resolved_node_id = str(comparator_taxonomy_node_id or "").strip() or None
    if resolved_node_id is None:
        return None
    if taxonomy is None:
        raise ValueError("Research scope requires a selected planning taxonomy.")
    node = session.scalar(
        select(TaxonomyNodeRecordModel.taxonomy_node_id)
        .where(
            TaxonomyNodeRecordModel.taxonomy_id == taxonomy.taxonomy_id,
            TaxonomyNodeRecordModel.taxonomy_node_id == resolved_node_id,
        )
    )
    if node is None:
        raise ValueError("Research scope node not found in the selected planning taxonomy.")
    return resolved_node_id


def _planning_taxonomy_options(portfolio_id: str) -> list[dict[str, object]]:
    return [
        {
            "taxonomy_id": item["taxonomy_id"],
            "name": item["name"],
            "taxonomy_type": item["taxonomy_type"],
            "budgeting_level": item.get("budgeting_level"),
        }
        for item in list_taxonomies(portfolio_id)
        if bool(item.get("planning_enabled"))
    ]


def _taxonomy_name_map(portfolio_id: str) -> dict[str, str]:
    return {
        str(item["taxonomy_id"]): str(item["name"])
        for item in list_taxonomies(portfolio_id)
        if item.get("taxonomy_id")
    }


def _scope_name_map(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str | None,
) -> dict[str, str]:
    if not planning_taxonomy_id:
        return {}
    return {
        str(item["taxonomy_node_id"]): str(item["node_name"])
        for item in list_taxonomy_nodes(portfolio_id)
        if str(item.get("taxonomy_id") or "") == planning_taxonomy_id and item.get("taxonomy_node_id")
    }


def _serialize_settings_row(
    row: ResearchSettingsRecordModel,
    taxonomy_name_map: dict[str, str],
    scope_name_map: dict[str, str],
) -> dict[str, object]:
    planning_taxonomy_id = str(row.planning_taxonomy_id or "").strip() or None
    comparator_taxonomy_node_id = str(row.comparator_taxonomy_node_id or "").strip() or None
    return {
        "portfolio_id": row.portfolio_id,
        "planning_taxonomy_id": planning_taxonomy_id,
        "planning_taxonomy_name": taxonomy_name_map.get(planning_taxonomy_id) if planning_taxonomy_id else None,
        "comparator_taxonomy_node_id": comparator_taxonomy_node_id,
        "comparator_taxonomy_node_name": (
            scope_name_map.get(comparator_taxonomy_node_id) if comparator_taxonomy_node_id else None
        ),
        "as_of_date": _iso_date(row.as_of_date),
        "start_date": _iso_date(row.start_date),
        "lookback_days": int(row.lookback_days or 90),
        "benchmark_mode": row.benchmark_mode or "none",
        "run_template": "taxonomy_backtest",
        "target_set_mode": row.target_set_mode or "taa_over_saa",
        "target_dimension": row.target_dimension or "scope_default",
        "rebalance_frequency": row.rebalance_frequency or "monthly",
        "notes": row.notes,
        "updated_at": row.updated_at,
    }


def _serialize_run_row(
    row: ResearchRunRecordModel,
    taxonomy_name_map: dict[str, str],
) -> dict[str, object]:
    planning_taxonomy_id = str(row.planning_taxonomy_id or "").strip() or None
    detail = deepcopy(row.detail_json or {})
    artifacts = deepcopy(row.artifacts_json or [])
    return {
        "research_run_id": row.research_run_id,
        "portfolio_id": row.portfolio_id,
        "job_type": row.job_type,
        "status": row.status,
        "requested_at": row.requested_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "as_of_date": _iso_date(row.as_of_date),
        "planning_taxonomy_id": planning_taxonomy_id,
        "planning_taxonomy_name": taxonomy_name_map.get(planning_taxonomy_id) if planning_taxonomy_id else None,
        "lookback_days": int(row.lookback_days or 90),
        "benchmark_mode": row.benchmark_mode or "none",
        "run_template": "taxonomy_backtest",
        "requested_by": row.requested_by,
        "headline": row.headline,
        "error_message": row.error_message,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "detail": detail,
    }


def _build_top_holdings_snapshot(
    statement_positions: list[dict[str, object]],
    *,
    base_currency: str,
) -> list[dict[str, object]]:
    sorted_positions = sorted(
        statement_positions,
        key=lambda item: _safe_float(item.get("portfolio_weight")) or 0.0,
        reverse=True,
    )
    rendered: list[dict[str, object]] = []
    for position in sorted_positions[:8]:
        instrument_ref = position.get("instrument_ref") or {}
        rendered.append(
            {
                "asset_id": str(position.get("asset_id") or ""),
                "asset_name": str(instrument_ref.get("asset_name") or position.get("asset_id") or ""),
                "asset_type": str(instrument_ref.get("asset_type") or ""),
                "allocation": _safe_float(position.get("portfolio_weight")),
                "market_value_base": _safe_float(position.get("market_value_base")),
                "cost_basis_base": _safe_float(position.get("cost_basis_base")),
                "base_currency": base_currency,
                "price": _safe_float(position.get("last_price")),
            }
        )
    return rendered


def _build_planning_group_snapshot(
    statement_positions: list[dict[str, object]],
    *,
    account_rows: list[dict[str, object]],
    portfolio_id: str,
    planning_taxonomy_id: str | None,
    as_of_date: date,
) -> list[dict[str, object]]:
    if not planning_taxonomy_id:
        return []

    node_name_by_id = {
        str(item.get("taxonomy_node_id") or ""): str(item.get("node_name") or "")
        for item in list_taxonomy_nodes(portfolio_id)
        if str(item.get("taxonomy_id") or "") == planning_taxonomy_id
    }
    assignment_by_entity: dict[tuple[str, str], dict[str, object]] = {}
    for item in list_taxonomy_assignments(portfolio_id):
        if str(item.get("taxonomy_id") or "") != planning_taxonomy_id:
            continue
        if str(item.get("status") or "") != "active":
            continue
        target_scope = str(item.get("target_scope") or "")
        if target_scope not in {"instrument", "cash_bucket"}:
            continue
        effective_from = item.get("effective_from")
        effective_to = item.get("effective_to")
        if effective_from and str(effective_from) > as_of_date.isoformat():
            continue
        if effective_to and str(effective_to) < as_of_date.isoformat():
            continue
        entity_id = str(item.get("target_entity_id") or "")
        if not entity_id:
            continue
        assignment_key = (target_scope, entity_id)
        current = assignment_by_entity.get(assignment_key)
        current_effective_from = str(current.get("effective_from") or "") if current else ""
        next_effective_from = str(item.get("effective_from") or "")
        if current is None or next_effective_from >= current_effective_from:
            assignment_by_entity[assignment_key] = item

    visible_cash_accounts = [
        account_row
        for account_row in account_rows
        if str((account_row.get("account") or {}).get("account_type") or "") == "deposit_account"
        and (
            abs(_safe_float(account_row.get("derived_cash_balance_base")) or 0.0) > 1e-9
            or ("cash_bucket", str((account_row.get("account") or {}).get("account_id") or "")) in assignment_by_entity
        )
    ]

    total_entity_value_base = sum(_safe_float(position.get("market_value_base")) or 0.0 for position in statement_positions)
    total_entity_value_base += sum(
        _safe_float(account_row.get("derived_cash_balance_base")) or 0.0 for account_row in visible_cash_accounts
    )

    buckets: dict[str, dict[str, object]] = {}
    for position in statement_positions:
        asset_id = str(position.get("asset_id") or "")
        assignment = assignment_by_entity.get(("instrument", asset_id))
        if assignment is None:
            group_key = "unassigned"
            group_label = "Unassigned"
        else:
            group_key = str(assignment.get("taxonomy_node_id") or "unassigned")
            group_label = node_name_by_id.get(group_key) or group_key
        bucket = buckets.setdefault(
            group_key,
            {
                "group_key": group_key,
                "group_label": group_label,
                "start_allocation": None,
                "end_allocation": 0.0,
                "allocation_change": None,
                "start_value_base": None,
                "end_value_base": 0.0,
                "period_contribution": None,
                "total_pnl": 0.0,
                "average_weight": None,
                "ending_weight": 0.0,
                "position_count": 0,
            },
        )
        market_value_base = _safe_float(position.get("market_value_base")) or 0.0
        cost_basis_base = _safe_float(position.get("cost_basis_base")) or 0.0
        bucket["end_value_base"] = float(bucket["end_value_base"] or 0.0) + market_value_base
        bucket["total_pnl"] = float(bucket["total_pnl"] or 0.0) + (market_value_base - cost_basis_base)
        bucket["position_count"] = int(bucket["position_count"] or 0) + 1

    for account_row in visible_cash_accounts:
        account = account_row.get("account") or {}
        account_id = str(account.get("account_id") or "")
        assignment = assignment_by_entity.get(("cash_bucket", account_id))
        if assignment is None:
            group_key = "unassigned"
            group_label = "Unassigned"
        else:
            group_key = str(assignment.get("taxonomy_node_id") or "unassigned")
            group_label = node_name_by_id.get(group_key) or group_key
        bucket = buckets.setdefault(
            group_key,
            {
                "group_key": group_key,
                "group_label": group_label,
                "start_allocation": None,
                "end_allocation": 0.0,
                "allocation_change": None,
                "start_value_base": None,
                "end_value_base": 0.0,
                "period_contribution": None,
                "total_pnl": 0.0,
                "average_weight": None,
                "ending_weight": 0.0,
                "position_count": 0,
            },
        )
        cash_value_base = _safe_float(account_row.get("derived_cash_balance_base")) or 0.0
        bucket["end_value_base"] = float(bucket["end_value_base"] or 0.0) + cash_value_base
        bucket["position_count"] = int(bucket["position_count"] or 0) + 1

    for bucket in buckets.values():
        end_value_base = _safe_float(bucket.get("end_value_base")) or 0.0
        ending_weight = end_value_base / total_entity_value_base if abs(total_entity_value_base) > 1e-9 else None
        bucket["end_allocation"] = ending_weight
        bucket["ending_weight"] = ending_weight

    rendered = list(buckets.values())

    rendered.sort(
        key=lambda item: (
            item.get("end_allocation") is not None,
            item.get("end_allocation") or 0.0,
        ),
        reverse=True,
    )
    return rendered


def _build_planning_target_summary(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str | None,
    as_of_date: date,
) -> dict[str, object]:
    if not planning_taxonomy_id:
        return {
            "root_saa_configured": False,
            "root_taa_configured": False,
            "scoped_target_set_count": 0,
        }

    active_target_sets = [
        item
        for item in list_target_sets(portfolio_id, taxonomy_id=planning_taxonomy_id)
        if str(item.get("status") or "") == "active"
        and (not item.get("effective_from") or str(item.get("effective_from")) <= as_of_date.isoformat())
        and (not item.get("effective_to") or str(item.get("effective_to")) >= as_of_date.isoformat())
    ]
    return {
        "root_saa_configured": any(
            str(item.get("target_set_type") or "") == "saa" and not item.get("comparator_taxonomy_node_id")
            for item in active_target_sets
        ),
        "root_taa_configured": any(
            str(item.get("target_set_type") or "") == "taa" and not item.get("comparator_taxonomy_node_id")
            for item in active_target_sets
        ),
        "scoped_target_set_count": sum(1 for item in active_target_sets if item.get("comparator_taxonomy_node_id")),
    }


def _build_research_context(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str | None,
    as_of_date: date,
    lookback_days: int,
) -> dict[str, object]:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise ValueError("Portfolio not found.")

    accounts = list_accounts(portfolio_id)
    transactions = list_transactions(portfolio_id)

    statement = build_statement_of_assets_report(
        portfolio,
        accounts,
        transactions,
        as_of_date=as_of_date,
    )
    account_workspace = build_account_workspace(
        portfolio_id,
        accounts,
        transactions,
        base_currency=str(statement.get("base_currency") or portfolio.get("base_currency") or "USD"),
        as_of_date=as_of_date,
    )
    lookback_start = as_of_date - timedelta(days=max(lookback_days - 1, 0))
    statement_positions = list(statement.get("positions", []))
    top_holdings = _build_top_holdings_snapshot(statement_positions, base_currency=str(statement.get("base_currency") or "USD"))
    planning_groups = _build_planning_group_snapshot(
        statement_positions,
        account_rows=list(account_workspace.get("accounts") or []),
        portfolio_id=portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
    )
    planning_target_summary = _build_planning_target_summary(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
    )
    chart_label = None
    chart_note = None
    chart_currency = None
    daily_points: list[dict[str, object]] = []
    if top_holdings:
        reference_asset = top_holdings[0]
        daily_points = build_asset_sparkline(str(reference_asset.get("asset_id") or ""), as_of_date=as_of_date, max_points=20)
        chart_label = "Reference Tape"
        chart_currency = str(reference_asset.get("base_currency") or statement.get("base_currency") or "USD")
        chart_note = (
            f"Using the six-month sparkline for {reference_asset.get('asset_name') or reference_asset.get('asset_id')} "
            "until a cheaper portfolio daily tape is wired into the research workbench."
        )

    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": str(portfolio.get("portfolio_name") or portfolio_id),
        "base_currency": str(statement.get("base_currency") or portfolio.get("base_currency") or "USD"),
        "as_of_date": as_of_date.isoformat(),
        "lookback_start": lookback_start.isoformat(),
        "lookback_end": as_of_date.isoformat(),
        "nav": _safe_float(portfolio.get("nav")) or _safe_float(statement.get("total_market_value_base")),
        "holdings_count": len(statement_positions),
        "planning_group_count": len(planning_groups),
        "chart_label": chart_label,
        "chart_note": chart_note,
        "chart_currency": chart_currency,
        "summary": {
            "period_return": None,
            "annualized_volatility": None,
            "current_drawdown": None,
            "max_drawdown": None,
            "start_nav": None,
            "end_nav": _safe_float(statement.get("total_market_value_base")) or _safe_float(portfolio.get("nav")),
        },
        "planning_target_summary": planning_target_summary,
        "chart_points": daily_points,
        "top_holdings": top_holdings,
        "planning_groups": planning_groups,
    }

def _metric_records_from_backtest(backtest: dict[str, object]) -> list[dict[str, object]]:
    metrics = backtest.get("metrics") or {}
    if not isinstance(metrics, dict):
        return []
    rendered: list[dict[str, object]] = []
    for metric_id in [
        "cumulative_return",
        "annualized_return",
        "annualized_volatility",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown",
        "calmar_ratio",
        "observations",
    ]:
        rendered.append(
            {
                "metric_id": metric_id,
                "label": BACKTEST_METRIC_LABELS.get(metric_id, metric_id),
                "value": _safe_float(metrics.get(metric_id)),
            }
        )
    return rendered


def _build_backtest_findings(backtest: dict[str, object]) -> tuple[list[dict[str, str]], list[str]]:
    metrics = backtest.get("metrics") or {}
    scope = backtest.get("scope") or {}
    warnings = list(backtest.get("warnings") or [])
    member_summaries = list(backtest.get("member_summaries") or [])
    rebalance_events = list(backtest.get("rebalance_events") or [])
    rebalance_suggestions = list(backtest.get("rebalance_suggestions") or [])

    findings: list[dict[str, str]] = []
    questions: list[str] = []

    scope_label = str(scope.get("label") or "Selected Scope")
    cumulative_return = _safe_float(metrics.get("cumulative_return"))
    annualized_volatility = _safe_float(metrics.get("annualized_volatility"))
    max_drawdown = _safe_float(metrics.get("max_drawdown"))
    if cumulative_return is not None or annualized_volatility is not None or max_drawdown is not None:
        findings.append(
            {
                "title": "Backtest Summary",
                "detail": (
                    f"{scope_label} returned {_format_pct(cumulative_return)} with volatility at "
                    f"{_format_pct(annualized_volatility)} and max drawdown {_format_pct(max_drawdown)} over the selected window."
                ),
            }
        )

    if member_summaries:
        best_member = max(member_summaries, key=lambda item: _safe_float(item.get("cumulative_return")) or float("-inf"))
        worst_member = min(member_summaries, key=lambda item: _safe_float(item.get("cumulative_return")) or float("inf"))
        findings.append(
            {
                "title": "Best/Worst Sleeve Path",
                "detail": (
                    f"Best contributing member was {best_member.get('label')} at "
                    f"{_format_pct(_safe_float(best_member.get('cumulative_return')), 3)}, while "
                    f"{worst_member.get('label')} finished at {_format_pct(_safe_float(worst_member.get('cumulative_return')), 3)}."
                ),
            }
        )

    if rebalance_events:
        largest_turnover = max(rebalance_events, key=lambda item: _safe_float(item.get("turnover")) or float("-inf"))
        findings.append(
            {
                "title": "Rebalance Pressure",
                "detail": (
                    f"The heaviest rebalance in scope landed on {largest_turnover.get('rebalance_date')} with "
                    f"{_format_pct(_safe_float(largest_turnover.get('turnover')), 3)} turnover."
                ),
            }
        )

    if rebalance_suggestions:
        top_gap = max(rebalance_suggestions, key=lambda item: abs(_safe_float(item.get("gap")) or 0.0))
        if abs(_safe_float(top_gap.get("gap")) or 0.0) > 0.01:
            questions.append(
                f"Decide whether {top_gap.get('label')} should be {str(top_gap.get('action') or '').lower()}d by {_format_pct(abs(_safe_float(top_gap.get('gap')) or 0.0), 3)} versus the selected target."
            )

    if warnings:
        findings.append(
            {
                "title": "Coverage / Solver Warnings",
                "detail": "; ".join(str(item) for item in warnings[:2]),
            }
        )

    if not questions:
        questions.append("Review whether the selected scope and target mode should stay active for the next rebalance cycle.")
    if len(rebalance_events) >= 3:
        questions.append("Check whether rebalance cadence is too frequent for this sleeve path relative to realized turnover.")
    if _safe_float(metrics.get("max_drawdown")) and (_safe_float(metrics.get("max_drawdown")) or 0.0) < -0.1:
        questions.append("Stress test whether the selected sleeve hierarchy still behaves acceptably under deeper drawdown conditions.")

    return findings[:4], questions[:3]


def _build_weight_schedule(
    rows: list[dict[str, object]],
    *,
    curve_points: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    if not rows:
        return []
    nav_by_date = {
        str(item.get("asof_date") or item.get("date") or "").strip(): _safe_float(item.get("nav"))
        for item in (curve_points or [])
        if str(item.get("asof_date") or item.get("date") or "").strip()
    }
    by_date: dict[str, dict[str, object]] = {}
    for row in rows:
        date_value = str(row.get("asof_date") or row.get("date") or "").strip()
        if not date_value:
            continue
        bucket = by_date.setdefault(
            date_value,
            {
                "date": date_value,
                "rebalance_flag": bool(row.get("rebalance_flag")),
                "nav": nav_by_date.get(date_value),
                "weights": {},
            },
        )
        bucket["rebalance_flag"] = bool(bucket.get("rebalance_flag")) or bool(row.get("rebalance_flag"))
        if bucket.get("nav") is None and nav_by_date.get(date_value) is not None:
            bucket["nav"] = nav_by_date.get(date_value)
        label = str(row.get("label") or "").strip()
        weight = _safe_float(row.get("weight"))
        if label and weight is not None:
            bucket["weights"][label] = weight
    return sorted(by_date.values(), key=lambda item: str(item.get("date") or ""))


def _build_backtest_signals(
    *,
    settings_payload: dict[str, object],
    metrics: dict[str, object],
    scope: dict[str, object],
    rebalance_events: list[dict[str, object]],
) -> list[dict[str, object]]:
    solver_labels = [
        _format_solver_kind(item.get("solver_kind"))
        for item in rebalance_events
        if str(item.get("solver_kind") or "").strip()
    ]
    unique_solver_labels: list[str] = []
    for label in solver_labels:
        if label not in unique_solver_labels:
            unique_solver_labels.append(label)

    max_turnover = max((_safe_float(item.get("turnover")) for item in rebalance_events), default=None)
    max_risk_gap = max(
        (_safe_float(item.get("max_risk_share_gap")) for item in rebalance_events if _safe_float(item.get("max_risk_share_gap")) is not None),
        default=None,
    )
    return [
        {"label": "Template", "value": "Taxonomy Backtest", "tone": "neutral"},
        {
            "label": "Target Layer",
            "value": "TAA over SAA" if settings_payload.get("target_set_mode") == "taa_over_saa" else "SAA",
            "tone": "neutral",
        },
        {
            "label": "Target Dimension",
            "value": _format_dimension(settings_payload.get("target_dimension") or "scope_default"),
            "tone": "neutral",
        },
        {
            "label": "Scope Default",
            "value": _format_dimension(scope.get("default_target_dimension") or "weight"),
            "tone": "neutral",
        },
        {
            "label": "Rebalance",
            "value": str(settings_payload.get("rebalance_frequency") or "monthly").title(),
            "tone": "neutral",
        },
        {
            "label": "Solver",
            "value": ", ".join(unique_solver_labels[:2]) if unique_solver_labels else "—",
            "tone": "neutral",
        },
        {
            "label": "Largest Turnover",
            "value": _format_pct(max_turnover, 3),
            "tone": "neutral",
        },
        {
            "label": "Largest Risk Gap",
            "value": _format_pct(max_risk_gap, 3),
            "tone": "neutral",
        },
        {
            "label": "Return",
            "value": _format_pct(_safe_float(metrics.get("cumulative_return"))),
            "tone": "neutral",
        },
        {
            "label": "Max Drawdown",
            "value": _format_pct(_safe_float(metrics.get("max_drawdown"))),
            "tone": "neutral",
        },
    ]


def _construction_source_label(target_row: dict[str, object] | None) -> str:
    if not target_row:
        return "—"
    target_set_type = str(target_row.get("source_target_set_type") or "").strip()
    if target_set_type == "taa":
        return "TAA"
    if target_set_type == "saa":
        return "SAA"
    selected_dimension = str(target_row.get("selected_dimension") or "").strip()
    if selected_dimension:
        return f"Fallback {_format_dimension(selected_dimension)}"
    return "Fallback"


def _build_construction_rows(backtest: dict[str, object]) -> list[dict[str, object]]:
    actual_rows = list(backtest.get("actual_rows") or [])
    target_rows = list(backtest.get("latest_target_rows") or [])
    suggestion_rows = list(backtest.get("rebalance_suggestions") or [])
    actual_by_key = {
        (str(row.get("member_type") or ""), str(row.get("member_id") or "")): row
        for row in actual_rows
    }
    target_by_key = {
        (str(row.get("member_type") or ""), str(row.get("member_id") or "")): row
        for row in target_rows
    }
    suggestion_by_key = {
        (str(row.get("member_type") or ""), str(row.get("member_id") or "")): row
        for row in suggestion_rows
    }

    ordered_keys: list[tuple[str, str]] = []
    for row in target_rows:
        key = (str(row.get("member_type") or ""), str(row.get("member_id") or ""))
        if key not in ordered_keys:
            ordered_keys.append(key)
    for row in actual_rows:
        key = (str(row.get("member_type") or ""), str(row.get("member_id") or ""))
        if key not in ordered_keys:
            ordered_keys.append(key)

    rendered: list[dict[str, object]] = []
    for key in ordered_keys:
        target_row = target_by_key.get(key)
        actual_row = actual_by_key.get(key)
        suggestion = suggestion_by_key.get(key)
        implementation_weight = _safe_float((target_row or {}).get("implementation_weight"))
        current_weight = _safe_float((actual_row or {}).get("current_weight"))
        gap_to_implementation = (
            None
            if implementation_weight is None or current_weight is None
            else float(implementation_weight - current_weight)
        )
        rendered.append(
            {
                "member_type": key[0],
                "member_id": key[1],
                "label": str(
                    (target_row or {}).get("label")
                    or (actual_row or {}).get("label")
                    or (suggestion or {}).get("label")
                    or key[1]
                ),
                "current_weight": current_weight,
                "current_value_base": _safe_float((actual_row or {}).get("current_value_base")),
                "default_target_dimension": (target_row or {}).get("default_target_dimension"),
                "selected_target_dimension": (target_row or {}).get("selected_dimension"),
                "source_target_set_type": (target_row or {}).get("source_target_set_type"),
                "source_target_set_id": (target_row or {}).get("source_target_set_id"),
                "source_label": _construction_source_label(target_row),
                "selected_target_value": _safe_float((target_row or {}).get("selected_value")),
                "target_weight": _safe_float((target_row or {}).get("target_weight")),
                "target_risk_share": _safe_float((target_row or {}).get("target_risk_share")),
                "implementation_weight": implementation_weight,
                "gap_to_implementation": gap_to_implementation,
                "action": str((suggestion or {}).get("action") or "").strip() or None,
            }
        )
    return rendered


def _build_construction_assumptions(
    *,
    settings_payload: dict[str, object],
    target_rows: list[dict[str, object]],
    rebalance_events: list[dict[str, object]],
) -> list[str]:
    assumptions = [
        "Local construction is long-only and fully invested within each selected scope; member weights are bounded between 0% and 100%.",
    ]
    if str(settings_payload.get("target_dimension") or "") == "scope_default":
        assumptions.append("Scope Default resolves each sleeve using that sleeve's own default target dimension before rolling results upward.")
    if any(str(item.get("target_dimension") or "") == "risk_budget" for item in rebalance_events):
        assumptions.append("Risk-budget sleeves solve implementation weights from the trailing local covariance window over the selected lookback horizon.")
    if any(str(item.get("solver_kind") or "").startswith("fallback") for item in rebalance_events):
        assumptions.append("If local covariance is weak or history is too short, the solver falls back to target shares instead of forcing an unstable optimization.")
    if any(not item.get("source_target_set_id") for item in target_rows):
        assumptions.append("Missing scoped target sets resolve to equal local defaults inside the affected sleeve until an explicit SAA/TAA set is configured.")
    return assumptions[:4]


def _build_taxonomy_backtest_detail(
    context: dict[str, object],
    *,
    planning_taxonomy_name: str | None,
    settings_payload: dict[str, object],
    backtest: dict[str, object],
    ) -> dict[str, object]:
    metrics = backtest.get("metrics") or {}
    findings, questions = _build_backtest_findings(backtest)
    scope = backtest.get("scope") or {}
    rebalance_events = list(backtest.get("rebalance_events") or [])
    latest_target_rows = list(backtest.get("latest_target_rows") or [])
    weight_schedule = _build_weight_schedule(
        list(backtest.get("weight_schedule_rows") or []),
        curve_points=list(backtest.get("curve_points") or []),
    )
    construction_rows = _build_construction_rows(backtest)
    signals = _build_backtest_signals(
        settings_payload=settings_payload,
        metrics=metrics,
        scope=scope,
        rebalance_events=rebalance_events,
    )
    headline = (
        f"{scope.get('label') or 'Selected Scope'} backtest through {context.get('as_of_date')} "
        f"under {planning_taxonomy_name or 'the selected planning taxonomy'}."
    )
    return {
        "headline": headline,
        "coverage_note": (
            "This run uses the current planning taxonomy as a recursive sleeve tree. "
            "Each sleeve resolves its own default target dimension locally, then rolls its realized return path upward."
        ),
        "signals": signals,
        "findings": findings,
        "next_questions": questions,
        "top_holdings": deepcopy(context.get("top_holdings") or []),
        "planning_groups": deepcopy(context.get("planning_groups") or []),
        "selected_scope": deepcopy(scope),
        "backtest_metrics": _metric_records_from_backtest(backtest),
        "backtest_curve": deepcopy(backtest.get("curve_points") or []),
        "weight_schedule": weight_schedule,
        "member_summaries": deepcopy(backtest.get("member_summaries") or []),
        "construction_assumptions": _build_construction_assumptions(
            settings_payload=settings_payload,
            target_rows=latest_target_rows,
            rebalance_events=rebalance_events,
        ),
        "construction_rows": construction_rows,
        "rebalance_events": deepcopy(rebalance_events),
        "rebalance_suggestions": deepcopy(backtest.get("rebalance_suggestions") or []),
        "warnings": deepcopy(backtest.get("warnings") or []),
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def _write_artifacts(
    portfolio_id: str,
    research_run_id: str,
    *,
    settings_payload: dict[str, object],
    context: dict[str, object],
    detail: dict[str, object],
) -> list[dict[str, object]]:
    run_root = _research_outputs_root() / portfolio_id / research_run_id
    run_root.mkdir(parents=True, exist_ok=True)

    report_path = run_root / "report.md"
    summary_path = run_root / "summary.json"
    settings_path = run_root / "request.json"
    holdings_path = run_root / "top_holdings.csv"
    groups_path = run_root / "planning_groups.csv"
    chart_path = run_root / "daily_nav.csv"
    backtest_curve_path = run_root / "backtest_curve.csv"
    member_weights_path = run_root / "member_weights.csv"
    rebalance_events_path = run_root / "rebalance_events.csv"
    rebalance_suggestions_path = run_root / "rebalance_suggestions.csv"

    report_path.write_text(
        "\n".join(
            [
                f"# Research Run `{research_run_id}`",
                "",
                detail.get("headline") or "",
                "",
                "## Signals",
                *[
                    f"- {item.get('label')}: {item.get('value')}"
                    for item in (detail.get("signals") or [])
                ],
                "",
                "## Findings",
                *[
                    f"- **{item.get('title')}**: {item.get('detail')}"
                    for item in (detail.get("findings") or [])
                ],
                "",
                "## Next Questions",
                *[
                    f"- {item}"
                    for item in (detail.get("next_questions") or [])
                ],
            ]
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps({"context": context, "detail": detail}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    settings_path.write_text(
        json.dumps(settings_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(holdings_path, list(detail.get("top_holdings") or []))
    _write_csv(groups_path, list(detail.get("planning_groups") or []))
    _write_csv(chart_path, list(context.get("chart_points") or []))
    _write_csv(backtest_curve_path, list(detail.get("backtest_curve") or []))
    _write_csv(member_weights_path, list(detail.get("member_summaries") or []))
    _write_csv(rebalance_events_path, list(detail.get("rebalance_events") or []))
    _write_csv(rebalance_suggestions_path, list(detail.get("rebalance_suggestions") or []))

    artifacts = []
    for artifact_id, label, path in [
        ("report", "Report", report_path),
        ("summary", "Summary JSON", summary_path),
        ("request", "Run Request", settings_path),
        ("holdings", "Top Holdings CSV", holdings_path),
        ("groups", "Planning Groups CSV", groups_path),
        ("chart", "Daily NAV CSV", chart_path),
        ("backtest_curve", "Backtest Curve CSV", backtest_curve_path),
        ("member_weights", "Member Weights CSV", member_weights_path),
        ("rebalance_events", "Rebalance Events CSV", rebalance_events_path),
        ("rebalance_suggestions", "Rebalance Suggestions CSV", rebalance_suggestions_path),
    ]:
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "label": label,
                "path": _relative_artifact_path(path),
                "media_type": _artifact_media_type(path),
                "preview_kind": _artifact_preview_kind(path),
            }
        )
    return artifacts


def get_research_workbench(
    portfolio_id: str,
    *,
    selected_run_id: str | None = None,
) -> dict[str, object] | None:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        return None

    taxonomy_name_map = _taxonomy_name_map(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        record = _ensure_research_settings_record(
            session,
            portfolio_id,
            default_planning_taxonomy_id=str(portfolio.get("default_planning_taxonomy_id") or "").strip() or None,
            default_as_of_date=_default_as_of_date(portfolio),
        )
        scope_name_map = _scope_name_map(
            portfolio_id,
            planning_taxonomy_id=str(record.planning_taxonomy_id or "").strip() or None,
        )
        settings_payload = _serialize_settings_row(record, taxonomy_name_map, scope_name_map)
        run_rows = session.scalars(
            select(ResearchRunRecordModel)
            .where(ResearchRunRecordModel.portfolio_id == portfolio_id)
            .order_by(ResearchRunRecordModel.requested_at.desc(), ResearchRunRecordModel.research_run_id.desc())
        ).all()

    runs = [_serialize_run_row(row, taxonomy_name_map) for row in run_rows]
    selected_run = None
    if selected_run_id:
        selected_run = next((item for item in runs if item["research_run_id"] == selected_run_id), None)
    if selected_run is None and runs:
        selected_run = runs[0]

    context = _build_research_context(
        portfolio_id,
        planning_taxonomy_id=str(settings_payload.get("planning_taxonomy_id") or "").strip() or None,
        as_of_date=date.fromisoformat(str(settings_payload["as_of_date"])),
        lookback_days=int(settings_payload.get("lookback_days") or 90),
    )

    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": str(portfolio.get("portfolio_name") or portfolio_id),
        "base_currency": str(portfolio.get("base_currency") or "USD"),
        "as_of_date": _iso_date(_default_as_of_date(portfolio)),
        "default_planning_taxonomy_id": str(portfolio.get("default_planning_taxonomy_id") or "").strip() or None,
        "planning_taxonomy_options": _planning_taxonomy_options(portfolio_id),
        "planning_scope_options": build_research_scope_options(
            portfolio_id,
            planning_taxonomy_id=str(settings_payload.get("planning_taxonomy_id") or "").strip() or None,
            as_of_date=date.fromisoformat(str(settings_payload["as_of_date"])),
        ),
        "settings": settings_payload,
        "current_context": context,
        "runs": runs,
        "selected_run": selected_run,
    }


def update_research_settings(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str | None,
    comparator_taxonomy_node_id: str | None,
    as_of_date: date | None,
    start_date: date | None,
    lookback_days: int,
    benchmark_mode: str,
    run_template: str,
    target_set_mode: str,
    target_dimension: str,
    rebalance_frequency: str,
    notes: str | None,
) -> dict[str, object] | None:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        return None

    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomy = _validate_planning_taxonomy(session, portfolio_id, planning_taxonomy_id)
        resolved_scope_node_id = _validate_research_scope(
            session,
            taxonomy=taxonomy,
            comparator_taxonomy_node_id=comparator_taxonomy_node_id,
        )
        row = _ensure_research_settings_record(
            session,
            portfolio_id,
            default_planning_taxonomy_id=str(portfolio.get("default_planning_taxonomy_id") or "").strip() or None,
            default_as_of_date=_default_as_of_date(portfolio),
        )
        row.planning_taxonomy_id = str(planning_taxonomy_id or "").strip() or None
        row.as_of_date = as_of_date or _default_as_of_date(portfolio)
        row.comparator_taxonomy_node_id = resolved_scope_node_id
        row.start_date = start_date or (row.as_of_date - timedelta(days=180) if row.as_of_date else None)
        row.lookback_days = int(lookback_days or 90)
        row.benchmark_mode = (benchmark_mode or "none").strip() or "none"
        row.run_template = "taxonomy_backtest"
        row.target_set_mode = (target_set_mode or "taa_over_saa").strip() or "taa_over_saa"
        row.target_dimension = (target_dimension or "scope_default").strip() or "scope_default"
        row.rebalance_frequency = (rebalance_frequency or "monthly").strip() or "monthly"
        row.notes = notes
        row.updated_at = _utc_now_iso()
        if row.start_date and row.as_of_date and row.as_of_date < row.start_date:
            raise ValueError("start_date must not be later than as_of_date.")
        session.commit()
        taxonomy_name_map = _taxonomy_name_map(portfolio_id)
        scope_name_map = _scope_name_map(
            portfolio_id,
            planning_taxonomy_id=str(row.planning_taxonomy_id or "").strip() or None,
        )
        return _serialize_settings_row(row, taxonomy_name_map, scope_name_map)


def run_portfolio_research(
    portfolio_id: str,
    *,
    requested_by: str | None = None,
) -> dict[str, object] | None:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        return None

    taxonomy_name_map = _taxonomy_name_map(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        settings_row = _ensure_research_settings_record(
            session,
            portfolio_id,
            default_planning_taxonomy_id=str(portfolio.get("default_planning_taxonomy_id") or "").strip() or None,
            default_as_of_date=_default_as_of_date(portfolio),
        )
        taxonomy = _validate_planning_taxonomy(session, portfolio_id, settings_row.planning_taxonomy_id)
        resolved_scope_node_id = _validate_research_scope(
            session,
            taxonomy=taxonomy,
            comparator_taxonomy_node_id=settings_row.comparator_taxonomy_node_id,
        )

        requested_at = _utc_now_iso()
        run_id = _next_research_run_id(portfolio_id)
        effective_as_of_date = settings_row.as_of_date or _default_as_of_date(portfolio)
        run_row = ResearchRunRecordModel(
            research_run_id=run_id,
            portfolio_id=portfolio_id,
            job_type=settings_row.run_template or "taxonomy_backtest",
            status="running",
            requested_at=requested_at,
            started_at=requested_at,
            finished_at=None,
            as_of_date=effective_as_of_date,
            planning_taxonomy_id=settings_row.planning_taxonomy_id,
            lookback_days=int(settings_row.lookback_days or 90),
            benchmark_mode=settings_row.benchmark_mode or "none",
            run_template=settings_row.run_template or "taxonomy_backtest",
            requested_by=requested_by,
            headline=None,
            detail_json=None,
            artifacts_json=None,
            request_payload_json={
                "portfolio_id": portfolio_id,
                "planning_taxonomy_id": settings_row.planning_taxonomy_id,
                "comparator_taxonomy_node_id": resolved_scope_node_id,
                "as_of_date": _iso_date(effective_as_of_date),
                "start_date": _iso_date(settings_row.start_date),
                "lookback_days": int(settings_row.lookback_days or 90),
                "benchmark_mode": settings_row.benchmark_mode or "none",
                "run_template": settings_row.run_template or "taxonomy_backtest",
                "target_set_mode": settings_row.target_set_mode or "taa_over_saa",
                "target_dimension": settings_row.target_dimension or "scope_default",
                "rebalance_frequency": settings_row.rebalance_frequency or "monthly",
                "notes": settings_row.notes,
            },
            error_message=None,
        )
        session.add(run_row)
        session.commit()

        try:
            context = _build_research_context(
                portfolio_id,
                planning_taxonomy_id=str(settings_row.planning_taxonomy_id or "").strip() or None,
                as_of_date=effective_as_of_date,
                lookback_days=int(settings_row.lookback_days or 90),
            )
            planning_taxonomy_name = taxonomy_name_map.get(str(settings_row.planning_taxonomy_id or "").strip() or "")
            resolved_start_date = settings_row.start_date or (effective_as_of_date - timedelta(days=180))
            backtest = run_taxonomy_backtest(
                portfolio_id,
                planning_taxonomy_id=str(settings_row.planning_taxonomy_id or "").strip(),
                comparator_taxonomy_node_id=resolved_scope_node_id,
                start_date=resolved_start_date,
                end_date=effective_as_of_date,
                lookback_days=int(settings_row.lookback_days or 90),
                target_set_mode=settings_row.target_set_mode or "taa_over_saa",
                target_dimension=settings_row.target_dimension or "scope_default",
                rebalance_frequency=settings_row.rebalance_frequency or "monthly",
            )
            detail = _build_taxonomy_backtest_detail(
                context,
                planning_taxonomy_name=planning_taxonomy_name,
                settings_payload=deepcopy(run_row.request_payload_json or {}),
                backtest=backtest,
            )
            artifacts = _write_artifacts(
                portfolio_id,
                run_id,
                settings_payload=deepcopy(run_row.request_payload_json or {}),
                context=context,
                detail=detail,
            )
            run_row.status = "completed"
            run_row.finished_at = _utc_now_iso()
            run_row.headline = str(detail.get("headline") or "")
            run_row.detail_json = detail
            run_row.artifacts_json = artifacts
            run_row.error_message = None
            session.commit()
        except (InstrumentRegistryError, ValueError) as error:
            run_row.status = "failed"
            run_row.finished_at = _utc_now_iso()
            run_row.error_message = str(error)
            run_row.artifacts_json = []
            session.commit()
            raise
        except Exception as error:  # pragma: no cover - defensive fallback
            run_row.status = "failed"
            run_row.finished_at = _utc_now_iso()
            run_row.error_message = str(error)
            run_row.artifacts_json = []
            session.commit()
            raise

        session.refresh(run_row)
        return _serialize_run_row(run_row, taxonomy_name_map)


def read_research_artifact_content(
    portfolio_id: str,
    *,
    path: str,
) -> dict[str, object]:
    artifact_path = _resolve_artifact_path(portfolio_id, path)
    preview_kind = _artifact_preview_kind(artifact_path)
    media_type = _artifact_media_type(artifact_path)
    return {
        "filename": artifact_path.name,
        "path": path,
        "media_type": media_type,
        "encoding": "text",
        "preview_kind": preview_kind,
        "content": artifact_path.read_text(encoding="utf-8"),
    }
