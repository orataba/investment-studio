from copy import deepcopy
from datetime import date
from typing import cast

from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy import select

from portfolio_app.services.calculation_frequency import CalculationFrequency
from portfolio_app.api.assemblers import resolve_transaction_net_cash_effect
from portfolio_app.db.models import PortfolioCalculationStateModel, PortfolioDailySnapshotModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.daily_snapshots import (
    DAILY_SNAPSHOT_CALCULATION_VERSION,
    build_materialized_position_holding_projection,
    ensure_portfolio_daily_snapshots,
)
from portfolio_app.services.analytics_scope import (
    resolve_instrument_analytics_scopes,
)
from portfolio_app.services.instrument_charts import (
    HOLDINGS_PRICE_CHART_RANGE_KEYS,
    build_instrument_holdings_market_profile,
    build_instrument_holdings_market_profile_from_detail,
    empty_instrument_holdings_market_profile,
)
from portfolio_app.services.risk_basis import calculation_frequency_profile_for_instruments
from portfolio_app.services.workspace_cache import (
    get_cached_materialized_holdings_workspace,
    preload_portfolio_workspace_cache,
)
from portfolio_app.services.ledger import (
    build_position_lots,
    summarize_position_lots,
)
from portfolio_app.services.instrument_registry import (
    InstrumentRegistryError,
    get_registry_instrument_details,
)
from portfolio_app.services.instrument_event_tasks import (
    instrument_event_task_quality_warnings,
    reconcile_instrument_event_tasks,
)
from portfolio_app.services.performance import (
    build_holdings_report,
    corporate_action_quality_warnings,
)
from portfolio_app.services.holdings_market_profile import (
    is_derivative_contract,
    is_cash_holding_instrument_id,
    is_market_priced_holding,
    is_pending_monetary_holding,
    summarize_holdings_operational_status,
    summarize_holding_day_change,
)
from portfolio_app.services.portfolio_store import (
    get_portfolio,
    get_portfolio_live_summary,
    list_accounts,
    list_transactions,
)
from portfolio_app.services.risk_model import enrich_holdings_forward_risk, get_portfolio_risk_policy
from portfolio_app.services.snapshot_selection import latest_fresh_complete_portfolio_snapshot
from portfolio_app.services.transaction_dates import transaction_cash_activity_date

router = APIRouter()

_HOLDINGS_TREND_FIELD_NAMES = (
    "instrument_trend_as_of_date",
    "instrument_trend_basis",
    "instrument_trend_coverage",
    "instrument_trend_reason",
    "instrument_trend_split_adjusted",
    "instrument_risk_frequency",
    "instrument_return_1w",
    "instrument_return_1m",
    "instrument_return_3m",
    "instrument_return_6m",
    "instrument_return_mtd",
    "instrument_return_ytd",
    "instrument_return_1y",
    "instrument_volatility_1m",
    "instrument_volatility_3m",
    "instrument_volatility_6m",
    "instrument_volatility_1y",
    "instrument_return_series_1m",
    "instrument_return_series_3m",
    "instrument_return_series_6m",
    "instrument_return_series_1y",
    "instrument_return_series_all",
    "instrument_holding_return_series",
    "instrument_current_drawdown",
    "instrument_max_drawdown",
    "instrument_holding_max_drawdown",
    "instrument_holding_start_date",
)
_HOLDINGS_RETURN_SERIES_FIELD_NAMES = (
    "instrument_return_series_1m",
    "instrument_return_series_3m",
    "instrument_return_series_6m",
    "instrument_return_series_1y",
    "instrument_return_series_all",
    "instrument_holding_return_series",
)
_HOLDINGS_CHART_FIELD_NAMES = tuple(
    f"price_chart_{range_key}" for range_key in HOLDINGS_PRICE_CHART_RANGE_KEYS
)
_COMPACT_HOLDINGS_SPARKLINE_POINT_LIMIT = 24
_EVENT_VALUATION_BASES = frozenset({"carried_cost", "premium_liability"})
_CASH_SCOPE_SYSTEM_EXCLUSION_REASON = (
    "Cash and settlement exposure is disclosed outside covariance risk."
)
_DERIVATIVE_SCOPE_SYSTEM_EXCLUSION_REASON = (
    "Derivative contracts are recorded operationally and excluded from market analytics."
)


def _holding_is_derivative(row: dict[str, object]) -> bool:
    holding_kind = str(row.get("holding_kind") or "position").strip().lower()
    return bool(
        row.get("derivative_contract_id")
        or is_derivative_contract(row.get("derivative_contract"))
        or holding_kind in {"derivative_contract", "option_obligation"}
        or str(row.get("holding_category") or "").strip().lower() == "derivatives"
    )


def _market_analytics_valuation_exclusion_reason(row: dict[str, object]) -> str | None:
    """Explain why a policy-eligible row cannot enter modeled market exposure.

    Taxonomy policy is an administrative eligibility assertion.  The holding
    read model still has to satisfy the valuation contract before that
    assertion can affect covariance, risk budget, or ordinary performance.
    Keeping this gate here prevents a generic ``__root__`` policy from
    accidentally re-enabling carried-cost FCN rows or premium liabilities.
    """

    if _holding_is_derivative(row):
        return _DERIVATIVE_SCOPE_SYSTEM_EXCLUSION_REASON

    holding_kind = str(row.get("holding_kind") or "position").strip().lower()
    if holding_kind != "position":
        return f"holding_kind={holding_kind or 'unknown'} is not a market position"
    valuation_basis = str(row.get("valuation_basis") or "").strip().lower()
    if valuation_basis != "market_quote":
        return f"valuation_basis={valuation_basis or 'unknown'} is not market_quote"
    coverage_status = str(row.get("fair_value_coverage_status") or "").strip().lower()
    if coverage_status != "complete":
        return (
            "fair_value_coverage_status="
            f"{coverage_status or 'unknown'} is not complete"
        )
    if row.get("last_price") is None:
        return "last_price is unavailable"
    return None


