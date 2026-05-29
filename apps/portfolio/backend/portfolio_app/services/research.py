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
    PortfolioRecordModel,
    ResearchRunRecordModel,
    ResearchSettingsRecordModel,
    TaxonomyNodeRecordModel,
    TaxonomyRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.instrument_charts import build_instrument_sparkline
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.ledger import build_account_workspace
from portfolio_app.services.performance import build_holdings_report
from portfolio_app.services.research_solver import (
    RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
    build_research_calculation_frequency_profile,
    build_research_scope_options,
    solve_current_target_weights,
)
from portfolio_app.services.portfolio_store import (
    get_portfolio,
    list_target_sets,
    list_taxonomies,
    list_taxonomy_assignments,
    list_taxonomy_nodes,
    list_accounts,
    list_transactions,
)
from portfolio_app.services.risk_model import get_portfolio_risk_policy, normalize_portfolio_risk_policy

TEXT_SUFFIXES = {".csv", ".json", ".md", ".txt", ".yaml", ".yml"}
HTML_SUFFIXES = {".html"}
CURRENT_TARGET_RUN_TEMPLATE = "target_weight_solve"


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
    return normalized.replace("-", " ").title()


def _format_missing_return_policy(value: object) -> str:
    normalized = str(value or RESEARCH_DEFAULT_MISSING_RETURN_POLICY).strip()
    if normalized == "complete_case_drop":
        return "Complete Case Drop"
    if normalized == "strict":
        return "Strict"
    return normalized.replace("_", " ").title()


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
        if not record.target_dimension:
            record.target_dimension = "scope_default"
            changed = True
        if not record.capital_mode:
            record.capital_mode = "unit_notional"
            changed = True
        if not getattr(record, "calculation_frequency", None):
            record.calculation_frequency = "auto"
            changed = True
        if not getattr(record, "missing_return_policy", None):
            record.missing_return_policy = RESEARCH_DEFAULT_MISSING_RETURN_POLICY
            changed = True
        if record.frozen_taxonomy_node_ids_json is None:
            record.frozen_taxonomy_node_ids_json = []
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
        lookback_days=90,
        calculation_frequency="auto",
        missing_return_policy=RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
        target_dimension="scope_default",
        capital_mode="unit_notional",
        gross_exposure=None,
        target_volatility=None,
        max_gross_exposure=None,
        frozen_taxonomy_node_ids_json=[],
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
        "lookback_days": int(row.lookback_days or 90),
        "calculation_frequency": row.calculation_frequency or "auto",
        "missing_return_policy": row.missing_return_policy or RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
        "target_dimension": row.target_dimension or "scope_default",
        "capital_mode": row.capital_mode or "unit_notional",
        "gross_exposure": _safe_float(row.gross_exposure),
        "target_volatility": _safe_float(row.target_volatility),
        "max_gross_exposure": _safe_float(row.max_gross_exposure),
        "frozen_taxonomy_node_ids": deepcopy(row.frozen_taxonomy_node_ids_json or []),
        "notes": row.notes,
        "updated_at": row.updated_at,
    }


def _serialize_run_row(
    row: ResearchRunRecordModel,
    taxonomy_name_map: dict[str, str],
) -> dict[str, object]:
    planning_taxonomy_id = str(row.planning_taxonomy_id or "").strip() or None
    detail = deepcopy(row.detail_json or {})
    if isinstance(detail.get("top_holdings"), list):
        detail["top_holdings"] = [
            _normalize_top_holding_snapshot(item)
            for item in detail["top_holdings"]
            if isinstance(item, dict)
        ]
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
        "requested_by": row.requested_by,
        "headline": row.headline,
        "error_message": row.error_message,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "detail": detail,
    }


