"""Rule-independent workspace inputs from the exact published research returns."""
from datetime import date

from watchlist_app.services.price_risk import period_loss_readings, recent_price_risk_points, series_limitation


def build_workspace_price_context(series, risk):
    # Also the legacy read path: never substitute display prices or another
    # monitoring sample for the chart's research_returns contract.
    points = series.get("points") or []
    change, previous_date = None, None
    current = risk.get("current_drawdown")
    if current is not None and len(points) >= 3 and (risk.get("data_quality") or {}).get("status") == "ready":
        from watchlist_app.services.canonical_recalc import _current_drawdown
        previous = _current_drawdown([{**point, "as_of_date": date.fromisoformat(point["date"])} for point in points[:-1]])
        if previous is not None:
            change = float(current) - previous
            previous_date = points[-2]["date"]
    return {
        "period_readings": period_loss_readings(series, {}),
        "series_limitation": series_limitation(series),
        "recent_point_count": len(recent_price_risk_points(series)),
        "minimum_returns": 90 if (series.get("frequency") or {}).get("gap_detection_basis") == "market_calendar:24/7" else 63,
        "return_kind": (series.get("metadata") or {}).get("return_kind"),
        "drawdown_change_pp": change,
        "previous_observation_date": previous_date,
    }


def apply_workspace_price_context(asset, context):
    """Overlay current configured thresholds without altering published readings."""
    limits = asset["period_limits"]
    asset["period_readings"] = [
        {**row, "limit_pct": limits.get(row["period"]),
         "breached": row["return_pct"] is not None and limits.get(row["period"]) is not None
                     and row["return_pct"] <= -limits[row["period"]]}
        for row in context["period_readings"]
    ]
    minimum = context["minimum_returns"]
    asset["price_risk_note"] = context["series_limitation"] or (
        f"不足 {minimum} 个日收益，尚未生成波动初值"
        if not limits and context["recent_point_count"] <= minimum else None)
    for key in ("return_kind", "drawdown_change_pp", "previous_observation_date"):
        asset[key] = context[key]