def _holding_registry_instrument_id(row: dict[str, object]) -> str:
    if _holding_is_derivative(row) or is_pending_monetary_holding(row):
        return ""
    instrument_core = (
        row.get("instrument_core")
        if isinstance(row.get("instrument_core"), dict)
        else {}
    )
    instrument_type = str(instrument_core.get("instrument_type") or "").lower()
    if instrument_type == "cash":
        return ""
    return str(
        instrument_core.get("instrument_id") or row.get("instrument_id") or ""
    ).strip()


def _transaction_has_derivative_contract(
    transaction: dict[str, object],
) -> bool:
    return is_derivative_contract(
        transaction.get("derivative_contract")
    )


def _enrich_holdings_analytics_scope(
    workspace: dict[str, object],
    *,
    portfolio_id: str,
    as_of_date: date,
    transactions: list[dict[str, object]],
) -> dict[str, object]:
    rows = workspace.get("rows")
    if not isinstance(rows, list):
        return workspace
    scope_instrument_ids = [
        instrument_id
        for row in rows
        if isinstance(row, dict)
        and (instrument_id := _holding_registry_instrument_id(row))
    ]
    transaction_instrument_ids = [
        str(transaction.get("instrument_id") or "").strip()
        for transaction in transactions
        if str(transaction.get("instrument_id") or "").strip()
    ]
    scopes = resolve_instrument_analytics_scopes(
        portfolio_id,
        as_of_date=as_of_date,
        instrument_ids=[*scope_instrument_ids, *transaction_instrument_ids],
    )

    excluded_rows: list[dict[str, object]] = []
    modeled_net_exposure = 0.0
    modeled_gross_exposure = 0.0
    excluded_carrying_value = 0.0
    excluded_liability = 0.0
    cash_unallocated_exposure = 0.0
    policy_versions: set[int] = set()
    configuration_versions: set[int] = set()
    taxonomy_selection_versions: set[int] = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        instrument_core = (
            row.get("instrument_core")
            if isinstance(row.get("instrument_core"), dict)
            else {}
        )
        instrument_type = str(instrument_core.get("instrument_type") or "").lower()
        instrument_id = _holding_registry_instrument_id(row)
        is_derivative = _holding_is_derivative(row)
        is_cash_or_settlement = (
            instrument_type == "cash"
            or is_pending_monetary_holding(row)
            or is_cash_holding_instrument_id(
                instrument_core.get("instrument_id") or row.get("line_id")
            )
        )
        scope = scopes.get(instrument_id) if instrument_id else None
        if is_derivative:
            scope = {
                "scope_status": "system_excluded",
                "taxonomy_id": None,
                "taxonomy_node_id": None,
                "resolved_policy_node_id": None,
                "inherited_from_node_id": None,
                "analytics_scope_policy_id": None,
                "scope_policy_version": None,
                "configuration_version": None,
                "taxonomy_selection_version": None,
                "risk_eligible": False,
                "risk_budget_eligible": False,
                "performance_scope": "derivative_lifecycle",
                "valuation_basis_policy": "event_accounting",
                "exclusion_reason": _DERIVATIVE_SCOPE_SYSTEM_EXCLUSION_REASON,
            }
        elif scope is None:
            scope = {
                "scope_status": "cash_or_settlement" if is_cash_or_settlement else "missing",
                "taxonomy_id": None,
                "taxonomy_node_id": None,
                "resolved_policy_node_id": None,
                "inherited_from_node_id": None,
                "analytics_scope_policy_id": None,
                "scope_policy_version": None,
                "configuration_version": None,
                "taxonomy_selection_version": None,
                "risk_eligible": False,
                "risk_budget_eligible": False,
                "performance_scope": "unallocated",
                "valuation_basis_policy": "cash" if is_cash_or_settlement else "unknown",
                "exclusion_reason": (
                    _CASH_SCOPE_SYSTEM_EXCLUSION_REASON
                    if is_cash_or_settlement
                    else "No effective analytics scope assignment or policy."
                ),
            }
        row.update(
            {
                field_name: field_value
                for field_name, field_value in scope.items()
                if field_name != "instrument_id"
            }
        )
        policy_performance_scope = str(scope.get("performance_scope") or "unallocated")
        policy_performance_eligible = policy_performance_scope == "ordinary"
        policy_risk_eligible = bool(scope.get("risk_eligible"))
        policy_risk_budget_eligible = bool(scope.get("risk_budget_eligible"))
        valuation_exclusion_reason = (
            _CASH_SCOPE_SYSTEM_EXCLUSION_REASON
            if is_cash_or_settlement
            else _market_analytics_valuation_exclusion_reason(row)
        )
        valuation_contract_eligible = valuation_exclusion_reason is None

        # Preserve the resolved policy separately from the effective runtime
        # scope.  A policy may say ``ordinary`` while the system valuation
        # contract still excludes this particular row.
        row["analytics_scope_policy"] = policy_performance_scope
        row["analytics_scope_valuation_eligible"] = valuation_contract_eligible
        row["analytics_scope_system_exclusion_reason"] = valuation_exclusion_reason
        if (
            valuation_exclusion_reason is not None
            and policy_performance_scope == "ordinary"
            and not is_cash_or_settlement
        ):
            row["exclusion_reason"] = (
                "System valuation gate: " + valuation_exclusion_reason + "."
            )
            effective_performance_scope = "operational_only"
        else:
            effective_performance_scope = policy_performance_scope
        row["analytics_scope"] = effective_performance_scope
        row["performance_scope"] = effective_performance_scope
        row["performance_eligible"] = (
            policy_performance_eligible
            and valuation_contract_eligible
            and not is_cash_or_settlement
        )
        row["risk_eligible"] = (
            policy_risk_eligible
            and valuation_contract_eligible
            and not is_cash_or_settlement
        )
        row["risk_budget_eligible"] = (
            policy_risk_budget_eligible
            and valuation_contract_eligible
            and not is_cash_or_settlement
        )

        policy_version = scope.get("scope_policy_version")
        if isinstance(policy_version, int):
            policy_versions.add(policy_version)
        configuration_version = scope.get("configuration_version")
        if isinstance(configuration_version, int):
            configuration_versions.add(configuration_version)
        taxonomy_selection_version = scope.get("taxonomy_selection_version")
        if isinstance(taxonomy_selection_version, int):
            taxonomy_selection_versions.add(taxonomy_selection_version)

        holding_kind = str(row.get("holding_kind") or "position")
        if is_cash_or_settlement:
            holding_category = "cash_and_settlement"
        elif is_derivative:
            holding_category = "derivatives"
        else:
            holding_category = "securities"
        row["holding_category"] = holding_category

        exposure = (
            float(row["market_value_base"])
            if row.get("market_value_base") is not None
            else float(row["carrying_value_base"])
            if row.get("carrying_value_base") is not None
            else float(row["liability_value_base"])
            if row.get("liability_value_base") is not None
            else 0.0
        )
        if is_cash_or_settlement:
            cash_unallocated_exposure += exposure
        elif row["risk_eligible"]:
            modeled_net_exposure += exposure
            modeled_gross_exposure += abs(exposure)
        else:
            is_liability = bool(row.get("is_liability")) or exposure < 0
            if is_liability:
                excluded_liability += abs(exposure)
            else:
                excluded_carrying_value += max(exposure, 0.0)
            if abs(exposure) > 1e-9:
                excluded_rows.append(
                    {
                        "line_id": row.get("line_id"),
                        "instrument_id": instrument_core.get("instrument_id"),
                        "instrument_name": instrument_core.get("instrument_name"),
                        "holding_category": holding_category,
                        "exposure_base": exposure,
                        "exclusion_reason": row.get("exclusion_reason"),
                        "scope_status": row.get("scope_status"),
                    }
                )

    # Transaction cash effects are denominated in each transaction's local
    # currency.  Never add those amounts across currencies; this disclosure
    # is intentionally local-currency activity, not a base-currency ledger.
    cash_scope_breakdown: dict[tuple[str, str], dict[str, float]] = {}
    for transaction in transactions:
        transaction_date = transaction_cash_activity_date(transaction)
        if transaction_date is not None and transaction_date > as_of_date:
            continue
        cash_effect = resolve_transaction_net_cash_effect(transaction)
        if cash_effect is None or abs(cash_effect) <= 1e-12:
            continue
        transaction_instrument_id = str(transaction.get("instrument_id") or "").strip()
        transaction_scope = scopes.get(transaction_instrument_id, {})
        performance_scope = (
            "derivative_lifecycle"
            if _transaction_has_derivative_contract(transaction)
            else str(transaction_scope.get("performance_scope") or "unallocated")
        )
        transaction_currency = str(
            transaction.get("currency") or ""
        ).strip().upper()
        if not transaction_currency:
            # Canonical transactions require currency.  Should an imported or
            # historical row violate that contract, keep it isolated instead
            # of silently mixing it with a real currency bucket.
            transaction_currency = "UNKNOWN"
        bucket = cash_scope_breakdown.setdefault(
            (performance_scope, transaction_currency),
            {"net_cash_effect": 0.0, "absolute_cash_activity": 0.0},
        )
        bucket["net_cash_effect"] += float(cash_effect)
        bucket["absolute_cash_activity"] += abs(float(cash_effect))

    totals = workspace.get("totals") if isinstance(workspace.get("totals"), dict) else {}
    total_nav = (
        float(totals["nav"])
        if totals.get("nav") is not None
        else None
    )
    coverage_denominator = (
        modeled_gross_exposure
        + excluded_carrying_value
        + excluded_liability
        + abs(cash_unallocated_exposure)
    )
    workspace["analytics_scope_summary"] = {
        "scope_name": "Modeled Market Sleeve",
        "scope_policy_versions": sorted(policy_versions),
        "configuration_versions": sorted(configuration_versions),
        "taxonomy_selection_versions": sorted(taxonomy_selection_versions),
        "total_nav": total_nav,
        "modeled_net_exposure": modeled_net_exposure,
        "modeled_gross_exposure": modeled_gross_exposure,
        "excluded_carrying_value": excluded_carrying_value,
        "excluded_liability": excluded_liability,
        "cash_unallocated_exposure": cash_unallocated_exposure,
        "coverage_ratio": (
            modeled_gross_exposure / coverage_denominator
            if coverage_denominator > 1e-12
            else None
        ),
        "excluded_rows": excluded_rows,
        "cash_scope_breakdown": [
            {
                "performance_scope": performance_scope,
                "currency": transaction_currency,
                **value,
            }
            for (
                performance_scope,
                transaction_currency,
            ), value in sorted(cash_scope_breakdown.items())
        ],
        "ordinary_sleeve_twr_status": "unavailable",
        "ordinary_sleeve_twr_reason": (
            "Sleeve boundary cash flows are not yet maintained as a cash subledger."
        ),
    }
    return workspace