def _normalize_top_holding_snapshot(item: dict[str, object]) -> dict[str, object]:
    instrument_id = str(item.get("instrument_id") or "").strip()
    instrument_name = str(item.get("instrument_name") or "").strip()
    instrument_type = str(item.get("instrument_type") or "").strip() or None
    if not instrument_id or not instrument_name or not instrument_type:
        raise ValueError("Top holding snapshots require canonical instrument_id, instrument_name, and instrument_type.")
    return {
        "instrument_id": instrument_id,
        "instrument_name": instrument_name,
        "instrument_type": instrument_type,
        "allocation": _safe_float(item.get("allocation")),
        "market_value_base": _safe_float(item.get("market_value_base")),
        "cost_basis_base": _safe_float(item.get("cost_basis_base")),
        "base_currency": str(item.get("base_currency") or "USD"),
        "price": _safe_float(item.get("price")),
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
        instrument_ref = position.get("instrument_ref") if isinstance(position.get("instrument_ref"), dict) else {}
        rendered.append(
            _normalize_top_holding_snapshot(
                {
                    "instrument_id": str(position.get("instrument_id") or ""),
                    "instrument_name": str(instrument_ref.get("instrument_name") or ""),
                    "instrument_type": str(instrument_ref.get("instrument_type") or ""),
                    "allocation": _safe_float(position.get("portfolio_weight")),
                    "market_value_base": _safe_float(position.get("market_value_base")),
                    "cost_basis_base": _safe_float(position.get("cost_basis_base")),
                    "base_currency": base_currency,
                    "price": _safe_float(position.get("last_price")),
                }
            )
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
        entity_id = str(item.get("target_entity_id") or "")
        if not entity_id:
            continue
        assignment_key = (target_scope, entity_id)
        assignment_by_entity.setdefault(assignment_key, item)

    visible_cash_accounts = [
        account_row
        for account_row in account_rows
        if str((account_row.get("account") or {}).get("account_type") or "") == "deposit_account"
        and (
            abs(_safe_float(account_row.get("account_value_base")) or 0.0) > 1e-9
            or ("cash_bucket", str((account_row.get("account") or {}).get("account_id") or "")) in assignment_by_entity
        )
    ]

    total_entity_value_base = sum(_safe_float(position.get("market_value_base")) or 0.0 for position in statement_positions)
    total_entity_value_base += sum(
        _safe_float(account_row.get("account_value_base")) or 0.0 for account_row in visible_cash_accounts
    )

    buckets: dict[str, dict[str, object]] = {}
    for position in statement_positions:
        instrument_id = str(position.get("instrument_id") or "")
        assignment = assignment_by_entity.get(("instrument", instrument_id))
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
        cash_value_base = _safe_float(account_row.get("account_value_base")) or 0.0
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

    configured_target_sets = [
        item
        for item in list_target_sets(portfolio_id, taxonomy_id=planning_taxonomy_id)
        if str(item.get("status") or "") == "active"
    ]
    return {
        "root_saa_configured": any(
            str(item.get("target_set_type") or "") == "saa" and not item.get("comparator_taxonomy_node_id")
            for item in configured_target_sets
        ),
        "root_taa_configured": any(
            str(item.get("target_set_type") or "") == "taa" and not item.get("comparator_taxonomy_node_id")
            for item in configured_target_sets
        ),
        "scoped_target_set_count": sum(1 for item in configured_target_sets if item.get("comparator_taxonomy_node_id")),
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

    statement = build_holdings_report(
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
        reference_instrument = top_holdings[0]
        daily_points = build_instrument_sparkline(
            str(reference_instrument.get("instrument_id") or ""),
            as_of_date=as_of_date,
            max_points=20,
        )
        chart_label = "Reference Tape"
        chart_currency = str(reference_instrument.get("base_currency") or statement.get("base_currency") or "USD")
        chart_note = (
            f"Using the six-month sparkline for {reference_instrument.get('instrument_name') or reference_instrument.get('instrument_id')} "
            "until a cheaper portfolio daily tape is wired into the research workbench."
        )

    statement_nav_base = _safe_float(statement.get("total_nav_base"))
    portfolio_nav_base = _safe_float(portfolio.get("nav"))
    resolved_nav_base = statement_nav_base if statement_nav_base is not None else portfolio_nav_base

    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": str(portfolio.get("portfolio_name") or portfolio_id),
        "base_currency": str(statement.get("base_currency") or portfolio.get("base_currency") or "USD"),
        "as_of_date": as_of_date.isoformat(),
        "lookback_start": lookback_start.isoformat(),
        "lookback_end": as_of_date.isoformat(),
        "nav": resolved_nav_base,
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
            "end_nav": resolved_nav_base,
        },
        "planning_target_summary": planning_target_summary,
        "chart_points": daily_points,
        "top_holdings": top_holdings,
        "planning_groups": planning_groups,
    }

def _build_current_target_findings(solution: dict[str, object]) -> tuple[list[dict[str, str]], list[str]]:
    scope = solution.get("scope") or {}
    target_weight_gaps = list(solution.get("target_weight_gaps") or [])
    solve_event = solution.get("solve_event") if isinstance(solution.get("solve_event"), dict) else None
    warnings = list(solution.get("warnings") or [])
    target_rows = _build_target_rows(solution)

    findings: list[dict[str, str]] = []
    questions: list[str] = []
    scope_label = str(scope.get("label") or "Selected Scope")

    if target_rows:
        largest_gap = max(target_rows, key=lambda item: abs(_safe_float(item.get("gap_to_implementation")) or 0.0))
        findings.append(
            {
                "title": "Current Target Weights",
                "detail": (
                    f"{scope_label} target weights were solved from current holdings, active targets, and the selected "
                    f"covariance lookback. Largest absolute gap is {largest_gap.get('label')} at "
                    f"{_format_pct(abs(_safe_float(largest_gap.get('gap_to_implementation')) or 0.0), 3)}."
                ),
            }
        )

    if solve_event:
        findings.append(
            {
                "title": "Volatility Overlay",
                "detail": (
                    f"Estimated risky-sleeve volatility is {_format_pct(_safe_float(solve_event.get('estimated_risk_sleeve_volatility')))} "
                    f"versus target volatility {_format_pct(_safe_float(solve_event.get('target_volatility')))}; gross exposure resolves to "
                    f"{_format_number(_safe_float(solve_event.get('gross_exposure')), 3)}."
                ),
            }
        )

    if target_weight_gaps:
        top_gap = max(target_weight_gaps, key=lambda item: abs(_safe_float(item.get("gap")) or 0.0))
        if abs(_safe_float(top_gap.get("gap")) or 0.0) > 0.01:
            questions.append(
                f"Review whether {top_gap.get('label')} should be {str(top_gap.get('action') or '').lower()}d by {_format_pct(abs(_safe_float(top_gap.get('gap')) or 0.0), 3)} versus the solved target weight."
            )

    if warnings:
        findings.append(
            {
                "title": "Coverage / Solver Warnings",
                "detail": "; ".join(str(item) for item in warnings[:2]),
            }
        )

    if not questions:
        questions.append("Review whether the active TAA target remains appropriate before translating solved weights into orders.")
    return findings[:4], questions[:3]


def _build_current_target_signals(
    *,
    settings_payload: dict[str, object],
    scope: dict[str, object],
    solve_event: dict[str, object] | None,
) -> list[dict[str, object]]:
    event = solve_event or {}
    solver_label = _format_solver_kind(event.get("solver_kind")) if str(event.get("solver_kind") or "").strip() else "—"
    return [
        {"label": "Template", "value": "Current Target Weight Solve", "tone": "neutral"},
        {"label": "Target Layer", "value": "Active TAA", "tone": "neutral"},
        {
            "label": "Capital Mode",
            "value": str(settings_payload.get("capital_mode") or "unit_notional").replace("_", " ").title(),
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
            "label": "Lookback",
            "value": f"{int(settings_payload.get('lookback_days') or 90)}D",
            "tone": "neutral",
        },
        {
            "label": "Frequency",
            "value": str(event.get("calculation_frequency") or settings_payload.get("calculation_frequency") or "auto").replace("_", " ").title(),
            "tone": "neutral",
        },
        {
            "label": "Missing Returns",
            "value": _format_missing_return_policy(event.get("missing_return_policy") or settings_payload.get("missing_return_policy")),
            "tone": "neutral",
        },
        {
            "label": "Frozen Sleeves",
            "value": str(len(list(settings_payload.get("frozen_taxonomy_node_ids") or []))) if list(settings_payload.get("frozen_taxonomy_node_ids") or []) else "—",
            "tone": "neutral",
        },
        {
            "label": "Solver",
            "value": solver_label,
            "tone": "neutral",
        },
        {
            "label": "Covariance Model",
            "value": str(event.get("covariance_model") or "—").replace("_", " ").title(),
            "tone": "neutral",
        },
        {
            "label": "Risk Contribution",
            "value": str(event.get("risk_contribution_mode") or "—").upper(),
            "tone": "neutral",
        },
        {
            "label": "Target Volatility",
            "value": _format_pct(_safe_float(settings_payload.get("target_volatility"))),
            "tone": "neutral",
        },
        {
            "label": "Estimated Volatility",
            "value": _format_pct(_safe_float(event.get("estimated_risk_sleeve_volatility"))),
            "tone": "neutral",
        },
        {
            "label": "Gross Exposure",
            "value": _format_number(_safe_float(event.get("gross_exposure")), 3),
            "tone": "neutral",
        },
        {
            "label": "Largest Weight Gap",
            "value": _format_pct(_safe_float(event.get("max_weight_gap")), 3),
            "tone": "neutral",
        },
        {
            "label": "Largest Risk Gap",
            "value": _format_pct(_safe_float(event.get("max_risk_share_gap")), 3),
            "tone": "neutral",
        },
    ]


def _target_source_label(target_row: dict[str, object] | None) -> str:
    if not target_row:
        return "—"
    override = str(target_row.get("source_label_override") or "").strip()
    if override:
        return override
    target_set_type = str(target_row.get("source_target_set_type") or "").strip()
    if target_set_type == "taa":
        return "TAA"
    if target_set_type == "saa":
        return "SAA"
    return "Unconfigured"


def _build_target_rows(solution: dict[str, object]) -> list[dict[str, object]]:
    actual_rows = list(solution.get("actual_rows") or [])
    target_rows = list(solution.get("resolved_target_rows") or [])
    gap_rows = list(solution.get("target_weight_gaps") or [])
    actual_by_key = {
        (str(row.get("member_type") or ""), str(row.get("member_id") or "")): row
        for row in actual_rows
    }
    target_by_key = {
        (str(row.get("member_type") or ""), str(row.get("member_id") or "")): row
        for row in target_rows
    }
    gap_by_key = {
        (str(row.get("member_type") or ""), str(row.get("member_id") or "")): row
        for row in gap_rows
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
        target_gap = gap_by_key.get(key)
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
                    or (target_gap or {}).get("label")
                    or key[1]
                ),
                "current_weight": current_weight,
                "current_value_base": _safe_float((actual_row or {}).get("current_value_base")),
                "default_target_dimension": (target_row or {}).get("default_target_dimension"),
                "selected_target_dimension": (target_row or {}).get("selected_dimension"),
                "source_target_set_type": (target_row or {}).get("source_target_set_type"),
                "source_target_set_id": (target_row or {}).get("source_target_set_id"),
                "source_label": _target_source_label(target_row),
                "selected_target_value": _safe_float((target_row or {}).get("selected_value")),
                "target_weight": _safe_float((target_row or {}).get("target_weight")),
                "target_risk_share": _safe_float((target_row or {}).get("target_risk_share")),
                "implementation_weight": implementation_weight,
                "gap_to_implementation": gap_to_implementation,
                "action": str((target_gap or {}).get("action") or "").strip() or None,
            }
        )
    return rendered


