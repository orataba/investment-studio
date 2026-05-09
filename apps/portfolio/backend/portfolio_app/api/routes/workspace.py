from copy import deepcopy
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException

from portfolio_app.services.calculation_frequency import (
    calculation_frequency_profile,
    infer_observation_frequency,
    selected_observation_dates_from_detail,
)
from portfolio_app.db.models import PortfolioCalculationStateModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.asset_charts import (
    build_asset_holdings_market_profile,
    empty_asset_holdings_market_profile,
)
from portfolio_app.services.workspace_cache import (
    get_cached_materialized_holdings_workspace,
    preload_portfolio_workspace_cache,
)
from portfolio_app.services.ledger import (
    build_position_lots,
    summarize_position_lots,
)
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.instrument_registry import get_registry_instrument_detail
from portfolio_app.services.performance import build_statement_of_assets_report
from portfolio_app.services.portfolio_store import (
    get_portfolio,
    get_portfolio_live_summary,
    list_accounts,
    list_transactions,
)

router = APIRouter()


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


def _holding_start_dates_by_asset(position_lots: list[dict[str, object]]) -> dict[str, date]:
    start_dates: dict[str, date] = {}
    for position_lot in position_lots:
        if str(position_lot.get("status") or "") != "open":
            continue
        asset_id = str(position_lot.get("asset_id") or "")
        if not asset_id:
            continue
        holding_start_date = _parse_iso_date(position_lot.get("acquisition_date")) or _parse_iso_date(
            position_lot.get("opened_at")
        )
        if holding_start_date is None:
            continue
        current_start_date = start_dates.get(asset_id)
        if current_start_date is None or holding_start_date < current_start_date:
            start_dates[asset_id] = holding_start_date
    return start_dates


def _enrich_holdings_workspace_market_data(
    workspace: dict[str, object],
    *,
    as_of_date: date,
    position_lots: list[dict[str, object]],
) -> dict[str, object]:
    enriched_workspace = deepcopy(workspace)
    enriched_workspace.pop("price_chart_range", None)
    holding_start_dates = _holding_start_dates_by_asset(position_lots)
    rows = enriched_workspace.get("rows")
    if not isinstance(rows, list):
        return enriched_workspace

    for row in rows:
        if not isinstance(row, dict):
            continue
        asset_core = row.get("asset_core") if isinstance(row.get("asset_core"), dict) else {}
        asset_id = str(asset_core.get("asset_id") or row.get("line_id") or "")
        if not asset_id:
            row.pop("price_chart", None)
            row.update(empty_asset_holdings_market_profile())
            continue
        holding_start_date = holding_start_dates.get(asset_id)
        row.pop("price_chart", None)
        row.update(
            build_asset_holdings_market_profile(
                asset_id,
                as_of_date=as_of_date,
                holding_start_date=holding_start_date,
            )
        )
    return enriched_workspace


def _materialized_summary_is_current(portfolio_id: str) -> bool:
    session_factory = get_session_factory()
    with session_factory() as session:
        state = session.get(PortfolioCalculationStateModel, portfolio_id)
        return state is not None and state.daily_snapshot_status == "current"


def _require_portfolio(portfolio_id: str | None, *, live_if_materialized_stale: bool = False) -> dict[str, object]:
    if not portfolio_id:
        raise HTTPException(status_code=400, detail="portfolio_id is required")
    if live_if_materialized_stale and not _materialized_summary_is_current(portfolio_id):
        resolved_portfolio = get_portfolio_live_summary(portfolio_id)
    else:
        resolved_portfolio = get_portfolio(portfolio_id)
    if resolved_portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return resolved_portfolio


def _portfolio_calculation_frequency_status(portfolio: dict[str, object], *, as_of_date: date) -> str:
    portfolio_id = str(portfolio.get("portfolio_id") or "")
    if not portfolio_id:
        return "Risk basis unavailable"
    materialized_workspace = get_cached_materialized_holdings_workspace(
        portfolio_id,
        as_of_date=as_of_date,
    )
    if not isinstance(materialized_workspace, dict):
        return "Risk basis unavailable"

    start_date = as_of_date - timedelta(days=366)
    source_frequencies = []
    asset_ids: list[str] = []
    for row in list(materialized_workspace.get("rows") or []):
        if not isinstance(row, dict):
            continue
        asset_core = row.get("asset_core") if isinstance(row.get("asset_core"), dict) else {}
        asset_id = str(asset_core.get("asset_id") or row.get("line_id") or "").strip()
        if asset_id and asset_id not in asset_ids:
            asset_ids.append(asset_id)
    for asset_id in asset_ids:
        if not asset_id:
            continue
        try:
            detail = get_registry_instrument_detail(asset_id)
        except InstrumentRegistryError:
            return "Risk basis unavailable"
        if not isinstance(detail, dict):
            continue
        dates = [
            point_date
            for point_date in selected_observation_dates_from_detail(detail, end_date=as_of_date)
            if start_date <= point_date <= as_of_date
        ]
        source_frequencies.append(infer_observation_frequency(dates))
    profile = calculation_frequency_profile(requested_frequency="auto", source_frequencies=source_frequencies)
    return str(profile["status_label"])


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
            {"label": "Review", "href": "/review", "status": "workspace-backed"},
            {"label": "Research", "href": "/research", "status": "workspace-backed"},
            {"label": "Taxonomies", "href": "/taxonomies", "status": "workspace-backed"},
        ],
    }