def _compact_sparkline_points(value: object) -> list[object]:
    points = value if isinstance(value, list) else []
    if len(points) <= _COMPACT_HOLDINGS_SPARKLINE_POINT_LIMIT:
        return list(points)
    last_index = len(points) - 1
    selected_indices = sorted(
        {
            round(
                position
                * last_index
                / (_COMPACT_HOLDINGS_SPARKLINE_POINT_LIMIT - 1)
            )
            for position in range(_COMPACT_HOLDINGS_SPARKLINE_POINT_LIMIT)
        }
    )
    return [points[index] for index in selected_indices]


def _public_holdings_workspace_response(
    workspace: dict[str, object],
    *,
    include_details: bool,
    transactions: list[dict[str, object]],
    as_of_date: date,
) -> dict[str, object]:
    rows = workspace.get("rows")
    row_items = rows if isinstance(rows, list) else []
    instrument_types = {
        str(instrument_core.get("instrument_type") or "").strip().lower()
        for row in row_items
        if isinstance(row, dict)
        and isinstance((instrument_core := row.get("instrument_core")), dict)
    }
    instrument_ids = set(_instrument_ids_from_holdings_workspace(workspace))
    portfolio_id = str(workspace.get("portfolio_id") or "").strip()
    if portfolio_id:
        reconcile_instrument_event_tasks(
            portfolio_ids=[portfolio_id],
            instrument_ids=instrument_ids,
        )
    workspace["quality_warnings"] = (
        corporate_action_quality_warnings(
            instrument_types,
            instrument_ids,
            transactions=transactions,
            as_of_date=as_of_date,
        )
        + (
            instrument_event_task_quality_warnings(portfolio_id)
            if portfolio_id
            else []
        )
    )
    workspace.update(
        summarize_holdings_operational_status(
            [row for row in row_items if isinstance(row, dict)],
            as_of_date=as_of_date,
        )
    )
    if include_details:
        workspace["detail_level"] = "full"
        return workspace
    workspace["detail_level"] = "compact"
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            compact_sparkline = _compact_sparkline_points(row.get("price_chart_6m"))
            for field_name in _HOLDINGS_CHART_FIELD_NAMES:
                row[field_name] = []
            row["price_chart_6m"] = compact_sparkline
            for field_name in _HOLDINGS_RETURN_SERIES_FIELD_NAMES:
                row.pop(field_name, None)
    return workspace


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return date.fromisoformat(normalized[:10])
        except ValueError:
            return None
    return None