def _build_target_assumptions(
    *,
    settings_payload: dict[str, object],
    target_rows: list[dict[str, object]],
    solve_event: dict[str, object] | None,
) -> list[str]:
    assumptions = [
        "Local target solves are long-only and fully invested within each selected scope; member weights are bounded between 0% and 100%.",
    ]
    frequency = str((solve_event or {}).get("calculation_frequency") or settings_payload.get("calculation_frequency") or "auto")
    assumptions.append(
        f"Risk inputs are first aligned to a {frequency.replace('_', ' ')} calculation frequency, using the last valid observation inside each target period."
    )
    missing_return_policy = str((solve_event or {}).get("missing_return_policy") or settings_payload.get("missing_return_policy") or RESEARCH_DEFAULT_MISSING_RETURN_POLICY)
    if missing_return_policy == "complete_case_drop":
        dropped_count = int((solve_event or {}).get("missing_return_row_count") or 0)
        assumptions.append(
            "Missing-return handling uses complete-case row drops: any period with an active member return missing is removed for covariance/RC, "
            f"subject to the configured coverage caps; dropped rows in this solve: {dropped_count}."
        )
    else:
        assumptions.append("Missing-return handling is strict: active covariance/RC inputs must have complete aligned returns.")
    if str(settings_payload.get("target_dimension") or "") == "scope_default":
        assumptions.append("Scope Default resolves each sleeve using that sleeve's own default target dimension before rolling results upward.")
    if str((solve_event or {}).get("target_dimension") or "") == "risk_budget":
        covariance_model = str((solve_event or {}).get("covariance_model") or "research covariance").replace("_", " ")
        contribution_mode = str((solve_event or {}).get("risk_contribution_mode") or "risk").upper()
        assumptions.append(
            f"Risk-budget sleeves solve current implementation weights from the trailing local {covariance_model} window using {contribution_mode} risk contributions; "
            "leaf implementation weights can roll up through the sleeve tree, but risk targets remain local and are not multiplied by ancestor risk targets."
        )
    if str(settings_payload.get("target_dimension") or "") != "scope_default":
        assumptions.append("The selected scope can use an explicit target-dimension override; child sleeves still use their own configured default target dimension.")
    if str(settings_payload.get("capital_mode") or "unit_notional") == "target_volatility":
        assumptions.append("After recursive sleeve targets are resolved, Research estimates risky-sleeve volatility, scales gross exposure toward target volatility, and sends the residual into cash.")
    elif str(settings_payload.get("capital_mode") or "unit_notional") == "fixed_gross":
        assumptions.append("After recursive sleeve targets are resolved, Research applies a fixed gross-exposure overlay and leaves the residual in cash.")
    if any(str(item.get("source_label_override") or "") == "Single Member" for item in target_rows):
        assumptions.append("Single-member sleeves resolve to 100% of that member; multi-member scopes require an active complete SAA/TAA target set.")
    return assumptions[:5]


