from copy import deepcopy
from datetime import date
from typing import cast

from fastapi import APIRouter, BackgroundTasks, HTTPException

from portfolio_app.services.calculation_frequency import CalculationFrequency
from portfolio_app.db.models import PortfolioCalculationStateModel
from portfolio_app.db.session import get_session_factory
from portfolio_app.services.instrument_charts import (
    HOLDINGS_PRICE_CHART_RANGE_KEYS,
    build_instrument_holdings_market_profile,
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
from portfolio_app.services.instrument_registry import InstrumentRegistryError
from portfolio_app.services.performance import build_holdings_report, is_cash_holding_instrument_id
from portfolio_app.services.portfolio_store import (
    get_portfolio,
    get_portfolio_live_summary,
    list_accounts,
    list_transactions,
)

router = APIRouter()

_HOLDINGS_TREND_FIELD_NAMES = (
    "instrument_trend_as_of_date",
    "instrument_trend_basis",
    "instrument_risk_frequency",
    "instrument_return_1w",
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
        instrument_core = row.get("instrument_core") if isinstance(row.get("instrument_core"), dict) else {}
        if (
            str(instrument_core.get("instrument_type") or "").strip().lower() == "cash"
            or is_cash_holding_instrument_id(instrument_core.get("instrument_id") or row.get("line_id"))
        ):
            continue
        instrument_id = str(instrument_core.get("instrument_id") or row.get("line_id") or "").strip()
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
    return response


def _enrich_holdings_workspace_market_data(
    workspace: dict[str, object],
    *,
    as_of_date: date,
    position_lots: list[dict[str, object]],
    risk_basis_profile: dict[str, object],
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
            str(instrument_core.get("instrument_type") or "").strip().lower() == "cash"
            or is_cash_holding_instrument_id(instrument_id)
        ):
            row.pop("price_chart", None)
            for range_key in HOLDINGS_PRICE_CHART_RANGE_KEYS:
                row.setdefault(f"price_chart_{range_key}", [])
            row.setdefault(
                "instrument_return_series_1m",
                {"first_return_start_date": None, "points": []},
            )
            row.setdefault(
                "instrument_return_series_3m",
                {"first_return_start_date": None, "points": []},
            )
            row.setdefault(
                "instrument_return_series_6m",
                {"first_return_start_date": None, "points": []},
            )
            row.setdefault(
                "instrument_return_series_1y",
                {"first_return_start_date": None, "points": []},
            )
            row.setdefault(
                "instrument_return_series_all",
                {"first_return_start_date": None, "points": []},
            )
            row.setdefault(
                "instrument_holding_return_series",
                {"first_return_start_date": None, "points": []},
            )
            row.setdefault("instrument_risk_frequency", calculation_frequency)
            continue
        if not instrument_id:
            row.pop("price_chart", None)
            row.update(
                empty_instrument_holdings_market_profile(
                    calculation_frequency=calculation_frequency,
                )
            )
            continue
        holding_start_date = holding_start_dates.get(instrument_id)
        row.pop("price_chart", None)
        row.update(
            build_instrument_holdings_market_profile(
                instrument_id,
                as_of_date=as_of_date,
                holding_start_date=holding_start_date,
                calculation_frequency=calculation_frequency,
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
        try:
            risk_basis_profile = calculation_frequency_profile_for_instruments(
                _instrument_ids_from_holdings_workspace(materialized_workspace),
                end_date=resolved_as_of_date,
            )
            calculation_frequency = cast(CalculationFrequency, str(risk_basis_profile.get("resolved_frequency") or "daily"))
            if _holdings_workspace_has_market_profile(
                materialized_workspace,
                calculation_frequency=calculation_frequency,
            ):
                return _materialized_holdings_workspace_response(
                    materialized_workspace,
                    risk_basis_profile=risk_basis_profile,
                )
            accounts = list_accounts(resolved_portfolio_id)
            position_lots = build_position_lots(
                resolved_portfolio_id,
                accounts,
                list_transactions(resolved_portfolio_id, end_date=resolved_as_of_date),
                as_of_date=resolved_as_of_date,
            )
            return _enrich_holdings_workspace_market_data(
                materialized_workspace,
                as_of_date=resolved_as_of_date,
                position_lots=position_lots,
                risk_basis_profile=risk_basis_profile,
            )
        except InstrumentRegistryError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    accounts = list_accounts(resolved_portfolio_id)
    transactions = list_transactions(resolved_portfolio_id)
    try:
        position_lots = build_position_lots(
            resolved_portfolio_id,
            accounts,
            list_transactions(resolved_portfolio_id, end_date=resolved_as_of_date),
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
        risk_basis_profile = calculation_frequency_profile_for_instruments(
            instrument_ids,
            end_date=resolved_as_of_date,
        )
        calculation_frequency = cast(CalculationFrequency, str(risk_basis_profile.get("resolved_frequency") or "daily"))
        statement = build_holdings_report(
            resolved_portfolio,
            accounts,
            transactions,
            as_of_date=resolved_as_of_date,
            include_cash_rows=True,
            calculation_frequency=calculation_frequency,
        )
        market_profile_by_instrument = {
            str(position.get("instrument_id") or ""): build_instrument_holdings_market_profile(
                str(position.get("instrument_id") or ""),
                as_of_date=resolved_as_of_date,
                holding_start_date=holding_start_dates.get(str(position.get("instrument_id") or "")),
                calculation_frequency=calculation_frequency,
            )
            for position in statement.get("positions", [])
            if str(position.get("instrument_id") or "") and not is_cash_holding_instrument_id(position.get("instrument_id"))
        }
    except InstrumentRegistryError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    positions = list(statement["positions"])
    position_count = len(positions)
    priced_position_count = sum(1 for position in positions if position.get("last_price") is not None)
    position_lot_summary = summarize_position_lots(position_lots)

    def market_profile_for_position(position: dict[str, object]) -> dict[str, object]:
        instrument_id = str(position.get("instrument_id") or "")
        if is_cash_holding_instrument_id(instrument_id):
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
        }
        for position in positions
    ]
    total_market_value_base = statement.get("total_market_value_base")
    total_nav_base = statement.get("total_nav_base")
    def is_cash_workspace_row(row: dict[str, object]) -> bool:
        instrument_core = row.get("instrument_core") if isinstance(row.get("instrument_core"), dict) else {}
        return (
            str(instrument_core.get("instrument_type") or "").strip().lower() == "cash"
            or is_cash_holding_instrument_id(instrument_core.get("instrument_id") or row.get("line_id"))
        )

    cost_basis_rows = [row for row in rows if not is_cash_workspace_row(row)]
    total_cost_basis_base = (
        sum(float(row["cost_basis_base"]) for row in cost_basis_rows)
        if cost_basis_rows and all(row.get("cost_basis_base") is not None for row in cost_basis_rows)
        else 0.0
        if not cost_basis_rows
        else None
    )

    return {
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