def _holding_requires_empty_market_profile(row: dict[str, object]) -> bool:
    return bool(
        row.get("risk_eligible") is False
        or str(row.get("valuation_basis") or "").strip().lower()
        in _EVENT_VALUATION_BASES
        or str(row.get("holding_kind") or "position").strip().lower()
        == "option_obligation"
    )


def _replace_with_empty_market_profile(
    row: dict[str, object],
    *,
    calculation_frequency: CalculationFrequency,
    holding_start_date: date | None = None,
) -> None:
    resolved_holding_start_date = holding_start_date or _parse_iso_date(
        row.get("instrument_holding_start_date")
    )
    row.pop("price_chart", None)
    row.update(
        empty_instrument_holdings_market_profile(
            holding_start_date=resolved_holding_start_date,
            calculation_frequency=calculation_frequency,
        )
    )


def _clear_ineligible_market_profiles(
    workspace: dict[str, object],
    *,
    calculation_frequency: CalculationFrequency,
) -> None:
    rows = workspace.get("rows")
    if not isinstance(rows, list):
        return
    for row in rows:
        if isinstance(row, dict) and _holding_requires_empty_market_profile(row):
            _replace_with_empty_market_profile(
                row,
                calculation_frequency=calculation_frequency,
            )


def _holding_start_dates_by_instrument(position_lots: list[dict[str, object]]) -> dict[str, date]:
    start_dates: dict[str, date] = {}
    for position_lot in position_lots:
        if str(position_lot.get("status") or "") != "open":
            continue
        instrument_id = str(position_lot.get("instrument_id") or "")
        if not instrument_id:
            continue
        holding_start_date = _parse_iso_date(position_lot.get("acquisition_date")) or _parse_iso_date(
            position_lot.get("opened_at")
        )
        if holding_start_date is None:
            continue
        current_start_date = start_dates.get(instrument_id)
        if current_start_date is None or holding_start_date < current_start_date:
            start_dates[instrument_id] = holding_start_date
    return start_dates


def _instrument_ids_from_holdings_workspace(workspace: dict[str, object]) -> list[str]:
    instrument_ids: list[str] = []
    rows = workspace.get("rows")
    if not isinstance(rows, list):
        return instrument_ids
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _holding_is_derivative(row) or is_pending_monetary_holding(row):
            continue
        instrument_core = row.get("instrument_core") if isinstance(row.get("instrument_core"), dict) else {}
        if (
            str(instrument_core.get("instrument_type") or "").strip().lower() == "cash"
            or is_cash_holding_instrument_id(instrument_core.get("instrument_id") or row.get("line_id"))
        ):
            continue
        instrument_id = str(
            instrument_core.get("instrument_id") or row.get("instrument_id") or ""
        ).strip()
        if instrument_id and instrument_id not in instrument_ids:
            instrument_ids.append(instrument_id)
    return instrument_ids


def _holdings_workspace_has_market_profile(
    workspace: dict[str, object],
    *,
    calculation_frequency: CalculationFrequency,
) -> bool:
    rows = workspace.get("rows")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            return False
        for range_key in HOLDINGS_PRICE_CHART_RANGE_KEYS:
            if not isinstance(row.get(f"price_chart_{range_key}"), list):
                return False
        for field_name in _HOLDINGS_TREND_FIELD_NAMES:
            if field_name not in row:
                return False
        if str(row.get("instrument_risk_frequency") or "") != calculation_frequency:
            return False
    return True


def _materialized_holdings_workspace_response(
    workspace: dict[str, object],
    *,
    risk_basis_profile: dict[str, object],
) -> dict[str, object]:
    response = deepcopy(workspace)
    response.pop("price_chart_range", None)
    response["risk_basis"] = risk_basis_profile
    calculation_frequency = cast(
        CalculationFrequency,
        str(risk_basis_profile.get("resolved_frequency") or "daily"),
    )
    _clear_ineligible_market_profiles(
        response,
        calculation_frequency=calculation_frequency,
    )
    return response


