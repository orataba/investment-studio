from __future__ import annotations

import csv
import hashlib
import json
import logging
import mimetypes
import shutil
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from studio_runtime import operation
from sqlalchemy import func, or_, select
from sqlalchemy.orm import defer
from investment_studio_instrument_core.db_models import Instrument

from portfolio_app.core.settings import get_settings
from portfolio_app.db.models import (
    AccountRecordModel,
    DerivativeContractRecordModel,
    PortfolioInstrumentUniverseRecordModel,
    PortfolioRecordModel,
    ResearchRunRecordModel,
    ResearchSettingsRecordModel,
    TaxonomyNodeRecordModel,
    TaxonomyRecordModel,
    TransactionRecordModel,
)
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.daily_snapshots import (
    _portfolio_source_instrument_ids,
    _run_portfolio_daily_snapshot_recalculation_synchronously,
    ensure_portfolio_daily_snapshots,
)
from portfolio_app.services.ledger import build_account_workspace
from portfolio_app.services.instrument_registry import get_registry_instrument_detail, get_registry_instrument_details
from portfolio_app.services.performance import build_holdings_report
from portfolio_app.services import valuation_fx
from portfolio_app.services.asset_deliveries import expand_asset_deliveries
from portfolio_app.services.research_inputs import capture_current_target_configuration
from portfolio_app.services.research_solver import (
    RESEARCH_BACKTEST_METHODOLOGY_WARNINGS,
    RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
    SYSTEM_CASH_TARGET_LABEL,
    SYSTEM_CASH_TARGET_MEMBER_ID,
    SYSTEM_DERIVATIVE_TARGET_LABEL,
    SYSTEM_DERIVATIVE_TARGET_MEMBER_ID,
    _build_taxonomy_state,
    build_research_calculation_frequency_profile,
    build_research_scope_options,
    build_current_target_backtest,
    build_research_backtest_benchmark_comparison,
    rebuild_backtest_metrics_from_points,
    research_window_start_date,
    solve_current_target_weights,
)
from portfolio_app.services.portfolio_store import (
    get_portfolio,
    list_portfolio_instrument_universe,
    list_taxonomies,
    list_target_sets,
    list_taxonomy_nodes,
    list_accounts,
    list_transactions,
)
from portfolio_app.services.research_eligibility import derive_research_lifecycle
from portfolio_app.services.risk_model import get_portfolio_risk_policy, normalize_portfolio_risk_policy, risk_window_label
from portfolio_app.services.workspace_cache import (
    get_cached_materialized_performance_report,
    get_cached_research_analysis,
)

TEXT_SUFFIXES = {".csv", ".json", ".md", ".txt", ".yaml", ".yml"}
HTML_SUFFIXES = {".html"}
CURRENT_TARGET_RUN_TEMPLATE = "target_weight_solve"
RESEARCH_AS_OF_MODE_DYNAMIC = "dynamic"
RESEARCH_AS_OF_MODE_PINNED = "pinned"
RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION = 5
logger = logging.getLogger(__name__)
DEFAULT_BACKTEST_ROBUSTNESS_SCENARIOS: list[dict[str, object]] = [
    {
        "scenario_id": "friction_1_5x",
        "label": "1.5x Friction",
        "cash_yield_annual": 0.0,
        "commission_bps": 3.0,
        "tax_bps": 15.0,
        "slippage_bps": 7.5,
        "implementation_delay_days": 2,
    },
    {
        "scenario_id": "friction_2x",
        "label": "2x Friction",
        "cash_yield_annual": 0.0,
        "commission_bps": 4.0,
        "tax_bps": 20.0,
        "slippage_bps": 10.0,
        "implementation_delay_days": 3,
    },
]
# PUT settings fields use explicit-null and omitted as different operations.
# The route passes this sentinel for omitted optional controls so older or
# partial clients cannot silently restore backtest defaults.
RESEARCH_SETTINGS_UNSET = object()


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


