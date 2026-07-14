from __future__ import annotations

import csv
import hashlib
import json
import mimetypes
import shutil
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import defer

from portfolio_app.core.operating_profiles import require_allocation_research_profile
from portfolio_app.core.settings import get_settings
from portfolio_app.db.models import (
    PortfolioRecordModel,
    AllocationResearchRunRecordModel,
    AllocationResearchSettingsRecordModel,
    TargetSetLineRecordModel,
    TargetSetRecordModel,
    TaxonomyAssignmentRecordModel,
    TaxonomyNodeRecordModel,
    TaxonomyRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.fact_currency import require_portfolio_fact_currency
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.published_holdings import (
    PublishedCashAccountValue,
    PublishedHoldingPosition,
    PublishedHoldingsStatement,
    read_current_published_holdings,
    read_current_published_holdings_as_of_date,
)
from portfolio_app.services.allocation_policy_replay import (
    build_current_target_policy_replay,
    build_allocation_research_policy_replay_benchmark_comparison,
)
from portfolio_app.services.allocation_research import (
    build_allocation_research_calculation_frequency_profile,
    build_allocation_research_scope_options,
    solve_current_target_weights,
)
from portfolio_app.services.allocation_solver import (
    SYSTEM_CASH_TARGET_LABEL,
    SYSTEM_CASH_TARGET_MEMBER_ID,
)
from portfolio_app.services.portfolio_market_data import (
    validate_portfolio_market_data_dependencies_current,
)
from portfolio_app.services.risk_math import (
    DEFAULT_MISSING_RETURN_POLICY,
    risk_window_start_date,
)
from portfolio_app.services.portfolio_store import (
    get_portfolio,
    list_target_sets,
    list_taxonomies,
    list_taxonomy_assignments,
    list_taxonomy_nodes,
)
from portfolio_app.services.risk_model import get_portfolio_risk_policy, normalize_portfolio_risk_policy, risk_window_label

TEXT_SUFFIXES = {".csv", ".json", ".md", ".txt", ".yaml", ".yml"}
HTML_SUFFIXES = {".html"}
CURRENT_TARGET_RUN_TEMPLATE = "target_weight_solve"
ALLOCATION_RESEARCH_AS_OF_MODE_DYNAMIC = "dynamic"
ALLOCATION_RESEARCH_AS_OF_MODE_PINNED = "pinned"
ALLOCATION_RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION = 1
ALLOCATION_RESEARCH_PUBLISHED_HOLDINGS_INPUT_SCHEMA_VERSION = 1
ALLOCATION_RESEARCH_MARKET_DATA_INPUT_SCHEMA_VERSION = 1


class AllocationResearchRunNotFoundError(LookupError):
    """An explicitly selected Allocation Research run does not exist in the portfolio."""


def _get_allocation_research_portfolio(
    portfolio_id: str,
) -> dict[str, object] | None:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is not None:
        require_allocation_research_profile(portfolio.get("operating_profile"))
    return portfolio


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


def _normalize_allocation_research_max_gross_exposure(capital_mode: object, value: object) -> float | None:
    if str(capital_mode or "unit_notional").strip() == "target_volatility":
        return _safe_float(value) or 1.0
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


def _format_capital_mode(value: object) -> str:
    normalized = str(value or "unit_notional").strip()
    labels = {
        "unit_notional": "Unit Notional",
        "fixed_gross": "Fixed Gross",
        "target_volatility": "Target Vol",
        "volatility_cap": "Vol Cap",
    }
    return labels.get(normalized, normalized.replace("_", " ").title())


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
    normalized = str(value or DEFAULT_MISSING_RETURN_POLICY).strip()
    if normalized == "complete_case_drop":
        return "Complete Case Drop"
    if normalized == "strict":
        return "Strict"
    return normalized.replace("_", " ").title()


def _allocation_research_outputs_root() -> Path:
    root = get_settings().allocation_research_outputs_root
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
    return path.resolve().relative_to(_allocation_research_outputs_root().resolve()).as_posix()


def _resolve_artifact_path(portfolio_id: str, path: str) -> Path:
    candidate = (_allocation_research_outputs_root() / path).resolve()
    root = _allocation_research_outputs_root().resolve()
    portfolio_root = (root / portfolio_id).resolve()
    if root not in candidate.parents and candidate != root:
        raise ValueError("Artifact path must stay within allocation_research outputs root.")
    if portfolio_root not in candidate.parents and candidate != portfolio_root:
        raise ValueError("Artifact path must stay within the selected portfolio allocation_research outputs.")
    if not candidate.exists() or not candidate.is_file():
        raise ValueError("Allocation Research artifact not found.")
    return candidate


def _next_allocation_research_run_id(portfolio_id: str) -> str:
    timestamp = _utc_now().strftime("%Y%m%d__%H%M%SZ")
    return f"{portfolio_id}__allocation_research__{timestamp}__{uuid4().hex[:8]}"


def _prune_portfolio_allocation_research_runs(session, portfolio_id: str, *, keep_run_id: str) -> None:
    rows = session.scalars(
        select(AllocationResearchRunRecordModel).where(
            AllocationResearchRunRecordModel.portfolio_id == portfolio_id,
            AllocationResearchRunRecordModel.allocation_research_run_id != keep_run_id,
        )
    ).all()
    for row in rows:
        session.delete(row)
    portfolio_output_root = _allocation_research_outputs_root() / portfolio_id
    if portfolio_output_root.exists():
        for child in portfolio_output_root.iterdir():
            if child.name == keep_run_id:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()


def _default_as_of_date(portfolio_id: str) -> date:
    """Resolve dynamic Allocation Research dates from the authoritative publication."""

    return read_current_published_holdings_as_of_date(portfolio_id)


def _allocation_research_as_of_mode(record: AllocationResearchSettingsRecordModel) -> str:
    mode = str(getattr(record, "as_of_mode", ALLOCATION_RESEARCH_AS_OF_MODE_DYNAMIC) or "").strip().lower()
    return mode if mode in {ALLOCATION_RESEARCH_AS_OF_MODE_DYNAMIC, ALLOCATION_RESEARCH_AS_OF_MODE_PINNED} else ALLOCATION_RESEARCH_AS_OF_MODE_DYNAMIC


def _effective_allocation_research_as_of_date(
    record: AllocationResearchSettingsRecordModel,
    *,
    default_as_of_date: date,
) -> date:
    if _allocation_research_as_of_mode(record) == ALLOCATION_RESEARCH_AS_OF_MODE_PINNED:
        if record.as_of_date is None:
            raise ValueError("Pinned Allocation Research settings require an as-of date.")
        return record.as_of_date
    return default_as_of_date


def _ensure_allocation_research_settings_record(
    session,
    portfolio_id: str,
    *,
    default_planning_taxonomy_id: str | None,
    default_as_of_date: date,
) -> AllocationResearchSettingsRecordModel:
    record = session.get(AllocationResearchSettingsRecordModel, portfolio_id)
    if record is not None:
        changed = False
        if _allocation_research_as_of_mode(record) == ALLOCATION_RESEARCH_AS_OF_MODE_DYNAMIC and record.as_of_date is not None:
            # Dynamic mode stores no resolved date.  The latest complete
            # portfolio date is resolved at read/run time instead.
            record.as_of_date = None
            changed = True
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
            record.missing_return_policy = DEFAULT_MISSING_RETURN_POLICY
            changed = True
        if record.frozen_taxonomy_node_ids_json is None:
            record.frozen_taxonomy_node_ids_json = []
            changed = True
        if record.top_sleeve_weight_bounds_json is None:
            record.top_sleeve_weight_bounds_json = []
            changed = True
        if not str(getattr(record, "policy_replay_rebalance_frequency", "") or "").strip():
            record.policy_replay_rebalance_frequency = "1m"
            changed = True
        if changed:
            record.updated_at = _utc_now_iso()
            session.commit()
        return record

    record = AllocationResearchSettingsRecordModel(
        portfolio_id=portfolio_id,
        planning_taxonomy_id=default_planning_taxonomy_id,
        comparator_taxonomy_node_id=None,
        as_of_mode=ALLOCATION_RESEARCH_AS_OF_MODE_DYNAMIC,
        as_of_date=None,
        lookback_days=90,
        calculation_frequency="auto",
        missing_return_policy=DEFAULT_MISSING_RETURN_POLICY,
        target_dimension="scope_default",
        capital_mode="unit_notional",
        gross_exposure=None,
        target_volatility=None,
        max_gross_exposure=None,
        frozen_taxonomy_node_ids_json=[],
        top_sleeve_weight_bounds_json=[],
        policy_replay_rebalance_frequency="1m",
        policy_replay_benchmark_instrument_id=None,
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
        raise ValueError("Allocation Research planning taxonomy must be planning-enabled.")
    if taxonomy.primary_assignment_scope != "instrument":
        raise ValueError("Allocation Research planning taxonomy must use instrument assignment scope.")
    return taxonomy


def _validate_allocation_research_scope(
    session,
    *,
    taxonomy: TaxonomyRecordModel | None,
    comparator_taxonomy_node_id: str | None,
) -> str | None:
    resolved_node_id = str(comparator_taxonomy_node_id or "").strip() or None
    if resolved_node_id is None:
        return None
    if taxonomy is None:
        raise ValueError("Allocation Research scope requires a selected planning taxonomy.")
    node = session.scalar(
        select(TaxonomyNodeRecordModel.taxonomy_node_id)
        .where(
            TaxonomyNodeRecordModel.taxonomy_id == taxonomy.taxonomy_id,
            TaxonomyNodeRecordModel.taxonomy_node_id == resolved_node_id,
        )
    )
    if node is None:
        raise ValueError("Allocation Research scope node not found in the selected planning taxonomy.")
    return resolved_node_id


def _normalize_top_sleeve_weight_bounds(
    bounds: list[dict[str, object]] | None,
) -> list[dict[str, object]] | None:
    if bounds is None:
        return None
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in bounds:
        node_id = str((item or {}).get("taxonomy_node_id") or "").strip()
        if not node_id:
            raise ValueError("Top sleeve weight bounds require taxonomy_node_id.")
        if node_id in seen:
            raise ValueError("Top sleeve weight bounds must not contain duplicate taxonomy nodes.")
        seen.add(node_id)
        min_weight = _safe_float((item or {}).get("min_weight"))
        max_weight = _safe_float((item or {}).get("max_weight"))
        if min_weight is None and max_weight is None:
            raise ValueError("Top sleeve weight bound must set min_weight or max_weight.")
        if min_weight is not None and not 0.0 <= min_weight <= 1.0:
            raise ValueError("Top sleeve min_weight must be between 0 and 1.")
        if max_weight is not None and not 0.0 <= max_weight <= 1.0:
            raise ValueError("Top sleeve max_weight must be between 0 and 1.")
        if min_weight is not None and max_weight is not None and min_weight > max_weight:
            raise ValueError("Top sleeve min_weight cannot exceed max_weight.")
        normalized.append(
            {
                "taxonomy_node_id": node_id,
                "min_weight": min_weight,
                "max_weight": max_weight,
            }
        )
    return normalized


def _validate_top_sleeve_weight_bounds(
    portfolio_id: str,
    *,
    taxonomy: TaxonomyRecordModel | None,
    bounds: list[dict[str, object]] | None,
) -> list[dict[str, object]] | None:
    normalized = _normalize_top_sleeve_weight_bounds(bounds)
    if normalized is None:
        return None
    if not normalized:
        return []
    if taxonomy is None:
        raise ValueError("Top sleeve weight bounds require a selected planning taxonomy.")
    top_node_ids = {
        str(item.get("taxonomy_node_id") or "")
        for item in list_taxonomy_nodes(portfolio_id)
        if str(item.get("taxonomy_id") or "") == taxonomy.taxonomy_id
        and str(item.get("status") or "active") == "active"
        and not str(item.get("parent_taxonomy_node_id") or "").strip()
    }
    unknown = [item["taxonomy_node_id"] for item in normalized if str(item["taxonomy_node_id"]) not in top_node_ids]
    if unknown:
        raise ValueError("Top sleeve weight bounds must reference top-level nodes in the selected planning taxonomy.")
    return normalized


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
    row: AllocationResearchSettingsRecordModel,
    taxonomy_name_map: dict[str, str],
    scope_name_map: dict[str, str],
    *,
    default_as_of_date: date,
) -> dict[str, object]:
    planning_taxonomy_id = str(row.planning_taxonomy_id or "").strip() or None
    comparator_taxonomy_node_id = str(row.comparator_taxonomy_node_id or "").strip() or None
    as_of_mode = _allocation_research_as_of_mode(row)
    effective_as_of_date = _effective_allocation_research_as_of_date(
        row,
        default_as_of_date=default_as_of_date,
    )
    return {
        "portfolio_id": row.portfolio_id,
        "planning_taxonomy_id": planning_taxonomy_id,
        "planning_taxonomy_name": taxonomy_name_map.get(planning_taxonomy_id) if planning_taxonomy_id else None,
        "comparator_taxonomy_node_id": comparator_taxonomy_node_id,
        "comparator_taxonomy_node_name": (
            scope_name_map.get(comparator_taxonomy_node_id) if comparator_taxonomy_node_id else None
        ),
        "as_of_mode": as_of_mode,
        "as_of_date": _iso_date(effective_as_of_date),
        "pinned_as_of_date": (
            _iso_date(row.as_of_date)
            if as_of_mode == ALLOCATION_RESEARCH_AS_OF_MODE_PINNED
            else None
        ),
        "lookback_days": int(row.lookback_days or 90),
        "calculation_frequency": row.calculation_frequency or "auto",
        "missing_return_policy": row.missing_return_policy or DEFAULT_MISSING_RETURN_POLICY,
        "target_dimension": row.target_dimension or "scope_default",
        "capital_mode": row.capital_mode or "unit_notional",
        "gross_exposure": _safe_float(row.gross_exposure),
        "target_volatility": _safe_float(row.target_volatility),
        "max_gross_exposure": _normalize_allocation_research_max_gross_exposure(row.capital_mode, row.max_gross_exposure),
        "frozen_taxonomy_node_ids": deepcopy(row.frozen_taxonomy_node_ids_json or []),
        "top_sleeve_weight_bounds": deepcopy(row.top_sleeve_weight_bounds_json or []),
        "policy_replay_rebalance_frequency": str(row.policy_replay_rebalance_frequency or "1m").strip() or "1m",
        "policy_replay_benchmark_instrument_id": str(row.policy_replay_benchmark_instrument_id or "").strip() or None,
        "notes": row.notes,
        "updated_at": row.updated_at,
    }


def _date_value(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _canonical_reliability_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _canonical_json_value(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_reliability_fingerprint(value: object) -> str:
    digest = hashlib.sha256(
        _canonical_json_value(value).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _is_sha256_fingerprint(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    return (
        len(normalized) == 71
        and normalized.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in normalized[7:])
    )


def _canonical_decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if not value.is_finite():
        raise ValueError("Allocation Research input decimals must be finite.")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _published_holdings_input(
    statement: PublishedHoldingsStatement,
) -> dict[str, object]:
    """Build the exact, order-independent holdings input contract for a run."""

    content = {
        "portfolio_id": statement.portfolio_id,
        "base_currency": statement.base_currency,
        "valuation_timezone": statement.valuation_timezone,
        "as_of_date": statement.as_of_date.isoformat(),
        "positions": sorted(
            [
                {
                    "instrument_id": item.instrument_id,
                    "instrument_name": item.instrument_name,
                    "instrument_type": item.instrument_type,
                    "currency": item.currency,
                    "quantity_exact": _canonical_decimal_text(item.quantity_exact),
                    "adopted_price_exact": _canonical_decimal_text(
                        item.adopted_price_exact
                    ),
                    "market_value_local_exact": _canonical_decimal_text(
                        item.market_value_local_exact
                    ),
                    "market_value_base_exact": _canonical_decimal_text(
                        item.market_value_base_exact
                    ),
                    "cost_basis_local_exact": _canonical_decimal_text(
                        item.cost_basis_local_exact
                    ),
                    "cost_basis_base_exact": _canonical_decimal_text(
                        item.cost_basis_base_exact
                    ),
                    "portfolio_weight": _canonical_decimal_text(
                        item.portfolio_weight
                    ),
                    "account_ids": sorted(item.account_ids),
                    "valuation_coverage_state": item.valuation_coverage_state,
                    "valuation_reason_codes": sorted(
                        item.valuation_reason_codes
                    ),
                }
                for item in statement.positions
            ],
            key=lambda item: str(item["instrument_id"]),
        ),
        "cash_accounts": sorted(
            [
                {
                    "account_id": item.account_id,
                    "account_name": item.account_name,
                    "account_type": item.account_type,
                    "currency": item.currency,
                    "value_base_exact": _canonical_decimal_text(
                        item.value_base_exact
                    ),
                    "coverage_state": item.coverage_state,
                    "reason_codes": sorted(item.reason_codes),
                }
                for item in statement.cash_accounts
            ],
            key=lambda item: str(item["account_id"]),
        ),
        "position_market_value_base_exact": _canonical_decimal_text(
            statement.position_market_value_base_exact
        ),
        "settled_cash_base_exact": _canonical_decimal_text(
            statement.settled_cash_base_exact
        ),
        "pending_settlement_base_exact": _canonical_decimal_text(
            statement.pending_settlement_base_exact
        ),
        "total_nav_base": _canonical_decimal_text(statement.total_nav_base),
        "nav_coverage_state": statement.nav_coverage_state,
        "nav_reason_codes": sorted(statement.nav_reason_codes),
    }
    return {
        "schema_version": ALLOCATION_RESEARCH_PUBLISHED_HOLDINGS_INPUT_SCHEMA_VERSION,
        "content_fingerprint": _canonical_reliability_fingerprint(content),
        "publication_lineage": {
            "publication_id": str(statement.publication_id),
            "run_id": str(statement.run_id),
            "manifest_id": str(statement.manifest_id),
            "captured_generation": int(statement.captured_generation),
        },
    }


def _market_data_input(
    dependency_manifests: dict[str, object],
) -> dict[str, object]:
    if not dependency_manifests:
        raise ValueError(
            "Completed Allocation Research requires canonical market-data dependency manifests."
        )
    rendered_manifests: list[dict[str, object]] = []
    for consumer, dependencies in sorted(dependency_manifests.items()):
        normalized_consumer = str(consumer or "").strip()
        required_list_fields = (
            "instrument_ids",
            "quote_dependencies",
            "fx_dependencies",
        )
        if (
            not normalized_consumer
            or not isinstance(dependencies, dict)
            or not str(dependencies.get("policy_version") or "").strip()
            or not str(dependencies.get("base_currency") or "").strip()
            or _date_value(dependencies.get("start_date")) is None
            or _date_value(dependencies.get("end_date")) is None
            or any(
                not isinstance(dependencies.get(field_name), list)
                for field_name in required_list_fields
            )
        ):
            raise ValueError(
                "Completed Allocation Research produced an invalid market-data dependency manifest."
            )
        canonical_dependencies = json.loads(
            _canonical_json_value(dependencies)
        )
        rendered_manifests.append(
            {
                "consumer": normalized_consumer,
                "fingerprint": _canonical_reliability_fingerprint(
                    canonical_dependencies
                ),
                "dependencies": canonical_dependencies,
            }
        )
    fingerprint_payload = {
        "schema_version": ALLOCATION_RESEARCH_MARKET_DATA_INPUT_SCHEMA_VERSION,
        "dependency_manifests": rendered_manifests,
    }
    return {
        **fingerprint_payload,
        "fingerprint": _canonical_reliability_fingerprint(
            fingerprint_payload
        ),
    }


def _planning_state_fingerprint(
    session,
    *,
    portfolio_id: str,
    planning_taxonomy_id: str | None,
) -> str | None:
    taxonomy_id = str(planning_taxonomy_id or "").strip()
    if not taxonomy_id:
        return None

    taxonomy = session.scalar(
        select(TaxonomyRecordModel).where(
            TaxonomyRecordModel.portfolio_id == portfolio_id,
            TaxonomyRecordModel.taxonomy_id == taxonomy_id,
        )
    )
    if taxonomy is None:
        return None

    nodes = session.scalars(
        select(TaxonomyNodeRecordModel).where(TaxonomyNodeRecordModel.taxonomy_id == taxonomy_id)
    ).all()
    assignments = session.scalars(
        select(TaxonomyAssignmentRecordModel).where(
            TaxonomyAssignmentRecordModel.taxonomy_id == taxonomy_id,
            TaxonomyAssignmentRecordModel.status == "active",
        )
    ).all()
    target_sets = session.scalars(
        select(TargetSetRecordModel).where(
            TargetSetRecordModel.taxonomy_id == taxonomy_id,
            TargetSetRecordModel.status == "active",
        )
    ).all()
    active_target_set_ids = [item.target_set_id for item in target_sets]
    target_lines = (
        session.scalars(
            select(TargetSetLineRecordModel).where(
                TargetSetLineRecordModel.target_set_id.in_(active_target_set_ids)
            )
        ).all()
        if active_target_set_ids
        else []
    )
    lines_by_target_set_id: dict[str, list[dict[str, object]]] = {}
    for item in target_lines:
        lines_by_target_set_id.setdefault(item.target_set_id, []).append(
            {
                "target_member_type": item.target_member_type,
                "target_member_id": item.target_member_id,
                "taxonomy_node_id": item.taxonomy_node_id,
                "target_weight": _safe_float(item.target_weight),
                "target_risk_share": _safe_float(item.target_risk_share),
            }
        )
    for lines in lines_by_target_set_id.values():
        lines.sort(
            key=lambda item: (
                str(item.get("target_member_type") or ""),
                str(item.get("target_member_id") or ""),
            )
        )

    state = {
        "schema_version": ALLOCATION_RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION,
        "taxonomy": {
            "taxonomy_id": taxonomy.taxonomy_id,
            "name": taxonomy.name,
            "taxonomy_type": taxonomy.taxonomy_type,
            "primary_assignment_scope": taxonomy.primary_assignment_scope,
            "planning_enabled": bool(taxonomy.planning_enabled),
            "budgeting_level": taxonomy.budgeting_level,
            "root_default_target_dimension": taxonomy.root_default_target_dimension,
            "status": taxonomy.status,
        },
        "nodes": sorted(
            [
                {
                    "taxonomy_node_id": item.taxonomy_node_id,
                    "parent_taxonomy_node_id": item.parent_taxonomy_node_id,
                    "node_name": item.node_name,
                    "node_code": item.node_code,
                    "sort_order": item.sort_order,
                    "is_terminal": bool(item.is_terminal),
                    "default_target_dimension": item.default_target_dimension,
                    "status": item.status,
                }
                for item in nodes
            ],
            key=lambda item: str(item["taxonomy_node_id"]),
        ),
        "active_assignments": sorted(
            [
                {
                    "target_scope": item.target_scope,
                    "target_entity_id": item.target_entity_id,
                    "taxonomy_node_id": item.taxonomy_node_id,
                }
                for item in assignments
            ],
            key=lambda item: (
                str(item["target_scope"]),
                str(item["target_entity_id"]),
                str(item["taxonomy_node_id"]),
            ),
        ),
        "active_target_sets": sorted(
            [
                {
                    "target_set_id": item.target_set_id,
                    "comparator_taxonomy_node_id": item.comparator_taxonomy_node_id,
                    "target_set_type": item.target_set_type,
                    "name": item.name,
                    "weight_enabled": bool(item.weight_enabled),
                    "risk_budget_enabled": bool(item.risk_budget_enabled),
                    "lines": lines_by_target_set_id.get(item.target_set_id, []),
                }
                for item in target_sets
            ],
            key=lambda item: (
                str(item["comparator_taxonomy_node_id"] or ""),
                str(item["target_set_type"]),
                str(item["target_set_id"]),
            ),
        ),
    }
    digest = hashlib.sha256(_canonical_reliability_value(state).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _allocation_research_run_reliability(
    row: AllocationResearchRunRecordModel,
    *,
    effective_allocation_research_as_of_date: date,
    settings_payload: dict[str, object],
    production_risk_model: dict[str, object],
    current_planning_state_fingerprint: str | None,
    current_published_holdings_input: dict[str, object],
) -> tuple[str, list[str]]:
    if row.status != "completed":
        return "not_completed", [f"Run status is {row.status}; no current solved result is available."]

    reasons: list[str] = []
    run_as_of_date = _date_value(row.as_of_date)
    if run_as_of_date is None:
        reasons.append("Run does not record an analysis date.")
    elif run_as_of_date != effective_allocation_research_as_of_date:
        reasons.append(
            f"Run analysis date {run_as_of_date.isoformat()} does not match the current Allocation Research date "
            f"{effective_allocation_research_as_of_date.isoformat()}."
        )

    request_payload = row.request_payload_json if isinstance(row.request_payload_json, dict) else {}
    run_planning_state_fingerprint = str(request_payload.get("planning_state_fingerprint") or "").strip()
    if not run_planning_state_fingerprint:
        reasons.append(
            "Run predates planning-state fingerprinting; rerun Allocation Research before treating it as current."
        )
    elif current_planning_state_fingerprint is None:
        reasons.append("The current planning taxonomy state is unavailable; rerun after repairing Allocation Research settings.")
    elif run_planning_state_fingerprint != current_planning_state_fingerprint:
        reasons.append(
            "Planning taxonomy structure, active assignments, or active SAA/TAA target configuration changed "
            "after this run was created."
        )

    if request_payload:
        expected_payload = {
            "planning_taxonomy_id": settings_payload.get("planning_taxonomy_id"),
            "comparator_taxonomy_node_id": settings_payload.get("comparator_taxonomy_node_id"),
            "as_of_mode": settings_payload.get("as_of_mode"),
            "as_of_date": settings_payload.get("as_of_date"),
            "lookback_days": int(production_risk_model.get("lookback_days") or settings_payload.get("lookback_days") or 90),
            "calculation_frequency": str(
                production_risk_model.get("calculation_frequency")
                or settings_payload.get("calculation_frequency")
                or "auto"
            ),
            "missing_return_policy": str(
                production_risk_model.get("missing_return_policy")
                or settings_payload.get("missing_return_policy")
                or DEFAULT_MISSING_RETURN_POLICY
            ),
            "target_dimension": settings_payload.get("target_dimension"),
            "capital_mode": settings_payload.get("capital_mode"),
            "gross_exposure": settings_payload.get("gross_exposure"),
            "target_volatility": settings_payload.get("target_volatility"),
            "max_gross_exposure": settings_payload.get("max_gross_exposure"),
            "frozen_taxonomy_node_ids": settings_payload.get("frozen_taxonomy_node_ids") or [],
            "top_sleeve_weight_bounds": settings_payload.get("top_sleeve_weight_bounds") or [],
            "policy_replay_rebalance_frequency": settings_payload.get("policy_replay_rebalance_frequency"),
            "policy_replay_benchmark_instrument_id": settings_payload.get("policy_replay_benchmark_instrument_id"),
        }
        material_keys = list(expected_payload)
        request_material = {key: request_payload.get(key) for key in material_keys}
        if _canonical_reliability_value(request_material) != _canonical_reliability_value(expected_payload):
            reasons.append("Allocation Research settings or the production risk policy changed after this run was created.")

        run_risk_model = request_payload.get("risk_model") if isinstance(request_payload.get("risk_model"), dict) else {}
        risk_keys = ("covariance_model_id", "lookback_days", "calculation_frequency", "missing_return_policy", "contribution_mode")
        expected_risk = {key: production_risk_model.get(key) for key in risk_keys}
        actual_risk = {key: run_risk_model.get(key) for key in risk_keys}
        if run_risk_model and _canonical_reliability_value(actual_risk) != _canonical_reliability_value(expected_risk):
            reasons.append("The production covariance/risk-contribution model changed after this run was created.")

    run_holdings_input = request_payload.get("published_holdings_input")
    run_holdings_lineage: dict[str, object] | None = None
    if isinstance(run_holdings_input, dict):
        candidate_lineage = run_holdings_input.get("publication_lineage")
        if isinstance(candidate_lineage, dict):
            run_holdings_lineage = candidate_lineage
    required_lineage_fields = (
        "publication_id",
        "run_id",
        "manifest_id",
        "captured_generation",
    )
    if (
        not isinstance(run_holdings_input, dict)
        or run_holdings_input.get("schema_version")
        != ALLOCATION_RESEARCH_PUBLISHED_HOLDINGS_INPUT_SCHEMA_VERSION
        or not _is_sha256_fingerprint(
            run_holdings_input.get("content_fingerprint")
        )
        or run_holdings_lineage is None
        or any(
            run_holdings_lineage.get(field_name) is None
            or run_holdings_lineage.get(field_name) == ""
            for field_name in required_lineage_fields
        )
    ):
        reasons.append(
            "Run does not contain the required published-holdings content fingerprint and publication lineage."
        )
    else:
        if (
            run_holdings_input.get("content_fingerprint")
            != current_published_holdings_input.get("content_fingerprint")
        ):
            reasons.append(
                "Published holdings at the Allocation Research analysis date changed after this run was created."
            )
        # Dynamic runs follow the authoritative current publication, so even a
        # same-date republish invalidates the run.  A pinned run intentionally
        # ignores publication-only churn when its exact historical holdings
        # content is unchanged.
        if str(settings_payload.get("as_of_mode") or "") == ALLOCATION_RESEARCH_AS_OF_MODE_DYNAMIC:
            current_lineage = current_published_holdings_input.get(
                "publication_lineage"
            )
            if _canonical_reliability_value(
                run_holdings_lineage
            ) != _canonical_reliability_value(current_lineage):
                reasons.append(
                    "The authoritative published-holdings publication lineage changed after this run was created."
                )

    run_market_input = request_payload.get("market_data_input")
    dependency_manifests = (
        run_market_input.get("dependency_manifests")
        if isinstance(run_market_input, dict)
        else None
    )
    if (
        not isinstance(run_market_input, dict)
        or run_market_input.get("schema_version")
        != ALLOCATION_RESEARCH_MARKET_DATA_INPUT_SCHEMA_VERSION
        or not isinstance(dependency_manifests, list)
        or not dependency_manifests
        or not _is_sha256_fingerprint(run_market_input.get("fingerprint"))
    ):
        reasons.append(
            "Run does not contain the required canonical market-data dependency manifest."
        )
    else:
        market_fingerprint_payload = {
            "schema_version": run_market_input.get("schema_version"),
            "dependency_manifests": dependency_manifests,
        }
        try:
            market_fingerprint_matches = run_market_input.get(
                "fingerprint"
            ) == _canonical_reliability_fingerprint(
                market_fingerprint_payload
            )
        except (TypeError, ValueError):
            market_fingerprint_matches = False
        if not market_fingerprint_matches:
            reasons.append(
                "Run's persisted market-data dependency manifest failed its canonical fingerprint check."
            )
        else:
            market_reason_messages = {
                "market_data_dependency_contract_invalid": (
                    "Run's canonical market-data dependency manifest is incomplete or invalid."
                ),
                "market_data_dependency_unavailable": (
                    "Current canonical market-data dependencies cannot be reconstructed for this run."
                ),
                "market_data_policy_changed": (
                    "The canonical Allocation Research market-data policy changed after this run was created."
                ),
                "quote_dependency_changed": (
                    "Canonical quote/NAV inputs used by this run were revised."
                ),
                "fx_dependency_changed": (
                    "Canonical FX inputs used by this run were revised."
                ),
            }
            seen_consumers: set[str] = set()
            manifest_contract_invalid = False
            for item in dependency_manifests:
                if not isinstance(item, dict):
                    manifest_contract_invalid = True
                    continue
                consumer = str(item.get("consumer") or "").strip()
                dependencies = item.get("dependencies")
                manifest_fingerprint = str(
                    item.get("fingerprint") or ""
                ).strip()
                try:
                    manifest_fingerprint_matches = (
                        isinstance(dependencies, dict)
                        and manifest_fingerprint
                        == _canonical_reliability_fingerprint(dependencies)
                    )
                except (TypeError, ValueError):
                    manifest_fingerprint_matches = False
                if (
                    not consumer
                    or consumer in seen_consumers
                    or not isinstance(dependencies, dict)
                    or not manifest_fingerprint_matches
                ):
                    manifest_contract_invalid = True
                    continue
                seen_consumers.add(consumer)
                for reason_code in validate_portfolio_market_data_dependencies_current(
                    dependencies
                ):
                    reasons.append(
                        market_reason_messages.get(
                            reason_code,
                            "A canonical market-data dependency used by this run is no longer current.",
                        )
                    )
            if seen_consumers != {
                "current_target_solve",
                "policy_replay",
            }:
                manifest_contract_invalid = True
            if manifest_contract_invalid:
                reasons.append(
                    market_reason_messages[
                        "market_data_dependency_contract_invalid"
                    ]
                )

    return ("stale", list(dict.fromkeys(reasons))) if reasons else ("current", [])


def _serialize_run_row(
    row: AllocationResearchRunRecordModel,
    taxonomy_name_map: dict[str, str],
    *,
    include_detail: bool = True,
    reliability_state: str = "unassessed",
    reliability_reasons: list[str] | None = None,
) -> dict[str, object]:
    planning_taxonomy_id = str(row.planning_taxonomy_id or "").strip() or None
    detail = None
    if include_detail:
        detail = deepcopy(row.detail_json or {})
        if isinstance(detail.get("top_holdings"), list):
            detail["top_holdings"] = [
                _normalize_top_holding_snapshot(item)
                for item in detail["top_holdings"]
                if isinstance(item, dict)
            ]
    artifacts = deepcopy(row.artifacts_json or [])
    return {
        "allocation_research_run_id": row.allocation_research_run_id,
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
        "reliability_state": reliability_state,
        "is_current": reliability_state == "current",
        "reliability_reasons": list(reliability_reasons or []),
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
        "base_currency": require_portfolio_fact_currency(
            item.get("base_currency"),
            context=f"Allocation Research top holding '{instrument_id}' base",
        ),
        "price": _safe_float(item.get("price")),
    }


def _build_top_holdings_snapshot(
    statement_positions: tuple[PublishedHoldingPosition, ...],
    *,
    base_currency: str,
) -> list[dict[str, object]]:
    sorted_positions = sorted(
        statement_positions,
        key=lambda item: _safe_float(item.portfolio_weight) or 0.0,
        reverse=True,
    )
    rendered: list[dict[str, object]] = []
    for position in sorted_positions[:8]:
        rendered.append(
            _normalize_top_holding_snapshot(
                {
                    "instrument_id": position.instrument_id,
                    "instrument_name": position.instrument_name,
                    "instrument_type": position.instrument_type,
                    "allocation": _safe_float(position.portfolio_weight),
                    "market_value_base": _safe_float(
                        position.market_value_base_exact
                    ),
                    "cost_basis_base": _safe_float(
                        position.cost_basis_base_exact
                    ),
                    "base_currency": base_currency,
                    "price": _safe_float(position.adopted_price_exact),
                }
            )
        )
    return rendered


def _build_planning_group_snapshot(
    statement_positions: tuple[PublishedHoldingPosition, ...],
    *,
    cash_accounts: tuple[PublishedCashAccountValue, ...],
    pending_settlement_base_exact: Decimal | None,
    portfolio_id: str,
    planning_taxonomy_id: str | None,
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

    if any(
        _safe_float(position.market_value_base_exact) is None
        for position in statement_positions
    ):
        return []
    if any(
        _safe_float(account.value_base_exact) is None
        for account in cash_accounts
    ):
        return []
    pending_settlement_base = _safe_float(pending_settlement_base_exact)
    if pending_settlement_base is None:
        return []

    visible_cash_accounts = [
        account
        for account in cash_accounts
        if (
            abs(_safe_float(account.value_base_exact) or 0.0) > 1e-9
            or ("cash_bucket", account.account_id) in assignment_by_entity
        )
    ]

    total_entity_value_base = sum(
        float(position.market_value_base_exact)
        for position in statement_positions
        if position.market_value_base_exact is not None
    )
    total_entity_value_base += sum(
        _safe_float(account.value_base_exact) or 0.0
        for account in visible_cash_accounts
    )
    total_entity_value_base += pending_settlement_base

    buckets: dict[str, dict[str, object]] = {}
    for position in statement_positions:
        instrument_id = position.instrument_id
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
        assert position.market_value_base_exact is not None
        market_value_base = float(position.market_value_base_exact)
        cost_basis_base = _safe_float(position.cost_basis_base_exact) or 0.0
        bucket["end_value_base"] = float(bucket["end_value_base"] or 0.0) + market_value_base
        bucket["total_pnl"] = float(bucket["total_pnl"] or 0.0) + (market_value_base - cost_basis_base)
        bucket["position_count"] = int(bucket["position_count"] or 0) + 1

    for account in visible_cash_accounts:
        account_id = account.account_id
        assignment = assignment_by_entity.get(("cash_bucket", account_id))
        if assignment is None:
            group_key = SYSTEM_CASH_TARGET_MEMBER_ID
            group_label = SYSTEM_CASH_TARGET_LABEL
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
        cash_value_base = _safe_float(account.value_base_exact) or 0.0
        bucket["end_value_base"] = float(bucket["end_value_base"] or 0.0) + cash_value_base
        bucket["position_count"] = int(bucket["position_count"] or 0) + 1

    if abs(pending_settlement_base) > 1e-9:
        pending_bucket = buckets.setdefault(
            SYSTEM_CASH_TARGET_MEMBER_ID,
            {
                "group_key": SYSTEM_CASH_TARGET_MEMBER_ID,
                "group_label": SYSTEM_CASH_TARGET_LABEL,
                "taxonomy_node_id": None,
                "start_value_base": None,
                "end_value_base": 0.0,
                "net_flow": None,
                "total_pnl": 0.0,
                "contribution": None,
                "allocation": None,
                "position_count": 0,
            },
        )
        pending_bucket["end_value_base"] = (
            float(pending_bucket["end_value_base"] or 0.0)
            + pending_settlement_base
        )

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


def _build_allocation_research_context(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str | None,
    as_of_date: date,
    lookback_days: int,
) -> dict[str, object]:
    portfolio = _get_allocation_research_portfolio(portfolio_id)
    if portfolio is None:
        raise ValueError("Portfolio not found.")

    statement = read_current_published_holdings(
        portfolio_id,
        as_of_date=as_of_date,
    )
    portfolio_base_currency = require_portfolio_fact_currency(
        portfolio.get("base_currency"),
        context=f"Portfolio '{portfolio_id}' base",
    )
    statement_base_currency = require_portfolio_fact_currency(
        statement.base_currency,
        context=f"Allocation Research holdings statement '{portfolio_id}/{as_of_date.isoformat()}' base",
    )
    if statement_base_currency != portfolio_base_currency:
        raise ValueError(
            f"Allocation Research holdings statement '{portfolio_id}/{as_of_date.isoformat()}' base "
            "currency does not match the portfolio fact."
        )
    lookback_start = risk_window_start_date(as_of_date, lookback_days)
    statement_positions = statement.positions
    top_holdings = _build_top_holdings_snapshot(
        statement_positions,
        base_currency=statement_base_currency,
    )
    planning_groups = _build_planning_group_snapshot(
        statement_positions,
        cash_accounts=statement.cash_accounts,
        pending_settlement_base_exact=statement.pending_settlement_base_exact,
        portfolio_id=portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
    )
    planning_target_summary = _build_planning_target_summary(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        as_of_date=as_of_date,
    )
    quality_warnings: list[str] = []
    unassigned_group = next(
        (item for item in planning_groups if str(item.get("group_key") or "") == "unassigned"),
        None,
    )
    if unassigned_group is not None and abs(_safe_float(unassigned_group.get("end_value_base")) or 0.0) > 1e-9:
        quality_warnings.append(
            "Allocation Research target solve is unavailable until all non-cash holdings are assigned to the selected planning taxonomy "
            f"({int(unassigned_group.get('position_count') or 0)} unassigned holding(s))."
        )
    resolved_nav_base = _safe_float(statement.total_nav_base)

    return {
        "_published_holdings_input": _published_holdings_input(statement),
        "portfolio_id": portfolio_id,
        "portfolio_name": str(portfolio.get("portfolio_name") or portfolio_id),
        "base_currency": statement_base_currency,
        "as_of_date": as_of_date.isoformat(),
        "lookback_start": lookback_start.isoformat(),
        "lookback_end": as_of_date.isoformat(),
        "nav": resolved_nav_base,
        "holdings_count": len(statement_positions),
        "planning_group_count": len(planning_groups),
        "summary": {
            "period_return": None,
            "annualized_volatility": None,
            "current_drawdown": None,
            "max_drawdown": None,
            "start_nav": None,
            "end_nav": resolved_nav_base,
        },
        "planning_target_summary": planning_target_summary,
        "quality_warnings": quality_warnings,
        "top_holdings": top_holdings,
        "planning_groups": planning_groups,
    }

def _build_current_target_findings(
    solution: dict[str, object],
    *,
    settings_payload: dict[str, object],
) -> tuple[list[dict[str, str]], list[str]]:
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
        capital_mode = str(settings_payload.get("capital_mode") or "unit_notional")
        volatility_label = "volatility cap" if capital_mode == "volatility_cap" else "target volatility"
        findings.append(
            {
                "title": "Volatility Overlay",
                "detail": (
                    f"Estimated risky-sleeve volatility is {_format_pct(_safe_float(solve_event.get('estimated_risk_sleeve_volatility')))} "
                    f"versus {volatility_label} {_format_pct(_safe_float(solve_event.get('target_volatility')))}; gross exposure resolves to "
                    f"{_format_number(_safe_float(solve_event.get('gross_exposure')), 3)}."
                ),
            }
        )

    if target_weight_gaps:
        top_gap = max(target_weight_gaps, key=lambda item: abs(_safe_float(item.get("gap")) or 0.0))
        if abs(_safe_float(top_gap.get("gap")) or 0.0) > 0.01:
            if str(top_gap.get("execution_status") or "") == "manual_review_required":
                questions.append(
                    str(top_gap.get("execution_note") or "").strip()
                    or f"Review the 0% target for {top_gap.get('label')} before creating any liquidation instruction."
                )
            else:
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
            "value": _format_capital_mode(settings_payload.get("capital_mode")),
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
            "label": "Risk Window",
            "value": risk_window_label(int(settings_payload.get("lookback_days") or 90)),
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
            "label": "Vol Target/Cap",
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
                "execution_status": str((target_gap or {}).get("execution_status") or "ready"),
                "execution_note": str((target_gap or {}).get("execution_note") or "").strip() or None,
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
    missing_return_policy = str((solve_event or {}).get("missing_return_policy") or settings_payload.get("missing_return_policy") or DEFAULT_MISSING_RETURN_POLICY)
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
        covariance_model = str((solve_event or {}).get("covariance_model") or "allocation_research covariance").replace("_", " ")
        contribution_mode = str((solve_event or {}).get("risk_contribution_mode") or "risk").upper()
        assumptions.append(
            f"Risk-budget sleeves solve current implementation weights from the trailing local {covariance_model} window using {contribution_mode} risk contributions; "
            "leaf implementation weights can roll up through the sleeve tree, but risk targets remain local and are not multiplied by ancestor risk targets."
        )
        assumptions.append(
            "Look-through forward RC is recomputed from the solved leaf weights under the portfolio-level leaf covariance. "
            "Because correlation shrinkage is re-estimated at each hierarchy level, look-through RC can differ from a parent sleeve's locally achieved risk-budget share."
        )
    if str(settings_payload.get("target_dimension") or "") != "scope_default":
        assumptions.append("The selected scope can use an explicit target-dimension override; child sleeves still use their own configured default target dimension.")
    if str(settings_payload.get("capital_mode") or "unit_notional") == "target_volatility":
        assumptions.append("After recursive sleeve targets are resolved, Allocation Research estimates risky-sleeve volatility, scales gross exposure toward target volatility, and sends the residual into cash.")
    elif str(settings_payload.get("capital_mode") or "unit_notional") == "volatility_cap":
        assumptions.append("After recursive sleeve targets are resolved, Allocation Research estimates risky-sleeve volatility and only scales risky exposure down when it exceeds the volatility cap.")
    elif str(settings_payload.get("capital_mode") or "unit_notional") == "fixed_gross":
        assumptions.append("After recursive sleeve targets are resolved, Allocation Research applies a fixed gross-exposure overlay and leaves the residual in cash.")
    if any(str(item.get("source_label_override") or "") == "Single Member" for item in target_rows):
        assumptions.append("Single-member sleeves resolve to 100% of that member; multi-member scopes require an active complete SAA/TAA target set.")
    assumptions.extend(
        [
            "Policy Replay applies the currently configured taxonomy membership and target policy across its full history; it is a policy simulation, not a point-in-time reconstruction of past classifications or mandates.",
            "PolicyReplay cash residual earns 0%, and reported simulated returns are gross of transaction costs, taxes, slippage, and implementation delay.",
        ]
    )
    return list(dict.fromkeys(assumptions))


def _build_current_target_detail(
    context: dict[str, object],
    *,
    planning_taxonomy_name: str | None,
    settings_payload: dict[str, object],
    solution: dict[str, object],
) -> dict[str, object]:
    findings, questions = _build_current_target_findings(solution, settings_payload=settings_payload)
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
        "solved_result_groups": deepcopy(solution.get("solved_result_groups") or []),
        "solve_event": deepcopy(solve_event),
        "scope_solve_events": deepcopy(solution.get("scope_solve_events") or []),
        "target_weight_gaps": deepcopy(solution.get("target_weight_gaps") or []),
        "policy_replay": deepcopy(solution.get("policy_replay")),
        "policy_replay_benchmark": deepcopy(solution.get("policy_replay_benchmark")),
        "policy_replay_relative_metrics": deepcopy(solution.get("policy_replay_relative_metrics")),
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
    allocation_research_run_id: str,
    *,
    settings_payload: dict[str, object],
    context: dict[str, object],
    detail: dict[str, object],
) -> list[dict[str, object]]:
    run_root = _allocation_research_outputs_root() / portfolio_id / allocation_research_run_id
    run_root.mkdir(parents=True, exist_ok=True)

    report_path = run_root / "report.md"
    summary_path = run_root / "summary.json"
    settings_path = run_root / "request.json"
    holdings_path = run_root / "top_holdings.csv"
    groups_path = run_root / "planning_groups.csv"
    target_weights_path = run_root / "target_weights.csv"
    member_targets_path = run_root / "member_targets.csv"
    leaf_targets_path = run_root / "leaf_targets.csv"
    solve_event_path = run_root / "solve_event.csv"
    scope_solve_events_path = run_root / "scope_solve_events.csv"
    target_weight_gaps_path = run_root / "target_weight_gaps.csv"

    report_warnings = [str(item) for item in list(detail.get("warnings") or []) if str(item).strip()]
    policy_replay = detail.get("policy_replay") if isinstance(detail.get("policy_replay"), dict) else {}
    report_warnings.extend(
        str(item)
        for item in list((policy_replay or {}).get("warnings") or [])
        if str(item).strip()
    )
    report_warnings = list(dict.fromkeys(report_warnings))

    report_path.write_text(
        "\n".join(
            [
                f"# Allocation Research Run `{allocation_research_run_id}`",
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
                "## Assumptions and Limitations",
                *[
                    f"- {item}"
                    for item in (detail.get("target_assumptions") or [])
                ],
                "",
                "## Warnings",
                *([f"- {item}" for item in report_warnings] or ["- None."]),
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


def get_allocation_research_workbench(
    portfolio_id: str,
    *,
    selected_run_id: str | None = None,
) -> dict[str, object] | None:
    portfolio = _get_allocation_research_portfolio(portfolio_id)
    if portfolio is None:
        return None

    latest_portfolio_as_of_date = _default_as_of_date(portfolio_id)
    taxonomy_name_map = _taxonomy_name_map(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        record = _ensure_allocation_research_settings_record(
            session,
            portfolio_id,
            default_planning_taxonomy_id=str(portfolio.get("default_planning_taxonomy_id") or "").strip() or None,
            default_as_of_date=latest_portfolio_as_of_date,
        )
        scope_name_map = _scope_name_map(
            portfolio_id,
            planning_taxonomy_id=str(record.planning_taxonomy_id or "").strip() or None,
        )
        settings_payload = _serialize_settings_row(
            record,
            taxonomy_name_map,
            scope_name_map,
            default_as_of_date=latest_portfolio_as_of_date,
        )
        production_risk_model = get_portfolio_risk_policy(portfolio_id) or {}
        current_planning_state_fingerprint = _planning_state_fingerprint(
            session,
            portfolio_id=portfolio_id,
            planning_taxonomy_id=str(settings_payload.get("planning_taxonomy_id") or "").strip() or None,
        )
        run_rows = session.scalars(
            select(AllocationResearchRunRecordModel)
            .where(AllocationResearchRunRecordModel.portfolio_id == portfolio_id)
            .order_by(AllocationResearchRunRecordModel.requested_at.desc(), AllocationResearchRunRecordModel.allocation_research_run_id.desc())
            .options(
                defer(AllocationResearchRunRecordModel.detail_json),
            )
        ).all()
        if selected_run_id and not any(
            row.allocation_research_run_id == selected_run_id for row in run_rows
        ):
            raise AllocationResearchRunNotFoundError(
                f"Allocation Research run '{selected_run_id}' was not found for portfolio '{portfolio_id}'."
            )

        risk_lookback_days = int(
            production_risk_model.get("lookback_days")
            or settings_payload.get("lookback_days")
            or 90
        )
        risk_calculation_frequency = str(
            production_risk_model.get("calculation_frequency")
            or settings_payload.get("calculation_frequency")
            or "auto"
        )
        effective_allocation_research_as_of_date = date.fromisoformat(
            str(settings_payload["as_of_date"])
        )
        context = _build_allocation_research_context(
            portfolio_id,
            planning_taxonomy_id=str(
                settings_payload.get("planning_taxonomy_id") or ""
            ).strip()
            or None,
            as_of_date=effective_allocation_research_as_of_date,
            lookback_days=risk_lookback_days,
        )
        current_published_holdings_input = context.pop(
            "_published_holdings_input", None
        )
        if not isinstance(current_published_holdings_input, dict):
            raise ValueError(
                "Current Allocation Research holdings input lineage is unavailable."
            )
        reliability_by_run_id = {
            row.allocation_research_run_id: _allocation_research_run_reliability(
                row,
                effective_allocation_research_as_of_date=effective_allocation_research_as_of_date,
                settings_payload=settings_payload,
                production_risk_model=production_risk_model,
                current_planning_state_fingerprint=current_planning_state_fingerprint,
                current_published_holdings_input=current_published_holdings_input,
            )
            for row in run_rows
        }
        runs = [
            _serialize_run_row(
                row,
                taxonomy_name_map,
                include_detail=False,
                reliability_state=reliability_by_run_id[row.allocation_research_run_id][0],
                reliability_reasons=reliability_by_run_id[row.allocation_research_run_id][1],
            )
            for row in run_rows
        ]
        selected_run_row = None
        if selected_run_id:
            selected_run_row = next(
                (row for row in run_rows if row.allocation_research_run_id == selected_run_id),
                None,
            )
        if selected_run_row is None and run_rows:
            selected_run_row = run_rows[0]
        selected_run = (
            _serialize_run_row(
                selected_run_row,
                taxonomy_name_map,
                include_detail=True,
                reliability_state=reliability_by_run_id[selected_run_row.allocation_research_run_id][0],
                reliability_reasons=reliability_by_run_id[selected_run_row.allocation_research_run_id][1],
            )
            if selected_run_row is not None
            else None
        )

    calculation_frequency_profile = build_allocation_research_calculation_frequency_profile(
        portfolio_id,
        planning_taxonomy_id=str(settings_payload.get("planning_taxonomy_id") or "").strip() or None,
        comparator_taxonomy_node_id=str(settings_payload.get("comparator_taxonomy_node_id") or "").strip() or None,
        as_of_date=effective_allocation_research_as_of_date,
        lookback_days=risk_lookback_days,
        requested_frequency=risk_calculation_frequency,
    )

    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": str(portfolio.get("portfolio_name") or portfolio_id),
        "base_currency": require_portfolio_fact_currency(
            portfolio.get("base_currency"),
            context=f"Portfolio '{portfolio_id}' base",
        ),
        "as_of_date": _iso_date(latest_portfolio_as_of_date),
        "default_planning_taxonomy_id": str(portfolio.get("default_planning_taxonomy_id") or "").strip() or None,
        "planning_taxonomy_options": _planning_taxonomy_options(portfolio_id),
        "planning_scope_options": build_allocation_research_scope_options(
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


def update_allocation_research_settings(
    portfolio_id: str,
    *,
    planning_taxonomy_id: str | None,
    comparator_taxonomy_node_id: str | None,
    as_of_mode: str,
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
    top_sleeve_weight_bounds: list[dict[str, object]] | None,
    policy_replay_rebalance_frequency: str,
    policy_replay_benchmark_instrument_id: str | None,
    notes: str | None,
) -> dict[str, object] | None:
    portfolio = _get_allocation_research_portfolio(portfolio_id)
    if portfolio is None:
        return None

    latest_portfolio_as_of_date = _default_as_of_date(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        taxonomy = _validate_planning_taxonomy(session, portfolio_id, planning_taxonomy_id)
        resolved_scope_node_id = _validate_allocation_research_scope(
            session,
            taxonomy=taxonomy,
            comparator_taxonomy_node_id=comparator_taxonomy_node_id,
        )
        row = _ensure_allocation_research_settings_record(
            session,
            portfolio_id,
            default_planning_taxonomy_id=str(portfolio.get("default_planning_taxonomy_id") or "").strip() or None,
            default_as_of_date=latest_portfolio_as_of_date,
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
        if top_sleeve_weight_bounds is None:
            resolved_top_sleeve_bounds = (
                []
                if resolved_planning_taxonomy_id != existing_planning_taxonomy_id
                else None
            )
        else:
            resolved_top_sleeve_bounds = _validate_top_sleeve_weight_bounds(
                portfolio_id,
                taxonomy=taxonomy,
                bounds=top_sleeve_weight_bounds,
            )
        row.planning_taxonomy_id = resolved_planning_taxonomy_id
        resolved_as_of_mode = str(as_of_mode or ALLOCATION_RESEARCH_AS_OF_MODE_DYNAMIC).strip().lower()
        if resolved_as_of_mode not in {ALLOCATION_RESEARCH_AS_OF_MODE_DYNAMIC, ALLOCATION_RESEARCH_AS_OF_MODE_PINNED}:
            raise ValueError("Allocation Research as-of mode must be dynamic or pinned.")
        if resolved_as_of_mode == ALLOCATION_RESEARCH_AS_OF_MODE_PINNED and as_of_date is None:
            raise ValueError("Pinned Allocation Research settings require an as-of date.")
        row.as_of_mode = resolved_as_of_mode
        row.as_of_date = as_of_date if resolved_as_of_mode == ALLOCATION_RESEARCH_AS_OF_MODE_PINNED else None
        row.comparator_taxonomy_node_id = resolved_scope_node_id
        row.lookback_days = int(lookback_days or 90)
        row.calculation_frequency = (calculation_frequency or "auto").strip() or "auto"
        row.missing_return_policy = (missing_return_policy or DEFAULT_MISSING_RETURN_POLICY).strip() or DEFAULT_MISSING_RETURN_POLICY
        row.target_dimension = (target_dimension or "scope_default").strip() or "scope_default"
        row.capital_mode = (capital_mode or "unit_notional").strip() or "unit_notional"
        row.gross_exposure = gross_exposure
        row.target_volatility = target_volatility
        row.max_gross_exposure = _normalize_allocation_research_max_gross_exposure(row.capital_mode, max_gross_exposure)
        if resolved_frozen_ids is not None:
            row.frozen_taxonomy_node_ids_json = resolved_frozen_ids
        if resolved_top_sleeve_bounds is not None:
            row.top_sleeve_weight_bounds_json = resolved_top_sleeve_bounds
        normalized_rebalance_frequency = str(policy_replay_rebalance_frequency or "").strip().lower()
        row.policy_replay_rebalance_frequency = (
            normalized_rebalance_frequency
            if normalized_rebalance_frequency in {"1w", "1m", "3m"}
            else "1m"
        )
        row.policy_replay_benchmark_instrument_id = str(policy_replay_benchmark_instrument_id or "").strip() or None
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
        return _serialize_settings_row(
            row,
            taxonomy_name_map,
            scope_name_map,
            default_as_of_date=latest_portfolio_as_of_date,
        )


def run_portfolio_allocation_research(
    portfolio_id: str,
    *,
    requested_by: str | None = None,
) -> dict[str, object] | None:
    portfolio = _get_allocation_research_portfolio(portfolio_id)
    if portfolio is None:
        return None

    latest_portfolio_as_of_date = _default_as_of_date(portfolio_id)
    taxonomy_name_map = _taxonomy_name_map(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        settings_row = _ensure_allocation_research_settings_record(
            session,
            portfolio_id,
            default_planning_taxonomy_id=str(portfolio.get("default_planning_taxonomy_id") or "").strip() or None,
            default_as_of_date=latest_portfolio_as_of_date,
        )
        taxonomy = _validate_planning_taxonomy(session, portfolio_id, settings_row.planning_taxonomy_id)
        resolved_scope_node_id = _validate_allocation_research_scope(
            session,
            taxonomy=taxonomy,
            comparator_taxonomy_node_id=settings_row.comparator_taxonomy_node_id,
        )
        requested_at = _utc_now_iso()
        run_id = _next_allocation_research_run_id(portfolio_id)
        effective_as_of_date = _effective_allocation_research_as_of_date(
            settings_row,
            default_as_of_date=latest_portfolio_as_of_date,
        )
        production_risk_model = get_portfolio_risk_policy(portfolio_id)
        risk_lookback_days = int((production_risk_model or {}).get("lookback_days") or settings_row.lookback_days or 90)
        risk_calculation_frequency = str(
            (production_risk_model or {}).get("calculation_frequency") or settings_row.calculation_frequency or "auto"
        )
        risk_missing_return_policy = str(
            (production_risk_model or {}).get("missing_return_policy")
            or settings_row.missing_return_policy
            or DEFAULT_MISSING_RETURN_POLICY
        )
        allocation_research_capital_mode = settings_row.capital_mode or "unit_notional"
        allocation_research_max_gross_exposure = _normalize_allocation_research_max_gross_exposure(
            allocation_research_capital_mode,
            settings_row.max_gross_exposure,
        )
        planning_state_fingerprint = _planning_state_fingerprint(
            session,
            portfolio_id=portfolio_id,
            planning_taxonomy_id=settings_row.planning_taxonomy_id,
        )
        if planning_state_fingerprint is None:
            raise ValueError("The selected planning taxonomy state is unavailable.")
        run_row = AllocationResearchRunRecordModel(
            allocation_research_run_id=run_id,
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
                "as_of_mode": _allocation_research_as_of_mode(settings_row),
                "lookback_days": risk_lookback_days,
                "calculation_frequency": risk_calculation_frequency,
                "missing_return_policy": risk_missing_return_policy,
                "risk_model": deepcopy(production_risk_model or {}),
                "planning_state_fingerprint_version": ALLOCATION_RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION,
                "planning_state_fingerprint": planning_state_fingerprint,
                "target_dimension": settings_row.target_dimension or "scope_default",
                "capital_mode": allocation_research_capital_mode,
                "gross_exposure": _safe_float(settings_row.gross_exposure),
                "target_volatility": _safe_float(settings_row.target_volatility),
                "max_gross_exposure": allocation_research_max_gross_exposure,
                "frozen_taxonomy_node_ids": deepcopy(settings_row.frozen_taxonomy_node_ids_json or []),
                "top_sleeve_weight_bounds": deepcopy(settings_row.top_sleeve_weight_bounds_json or []),
                "policy_replay_rebalance_frequency": str(settings_row.policy_replay_rebalance_frequency or "1m"),
                "policy_replay_benchmark_instrument_id": str(settings_row.policy_replay_benchmark_instrument_id or "").strip() or None,
                "notes": settings_row.notes,
            },
            error_message=None,
        )
        session.add(run_row)
        session.commit()

        try:
            context = _build_allocation_research_context(
                portfolio_id,
                planning_taxonomy_id=str(settings_row.planning_taxonomy_id or "").strip() or None,
                as_of_date=effective_as_of_date,
                lookback_days=risk_lookback_days,
            )
            published_holdings_input = context.get(
                "_published_holdings_input"
            )
            if not isinstance(published_holdings_input, dict):
                raise ValueError(
                    "Allocation Research holdings input lineage was not captured."
                )
            request_payload = deepcopy(run_row.request_payload_json or {})
            request_payload["published_holdings_input"] = deepcopy(
                published_holdings_input
            )
            run_row.request_payload_json = request_payload
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
                capital_mode=allocation_research_capital_mode,
                gross_exposure=_safe_float(settings_row.gross_exposure),
                target_volatility=_safe_float(settings_row.target_volatility),
                max_gross_exposure=allocation_research_max_gross_exposure,
                frozen_taxonomy_node_ids=deepcopy(settings_row.frozen_taxonomy_node_ids_json or []),
                top_sleeve_weight_bounds=deepcopy(settings_row.top_sleeve_weight_bounds_json or []),
                risk_model_config=deepcopy(production_risk_model or {}),
            )
            current_target_market_dependencies = deepcopy(
                solution.get("market_data_dependencies")
            )
            policy_replay_payload = build_current_target_policy_replay(
                portfolio_id,
                planning_taxonomy_id=str(settings_row.planning_taxonomy_id or "").strip(),
                comparator_taxonomy_node_id=resolved_scope_node_id,
                as_of_date=effective_as_of_date,
                lookback_days=risk_lookback_days,
                calculation_frequency=risk_calculation_frequency,
                missing_return_policy=risk_missing_return_policy,
                target_dimension=settings_row.target_dimension or "scope_default",
                capital_mode=allocation_research_capital_mode,
                gross_exposure=_safe_float(settings_row.gross_exposure),
                target_volatility=_safe_float(settings_row.target_volatility),
                max_gross_exposure=allocation_research_max_gross_exposure,
                frozen_taxonomy_node_ids=deepcopy(settings_row.frozen_taxonomy_node_ids_json or []),
                top_sleeve_weight_bounds=deepcopy(settings_row.top_sleeve_weight_bounds_json or []),
                risk_model_config=deepcopy(production_risk_model or {}),
                rebalance_frequency=str(settings_row.policy_replay_rebalance_frequency or "1m"),
                benchmark_instrument_id=str(settings_row.policy_replay_benchmark_instrument_id or "").strip() or None,
                current_solution=solution,
            )
            solution.update(policy_replay_payload)
            request_payload["market_data_input"] = _market_data_input(
                {
                    "current_target_solve": current_target_market_dependencies,
                    "policy_replay": policy_replay_payload.get(
                        "market_data_dependencies"
                    ),
                }
            )
            run_row.request_payload_json = deepcopy(request_payload)
            detail = _build_current_target_detail(
                context,
                planning_taxonomy_name=planning_taxonomy_name,
                settings_payload=deepcopy(request_payload),
                solution=solution,
            )
            artifacts = _write_artifacts(
                portfolio_id,
                run_id,
                settings_payload=deepcopy(request_payload),
                context=context,
                detail=detail,
            )
            run_row.status = "completed"
            run_row.finished_at = _utc_now_iso()
            run_row.headline = str(detail.get("headline") or "")
            run_row.detail_json = detail
            run_row.artifacts_json = artifacts
            run_row.error_message = None
            _prune_portfolio_allocation_research_runs(session, portfolio_id, keep_run_id=run_id)
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


def get_allocation_research_run(
    portfolio_id: str,
    *,
    allocation_research_run_id: str,
) -> dict[str, object] | None:
    """Return one full run without expanding every run in the workbench list."""

    if _get_allocation_research_portfolio(portfolio_id) is None:
        return None

    session_factory = get_session_factory()
    with session_factory() as session:
        row = session.get(AllocationResearchRunRecordModel, allocation_research_run_id)
        if row is None or row.portfolio_id != portfolio_id:
            return None
        return _serialize_run_row(row, _taxonomy_name_map(portfolio_id))


def get_allocation_research_policy_replay_benchmark_comparison(
    portfolio_id: str,
    *,
    allocation_research_run_id: str,
    benchmark_instrument_id: str,
) -> dict[str, object] | None:
    portfolio = _get_allocation_research_portfolio(portfolio_id)
    if portfolio is None:
        return None

    session_factory = get_session_factory()
    with session_factory() as session:
        run_row = session.get(AllocationResearchRunRecordModel, allocation_research_run_id)
        if run_row is None or run_row.portfolio_id != portfolio_id:
            return None
        if run_row.status != "completed":
            raise ValueError(
                "Benchmark comparison requires a completed Allocation Research run."
            )

        detail = deepcopy(run_row.detail_json or {})
        policy_replay = detail.get("policy_replay") if isinstance(detail, dict) else None
        if not isinstance(policy_replay, dict):
            raise ValueError(
                "Benchmark comparison requires an Allocation Research run with "
                "Policy Replay output."
            )
        portfolio_points = [
            item for item in list(policy_replay.get("points") or []) if isinstance(item, dict)
        ]
        if not portfolio_points:
            raise ValueError(
                "Benchmark comparison requires a non-empty Policy Replay series."
            )

        request_payload = run_row.request_payload_json or {}
        planning_taxonomy_id = str(
            run_row.planning_taxonomy_id
            or (request_payload.get("planning_taxonomy_id") if isinstance(request_payload, dict) else "")
            or ""
        ).strip()
        if not planning_taxonomy_id:
            raise ValueError("Benchmark comparison requires a planning taxonomy.")

        return build_allocation_research_policy_replay_benchmark_comparison(
            portfolio_id,
            planning_taxonomy_id=planning_taxonomy_id,
            as_of_date=run_row.as_of_date or _default_as_of_date(portfolio_id),
            benchmark_instrument_id=benchmark_instrument_id,
            portfolio_points=portfolio_points,
        )


def read_allocation_research_artifact_content(
    portfolio_id: str,
    *,
    path: str,
) -> dict[str, object]:
    if _get_allocation_research_portfolio(portfolio_id) is None:
        raise ValueError("Portfolio not found.")
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