def _enrich_holdings_workspace_market_data(
    workspace: dict[str, object],
    *,
    as_of_date: date,
    position_lots: list[dict[str, object]],
    risk_basis_profile: dict[str, object],
    instrument_details: dict[str, dict[str, object] | None],
) -> dict[str, object]:
    enriched_workspace = deepcopy(workspace)
    enriched_workspace.pop("price_chart_range", None)
    enriched_workspace["risk_basis"] = risk_basis_profile
    calculation_frequency = cast(CalculationFrequency, str(risk_basis_profile.get("resolved_frequency") or "daily"))
    holding_start_dates = _holding_start_dates_by_instrument(position_lots)
    rows = enriched_workspace.get("rows")
    if not isinstance(rows, list):
        return enriched_workspace

    for row in rows:
        if not isinstance(row, dict):
            continue
        instrument_core = row.get("instrument_core") if isinstance(row.get("instrument_core"), dict) else {}
        instrument_id = str(instrument_core.get("instrument_id") or row.get("line_id") or "")
        if (
            is_pending_monetary_holding(row)
            or str(instrument_core.get("instrument_type") or "").strip().lower()
            == "cash"
            or is_cash_holding_instrument_id(instrument_id)
            or _holding_requires_empty_market_profile(row)
        ):
            _replace_with_empty_market_profile(
                row,
                calculation_frequency=calculation_frequency,
                holding_start_date=holding_start_dates.get(instrument_id),
            )
            continue
        if not instrument_id:
            _replace_with_empty_market_profile(
                row,
                calculation_frequency=calculation_frequency,
            )
            continue
        holding_start_date = holding_start_dates.get(instrument_id)
        row.pop("price_chart", None)
        detail = instrument_details.get(instrument_id)
        if isinstance(detail, dict):
            row.update(
                build_instrument_holdings_market_profile_from_detail(
                    detail,
                    instrument_id=instrument_id,
                    as_of_date=as_of_date,
                    holding_start_date=holding_start_date,
                    calculation_frequency=calculation_frequency,
                )
            )
        else:
            row.update(
                empty_instrument_holdings_market_profile(
                    holding_start_date=holding_start_date,
                    calculation_frequency=calculation_frequency,
                )
            )
    return enriched_workspace


def _materialized_summary_is_current(portfolio_id: str) -> bool:
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        if state is None or state.daily_snapshot_status != "current":
            return False
        payload = session.scalar(
            select(PortfolioDailySnapshotModel.snapshot_json)
            .where(PortfolioDailySnapshotModel.portfolio_id == portfolio_id)
            .order_by(PortfolioDailySnapshotModel.as_of_date.desc())
            .limit(1)
        )
        if not isinstance(payload, dict):
            return False
        return (
            str(payload.get("calculation_version") or "") == DAILY_SNAPSHOT_CALCULATION_VERSION
            and latest_fresh_complete_portfolio_snapshot(session, portfolio_id) is not None
        )


def _require_portfolio(portfolio_id: str | None, *, live_if_materialized_stale: bool = False) -> dict[str, object]:
    if not portfolio_id:
        raise HTTPException(status_code=400, detail="portfolio_id is required")
    if live_if_materialized_stale:
        try:
            ensure_portfolio_daily_snapshots(portfolio_id)
        except InstrumentRegistryError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
    if live_if_materialized_stale and not _materialized_summary_is_current(portfolio_id):
        resolved_portfolio = get_portfolio_live_summary(portfolio_id)
    else:
        resolved_portfolio = get_portfolio(portfolio_id)
    if resolved_portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return resolved_portfolio


def _portfolio_calculation_frequency_status(portfolio: dict[str, object], *, as_of_date: date) -> str:
    try:
        return str(_portfolio_calculation_frequency_profile(portfolio, as_of_date=as_of_date)["status_label"])
    except InstrumentRegistryError:
        return "Risk basis unavailable"


def _portfolio_calculation_frequency_profile(portfolio: dict[str, object], *, as_of_date: date) -> dict[str, object]:
    portfolio_id = str(portfolio.get("portfolio_id") or "")
    if not portfolio_id:
        return calculation_frequency_profile_for_instruments([], end_date=as_of_date)

    materialized_workspace = get_cached_materialized_holdings_workspace(
        portfolio_id,
        as_of_date=as_of_date,
    )
    if isinstance(materialized_workspace, dict):
        instrument_ids = _instrument_ids_from_holdings_workspace(materialized_workspace)
    else:
        instrument_ids = []
        accounts = list_accounts(portfolio_id)
        position_lots = build_position_lots(
            portfolio_id,
            accounts,
            list_transactions(portfolio_id, end_date=as_of_date),
            as_of_date=as_of_date,
        )
        for position_lot in position_lots:
            if str(position_lot.get("status") or "") != "open":
                continue
            instrument_id = str(position_lot.get("instrument_id") or "").strip()
            if instrument_id and instrument_id not in instrument_ids:
                instrument_ids.append(instrument_id)
    return calculation_frequency_profile_for_instruments(instrument_ids, end_date=as_of_date)


@router.get("/summary")
def workspace_summary(portfolio_id: str | None = None) -> dict[str, object]:
    resolved_portfolio = _require_portfolio(portfolio_id, live_if_materialized_stale=True)

    as_of_date = str(resolved_portfolio.get("as_of_date") or date.today().isoformat())
    parsed_as_of_date = date.fromisoformat(as_of_date)
    calculation_frequency_status = _portfolio_calculation_frequency_status(
        resolved_portfolio,
        as_of_date=parsed_as_of_date,
    )
    return {
        "portfolio_id": resolved_portfolio["portfolio_id"],
        "portfolio_name": resolved_portfolio["portfolio_name"],
        "base_currency": resolved_portfolio.get("base_currency", "USD"),
        "as_of_date": as_of_date,
        "nav": resolved_portfolio.get("nav", 0.0),
        "day_change_value": resolved_portfolio.get("day_change_value", 0.0),
        "day_change_pct": resolved_portfolio.get("day_change_pct", 0.0),
        "default_planning_taxonomy_id": resolved_portfolio.get("default_planning_taxonomy_id"),
        "toolbar_label": "View: Portfolio Summary",
        "badges": [
            calculation_frequency_status,
            "Ledger and performance kernel live",
            "Planning taxonomy and target sets live",
            "Current research target-weight solve live",
        ],
        "sections": [
            {"label": "Holdings", "href": "/holdings", "status": "api-backed"},
            {"label": "Performance", "href": "/performance", "status": "workspace-backed"},
            {"label": "Risk", "href": "/risk", "status": "workspace-backed"},
            {"label": "Transactions", "href": "/transactions", "status": "api-backed"},
            {"label": "Accounts", "href": "/accounts", "status": "api-backed"},
            {"label": "Taxonomies", "href": "/taxonomies", "status": "workspace-backed"},
            {"label": "Research", "href": "/research", "status": "workspace-backed"},
        ],
    }