def _build_current_target_detail(
    context: dict[str, object],
    *,
    planning_taxonomy_name: str | None,
    settings_payload: dict[str, object],
    solution: dict[str, object],
) -> dict[str, object]:
    findings, questions = _build_current_target_findings(solution)
    scope = solution.get("scope") or {}
    solve_event = solution.get("solve_event") if isinstance(solution.get("solve_event"), dict) else None
    resolved_target_rows = list(solution.get("resolved_target_rows") or [])
    target_rows = _build_target_rows(solution)
    signals = _build_current_target_signals(
        settings_payload=settings_payload,
        scope=scope,
        solve_event=solve_event,
    )
    headline = (
        f"{scope.get('label') or 'Selected Scope'} target weights solved as of {context.get('as_of_date')} "
        f"under {planning_taxonomy_name or 'the selected planning taxonomy'}."
    )
    return {
        "headline": headline,
        "coverage_note": (
            "This run resolves current target weights from current holdings, the selected planning taxonomy, "
            "active TAA, the covariance lookback, and the configured capital overlay."
        ),
        "signals": signals,
        "findings": findings,
        "next_questions": questions,
        "top_holdings": deepcopy(context.get("top_holdings") or []),
        "planning_groups": deepcopy(context.get("planning_groups") or []),
        "selected_scope": deepcopy(scope),
        "target_assumptions": _build_target_assumptions(
            settings_payload=settings_payload,
            target_rows=resolved_target_rows,
            solve_event=solve_event,
        ),
        "target_rows": target_rows,
        "member_targets": deepcopy(solution.get("member_targets") or []),
        "leaf_targets": deepcopy(solution.get("leaf_targets") or []),
        "solve_event": deepcopy(solve_event),
        "scope_solve_events": deepcopy(solution.get("scope_solve_events") or []),
        "target_weight_gaps": deepcopy(solution.get("target_weight_gaps") or []),
        "warnings": deepcopy(solution.get("warnings") or []),
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
    reference_tape_path = run_root / "reference_tape.csv"
    target_weights_path = run_root / "target_weights.csv"
    member_targets_path = run_root / "member_targets.csv"
    leaf_targets_path = run_root / "leaf_targets.csv"
    solve_event_path = run_root / "solve_event.csv"
    scope_solve_events_path = run_root / "scope_solve_events.csv"
    target_weight_gaps_path = run_root / "target_weight_gaps.csv"

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
    _write_csv(reference_tape_path, list(context.get("chart_points") or []))
    _write_csv(target_weights_path, list(detail.get("target_rows") or []))
    _write_csv(member_targets_path, list(detail.get("member_targets") or []))
    _write_csv(leaf_targets_path, list(detail.get("leaf_targets") or []))
    solve_event = detail.get("solve_event") if isinstance(detail.get("solve_event"), dict) else None
    _write_csv(solve_event_path, [solve_event] if solve_event else [])
    _write_csv(scope_solve_events_path, list(detail.get("scope_solve_events") or []))
    _write_csv(target_weight_gaps_path, list(detail.get("target_weight_gaps") or []))

    artifacts = []
    for artifact_id, label, path in [
        ("report", "Report", report_path),
        ("summary", "Summary JSON", summary_path),
        ("request", "Run Request", settings_path),
        ("holdings", "Top Holdings CSV", holdings_path),
        ("groups", "Planning Groups CSV", groups_path),
        ("reference_tape", "Reference Tape CSV", reference_tape_path),
        ("target_weights", "Target Weights CSV", target_weights_path),
        ("member_targets", "Member Targets CSV", member_targets_path),
        ("leaf_targets", "Leaf Targets CSV", leaf_targets_path),
        ("solve_event", "Solve Event CSV", solve_event_path),
        ("scope_solve_events", "Scope Solve Events CSV", scope_solve_events_path),
        ("target_weight_gaps", "Target Weight Gaps CSV", target_weight_gaps_path),
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
        production_risk_model = get_portfolio_risk_policy(portfolio_id) or {}
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

    risk_lookback_days = int(production_risk_model.get("lookback_days") or settings_payload.get("lookback_days") or 90)
    risk_calculation_frequency = str(
        production_risk_model.get("calculation_frequency") or settings_payload.get("calculation_frequency") or "auto"
    )
    context = _build_research_context(
        portfolio_id,
        planning_taxonomy_id=str(settings_payload.get("planning_taxonomy_id") or "").strip() or None,
        as_of_date=date.fromisoformat(str(settings_payload["as_of_date"])),
        lookback_days=risk_lookback_days,
    )
    calculation_frequency_profile = build_research_calculation_frequency_profile(
        portfolio_id,
        planning_taxonomy_id=str(settings_payload.get("planning_taxonomy_id") or "").strip() or None,
        comparator_taxonomy_node_id=str(settings_payload.get("comparator_taxonomy_node_id") or "").strip() or None,
        as_of_date=date.fromisoformat(str(settings_payload["as_of_date"])),
        lookback_days=risk_lookback_days,
        requested_frequency=risk_calculation_frequency,
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
        "calculation_frequency": calculation_frequency_profile,
        "settings": settings_payload,
        "risk_policy": production_risk_model,
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
    lookback_days: int,
    calculation_frequency: str,
    missing_return_policy: str,
    covariance_model_id: str,
    contribution_mode: str,
    target_dimension: str,
    capital_mode: str,
    gross_exposure: float | None,
    target_volatility: float | None,
    max_gross_exposure: float | None,
    frozen_taxonomy_node_ids: list[str] | None,
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
        resolved_planning_taxonomy_id = str(planning_taxonomy_id or "").strip() or None
        existing_planning_taxonomy_id = str(row.planning_taxonomy_id or "").strip() or None
        if frozen_taxonomy_node_ids is None:
            resolved_frozen_ids = [] if resolved_planning_taxonomy_id != existing_planning_taxonomy_id else None
        else:
            resolved_frozen_ids = list(
                dict.fromkeys(str(item).strip() for item in frozen_taxonomy_node_ids if str(item).strip())
            )
        if taxonomy is None and resolved_frozen_ids:
            raise ValueError("Frozen taxonomy nodes require a selected planning taxonomy.")
        if taxonomy is not None and resolved_frozen_ids:
            valid_node_ids = {
                str(item.get("taxonomy_node_id") or "")
                for item in list_taxonomy_nodes(portfolio_id)
                if str(item.get("taxonomy_id") or "") == taxonomy.taxonomy_id
            }
            unknown = [item for item in resolved_frozen_ids if item not in valid_node_ids]
            if unknown:
                raise ValueError("Frozen taxonomy nodes must belong to the selected planning taxonomy.")
        row.planning_taxonomy_id = resolved_planning_taxonomy_id
        row.as_of_date = as_of_date or _default_as_of_date(portfolio)
        row.comparator_taxonomy_node_id = resolved_scope_node_id
        row.lookback_days = int(lookback_days or 90)
        row.calculation_frequency = (calculation_frequency or "auto").strip() or "auto"
        row.missing_return_policy = (missing_return_policy or RESEARCH_DEFAULT_MISSING_RETURN_POLICY).strip() or RESEARCH_DEFAULT_MISSING_RETURN_POLICY
        row.target_dimension = (target_dimension or "scope_default").strip() or "scope_default"
        row.capital_mode = (capital_mode or "unit_notional").strip() or "unit_notional"
        row.gross_exposure = gross_exposure
        row.target_volatility = target_volatility
        row.max_gross_exposure = max_gross_exposure
        if resolved_frozen_ids is not None:
            row.frozen_taxonomy_node_ids_json = resolved_frozen_ids
        row.notes = notes
        row.updated_at = _utc_now_iso()
        portfolio_row = session.get(PortfolioRecordModel, portfolio_id)
        if portfolio_row is None:
            return None
        portfolio_row.risk_policy_json = normalize_portfolio_risk_policy(
            {
                "covariance_model_id": covariance_model_id,
                "lookback_days": row.lookback_days,
                "calculation_frequency": row.calculation_frequency,
                "missing_return_policy": row.missing_return_policy,
                "contribution_mode": contribution_mode,
            }
        )
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
        production_risk_model = get_portfolio_risk_policy(portfolio_id)
        risk_lookback_days = int((production_risk_model or {}).get("lookback_days") or settings_row.lookback_days or 90)
        risk_calculation_frequency = str(
            (production_risk_model or {}).get("calculation_frequency") or settings_row.calculation_frequency or "auto"
        )
        risk_missing_return_policy = str(
            (production_risk_model or {}).get("missing_return_policy")
            or settings_row.missing_return_policy
            or RESEARCH_DEFAULT_MISSING_RETURN_POLICY
        )
        run_row = ResearchRunRecordModel(
            research_run_id=run_id,
            portfolio_id=portfolio_id,
            job_type=CURRENT_TARGET_RUN_TEMPLATE,
            status="running",
            requested_at=requested_at,
            started_at=requested_at,
            finished_at=None,
            as_of_date=effective_as_of_date,
            planning_taxonomy_id=settings_row.planning_taxonomy_id,
            lookback_days=risk_lookback_days,
            requested_by=requested_by,
            headline=None,
            detail_json=None,
            artifacts_json=None,
            request_payload_json={
                "portfolio_id": portfolio_id,
                "planning_taxonomy_id": settings_row.planning_taxonomy_id,
                "comparator_taxonomy_node_id": resolved_scope_node_id,
                "as_of_date": _iso_date(effective_as_of_date),
                "lookback_days": risk_lookback_days,
                "calculation_frequency": risk_calculation_frequency,
                "missing_return_policy": risk_missing_return_policy,
                "risk_model": deepcopy(production_risk_model or {}),
                "target_dimension": settings_row.target_dimension or "scope_default",
                "capital_mode": settings_row.capital_mode or "unit_notional",
                "gross_exposure": _safe_float(settings_row.gross_exposure),
                "target_volatility": _safe_float(settings_row.target_volatility),
                "max_gross_exposure": _safe_float(settings_row.max_gross_exposure),
                "frozen_taxonomy_node_ids": deepcopy(settings_row.frozen_taxonomy_node_ids_json or []),
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
                lookback_days=risk_lookback_days,
            )
            planning_taxonomy_name = taxonomy_name_map.get(str(settings_row.planning_taxonomy_id or "").strip() or "")
            solution = solve_current_target_weights(
                portfolio_id,
                planning_taxonomy_id=str(settings_row.planning_taxonomy_id or "").strip(),
                comparator_taxonomy_node_id=resolved_scope_node_id,
                as_of_date=effective_as_of_date,
                lookback_days=risk_lookback_days,
                calculation_frequency=risk_calculation_frequency,
                missing_return_policy=risk_missing_return_policy,
                target_dimension=settings_row.target_dimension or "scope_default",
                capital_mode=settings_row.capital_mode or "unit_notional",
                gross_exposure=_safe_float(settings_row.gross_exposure),
                target_volatility=_safe_float(settings_row.target_volatility),
                max_gross_exposure=_safe_float(settings_row.max_gross_exposure),
                frozen_taxonomy_node_ids=deepcopy(settings_row.frozen_taxonomy_node_ids_json or []),
                risk_model_config=deepcopy(production_risk_model or {}),
            )
            detail = _build_current_target_detail(
                context,
                planning_taxonomy_name=planning_taxonomy_name,
                settings_payload=deepcopy(run_row.request_payload_json or {}),
                solution=solution,
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
        except Exception as error:  # pragma: no cover - defensive error handling
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