@router.post("/preload")
def preload_workspace(
    background_tasks: BackgroundTasks,
    portfolio_id: str | None = None,
) -> dict[str, object]:
    resolved_portfolio = _require_portfolio(portfolio_id)
    resolved_portfolio_id = str(resolved_portfolio["portfolio_id"])
    warmed_surfaces = [
        "holdings",
        "performance",
        "contribution:instrument",
        "contribution:account",
    ]
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
) -> dict[str, object]:
    resolved_portfolio = _require_portfolio(portfolio_id)

    portfolio_as_of_date = (
        date.fromisoformat(str(resolved_portfolio.get("as_of_date")))
        if resolved_portfolio.get("as_of_date")
        else None
    )
    resolved_as_of_date = as_of_date or portfolio_as_of_date or date.today()
    resolved_portfolio_id = str(resolved_portfolio["portfolio_id"])
    materialized_workspace = get_cached_materialized_holdings_workspace(
        resolved_portfolio_id,
        as_of_date=resolved_as_of_date,
    )
    if materialized_workspace is not None:
        accounts = list_accounts(resolved_portfolio_id)
        position_lots = build_position_lots(
            resolved_portfolio_id,
            accounts,
            list_transactions(resolved_portfolio_id, end_date=resolved_as_of_date),
            as_of_date=resolved_as_of_date,
        )
        try:
            return _enrich_holdings_workspace_market_data(
                materialized_workspace,
                as_of_date=resolved_as_of_date,
                position_lots=position_lots,
            )
        except InstrumentRegistryError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    accounts = list_accounts(resolved_portfolio_id)
    transactions = list_transactions(resolved_portfolio_id)
    try:
        statement = build_statement_of_assets_report(
            resolved_portfolio,
            accounts,
            transactions,
            as_of_date=resolved_as_of_date,
        )
        position_lots = build_position_lots(
            resolved_portfolio_id,
            accounts,
            list_transactions(resolved_portfolio_id, end_date=resolved_as_of_date),
            as_of_date=resolved_as_of_date,
        )
        holding_start_dates = _holding_start_dates_by_asset(position_lots)
        market_profile_by_asset = {
            str(position.get("asset_id") or ""): build_asset_holdings_market_profile(
                str(position.get("asset_id") or ""),
                as_of_date=resolved_as_of_date,
                holding_start_date=holding_start_dates.get(str(position.get("asset_id") or "")),
            )
            for position in statement.get("positions", [])
            if str(position.get("asset_id") or "")
        }
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    positions = list(statement["positions"])
    position_count = len(positions)
    priced_position_count = sum(1 for position in positions if position.get("last_price") is not None)
    position_lot_summary = summarize_position_lots(position_lots)

    def market_profile_for_position(position: dict[str, object]) -> dict[str, object]:
        asset_id = str(position.get("asset_id") or "")
        return (
            market_profile_by_asset[asset_id]
            if asset_id
            else empty_asset_holdings_market_profile()
        )

    rows = [
        {
            "line_id": str(position.get("position_id") or position.get("asset_id") or ""),
            "asset_core": position["instrument_ref"],
            "quantity": position["quantity"],
            "last_price": position.get("last_price"),
            "quote_as_of_date": position.get("quote_as_of_date"),
            "quote_metric_family": position.get("quote_metric_family"),
            "quote_basis": position.get("quote_basis"),
            "quote_provider": position.get("quote_provider"),
            "quote_status": position.get("quote_status"),
            "market_value": position.get("market_value"),
            "market_value_base": position.get("market_value_base"),
            "day_change_pct": None,
            "day_change_value": None,
            "cost_basis_method": position.get("cost_basis_method"),
            "cost_basis": position.get("cost_basis"),
            "cost_basis_base": position.get("cost_basis_base"),
            "allocation": position.get("portfolio_weight"),
            **market_profile_for_position(position),
            "coverage_status": "price-nav-fx" if position.get("market_value_base") is not None else "unpriced",
            "account_count": int(position.get("account_count") or 0),
            "open_position_lot_count": int(position.get("open_position_lot_count") or 0),
        }
        for position in positions
    ]
    total_market_value_base = statement.get("total_market_value_base")
    total_nav_base = statement.get("total_nav_base")
    total_cost_basis_base = sum(
        float(row["cost_basis_base"])
        for row in rows
        if row.get("cost_basis_base") is not None
    ) if rows and all(row.get("cost_basis_base") is not None for row in rows if row.get("cost_basis") is not None) else None

    return {
        "portfolio_id": resolved_portfolio["portfolio_id"],
        "portfolio_name": resolved_portfolio["portfolio_name"],
        "base_currency": statement["base_currency"],
        "as_of_date": resolved_as_of_date.isoformat(),
        "view_label": "View: Holdings",
        "coverage_note": (
            "Statement of assets now replays portfolio facts to the selected as-of date and values holdings "
            "with shared registry market data and shared FX at that boundary. PositionLots stay portfolio-private, "
            "account-aware, and are derived from the same fact ledger."
        ),
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
            {"label": "Coverage", "value": "Statement of Assets", "tone": "neutral"},
        ],
        "rows": rows,
        "totals": {
            "market_value": total_market_value_base,
            "cash_balance": statement.get("cash_balance_base"),
            "pending_settlement": statement.get("pending_settlement_base"),
            "nav": total_nav_base,
            "day_change_pct": resolved_portfolio.get("day_change_pct", 0.0),
            "day_change_value": resolved_portfolio.get("day_change_value", 0.0),
            "cost_basis": total_cost_basis_base,
            "allocation": (
                total_market_value_base / total_nav_base
                if total_market_value_base is not None and total_nav_base is not None and total_nav_base > 1e-9
                else None
            ),
        },
    }