@router.post("/preload")
def preload_workspace(
    background_tasks: BackgroundTasks,
    portfolio_id: str | None = None,
) -> dict[str, object]:
    resolved_portfolio = _require_portfolio(portfolio_id)
    resolved_portfolio_id = str(resolved_portfolio["portfolio_id"])
    warmed_surfaces = ["performance"]
    background_tasks.add_task(preload_portfolio_workspace_cache, resolved_portfolio_id)
    return {
        "portfolio_id": resolved_portfolio_id,
        "status": "queued",
        "warmed_surfaces": warmed_surfaces,
    }


@router.get("/holdings")
def holdings_workspace(
    portfolio_id: str | None = None,
    as_of_date: date | None = None,
    include_details: bool = False,
) -> dict[str, object]:
    resolved_portfolio = _require_portfolio(portfolio_id, live_if_materialized_stale=as_of_date is None)

    portfolio_as_of_date = (
        date.fromisoformat(str(resolved_portfolio.get("as_of_date")))
        if resolved_portfolio.get("as_of_date")
        else None
    )
    resolved_as_of_date = as_of_date or portfolio_as_of_date or date.today()
    resolved_portfolio_id = str(resolved_portfolio["portfolio_id"])
    transactions = list_transactions(resolved_portfolio_id)
    risk_policy = get_portfolio_risk_policy(resolved_portfolio_id)
    requested_risk_frequency = str((risk_policy or {}).get("calculation_frequency") or "auto")
    materialized_workspace = get_cached_materialized_holdings_workspace(
        resolved_portfolio_id,
        as_of_date=resolved_as_of_date,
    )
    if materialized_workspace is not None:
        try:
            instrument_ids = _instrument_ids_from_holdings_workspace(materialized_workspace)
            instrument_details = get_registry_instrument_details(instrument_ids)
            risk_basis_profile = calculation_frequency_profile_for_instruments(
                instrument_ids,
                end_date=resolved_as_of_date,
                requested_frequency=requested_risk_frequency,
                detail_loader=instrument_details.get,
            )
            calculation_frequency = cast(CalculationFrequency, str(risk_basis_profile.get("resolved_frequency") or "daily"))
            if _holdings_workspace_has_market_profile(
                materialized_workspace,
                calculation_frequency=calculation_frequency,
            ):
                response = _materialized_holdings_workspace_response(
                    materialized_workspace,
                    risk_basis_profile=risk_basis_profile,
                )
                scoped_response = _enrich_holdings_analytics_scope(
                    response,
                    portfolio_id=resolved_portfolio_id,
                    as_of_date=resolved_as_of_date,
                    transactions=transactions,
                )
                enriched_response = enrich_holdings_forward_risk(
                    scoped_response,
                    as_of_date=resolved_as_of_date,
                    calculation_frequency=calculation_frequency,
                    risk_policy=risk_policy or {},
                )
                return _public_holdings_workspace_response(
                    enriched_response,
                    include_details=include_details,
                    transactions=transactions,
                    as_of_date=resolved_as_of_date,
                )
            accounts = list_accounts(resolved_portfolio_id)
            position_lots = build_position_lots(
                resolved_portfolio_id,
                accounts,
                list_transactions(
                    resolved_portfolio_id,
                    end_date=resolved_as_of_date,
                ),
                as_of_date=resolved_as_of_date,
            )
            response = _enrich_holdings_workspace_market_data(
                materialized_workspace,
                as_of_date=resolved_as_of_date,
                position_lots=position_lots,
                risk_basis_profile=risk_basis_profile,
                instrument_details=instrument_details,
            )
            scoped_response = _enrich_holdings_analytics_scope(
                response,
                portfolio_id=resolved_portfolio_id,
                as_of_date=resolved_as_of_date,
                transactions=transactions,
            )
            enriched_response = enrich_holdings_forward_risk(
                scoped_response,
                as_of_date=resolved_as_of_date,
                calculation_frequency=calculation_frequency,
                risk_policy=risk_policy or {},
            )
            return _public_holdings_workspace_response(
                enriched_response,
                include_details=include_details,
                transactions=transactions,
                as_of_date=resolved_as_of_date,
            )
        except InstrumentRegistryError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    accounts = list_accounts(resolved_portfolio_id)
    try:
        position_lots = build_position_lots(
            resolved_portfolio_id,
            accounts,
            list_transactions(
                resolved_portfolio_id,
                end_date=resolved_as_of_date,
            ),
            as_of_date=resolved_as_of_date,
        )
        holding_start_dates = _holding_start_dates_by_instrument(position_lots)
        instrument_ids = []
        for position_lot in position_lots:
            if str(position_lot.get("status") or "") != "open":
                continue
            instrument_id = str(position_lot.get("instrument_id") or "").strip()
            if instrument_id and instrument_id not in instrument_ids:
                instrument_ids.append(instrument_id)
        instrument_details = get_registry_instrument_details(instrument_ids)
        risk_basis_profile = calculation_frequency_profile_for_instruments(
            instrument_ids,
            end_date=resolved_as_of_date,
            requested_frequency=requested_risk_frequency,
            detail_loader=instrument_details.get,
        )
        calculation_frequency = cast(CalculationFrequency, str(risk_basis_profile.get("resolved_frequency") or "daily"))
        statement = build_holdings_report(
            resolved_portfolio,
            accounts,
            transactions,
            as_of_date=resolved_as_of_date,
            include_cash_rows=True,
            calculation_frequency=calculation_frequency,
            instrument_detail_cache={
                instrument_id: detail
                for instrument_id, detail in instrument_details.items()
                if isinstance(detail, dict)
            },
        )
        market_profile_by_instrument = {
            instrument_id: build_instrument_holdings_market_profile_from_detail(
                detail,
                instrument_id=instrument_id,
                as_of_date=resolved_as_of_date,
                holding_start_date=holding_start_dates.get(instrument_id),
                calculation_frequency=calculation_frequency,
            )
            for position in statement.get("positions", [])
            if (instrument_id := str(position.get("instrument_id") or ""))
            and not is_cash_holding_instrument_id(instrument_id)
            and not is_pending_monetary_holding(position)
            and not _holding_requires_empty_market_profile(position)
            and isinstance((detail := instrument_details.get(instrument_id)), dict)
        }
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    positions = list(statement["positions"])
    formal_positions = [
        position
        for position in positions
        if str(position.get("holding_kind") or "position") == "position"
    ]
    position_count = len(formal_positions)
    priced_position_count = sum(
        1
        for position in formal_positions
        if is_market_priced_holding(position)
    )
    position_lot_summary = summarize_position_lots(position_lots)

    def market_profile_for_position(position: dict[str, object]) -> dict[str, object]:
        instrument_id = str(position.get("instrument_id") or "")
        if _holding_requires_empty_market_profile(position):
            return empty_instrument_holdings_market_profile(
                holding_start_date=holding_start_dates.get(instrument_id),
                calculation_frequency=calculation_frequency,
            )
        if (
            is_cash_holding_instrument_id(instrument_id)
            or is_pending_monetary_holding(position)
        ):
            profile = {
                f"price_chart_{range_key}": (
                    position.get(f"price_chart_{range_key}")
                    if isinstance(position.get(f"price_chart_{range_key}"), list)
                    else []
                )
                for range_key in HOLDINGS_PRICE_CHART_RANGE_KEYS
            }
            for field_name in _HOLDINGS_TREND_FIELD_NAMES:
                profile[field_name] = position.get(field_name)
            profile["instrument_holding_start_date"] = position.get("instrument_holding_start_date")
            profile["instrument_trend_as_of_date"] = position.get("instrument_trend_as_of_date")
            profile["instrument_trend_basis"] = position.get("instrument_trend_basis")
            profile["instrument_risk_frequency"] = position.get("instrument_risk_frequency") or calculation_frequency
            return profile
        return (
            market_profile_by_instrument[instrument_id]
            if instrument_id
            and instrument_id in market_profile_by_instrument
            else empty_instrument_holdings_market_profile(calculation_frequency=calculation_frequency)
        )

    rows = [
        {
            "line_id": str(position.get("position_id") or position.get("instrument_id") or ""),
            "holding_kind": position.get("holding_kind") or "position",
            "available_for_trading": position.get("available_for_trading", True),
            "economic_instrument_id": position.get("economic_instrument_id"),
            "economic_instrument_ref": position.get("economic_instrument_ref"),
            "transaction_ids": list(position.get("transaction_ids") or []),
            "instrument_core": position["instrument_ref"],
            "quantity": position["quantity"],
            "last_price": position.get("last_price"),
            "quote_as_of_date": position.get("quote_as_of_date"),
            "quote_metric_family": position.get("quote_metric_family"),
            "quote_basis": position.get("quote_basis"),
            "quote_provider": position.get("quote_provider"),
            "quote_status": position.get("quote_status"),
            "market_value": position.get("market_value"),
            "market_value_base": position.get("market_value_base"),
            "day_change_pct": position.get("day_change_pct"),
            "day_change_value": position.get("day_change_value"),
            "day_change_value_base": position.get("day_change_value_base"),
            "cost_basis_method": position.get("cost_basis_method"),
            "cost_basis": position.get("cost_basis"),
            "cost_basis_base": position.get("cost_basis_base"),
            "allocation": position.get("portfolio_weight"),
            **market_profile_for_position(position),
            "coverage_status": position.get("coverage_status")
            or ("price-nav-fx" if position.get("market_value_base") is not None else "unpriced"),
            "account_ids": [
                str(account_id)
                for account_id in list(position.get("account_ids") or [])
                if str(account_id or "")
            ],
            "account_count": int(position.get("account_count") or 0),
            "open_position_lot_count": int(position.get("open_position_lot_count") or 0),
            "is_liability": bool(position.get("is_liability", False)),
            "performance_eligible": bool(position.get("performance_eligible", True)),
            "risk_eligible": bool(position.get("risk_eligible", True)),
            "open_contract_quantity": position.get("open_contract_quantity"),
            "required_underlying_quantity": position.get(
                "required_underlying_quantity"
            ),
            "obligation_status": position.get("obligation_status"),
            "related_underlying_id": position.get("related_underlying_id"),
            "expiry_date": position.get("expiry_date"),
            "days_to_expiry": position.get("days_to_expiry"),
            "strike": position.get("strike"),
            "option_type": position.get("option_type"),
            "contract_multiplier": position.get("contract_multiplier"),
            "strike_notional": position.get("strike_notional"),
            "strike_notional_base": position.get("strike_notional_base"),
            "premium_received_gross": position.get("premium_received_gross"),
            "premium_basis_remaining": position.get("premium_basis_remaining"),
            "liability_value": position.get("liability_value"),
            "liability_value_base": position.get("liability_value_base"),
            "carrying_value": position.get("carrying_value"),
            "carrying_value_base": position.get("carrying_value_base"),
            "fair_value": position.get("fair_value"),
            "fair_value_coverage_status": position.get("fair_value_coverage_status"),
            "valuation_basis": position.get("valuation_basis"),
            "settlement_date": position.get("settlement_date"),
            "pending_until_date": position.get("pending_until_date"),
            "pending_status": position.get("pending_status"),
            "settlement_amount": position.get("settlement_amount"),
            "settlement_amount_base": position.get("settlement_amount_base"),
        }
        for position in positions
    ]
    total_market_value_base = statement.get("total_market_value_base")
    total_nav_base = statement.get("total_nav_base")
    day_change_totals = summarize_holding_day_change(rows, total_market_value_base=total_market_value_base)

    def is_cash_workspace_row(row: dict[str, object]) -> bool:
        instrument_core = row.get("instrument_core") if isinstance(row.get("instrument_core"), dict) else {}
        return (
            str(instrument_core.get("instrument_type") or "").strip().lower() == "cash"
            or is_cash_holding_instrument_id(instrument_core.get("instrument_id") or row.get("line_id"))
        )

    cost_basis_rows = [
        row
        for row in rows
        if not is_cash_workspace_row(row)
        and not is_pending_monetary_holding(row)
        and str(row.get("holding_kind") or "position") != "option_obligation"
    ]
    total_cost_basis_base = (
        sum(float(row["cost_basis_base"]) for row in cost_basis_rows)
        if cost_basis_rows and all(row.get("cost_basis_base") is not None for row in cost_basis_rows)
        else 0.0
        if not cost_basis_rows
        else None
    )

    response = {
        "portfolio_id": resolved_portfolio["portfolio_id"],
        "portfolio_name": resolved_portfolio["portfolio_name"],
        "base_currency": statement["base_currency"],
        "as_of_date": resolved_as_of_date.isoformat(),
        "view_label": "View: Holdings",
        "coverage_note": (
            "Holdings now replay portfolio facts to the selected as-of date and value positions "
            "with shared registry market data and shared FX at that boundary. PositionLots stay portfolio-private, "
            "account-aware, and are derived from the same fact ledger."
        ),
        "risk_basis": risk_basis_profile,
        "summary_cards": [
            {"label": "Positions", "value": str(position_count), "tone": "neutral"},
            {
                "label": "Open PositionLots",
                "value": str(position_lot_summary["open_position_lot_count"]),
                "tone": "neutral",
            },
            {
                "label": "Priced Lines",
                "value": f"{priced_position_count} / {position_count}",
                "tone": "neutral",
            },
            {"label": "Coverage", "value": "Holdings", "tone": "neutral"},
        ],
        "rows": rows,
        "totals": {
            "market_value": total_market_value_base,
            "cash_balance": statement.get("cash_balance_base"),
            "pending_settlement": statement.get("pending_settlement_base"),
            "nav": total_nav_base,
            "day_change_pct": day_change_totals["day_change_pct"],
            "day_change_value": day_change_totals["day_change_value"],
            "cost_basis": total_cost_basis_base,
            "allocation": (
                total_market_value_base / total_nav_base
                if total_market_value_base is not None and total_nav_base is not None and total_nav_base > 1e-9
                else None
            ),
        },
    }
    scoped_response = _enrich_holdings_analytics_scope(
        response,
        portfolio_id=resolved_portfolio_id,
        as_of_date=resolved_as_of_date,
        transactions=transactions,
    )
    enriched_response = enrich_holdings_forward_risk(
        scoped_response,
        as_of_date=resolved_as_of_date,
        calculation_frequency=calculation_frequency,
        risk_policy=risk_policy or {},
    )
    return _public_holdings_workspace_response(
        enriched_response,
        include_details=include_details,
        transactions=transactions,
        as_of_date=resolved_as_of_date,
    )