def _normalized_backtest_robustness_scenarios(
    scenarios: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    source = scenarios if scenarios is not None else DEFAULT_BACKTEST_ROBUSTNESS_SCENARIOS
    normalized: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for item in source:
        scenario_id = str(item.get("scenario_id") or "").strip()
        label = str(item.get("label") or "").strip()
        if not scenario_id or not label or scenario_id in seen_ids:
            raise ValueError(
                "Backtest robustness scenarios require unique scenario_id and label values."
            )
        seen_ids.add(scenario_id)
        normalized.append(
            {
                "scenario_id": scenario_id,
                "label": label,
                "cash_yield_annual": float(item.get("cash_yield_annual") or 0.0),
                "commission_bps": float(item.get("commission_bps") or 0.0),
                "tax_bps": float(item.get("tax_bps") or 0.0),
                "slippage_bps": float(item.get("slippage_bps") or 0.0),
                "implementation_delay_days": int(
                    item.get("implementation_delay_days") or 0
                ),
            }
        )
    return normalized


def _normalize_research_max_gross_exposure(capital_mode: object, value: object) -> float | None:
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


def _prune_portfolio_research_runs(session, portfolio_id: str, *, keep_run_id: str) -> list[str]:
    current = session.get(ResearchRunRecordModel, keep_run_id)
    rows = session.scalars(
        select(ResearchRunRecordModel).where(
            ResearchRunRecordModel.portfolio_id == portfolio_id,
            ResearchRunRecordModel.research_run_id != keep_run_id,
            ResearchRunRecordModel.status != "running",
            ResearchRunRecordModel.requested_at <= current.requested_at,
        )
    ).all()
    for row in rows:
        session.delete(row)
    return [row.research_run_id for row in rows]


def _remove_pruned_research_artifacts(portfolio_id: str, run_ids: list[str]) -> None:
    # Only remove the terminal runs whose database deletion committed. Other
    # directories can belong to active calculations or later requests.
    for run_id in run_ids:
        path = _research_outputs_root() / portfolio_id / run_id
        if path.exists():
            try:
                shutil.rmtree(path)
            except OSError:
                logger.exception("Could not remove retired research artifacts for %s.", run_id)


def _default_as_of_date(portfolio: dict[str, object]) -> date:
    if portfolio.get("as_of_date"):
        return date.fromisoformat(str(portfolio["as_of_date"]))
    return date.today()


def _research_as_of_mode(record: ResearchSettingsRecordModel) -> str:
    mode = str(getattr(record, "as_of_mode", RESEARCH_AS_OF_MODE_DYNAMIC) or "").strip().lower()
    return mode if mode in {RESEARCH_AS_OF_MODE_DYNAMIC, RESEARCH_AS_OF_MODE_PINNED} else RESEARCH_AS_OF_MODE_DYNAMIC


def _effective_research_as_of_date(
    record: ResearchSettingsRecordModel,
    *,
    default_as_of_date: date,
) -> date:
    if _research_as_of_mode(record) == RESEARCH_AS_OF_MODE_PINNED:
        if record.as_of_date is None:
            raise ValueError("Pinned Research settings require an as-of date.")
        return record.as_of_date
    return default_as_of_date


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
        if _research_as_of_mode(record) == RESEARCH_AS_OF_MODE_DYNAMIC and record.as_of_date is not None:
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
        if not getattr(record, "missing_return_policy", None):
            record.missing_return_policy = RESEARCH_DEFAULT_MISSING_RETURN_POLICY
            changed = True
        if record.frozen_taxonomy_node_ids_json is None:
            record.frozen_taxonomy_node_ids_json = []
            changed = True
        if record.top_sleeve_weight_bounds_json is None:
            record.top_sleeve_weight_bounds_json = []
            changed = True
        if not str(getattr(record, "backtest_rebalance_frequency", "") or "").strip():
            record.backtest_rebalance_frequency = "1m"
            changed = True
        if record.backtest_robustness_scenarios_json is None:
            record.backtest_robustness_scenarios_json = deepcopy(
                DEFAULT_BACKTEST_ROBUSTNESS_SCENARIOS
            )
            changed = True
        if changed:
            record.updated_at = _utc_now_iso()
            session.commit()
        return record

    record = ResearchSettingsRecordModel(
        portfolio_id=portfolio_id,
        planning_taxonomy_id=default_planning_taxonomy_id,
        comparator_taxonomy_node_id=None,
        as_of_mode=RESEARCH_AS_OF_MODE_DYNAMIC,
        as_of_date=None,
        lookback_days=90,
        calculation_frequency="daily",
        missing_return_policy=RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
        target_dimension="scope_default",
        capital_mode="unit_notional",
        gross_exposure=None,
        target_volatility=None,
        max_gross_exposure=None,
        frozen_taxonomy_node_ids_json=[],
        top_sleeve_weight_bounds_json=[],
        backtest_rebalance_frequency="1m",
        backtest_benchmark_instrument_id=None,
        backtest_cash_yield_annual=0.02,
        backtest_commission_bps=2.0,
        backtest_tax_bps=10.0,
        backtest_slippage_bps=5.0,
        backtest_implementation_delay_days=1,
        backtest_robustness_scenarios_json=deepcopy(
            DEFAULT_BACKTEST_ROBUSTNESS_SCENARIOS
        ),
        backtest_walk_forward_training_months=24,
        backtest_walk_forward_test_months=6,
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
    if taxonomy.status != "active":
        raise ValueError("Research planning taxonomy must be active.")
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
    configured = {item["taxonomy_id"] for item in list_target_sets(portfolio_id)
                  if item.get("status") == "active" and (item.get("weight_enabled") or item.get("risk_budget_enabled"))}
    return [
        {
            "taxonomy_id": item["taxonomy_id"],
            "name": item["name"],
            "taxonomy_type": item["taxonomy_type"],
            "budgeting_level": item.get("budgeting_level"),
            "targets_available": item["taxonomy_id"] in configured,
        }
        for item in list_taxonomies(portfolio_id)
        if item.get("status") == "active"
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
    *,
    default_as_of_date: date,
) -> dict[str, object]:
    planning_taxonomy_id = str(row.planning_taxonomy_id or "").strip() or None
    comparator_taxonomy_node_id = str(row.comparator_taxonomy_node_id or "").strip() or None
    as_of_mode = _research_as_of_mode(row)
    effective_as_of_date = _effective_research_as_of_date(
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
            if as_of_mode == RESEARCH_AS_OF_MODE_PINNED
            else None
        ),
        "lookback_days": int(row.lookback_days or 90),
        "calculation_frequency": "daily",
        "missing_return_policy": row.missing_return_policy or RESEARCH_DEFAULT_MISSING_RETURN_POLICY,
        "target_dimension": row.target_dimension or "scope_default",
        "capital_mode": row.capital_mode or "unit_notional",
        "gross_exposure": _safe_float(row.gross_exposure),
        "target_volatility": _safe_float(row.target_volatility),
        "max_gross_exposure": _normalize_research_max_gross_exposure(row.capital_mode, row.max_gross_exposure),
        "frozen_taxonomy_node_ids": deepcopy(row.frozen_taxonomy_node_ids_json or []),
        "top_sleeve_weight_bounds": deepcopy(row.top_sleeve_weight_bounds_json or []),
        "backtest_rebalance_frequency": str(row.backtest_rebalance_frequency or "1m").strip() or "1m",
        "backtest_benchmark_instrument_id": str(row.backtest_benchmark_instrument_id or "").strip() or None,
        "backtest_cash_yield_annual": float(row.backtest_cash_yield_annual),
        "backtest_commission_bps": float(row.backtest_commission_bps),
        "backtest_tax_bps": float(row.backtest_tax_bps),
        "backtest_slippage_bps": float(row.backtest_slippage_bps),
        "backtest_implementation_delay_days": int(
            row.backtest_implementation_delay_days
        ),
        "backtest_robustness_scenarios": deepcopy(
            row.backtest_robustness_scenarios_json or []
        ),
        "backtest_walk_forward_training_months": int(
            row.backtest_walk_forward_training_months
        ),
        "backtest_walk_forward_test_months": int(
            row.backtest_walk_forward_test_months
        ),
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


def _latest_research_transaction_date(portfolio_id: str) -> date | None:
    session_factory = get_session_factory()
    with session_factory() as session:
        return session.scalar(
            select(func.max(TransactionRecordModel.trade_date)).where(
                TransactionRecordModel.portfolio_id == portfolio_id
            )
        )


def _canonical_reliability_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _planning_state_fingerprint(
    session,
    *,
    portfolio_id: str,
    planning_taxonomy_id: str | None,
    as_of_date: date,
    target_configuration: dict[str, object] | None = None,
) -> str | None:
    taxonomy_id = str(planning_taxonomy_id or "").strip()
    if not taxonomy_id:
        return None

    configuration = target_configuration if target_configuration is not None else capture_current_target_configuration(
        portfolio_id, taxonomy_id, session=session,
    )
    instrument_universe = session.scalars(
        select(PortfolioInstrumentUniverseRecordModel).where(
            PortfolioInstrumentUniverseRecordModel.portfolio_id == portfolio_id,
            PortfolioInstrumentUniverseRecordModel.status == "active",
        )
    ).all()
    source_instrument_ids = _portfolio_source_instrument_ids(session, portfolio_id)
    source_instrument_ids.update(item.instrument_id for item in instrument_universe)
    source_instrument_ids.update(
        str(item["target_entity_id"]) for item in configuration.get("taxonomy_assignments", [])
        if item.get("status") == "active" and item.get("target_scope") == "instrument"
    )
    source_instrument_ids.update(
        str(item["target_member_id"]) for item in configuration.get("target_set_lines", [])
        if item.get("target_member_type") == "instrument"
    )
    settings = session.get(ResearchSettingsRecordModel, portfolio_id)
    if settings is not None and settings.backtest_benchmark_instrument_id:
        source_instrument_ids.add(settings.backtest_benchmark_instrument_id)
    portfolio = session.get(PortfolioRecordModel, portfolio_id)
    if portfolio is None:
        return None
    state = {
        "schema_version": RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION,
        "as_of_date": as_of_date.isoformat(),
        "target_snapshot_fingerprint": configuration["target_snapshot_fingerprint"],
        # Dates alone do not detect an amended/deleted historical transaction.
        # Reuse canonical row versions and shared source watermarks; a refresh
        # request itself is not evidence that any financial input changed.
        "financial_inputs": {
            "portfolio": [portfolio.base_currency, portfolio.inception_date, portfolio.risk_policy_json],
            "transactions": [tuple(row) for row in session.execute(select(
                TransactionRecordModel.transaction_id, TransactionRecordModel.row_version,
            ).where(TransactionRecordModel.portfolio_id == portfolio_id,
                TransactionRecordModel.trade_date <= as_of_date).order_by(TransactionRecordModel.transaction_id))],
            "accounts": [tuple(row) for row in session.execute(select(
                AccountRecordModel.account_id, AccountRecordModel.account_category,
                AccountRecordModel.currency, AccountRecordModel.cost_basis_method,
                AccountRecordModel.opened_at, AccountRecordModel.closed_at, AccountRecordModel.status,
            ).where(AccountRecordModel.portfolio_id == portfolio_id).order_by(AccountRecordModel.account_id))],
            "contracts": [tuple(row) for row in session.execute(select(
                DerivativeContractRecordModel.derivative_contract_id, DerivativeContractRecordModel.row_version,
            ).where(DerivativeContractRecordModel.portfolio_id == portfolio_id).order_by(DerivativeContractRecordModel.derivative_contract_id))],
            "market_sources": [tuple(row) for row in session.execute(select(
                Instrument.instrument_id, Instrument.market_data_updated_at, Instrument.calculation_inputs_updated_at,
            ).where(or_(Instrument.instrument_id.in_(source_instrument_ids), Instrument.instrument_type == "fx"))
                .order_by(Instrument.instrument_id))],
        },
        "research_instrument_eligibility": sorted(
            [
                {
                    "instrument_id": item.instrument_id,
                    "research_lifecycle": derive_research_lifecycle(
                        holding_state=item.holding_state,
                        transaction_count=item.transaction_count,
                    ),
                    "research_pm_approved": bool(item.research_pm_approved),
                }
                for item in instrument_universe
            ],
            key=lambda item: str(item["instrument_id"]),
        ),
    }
    digest = hashlib.sha256(_canonical_reliability_value(state).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _research_run_reliability(
    row: ResearchRunRecordModel,
    *,
    latest_portfolio_as_of_date: date,
    settings_payload: dict[str, object],
    production_risk_model: dict[str, object],
    latest_transaction_date: date | None,
    current_planning_state_fingerprint: str | None,
) -> tuple[str, list[str]]:
    if row.status != "completed":
        return "not_completed", [f"Run status is {row.status}; no current solved result is available."]

    reasons: list[str] = []
    run_as_of_date = _date_value(row.as_of_date)
    pinned = settings_payload.get("as_of_mode") == RESEARCH_AS_OF_MODE_PINNED
    expected_as_of_date = _date_value(settings_payload.get("as_of_date")) if pinned else latest_portfolio_as_of_date
    if run_as_of_date is None:
        reasons.append("Run does not record an analysis date.")
    elif run_as_of_date != expected_as_of_date:
        date_label = "selected pinned date" if pinned else "latest portfolio date"
        reasons.append(
            f"Run analysis date {run_as_of_date.isoformat()} does not match the {date_label} "
            f"{_iso_date(expected_as_of_date)}."
        )
    if not pinned and run_as_of_date is not None and latest_transaction_date is not None and latest_transaction_date > run_as_of_date:
        reasons.append(
            f"Portfolio transactions exist through {latest_transaction_date.isoformat()}, after this run's "
            f"{run_as_of_date.isoformat()} analysis date."
        )

    request_payload = row.request_payload_json if isinstance(row.request_payload_json, dict) else {}
    run_planning_state_fingerprint = str(request_payload.get("planning_state_fingerprint") or "").strip()
    if not run_planning_state_fingerprint:
        reasons.append(
            "Run predates planning-state fingerprinting; rerun Research before treating it as current."
        )
    elif current_planning_state_fingerprint is None:
        reasons.append("The current planning taxonomy state is unavailable; rerun after repairing Research settings.")
    elif run_planning_state_fingerprint != current_planning_state_fingerprint:
        reasons.append(
            "Planning taxonomy structure, active assignments, active SAA/TAA target configuration, "
            "portfolio facts, or market-data inputs changed after this run was created; rerun Research."
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
                or "daily"
            ),
            "missing_return_policy": str(
                production_risk_model.get("missing_return_policy")
                or settings_payload.get("missing_return_policy")
                or RESEARCH_DEFAULT_MISSING_RETURN_POLICY
            ),
            "target_dimension": settings_payload.get("target_dimension"),
            "capital_mode": settings_payload.get("capital_mode"),
            "gross_exposure": settings_payload.get("gross_exposure"),
            "target_volatility": settings_payload.get("target_volatility"),
            "max_gross_exposure": settings_payload.get("max_gross_exposure"),
            "frozen_taxonomy_node_ids": settings_payload.get("frozen_taxonomy_node_ids") or [],
            "top_sleeve_weight_bounds": settings_payload.get("top_sleeve_weight_bounds") or [],
            "backtest_rebalance_frequency": settings_payload.get("backtest_rebalance_frequency"),
            "backtest_benchmark_instrument_id": settings_payload.get("backtest_benchmark_instrument_id"),
            "backtest_cash_yield_annual": settings_payload.get(
                "backtest_cash_yield_annual"
            ),
            "backtest_commission_bps": settings_payload.get(
                "backtest_commission_bps"
            ),
            "backtest_tax_bps": settings_payload.get("backtest_tax_bps"),
            "backtest_slippage_bps": settings_payload.get("backtest_slippage_bps"),
            "backtest_implementation_delay_days": settings_payload.get(
                "backtest_implementation_delay_days"
            ),
            "backtest_robustness_scenarios": settings_payload.get(
                "backtest_robustness_scenarios"
            )
            or [],
            "backtest_walk_forward_training_months": settings_payload.get(
                "backtest_walk_forward_training_months"
            ),
            "backtest_walk_forward_test_months": settings_payload.get(
                "backtest_walk_forward_test_months"
            ),
        }
        material_keys = list(expected_payload)
        request_material = {key: request_payload.get(key) for key in material_keys}
        if _canonical_reliability_value(request_material) != _canonical_reliability_value(expected_payload):
            reasons.append("Research settings or the production risk policy changed after this run was created.")

        run_risk_model = request_payload.get("risk_model") if isinstance(request_payload.get("risk_model"), dict) else {}
        risk_keys = ("covariance_model_id", "lookback_days", "calculation_frequency", "missing_return_policy", "contribution_mode")
        expected_risk = {key: production_risk_model.get(key) for key in risk_keys}
        actual_risk = {key: run_risk_model.get(key) for key in risk_keys}
        if run_risk_model and _canonical_reliability_value(actual_risk) != _canonical_reliability_value(expected_risk):
            reasons.append("The production covariance/risk-contribution model changed after this run was created.")

    return ("stale", list(dict.fromkeys(reasons))) if reasons else ("current", [])


def _serialize_run_row(
    row: ResearchRunRecordModel,
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
        for backtest_key in ("backtest", "backtest_benchmark"):
            backtest_payload = detail.get(backtest_key)
            if not isinstance(backtest_payload, dict):
                continue
            points = backtest_payload.get("points")
            if isinstance(points, list):
                backtest_payload["metrics"] = rebuild_backtest_metrics_from_points(
                    [item for item in points if isinstance(item, dict)]
                )
    artifact_rows = deepcopy(row.artifacts_json or [])
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
        "reliability_state": reliability_state,
        "is_current": reliability_state == "current",
        "reliability_reasons": list(reliability_reasons or []),
        "artifact_count": len(artifact_rows),
        "artifacts": artifact_rows if include_detail else [],
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
    target_configuration: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    if not planning_taxonomy_id:
        return []

    configuration = target_configuration if target_configuration is not None else capture_current_target_configuration(portfolio_id, planning_taxonomy_id)
    node_name_by_id = {
        str(item.get("taxonomy_node_id") or ""): str(item.get("node_name") or "")
        for item in configuration.get("taxonomy_nodes", [])
        if str(item.get("taxonomy_id") or "") == planning_taxonomy_id
    }
    assignment_by_instrument: dict[str, dict[str, object]] = {}
    for item in configuration.get("taxonomy_assignments", []):
        if str(item.get("taxonomy_id") or "") != planning_taxonomy_id:
            continue
        if str(item.get("status") or "") != "active":
            continue
        if str(item.get("target_scope") or "") != "instrument":
            continue
        entity_id = str(item.get("target_entity_id") or "")
        if not entity_id:
            continue
        assignment_by_instrument.setdefault(entity_id, item)

    if any(_safe_float(position.get("market_value_base")) is None for position in statement_positions):
        return []

    security_positions = [
        position
        for position in statement_positions
        if not position.get("derivative_contract_id")
        and not isinstance(position.get("derivative_contract"), dict)
        and str(position.get("instrument_id") or "")
    ]
    derivative_positions = [
        position
        for position in statement_positions
        if position.get("derivative_contract_id")
        or isinstance(position.get("derivative_contract"), dict)
    ]

    def account_liquidity_base(account_row: dict[str, object]) -> float | None:
        cash_balance_base = _safe_float(account_row.get("derived_cash_balance_base"))
        pending_settlement_base = _safe_float(account_row.get("pending_settlement_base"))
        if cash_balance_base is None or pending_settlement_base is None:
            return None
        return cash_balance_base + pending_settlement_base

    if any(account_liquidity_base(account_row) is None for account_row in account_rows):
        return []

    visible_cash_accounts = [
        account_row
        for account_row in account_rows
        if (
            str((account_row.get("account") or {}).get("account_type") or "") == "deposit_account"
            or abs(account_liquidity_base(account_row) or 0.0) > 1e-9
        )
    ]

    total_entity_value_base = sum(float(position["market_value_base"]) for position in security_positions)
    total_entity_value_base += sum(float(position["market_value_base"]) for position in derivative_positions)
    total_entity_value_base += sum(
        account_liquidity_base(account_row) or 0.0 for account_row in visible_cash_accounts
    )

    buckets: dict[str, dict[str, object]] = {}
    for position in security_positions:
        instrument_id = str(position.get("instrument_id") or "")
        assignment = assignment_by_instrument.get(instrument_id)
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
        market_value_base = float(position["market_value_base"])
        cost_basis_base = _safe_float(position.get("cost_basis_base")) or 0.0
        bucket["end_value_base"] = float(bucket["end_value_base"] or 0.0) + market_value_base
        bucket["total_pnl"] = float(bucket["total_pnl"] or 0.0) + (market_value_base - cost_basis_base)
        bucket["position_count"] = int(bucket["position_count"] or 0) + 1

    if derivative_positions:
        derivative_bucket = buckets.setdefault(
            SYSTEM_DERIVATIVE_TARGET_MEMBER_ID,
            {
                "group_key": SYSTEM_DERIVATIVE_TARGET_MEMBER_ID,
                "group_label": SYSTEM_DERIVATIVE_TARGET_LABEL,
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
        derivative_bucket["end_value_base"] = sum(
            float(position["market_value_base"]) for position in derivative_positions
        )
        derivative_bucket["position_count"] = len(derivative_positions)

    if visible_cash_accounts:
        cash_bucket = buckets.setdefault(
            SYSTEM_CASH_TARGET_MEMBER_ID,
            {
                "group_key": SYSTEM_CASH_TARGET_MEMBER_ID,
                "group_label": SYSTEM_CASH_TARGET_LABEL,
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
        cash_bucket["end_value_base"] = sum(
            account_liquidity_base(account_row) or 0.0 for account_row in visible_cash_accounts
        )
        cash_bucket["position_count"] = len(visible_cash_accounts)

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
    target_configuration: dict[str, object] | None = None,
) -> dict[str, object]:
    if not planning_taxonomy_id:
        return {
            "root_saa_configured": False,
            "root_taa_configured": False,
            "scoped_target_set_count": 0,
        }

    configuration = target_configuration if target_configuration is not None else capture_current_target_configuration(portfolio_id, planning_taxonomy_id)
    configured_target_sets = [
        item
        for item in configuration.get("target_sets", [])
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
    target_configuration: dict[str, object] | None = None,
    lookback_days: int,
    instrument_detail_cache: dict[str, dict[str, object] | None] | None = None,
    direct_fx_instruments: valuation_fx.FxInstrumentMap | None = None,
) -> dict[str, object]:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        raise ValueError("Portfolio not found.")

    accounts = list_accounts(portfolio_id)
    transactions = list_transactions(portfolio_id)

    resolved_instrument_detail_cache = (
        instrument_detail_cache if instrument_detail_cache is not None
        else valuation_fx.HistoricalInstrumentDetails(end_date=as_of_date)
    )
    # Financial views share observations and their validated date index for
    # this request. FX discovery checks only actual paths, with the same full
    # history eligibility as the shared catalog. Explicit inputs remain owned
    # by the caller, including an empty map or a plain detail dictionary.
    if direct_fx_instruments is None:
        direct_fx_instruments = valuation_fx.HistoricalFxInstruments(
            resolved_instrument_detail_cache,
            detail_loader=get_registry_instrument_detail,
        )
    missing_instrument_ids = {
        str(transaction.get("instrument_id"))
        for transaction in expand_asset_deliveries(transactions)
        if transaction.get("instrument_id")
        and str(transaction["instrument_id"]) not in resolved_instrument_detail_cache
    }
    if missing_instrument_ids:
        resolved_instrument_detail_cache.update(get_registry_instrument_details(sorted(missing_instrument_ids)))
    statement = build_holdings_report(
        portfolio,
        accounts,
        transactions,
        as_of_date=as_of_date,
        instrument_detail_cache=resolved_instrument_detail_cache,
        direct_fx_instruments=direct_fx_instruments,
    )
    account_workspace = build_account_workspace(
        portfolio_id,
        accounts,
        transactions,
        base_currency=str(statement.get("base_currency") or portfolio.get("base_currency") or "USD"),
        as_of_date=as_of_date,
        instrument_detail_cache=resolved_instrument_detail_cache,
        direct_fx_instruments=direct_fx_instruments,
    )
    lookback_start = research_window_start_date(as_of_date, lookback_days)
    performance_report = get_cached_materialized_performance_report(
        portfolio_id,
        start_date=lookback_start,
        end_date=as_of_date,
    )
    performance_summary = (
        performance_report.get("summary")
        if isinstance(performance_report, dict) and isinstance(performance_report.get("summary"), dict)
        else {}
    )
    statement_positions = list(statement.get("positions", []))
    research_security_positions = [
        position
        for position in statement_positions
        if not position.get("derivative_contract_id")
        and not isinstance(position.get("derivative_contract"), dict)
        and str(position.get("instrument_id") or "")
    ]
    top_holdings = _build_top_holdings_snapshot(
        research_security_positions,
        base_currency=str(statement.get("base_currency") or "USD"),
    )
    configuration = target_configuration if target_configuration is not None else (
        capture_current_target_configuration(portfolio_id, planning_taxonomy_id) if planning_taxonomy_id else None
    )
    planning_groups = _build_planning_group_snapshot(
        statement_positions,
        account_rows=list(account_workspace.get("accounts") or []),
        portfolio_id=portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        target_configuration=configuration,
    )
    planning_target_summary = _build_planning_target_summary(
        portfolio_id,
        planning_taxonomy_id=planning_taxonomy_id,
        target_configuration=configuration,
    )
    quality_warnings: list[str] = []
    unassigned_group = next(
        (item for item in planning_groups if str(item.get("group_key") or "") == "unassigned"),
        None,
    )
    if unassigned_group is not None and int(unassigned_group.get("position_count") or 0) > 0:
        quality_warnings.append(
            "Research target solve is unavailable until all securities are assigned to the selected planning taxonomy "
            f"({int(unassigned_group.get('position_count') or 0)} unassigned security holding(s))."
        )
    daily_points = [
        {
            "date": point["as_of_date"].isoformat(),
            "value": float(point["ending_nav"]),
        }
        for point in (
            list(performance_report.get("daily_series") or [])
            if isinstance(performance_report, dict)
            else []
        )
        if isinstance(point, dict)
        and isinstance(point.get("as_of_date"), date)
        and bool(point.get("return_observation_eligible"))
        and _safe_float(point.get("ending_nav")) is not None
    ]
    chart_label = "Portfolio NAV" if performance_report is not None else None
    chart_note = (
        "Canonical portfolio NAV from Performance; only return-observation-eligible dates are included."
        if performance_report is not None
        else None
    )
    chart_currency = (
        str(performance_report.get("base_currency") or statement.get("base_currency") or "USD")
        if isinstance(performance_report, dict)
        else None
    )
    if performance_report is None:
        quality_warnings.append("Canonical portfolio Performance context is unavailable for the selected date.")

    statement_nav_base = _safe_float(statement.get("total_nav_base"))
    portfolio_nav_base = _safe_float(portfolio.get("nav"))
    performance_end_nav = _safe_float(performance_summary.get("end_nav"))
    resolved_nav_base = (
        performance_end_nav
        if performance_end_nav is not None
        else statement_nav_base
        if statement_nav_base is not None
        else portfolio_nav_base
    )

    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": str(portfolio.get("portfolio_name") or portfolio_id),
        "base_currency": str(statement.get("base_currency") or portfolio.get("base_currency") or "USD"),
        "as_of_date": as_of_date.isoformat(),
        "target_configuration": "current_snapshot",
        "target_snapshot_fingerprint": (configuration or {}).get("target_snapshot_fingerprint"),
        "lookback_start": lookback_start.isoformat(),
        "lookback_end": as_of_date.isoformat(),
        "nav": resolved_nav_base,
        "holdings_count": len(research_security_positions),
        "planning_group_count": len(planning_groups),
        "chart_label": chart_label,
        "chart_note": chart_note,
        "chart_currency": chart_currency,
        "summary": {
            "period_return": _safe_float(performance_summary.get("cumulative_twr")),
            "annualized_volatility": _safe_float(performance_summary.get("annualized_volatility")),
            "current_drawdown": _safe_float(performance_summary.get("current_drawdown")),
            "max_drawdown": _safe_float(performance_summary.get("max_drawdown")),
            "start_nav": _safe_float(performance_summary.get("start_nav")),
            "end_nav": performance_end_nav if performance_end_nav is not None else resolved_nav_base,
        },
        "planning_target_summary": planning_target_summary,
        "quality_warnings": quality_warnings,
        "chart_points": daily_points,
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
            "value": "Daily",
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
                "trade_constraint": str(
                    (target_gap or {}).get("trade_constraint") or "adjustable"
                ),
                "risk_model_status": str(
                    (target_gap or {}).get("risk_model_status") or "modeled"
                ),
                "research_lifecycle": (target_gap or {}).get("research_lifecycle"),
                "research_eligibility": (target_gap or {}).get("research_eligibility"),
                "research_pm_approved": (target_gap or {}).get("research_pm_approved"),
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
    frequency = "daily"
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
        assumptions.append(
            "Look-through forward RC is recomputed from the solved leaf weights under the portfolio-level leaf covariance. "
            "Because correlation shrinkage is re-estimated at each hierarchy level, look-through RC can differ from a parent sleeve's locally achieved risk-budget share."
        )
    if str(settings_payload.get("target_dimension") or "") != "scope_default":
        assumptions.append("The selected scope can use an explicit target-dimension override; child sleeves still use their own configured default target dimension.")
    if str(settings_payload.get("capital_mode") or "unit_notional") == "target_volatility":
        assumptions.append("After recursive sleeve targets are resolved, Research estimates risky-sleeve volatility, scales gross exposure toward target volatility, and sends the residual into cash.")
    elif str(settings_payload.get("capital_mode") or "unit_notional") == "volatility_cap":
        assumptions.append("After recursive sleeve targets are resolved, Research estimates risky-sleeve volatility and only scales risky exposure down when it exceeds the volatility cap.")
    elif str(settings_payload.get("capital_mode") or "unit_notional") == "fixed_gross":
        assumptions.append("After recursive sleeve targets are resolved, Research applies a fixed gross-exposure overlay and leaves the residual in cash.")
    if any(str(item.get("source_label_override") or "") == "Single Member" for item in target_rows):
        assumptions.append("Single-member sleeves resolve to 100% of that member; multi-member scopes require an active complete SAA/TAA target set.")
    assumptions.extend(RESEARCH_BACKTEST_METHODOLOGY_WARNINGS)
    assumptions.append(
        "Base replay assumptions: "
        f"cash yield {_format_pct(_safe_float(settings_payload.get('backtest_cash_yield_annual')))}, "
        f"commission {_format_number(_safe_float(settings_payload.get('backtest_commission_bps')), 2)} bps, "
        f"sell-side tax {_format_number(_safe_float(settings_payload.get('backtest_tax_bps')), 2)} bps, "
        f"slippage {_format_number(_safe_float(settings_payload.get('backtest_slippage_bps')), 2)} bps, and "
        f"implementation delay {int(_safe_float(settings_payload.get('backtest_implementation_delay_days')) or 0)} calendar day(s)."
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
            f"Valuation and market observations through {context.get('as_of_date')}; "
            "current target weights and historical simulation both use the same captured current "
            "taxonomy, targets and eligibility. Historical market and ledger inputs retain their own dates."
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
        "backtest": deepcopy(solution.get("backtest")),
        "backtest_benchmark": deepcopy(solution.get("backtest_benchmark")),
        "backtest_relative_metrics": deepcopy(solution.get("backtest_relative_metrics")),
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
    portfolio_nav_tape_path = run_root / "portfolio_nav_tape.csv"
    target_weights_path = run_root / "target_weights.csv"
    member_targets_path = run_root / "member_targets.csv"
    leaf_targets_path = run_root / "leaf_targets.csv"
    solve_event_path = run_root / "solve_event.csv"
    scope_solve_events_path = run_root / "scope_solve_events.csv"
    target_weight_gaps_path = run_root / "target_weight_gaps.csv"

    report_warnings = [str(item) for item in list(detail.get("warnings") or []) if str(item).strip()]
    backtest = detail.get("backtest") if isinstance(detail.get("backtest"), dict) else {}
    report_warnings.extend(
        str(item)
        for item in list((backtest or {}).get("warnings") or [])
        if str(item).strip()
    )
    report_warnings = list(dict.fromkeys(report_warnings))

    report_path.write_text(
        "\n".join(
            [
                f"# Research Run `{research_run_id}`",
                "",
                detail.get("headline") or "",
                "",
                detail.get("coverage_note") or "",
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
    _write_csv(portfolio_nav_tape_path, list(context.get("chart_points") or []))
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
        ("portfolio_nav_tape", "Portfolio NAV Tape CSV", portfolio_nav_tape_path),
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
    include_details: bool = False,
) -> dict[str, object] | None:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        return None

    latest_portfolio_as_of_date = _default_as_of_date(portfolio)
    latest_transaction_date = _latest_research_transaction_date(portfolio_id)
    taxonomy_name_map = _taxonomy_name_map(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        record = _ensure_research_settings_record(
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
        configuration = capture_current_target_configuration(
            portfolio_id, str(settings_payload["planning_taxonomy_id"]), session=session,
        ) if settings_payload.get("planning_taxonomy_id") else None
        production_risk_model = get_portfolio_risk_policy(portfolio_id) or {}
        current_planning_state_fingerprint = _planning_state_fingerprint(
            session,
            portfolio_id=portfolio_id,
            planning_taxonomy_id=str(settings_payload.get("planning_taxonomy_id") or "").strip() or None,
            as_of_date=_date_value(settings_payload.get("as_of_date")) or latest_portfolio_as_of_date,
            target_configuration=configuration,
        )
        run_rows = session.scalars(
            select(ResearchRunRecordModel)
            .where(ResearchRunRecordModel.portfolio_id == portfolio_id)
            .order_by(ResearchRunRecordModel.requested_at.desc(), ResearchRunRecordModel.research_run_id.desc())
            .options(
                defer(ResearchRunRecordModel.detail_json),
            )
        ).all()
        reliability_by_run_id = {
            row.research_run_id: _research_run_reliability(
                row,
                latest_portfolio_as_of_date=latest_portfolio_as_of_date,
                settings_payload=settings_payload,
                production_risk_model=production_risk_model,
                latest_transaction_date=latest_transaction_date,
                current_planning_state_fingerprint=current_planning_state_fingerprint,
            )
            for row in run_rows
        }
        runs = [
            _serialize_run_row(
                row,
                taxonomy_name_map,
                include_detail=False,
                reliability_state=reliability_by_run_id[row.research_run_id][0],
                reliability_reasons=reliability_by_run_id[row.research_run_id][1],
            )
            for row in run_rows
        ]
        selected_run_row = None
        explicit_selected_run = False
        if selected_run_id:
            selected_run_row = next(
                (row for row in run_rows if row.research_run_id == selected_run_id),
                None,
            )
            explicit_selected_run = selected_run_row is not None
        if selected_run_row is None and run_rows:
            selected_run_row = run_rows[0]
        selected_run = (
            _serialize_run_row(
                selected_run_row,
                taxonomy_name_map,
                include_detail=include_details or explicit_selected_run,
                reliability_state=reliability_by_run_id[selected_run_row.research_run_id][0],
                reliability_reasons=reliability_by_run_id[selected_run_row.research_run_id][1],
            )
            if selected_run_row is not None
            else None
        )

    risk_lookback_days = int(production_risk_model.get("lookback_days") or settings_payload.get("lookback_days") or 90)
    research_as_of_date = date.fromisoformat(str(settings_payload["as_of_date"]))
    planning_taxonomy_id = str(settings_payload.get("planning_taxonomy_id") or "").strip() or None
    comparator_taxonomy_node_id = str(settings_payload.get("comparator_taxonomy_node_id") or "").strip() or None

    def build_analysis() -> dict[str, object]:
        instrument_detail_cache = valuation_fx.HistoricalInstrumentDetails(
            end_date=research_as_of_date,
        )
        with operation("research_context"):
            context = _build_research_context(
                portfolio_id,
                planning_taxonomy_id=planning_taxonomy_id,
                as_of_date=research_as_of_date,
                target_configuration=configuration,
                lookback_days=risk_lookback_days,
                instrument_detail_cache=instrument_detail_cache,
            )
            frequency = build_research_calculation_frequency_profile(
                portfolio_id,
                planning_taxonomy_id=planning_taxonomy_id,
                comparator_taxonomy_node_id=comparator_taxonomy_node_id,
                as_of_date=research_as_of_date,
                target_configuration=configuration,
                lookback_days=risk_lookback_days,
                _instrument_detail_cache=instrument_detail_cache,
            )
        return {"context": context, "calculation_frequency": frequency}

    # Saved runs, settings and permissions remain live. Only the derived
    # financial context is reused for the same current targets and sources.
    analysis = get_cached_research_analysis(
        portfolio_id,
        planning_state_fingerprint=current_planning_state_fingerprint,
        as_of_date=research_as_of_date,
        lookback_days=risk_lookback_days,
        comparator_taxonomy_node_id=comparator_taxonomy_node_id,
        builder=build_analysis,
    )

    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": str(portfolio.get("portfolio_name") or portfolio_id),
        "base_currency": str(portfolio.get("base_currency") or "USD"),
        "as_of_date": _iso_date(latest_portfolio_as_of_date),
        "default_planning_taxonomy_id": str(portfolio.get("default_planning_taxonomy_id") or "").strip() or None,
        "planning_taxonomy_options": _planning_taxonomy_options(portfolio_id),
        "planning_scope_options": build_research_scope_options(
            portfolio_id,
            planning_taxonomy_id=str(settings_payload.get("planning_taxonomy_id") or "").strip() or None,
            as_of_date=date.fromisoformat(str(settings_payload["as_of_date"])),
            target_configuration=configuration,
        ),
        "calculation_frequency": analysis["calculation_frequency"],
        "settings": settings_payload,
        "risk_policy": production_risk_model,
        "current_context": analysis["context"],
        "instrument_universe": list_portfolio_instrument_universe(portfolio_id),
        "detail_level": (
            "selected_run"
            if selected_run is not None and (include_details or explicit_selected_run)
            else "compact"
        ),
        "runs": runs,
        "selected_run": selected_run,
    }


def update_research_settings(
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
    backtest_rebalance_frequency: object = RESEARCH_SETTINGS_UNSET,
    backtest_benchmark_instrument_id: object = RESEARCH_SETTINGS_UNSET,
    backtest_cash_yield_annual: object = RESEARCH_SETTINGS_UNSET,
    backtest_commission_bps: object = RESEARCH_SETTINGS_UNSET,
    backtest_tax_bps: object = RESEARCH_SETTINGS_UNSET,
    backtest_slippage_bps: object = RESEARCH_SETTINGS_UNSET,
    backtest_implementation_delay_days: object = RESEARCH_SETTINGS_UNSET,
    backtest_robustness_scenarios: object = RESEARCH_SETTINGS_UNSET,
    backtest_walk_forward_training_months: object = RESEARCH_SETTINGS_UNSET,
    backtest_walk_forward_test_months: object = RESEARCH_SETTINGS_UNSET,
    notes: str | None = None,
) -> dict[str, object] | None:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        return None

    latest_portfolio_as_of_date = _default_as_of_date(portfolio)
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
        resolved_as_of_mode = str(as_of_mode or RESEARCH_AS_OF_MODE_DYNAMIC).strip().lower()
        if resolved_as_of_mode not in {RESEARCH_AS_OF_MODE_DYNAMIC, RESEARCH_AS_OF_MODE_PINNED}:
            raise ValueError("Research as-of mode must be dynamic or pinned.")
        if resolved_as_of_mode == RESEARCH_AS_OF_MODE_PINNED and as_of_date is None:
            raise ValueError("Pinned Research settings require an as-of date.")
        row.as_of_mode = resolved_as_of_mode
        row.as_of_date = as_of_date if resolved_as_of_mode == RESEARCH_AS_OF_MODE_PINNED else None
        row.comparator_taxonomy_node_id = resolved_scope_node_id
        row.lookback_days = int(lookback_days or 90)
        row.calculation_frequency = "daily"
        row.missing_return_policy = (missing_return_policy or RESEARCH_DEFAULT_MISSING_RETURN_POLICY).strip() or RESEARCH_DEFAULT_MISSING_RETURN_POLICY
        row.target_dimension = (target_dimension or "scope_default").strip() or "scope_default"
        row.capital_mode = (capital_mode or "unit_notional").strip() or "unit_notional"
        row.gross_exposure = gross_exposure
        row.target_volatility = target_volatility
        row.max_gross_exposure = _normalize_research_max_gross_exposure(row.capital_mode, max_gross_exposure)
        if resolved_frozen_ids is not None:
            row.frozen_taxonomy_node_ids_json = resolved_frozen_ids
        if resolved_top_sleeve_bounds is not None:
            row.top_sleeve_weight_bounds_json = resolved_top_sleeve_bounds
        resolved_rebalance_frequency = (
            row.backtest_rebalance_frequency
            if backtest_rebalance_frequency is RESEARCH_SETTINGS_UNSET
            else backtest_rebalance_frequency
        )
        normalized_rebalance_frequency = str(
            resolved_rebalance_frequency or ""
        ).strip().lower()
        if normalized_rebalance_frequency not in {"1w", "1m", "3m"}:
            raise ValueError("Backtest rebalance frequency must be 1w, 1m, or 3m.")
        resolved_cash_yield = (
            row.backtest_cash_yield_annual
            if backtest_cash_yield_annual is RESEARCH_SETTINGS_UNSET
            else backtest_cash_yield_annual
        )
        resolved_commission_bps = (
            row.backtest_commission_bps
            if backtest_commission_bps is RESEARCH_SETTINGS_UNSET
            else backtest_commission_bps
        )
        resolved_tax_bps = (
            row.backtest_tax_bps
            if backtest_tax_bps is RESEARCH_SETTINGS_UNSET
            else backtest_tax_bps
        )
        resolved_slippage_bps = (
            row.backtest_slippage_bps
            if backtest_slippage_bps is RESEARCH_SETTINGS_UNSET
            else backtest_slippage_bps
        )
        resolved_delay_days = (
            row.backtest_implementation_delay_days
            if backtest_implementation_delay_days is RESEARCH_SETTINGS_UNSET
            else backtest_implementation_delay_days
        )
        resolved_training_months = (
            row.backtest_walk_forward_training_months
            if backtest_walk_forward_training_months is RESEARCH_SETTINGS_UNSET
            else backtest_walk_forward_training_months
        )
        resolved_test_months = (
            row.backtest_walk_forward_test_months
            if backtest_walk_forward_test_months is RESEARCH_SETTINGS_UNSET
            else backtest_walk_forward_test_months
        )
        if not -1.0 <= float(resolved_cash_yield) <= 1.0:
            raise ValueError("Backtest cash yield must be between -100% and 100%.")
        friction_values = (
            resolved_commission_bps,
            resolved_tax_bps,
            resolved_slippage_bps,
        )
        if any(float(value) < 0.0 or float(value) > 1000.0 for value in friction_values):
            raise ValueError("Backtest friction inputs must be between 0 and 1,000 bps.")
        if not 0 <= int(resolved_delay_days) <= 30:
            raise ValueError("Backtest implementation delay must be between 0 and 30 days.")
        if not 1 <= int(resolved_training_months) <= 120:
            raise ValueError("Walk-forward training window must be between 1 and 120 months.")
        if not 1 <= int(resolved_test_months) <= 60:
            raise ValueError("Walk-forward test window must be between 1 and 60 months.")
        row.backtest_rebalance_frequency = normalized_rebalance_frequency
        if backtest_benchmark_instrument_id is not RESEARCH_SETTINGS_UNSET:
            row.backtest_benchmark_instrument_id = (
                str(backtest_benchmark_instrument_id or "").strip() or None
            )
        row.backtest_cash_yield_annual = float(resolved_cash_yield)
        row.backtest_commission_bps = float(resolved_commission_bps)
        row.backtest_tax_bps = float(resolved_tax_bps)
        row.backtest_slippage_bps = float(resolved_slippage_bps)
        row.backtest_implementation_delay_days = int(resolved_delay_days)
        if backtest_robustness_scenarios is not RESEARCH_SETTINGS_UNSET:
            row.backtest_robustness_scenarios_json = (
                _normalized_backtest_robustness_scenarios(
                    backtest_robustness_scenarios
                    if isinstance(backtest_robustness_scenarios, list)
                    else None
                )
            )
        row.backtest_walk_forward_training_months = int(resolved_training_months)
        row.backtest_walk_forward_test_months = int(resolved_test_months)
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


def run_portfolio_research(
    portfolio_id: str,
    *,
    requested_by: str | None = None,
) -> dict[str, object] | None:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        return None

    # This POST explicitly requests a calculation. Finish its snapshot inputs
    # before creating the research run; financial GETs only queue this work.
    _run_portfolio_daily_snapshot_recalculation_synchronously(portfolio_id)
    ensure_portfolio_daily_snapshots(portfolio_id)
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        return None
    latest_portfolio_as_of_date = _default_as_of_date(portfolio)
    taxonomy_name_map = _taxonomy_name_map(portfolio_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        settings_row = _ensure_research_settings_record(
            session,
            portfolio_id,
            default_planning_taxonomy_id=str(portfolio.get("default_planning_taxonomy_id") or "").strip() or None,
            default_as_of_date=latest_portfolio_as_of_date,
        )
        taxonomy = _validate_planning_taxonomy(session, portfolio_id, settings_row.planning_taxonomy_id)
        resolved_scope_node_id = _validate_research_scope(
            session,
            taxonomy=taxonomy,
            comparator_taxonomy_node_id=settings_row.comparator_taxonomy_node_id,
        )
        requested_at = _utc_now_iso()
        run_id = _next_research_run_id(portfolio_id)
        effective_as_of_date = _effective_research_as_of_date(
            settings_row,
            default_as_of_date=latest_portfolio_as_of_date,
        )
        configuration = capture_current_target_configuration(
            portfolio_id, str(settings_row.planning_taxonomy_id), session=session,
        ) if settings_row.planning_taxonomy_id else None
        if not any(item.get("status") == "active" and (item.get("weight_enabled") or item.get("risk_budget_enabled"))
                   for item in (configuration or {}).get("target_sets", [])):
            raise ValueError("Configure active weight or risk-contribution targets for the selected taxonomy before running Research.")
        production_risk_model = get_portfolio_risk_policy(portfolio_id)
        risk_lookback_days = int((production_risk_model or {}).get("lookback_days") or settings_row.lookback_days or 90)
        risk_calculation_frequency = "daily"
        risk_missing_return_policy = str(
            (production_risk_model or {}).get("missing_return_policy")
            or settings_row.missing_return_policy
            or RESEARCH_DEFAULT_MISSING_RETURN_POLICY
        )
        research_capital_mode = settings_row.capital_mode or "unit_notional"
        research_max_gross_exposure = _normalize_research_max_gross_exposure(
            research_capital_mode,
            settings_row.max_gross_exposure,
        )
        planning_state_fingerprint = _planning_state_fingerprint(
            session,
            portfolio_id=portfolio_id,
            planning_taxonomy_id=settings_row.planning_taxonomy_id,
            as_of_date=effective_as_of_date,
            target_configuration=configuration,
        )
        if planning_state_fingerprint is None:
            raise ValueError("The selected planning taxonomy state is unavailable.")
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
                "target_configuration": "current_snapshot",
                "target_snapshot_fingerprint": configuration["target_snapshot_fingerprint"],
                "target_configuration_snapshot": deepcopy(configuration),
                "base_currency": configuration["base_currency"],
                "as_of_mode": _research_as_of_mode(settings_row),
                "lookback_days": risk_lookback_days,
                "calculation_frequency": risk_calculation_frequency,
                "missing_return_policy": risk_missing_return_policy,
                "risk_model": deepcopy(production_risk_model or {}),
                "planning_state_fingerprint_version": RESEARCH_PLANNING_STATE_FINGERPRINT_VERSION,
                "planning_state_fingerprint": planning_state_fingerprint,
                "target_dimension": settings_row.target_dimension or "scope_default",
                "capital_mode": research_capital_mode,
                "gross_exposure": _safe_float(settings_row.gross_exposure),
                "target_volatility": _safe_float(settings_row.target_volatility),
                "max_gross_exposure": research_max_gross_exposure,
                "frozen_taxonomy_node_ids": deepcopy(settings_row.frozen_taxonomy_node_ids_json or []),
                "top_sleeve_weight_bounds": deepcopy(settings_row.top_sleeve_weight_bounds_json or []),
                "backtest_rebalance_frequency": str(settings_row.backtest_rebalance_frequency or "1m"),
                "backtest_benchmark_instrument_id": str(settings_row.backtest_benchmark_instrument_id or "").strip() or None,
                "backtest_cash_yield_annual": float(
                    settings_row.backtest_cash_yield_annual
                ),
                "backtest_commission_bps": float(
                    settings_row.backtest_commission_bps
                ),
                "backtest_tax_bps": float(settings_row.backtest_tax_bps),
                "backtest_slippage_bps": float(
                    settings_row.backtest_slippage_bps
                ),
                "backtest_implementation_delay_days": int(
                    settings_row.backtest_implementation_delay_days
                ),
                "backtest_robustness_scenarios": deepcopy(
                    settings_row.backtest_robustness_scenarios_json or []
                ),
                "backtest_walk_forward_training_months": int(
                    settings_row.backtest_walk_forward_training_months
                ),
                "backtest_walk_forward_test_months": int(
                    settings_row.backtest_walk_forward_test_months
                ),
                "notes": settings_row.notes,
            },
            error_message=None,
        )
        session.add(run_row)
        session.commit()

        try:
            instrument_detail_cache: dict[str, dict[str, object] | None] = {}
            state = _build_taxonomy_state(
                portfolio_id,
                planning_taxonomy_id=str(settings_row.planning_taxonomy_id),
                as_of_date=effective_as_of_date,
                target_configuration=configuration,
                frozen_taxonomy_node_ids=settings_row.frozen_taxonomy_node_ids_json or [],
                top_sleeve_weight_bounds=settings_row.top_sleeve_weight_bounds_json or [],
                instrument_detail_cache=instrument_detail_cache,
            )
            context = _build_research_context(
                portfolio_id,
                planning_taxonomy_id=str(settings_row.planning_taxonomy_id or "").strip() or None,
                as_of_date=effective_as_of_date,
                target_configuration=configuration,
                lookback_days=risk_lookback_days,
                instrument_detail_cache=instrument_detail_cache,
                direct_fx_instruments=state.direct_fx_instruments,
            )
            planning_taxonomy_name = taxonomy_name_map.get(str(settings_row.planning_taxonomy_id or "").strip() or "")
            with operation("portfolio_research_solve", portfolio_id=portfolio_id, run_id=run_id):
                solution = solve_current_target_weights(
                    portfolio_id,
                    planning_taxonomy_id=str(settings_row.planning_taxonomy_id or "").strip(),
                    comparator_taxonomy_node_id=resolved_scope_node_id,
                    as_of_date=effective_as_of_date,
                    target_configuration=configuration,
                    lookback_days=risk_lookback_days,
                    calculation_frequency=risk_calculation_frequency,
                    missing_return_policy=risk_missing_return_policy,
                    target_dimension=settings_row.target_dimension or "scope_default",
                    capital_mode=research_capital_mode,
                    gross_exposure=_safe_float(settings_row.gross_exposure),
                    target_volatility=_safe_float(settings_row.target_volatility),
                    max_gross_exposure=research_max_gross_exposure,
                    frozen_taxonomy_node_ids=deepcopy(settings_row.frozen_taxonomy_node_ids_json or []),
                    top_sleeve_weight_bounds=deepcopy(settings_row.top_sleeve_weight_bounds_json or []),
                    risk_model_config=deepcopy(production_risk_model or {}),
                    _instrument_detail_cache=instrument_detail_cache,
                    _state=state,
                )
            with operation("portfolio_research_backtest", portfolio_id=portfolio_id, run_id=run_id):
                backtest_payload = build_current_target_backtest(
                    portfolio_id,
                    planning_taxonomy_id=str(settings_row.planning_taxonomy_id or "").strip(),
                    comparator_taxonomy_node_id=resolved_scope_node_id,
                    as_of_date=effective_as_of_date,
                    target_configuration=configuration,
                    lookback_days=risk_lookback_days,
                    calculation_frequency=risk_calculation_frequency,
                    missing_return_policy=risk_missing_return_policy,
                    target_dimension=settings_row.target_dimension or "scope_default",
                    capital_mode=research_capital_mode,
                    gross_exposure=_safe_float(settings_row.gross_exposure),
                    target_volatility=_safe_float(settings_row.target_volatility),
                    max_gross_exposure=research_max_gross_exposure,
                    frozen_taxonomy_node_ids=deepcopy(settings_row.frozen_taxonomy_node_ids_json or []),
                    top_sleeve_weight_bounds=deepcopy(settings_row.top_sleeve_weight_bounds_json or []),
                    risk_model_config=deepcopy(production_risk_model or {}),
                    rebalance_frequency=str(settings_row.backtest_rebalance_frequency or "1m"),
                    benchmark_instrument_id=str(settings_row.backtest_benchmark_instrument_id or "").strip() or None,
                    cash_yield_annual=float(settings_row.backtest_cash_yield_annual),
                    commission_bps=float(settings_row.backtest_commission_bps),
                    tax_bps=float(settings_row.backtest_tax_bps),
                    slippage_bps=float(settings_row.backtest_slippage_bps),
                    implementation_delay_days=int(
                        settings_row.backtest_implementation_delay_days
                    ),
                    robustness_scenarios=deepcopy(
                        settings_row.backtest_robustness_scenarios_json or []
                    ),
                    walk_forward_training_months=int(
                        settings_row.backtest_walk_forward_training_months
                    ),
                    walk_forward_test_months=int(
                        settings_row.backtest_walk_forward_test_months
                    ),
                    _instrument_detail_cache=instrument_detail_cache,
                    _state=state,
                )
            solution.update(backtest_payload)
            with session_factory() as verification_session:
                latest_fingerprint = _planning_state_fingerprint(
                    verification_session, portfolio_id=portfolio_id,
                    planning_taxonomy_id=run_row.planning_taxonomy_id,
                    as_of_date=effective_as_of_date,
                )
            if latest_fingerprint != planning_state_fingerprint:
                raise ValueError("Research inputs changed during calculation; rerun with the current inputs.")
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
            retired_run_ids = _prune_portfolio_research_runs(session, portfolio_id, keep_run_id=run_id)
            session.commit()
        except Exception as error:
            # A failed publish must not commit pending pruning or leave the
            # session in SQLAlchemy's failed-transaction state.
            session.rollback()
            failed_run = session.get(ResearchRunRecordModel, run_id)
            if failed_run is not None:
                failed_run.status = "failed"
                failed_run.finished_at = _utc_now_iso()
                failed_run.error_message = str(error)
                failed_run.artifacts_json = []
                session.commit()
            _remove_pruned_research_artifacts(portfolio_id, [run_id])
            raise

        _remove_pruned_research_artifacts(portfolio_id, retired_run_ids)
        return _serialize_run_row(run_row, taxonomy_name_map)


def get_research_run(
    portfolio_id: str,
    *,
    research_run_id: str,
) -> dict[str, object] | None:
    """Return one full run without expanding every run in the workbench list."""

    session_factory = get_session_factory()
    with session_factory() as session:
        row = session.get(ResearchRunRecordModel, research_run_id)
        if row is None or row.portfolio_id != portfolio_id:
            return None
        return _serialize_run_row(row, _taxonomy_name_map(portfolio_id))


def get_research_backtest_benchmark_comparison(
    portfolio_id: str,
    *,
    research_run_id: str,
    benchmark_instrument_id: str,
) -> dict[str, object] | None:
    portfolio = get_portfolio(portfolio_id)
    if portfolio is None:
        return None

    session_factory = get_session_factory()
    with session_factory() as session:
        run_row = session.get(ResearchRunRecordModel, research_run_id)
        if run_row is None or run_row.portfolio_id != portfolio_id:
            return None
        if run_row.status != "completed":
            raise ValueError("Benchmark comparison requires a completed research run.")

        detail = deepcopy(run_row.detail_json or {})
        backtest = detail.get("backtest") if isinstance(detail, dict) else None
        if not isinstance(backtest, dict):
            raise ValueError("Benchmark comparison requires a research run with backtest output.")
        portfolio_points = [
            item for item in list(backtest.get("points") or []) if isinstance(item, dict)
        ]
        if not portfolio_points:
            raise ValueError("Benchmark comparison requires a non-empty backtest series.")

        request_payload = run_row.request_payload_json or {}
        target_snapshot = request_payload.get("target_configuration_snapshot") or {}
        holding_currencies = {str(item["base_currency"]) for item in detail.get("top_holdings", [])
                              if isinstance(item, dict) and item.get("base_currency")}
        saved_currency = target_snapshot.get("base_currency") or request_payload.get("base_currency")
        if not saved_currency and len(holding_currencies) == 1:
            saved_currency = next(iter(holding_currencies))
        currency_warning = None
        if not saved_currency:
            saved_currency = portfolio["base_currency"]
            currency_warning = (
                "This archived run did not record its reporting currency; benchmark comparison "
                f"uses the portfolio's current reporting currency ({saved_currency})."
            )
        comparison = build_research_backtest_benchmark_comparison(
            base_currency=saved_currency,
            as_of_date=run_row.as_of_date or _default_as_of_date(portfolio),
            benchmark_instrument_id=benchmark_instrument_id,
            portfolio_points=portfolio_points,
        )
        if currency_warning and comparison.get("backtest_benchmark"):
            comparison["backtest_benchmark"]["warnings"].append(currency_warning)
        return comparison



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
