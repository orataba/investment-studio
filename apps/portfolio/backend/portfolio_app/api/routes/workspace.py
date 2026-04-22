from datetime import date

from fastapi import APIRouter, HTTPException

from portfolio_app.services.asset_charts import build_asset_sparkline
from portfolio_app.services.ledger import (
    build_position_lots,
    summarize_position_lots,
)
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.performance import build_statement_of_assets_report
from portfolio_app.services.portfolio_store import get_portfolio, list_accounts, list_transactions

router = APIRouter()


def _require_portfolio(portfolio_id: str | None) -> dict[str, object]:
    if not portfolio_id:
        raise HTTPException(status_code=400, detail="portfolio_id is required")
    resolved_portfolio = get_portfolio(portfolio_id)
    if resolved_portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return resolved_portfolio

@router.get("/summary")
def workspace_summary(portfolio_id: str | None = None) -> dict[str, object]:
    resolved_portfolio = _require_portfolio(portfolio_id)

    as_of_date = str(resolved_portfolio.get("as_of_date") or date.today().isoformat())
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
            "Ledger and performance kernel live",
            "Planning taxonomy and target sets live",
            "Recursive research backtest live",
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
        sparkline_by_asset = {
            str(position.get("asset_id") or ""): build_asset_sparkline(
                str(position.get("asset_id") or ""),
                as_of_date=resolved_as_of_date,
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
    rows = [
        {
            "line_id": str(position.get("position_id") or position.get("asset_id") or ""),
            "asset_core": position["instrument_ref"],
            "quantity": position["quantity"],
            "last_price": position.get("last_price"),
            "market_value": position.get("market_value"),
            "market_value_base": position.get("market_value_base"),
            "day_change_pct": None,
            "day_change_value": None,
            "cost_basis": position.get("cost_basis"),
            "cost_basis_base": position.get("cost_basis_base"),
            "allocation": position.get("portfolio_weight"),
            "price_chart": sparkline_by_asset.get(str(position.get("asset_id") or ""), []),
            "coverage_status": "price-nav-fx" if position.get("market_value_base") is not None else "unpriced",
            "account_count": int(position.get("account_count") or 0),
            "open_position_lot_count": int(position.get("open_position_lot_count") or 0),
        }
        for position in positions
    ]
    total_market_value_base = statement.get("total_market_value_base")
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
            "day_change_pct": resolved_portfolio.get("day_change_pct", 0.0),
            "day_change_value": resolved_portfolio.get("day_change_value", 0.0),
            "cost_basis": total_cost_basis_base,
            "allocation": 1.0 if total_market_value_base and total_market_value_base > 0 else None,
        },
    }