@router.get("/holdings/position")
def position_holding_projection(
    portfolio_id: str | None = None,
    position_reference_id: str | None = None,
    as_of_date: date | None = None,
) -> dict[str, object]:
    if not position_reference_id or not position_reference_id.strip():
        raise HTTPException(
            status_code=400,
            detail="position_reference_id is required",
        )
    resolved_portfolio = _require_portfolio(portfolio_id)
    resolved_portfolio_id = str(resolved_portfolio["portfolio_id"])
    normalized_position_reference_id = position_reference_id.strip()
    try:
        response = build_materialized_position_holding_projection(
            resolved_portfolio_id,
            normalized_position_reference_id,
            as_of_date=as_of_date,
        )
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if response is None:
        raise HTTPException(
            status_code=409,
            detail="Materialized holding projection is unavailable for the requested date.",
        )

    rows = response.get("rows")
    row_items = rows if isinstance(rows, list) else []
    instrument_types = {
        str(instrument_core.get("instrument_type") or "").strip().lower()
        for row in row_items
        if isinstance(row, dict)
        and isinstance((instrument_core := row.get("instrument_core")), dict)
    }
    instrument_ids = {
        str(instrument_core.get("instrument_id") or "").strip()
        for row in row_items
        if isinstance(row, dict)
        and isinstance((instrument_core := row.get("instrument_core")), dict)
        and str(instrument_core.get("instrument_id") or "").strip()
    }
    if instrument_ids:
        reconcile_instrument_event_tasks(
            portfolio_ids=[resolved_portfolio_id],
            instrument_ids=instrument_ids,
        )
        response["quality_warnings"] = (
            corporate_action_quality_warnings(
                instrument_types,
                instrument_ids,
                transactions=list_transactions(resolved_portfolio_id),
                as_of_date=(
                    as_of_date
                    or _parse_iso_date(response.get("as_of_date"))
                    or date.today()
                ),
            )
            + instrument_event_task_quality_warnings(resolved_portfolio_id)
        )
    else:
        response["quality_warnings"] = []
    return response
