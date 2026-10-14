"""Rolling-period loss review lines, calibrated once and then kept fixed."""
from math import ceil, exp, log, sqrt
from statistics import stdev
from watchlist_app.services.return_windows import confirmed_return_points, return_sample_limitation

# Short horizons look for shocks; longer horizons also flag persistent losses.
# Floors are starting review tolerances, not estimated tail probabilities.
PERIODS = {
    "day": ("近1日", 1, 3.0, 0.5),
    "week": ("近1周", 5, 2.5, 1.0),
    "month": ("近1月", 21, 2.0, 2.0),
    "quarter": ("近1季", 63, 1.5, 3.0),
}


def rule_version(rule):
    return f"risk-rule:{rule.instrument_id}:{int((rule.calibration_json or {}).get('rule_revision', 0))}" if rule else None


def rule_configuration(rule):
    return {"drawdown_limit": rule.drawdown_limit, "period_limits": dict(rule.period_limits_json or {}),
            "severities": dict((rule.calibration_json or {}).get("severities") or {}),
            "calibration": {key: value for key, value in (rule.calibration_json or {}).items()
                            if key not in {"rule_revision", "rule_history", "severities"}}}


def record_rule_revision(rule, *, previous, actor, reason, at):
    """Keep prior rule values and calibration evidence; never rewrite old decisions."""
    current = rule_configuration(rule)
    if current == previous:
        return
    calibration = dict(rule.calibration_json or {})
    revision = int(calibration.get("rule_revision", 0)) + 1
    history = list(calibration.get("rule_history") or [])
    history.append({"revision": revision, "effective_at": at.isoformat(), "changed_by": actor,
                    "reason": reason, "previous": previous, "current": current})
    rule.calibration_json = {**calibration, "rule_revision": revision, "rule_history": history}


def _continuous_daily_market(series):
    return (series.get("frequency") or {}).get("gap_detection_basis") == "market_calendar:24/7"


def _periods(series):
    if not _continuous_daily_market(series):
        return PERIODS
    windows = {"day": 1, "week": 7, "month": 30, "quarter": 90}
    return {key: (label, windows[key], multiple, floor) for key, (label, _, multiple, floor) in PERIODS.items()}


def recent_price_risk_points(series):
    """Use the latest confirmed return chain, never cross a rebasing boundary."""
    points = confirmed_return_points(series)
    last_break = 0
    for index in range(1, len(points)):
        if points[index].get("segment_id") != points[index - 1].get("segment_id"):
            last_break = index
    return points[last_break:]


def series_limitation(series: dict, *, sample=None) -> str | None:
    metadata = series.get("metadata") or {}
    frequency = series.get("frequency") or {}
    if metadata.get("return_kind") not in {"total_return", "price_return"}:
        return "收益经济口径尚未确认"
    if metadata.get("return_series_status") not in {"ready", "complete", "partial"}:
        return "收益口径尚未就绪"
    points = recent_price_risk_points(series) if sample is None else sample
    if sample is None:
        points = points[-366:] if _continuous_daily_market(series) else points[-253:]
    if not points:
        return "收益序列缺少有效观察值或已确认的分段身份"
    if frequency.get("resolved_frequency") != "daily":
        return "当前为低频或未知频率数据，未套用日度交易窗口"
    return return_sample_limitation(series, points)


def initial_price_limits(series: dict) -> tuple[dict, dict]:
    """At least one quarter of daily returns; use up to one trading year."""
    points = recent_price_risk_points(series)
    sample = points[-366:] if _continuous_daily_market(series) else points[-253:]
    if series_limitation(series, sample=sample):
        return {}, {}
    if len(sample) < (91 if _continuous_daily_market(series) else 64):
        return {}, {}
    returns = [log(b["value"] / a["value"]) for a, b in zip(sample, sample[1:])]
    sigma = stdev(returns)
    limits = {
        key: ceil(max(floor, 100 * (1 - exp(-multiple * sigma * sqrt(window)))) * 2) / 2
        for key, (_, window, multiple, floor) in _periods(series).items()
    }
    return limits, {
        "method": "volatility_review_v1", "sample_start": sample[0]["date"],
        "sample_end": sample[-1]["date"], "observations": len(returns),
        "daily_volatility_pct": sigma * 100,
        "period_observations": {key: value[1] for key, value in _periods(series).items()},
        "calendar_basis": (series.get("frequency") or {}).get("gap_detection_basis"),
        "return_kind": (series.get("metadata") or {}).get("return_kind"),
        "return_measure": "log",
    }


def period_loss_readings(series: dict, limits: dict) -> list[dict]:
    points = recent_price_risk_points(series)
    rows = []
    for key, (label, window, _, _) in _periods(series).items():
        limitation = series_limitation(series, sample=points[-window - 1:])
        reason = limitation or (f"不足 {window + 1} 个日度观察值" if len(points) <= window else None)
        start = points[-window - 1] if not reason else None
        end = points[-1] if points else None
        value = (end["value"] / start["value"] - 1) * 100 if start and end else None
        limit = limits.get(key)
        rows.append({
            "period": key, "label": label, "observations": window,
            "start_date": start["date"] if start else None,
            "end_date": end["date"] if end else None,
            "return_pct": value, "limit_pct": limit,
            "breached": value is not None and limit is not None and value <= -limit,
            "limitation": reason,
        })
    return rows


def period_rule_sources(limits: dict, calibration: dict) -> dict:
    """Only attribute origins supported by retained settings, including legacy edits."""
    retained = calibration.get("rule_sources") or {}
    initial = calibration.get("method") == "volatility_review_v1" and not calibration.get("manually_edited")
    return {key: (retained.get(key) or ("default" if initial else "unknown"))
            for key in PERIODS if limits.get(key) is not None}


def price_rule_summary(asset: dict, updated_at=None) -> dict:
    """The actual configured rules and their evaluation, independent of case counts.

    Settings timestamps describe when this retained configuration took effect;
    they are not observation dates. A run freezes this object with its inputs.
    """
    sources = period_rule_sources(asset.get("period_limits") or {}, asset.get("price_risk_calibration") or {})
    risk = asset.get("risk") or {}
    calibration = asset.get("price_risk_calibration") or {}
    severities = calibration.get("severities") or {}
    coverage = "行情新鲜度不足" if asset.get("freshness") != "fresh" else None
    readings = [{**row, "key": row["period"], "source": sources.get(row["period"], "none")}
                for row in asset.get("period_readings") or []]
    readings.append({"key": "drawdown", "source": "custom" if asset.get("drawdown_limit") is not None else "none",
        "limit_pct": asset.get("drawdown_limit"), "return_pct": risk.get("current_drawdown"),
        "end_date": next((row.get("end_date") for row in readings if row.get("end_date")), None),
        "observations": None, "start_date": None,
        "limitation": "风险数据尚未就绪" if (risk.get("data_quality") or {}).get("status") != "ready" else None})
    counts = dict.fromkeys(("configured", "evaluable", "triggered", "unavailable", "default", "custom", "unknown"), 0)
    rules = []
    for reading in readings:
        configured = reading["limit_pct"] is not None
        limitation = coverage or reading.get("limitation") or ("缺少当前读数" if reading.get("return_pct") is None else None)
        state = ("not_configured" if not configured else "unavailable" if limitation else
                 "triggered" if reading["return_pct"] <= -reading["limit_pct"] else "not_triggered")
        if configured:
            counts["configured"] += 1
            counts[reading["source"]] += 1
            counts["unavailable" if state == "unavailable" else "evaluable"] += 1
            counts["triggered"] += state == "triggered"
        rules.append({"key": reading["key"], "source": reading["source"], "state": state,
            "rule_version": asset.get("rule_version"), "metric_snapshot_id": risk.get("metric_snapshot_id"),
            "severity": severities.get(reading["key"], "attention"),
            "mandatory_notice": state == "triggered" and severities.get(reading["key"]) == "severe",
            "limit_pct": reading["limit_pct"], "value_pct": reading.get("return_pct"),
            "metric": "current_drawdown" if reading["key"] == "drawdown" else "rolling_return",
            "loss_magnitude_pct": max(0.0, -reading["return_pct"]) if reading.get("return_pct") is not None else None,
            "trigger_at_or_below_pct": -reading["limit_pct"] if configured else None,
            "observations": reading.get("observations"), "start_date": reading.get("start_date"),
            "end_date": reading.get("end_date"), "limitation": limitation if configured else None})
    return {"contract_version": 2, "settings_updated_at": updated_at, "counts": counts, "rules": rules,
            "rule_version": asset.get("rule_version"), "rule_history": calibration.get("rule_history") or []}


def scope_price_rule_summary(instruments: list[dict]) -> dict:
    summaries = [item["price_rule_summary"] for item in instruments]
    return {"contract_version": 2, "counts": {key: sum(item["counts"][key] for item in summaries)
        for key in ("configured", "evaluable", "triggered", "unavailable", "default", "custom", "unknown")}}
