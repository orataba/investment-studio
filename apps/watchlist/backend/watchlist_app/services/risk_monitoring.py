"""Fixed, retained monitoring metrics from the canonical economic return path.

This service is invoked by the deterministic recalculation worker. HTTP and
research readers consume its saved output and never manufacture a fresh sample.
"""
from bisect import bisect_right
from datetime import UTC, date
from hashlib import sha256
import json
from math import sqrt
from statistics import stdev

from watchlist_app.services.instrument_tail_risk import instrument_tail_risk
from watchlist_app.services.price_risk import _periods, period_loss_readings
from watchlist_app.services.return_windows import (
    _shift_months, confirmed_return_points, return_sample_limitation,
)

MONITORING_DEFINITION_VERSION = "instrument-monitoring/v1"


def _identity(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def build_monitoring_snapshot(chart, *, instrument_type, source_settings, risk_payload,
                              input_snapshot_version, calculated_at, source_cutoff_at):
    series = chart.get("return_series") or {}
    metadata = series.get("metadata") or {}
    frequency = series.get("frequency") or {}
    points = confirmed_return_points({**series, "points": series.get("wealth_points") or series.get("points") or []})
    dates = [point["as_of_date"] for point in points]
    as_of = dates[-1].isoformat() if dates else None
    definition = {"version": MONITORING_DEFINITION_VERSION, "rolling_window_months": 1,
                  "minimum_return_observations": 3, "return_measure": "simple",
                  "annualization": "actual_observations / elapsed_calendar_days * 365.25"}
    options = [instrument_tail_risk(chart, instrument_type=instrument_type,
               source_settings=source_settings, confidence=confidence, lookback_days=days, now=calculated_at)
               for days in (365, 1095, 1825) for confidence in (.95, .99)]
    # Source publication deadlines affect sample eligibility; the wall clock itself
    # is not an input identity. A new due session is a real coverage-state change.
    input_version = _identity({"canonical_input": input_snapshot_version, "series": series,
        "source_settings": source_settings, "definition": definition,
        "expected_latest_dates": [option["latest_expected_date"] for option in options]})
    snapshot_id = f"monitoring:{chart.get('instrument_id')}:{input_version}"
    base = {"metric_snapshot_id": snapshot_id, "definition_version": MONITORING_DEFINITION_VERSION,
        "input_snapshot_version": input_version, "as_of": as_of, "currency": series.get("currency"),
        "return_kind": metadata.get("return_kind"), "frequency": frequency,
        "window_start_date": dates[0].isoformat() if dates else None, "window_end_date": as_of,
        "observation_count": max(0, len(points) - 1), "unit": "percent",
        "coverage": {key: frequency.get(key) for key in ("gap_count", "gap_status", "gap_detection_basis", "missing_observation_date_sample")}}
    metrics = []

    def metric(identifier, label, value, *, trend=None, reason=None, status=None, **details):
        row = {**base, "metric_id": identifier, "label": label, "value": value,
               "status": status or ("ready" if value is not None else "unavailable"),
               "unavailable_reason": reason, "series": trend or [], **details}
        metrics.append(row)
        return row

    limitation = return_sample_limitation(series, points)
    eligible = metadata.get("return_kind") in {"total_return", "price_return"} and metadata.get("return_measure") == "simple"
    if instrument_type in {"equity", "etf", "public_fund", "private_fund"}:
        eligible = eligible and metadata.get("return_kind") == "total_return"
    if not eligible:
        limitation = "收益经济口径尚未确认"
    # Retain unavailable observations in charts: a line must not bridge a gap.
    drawdowns, maximums = [], []
    peak, peak_day, worst = None, None, 0.0
    broken = not eligible
    intervals = {}
    for interval in series.get("intervals") or []:
        intervals.setdefault((interval.get("start_date"), interval.get("date")), []).append(interval)
    bad_prefix = [0]
    returns = []
    for index, point in enumerate(points):
        if index:
            before = points[index - 1]
            evidence = intervals.get((before["date"], point["date"]), [])
            bad = (len(evidence) != 1 or evidence[0].get("status") != "complete"
                   or before.get("segment_id") != point.get("segment_id"))
            resolved = frequency.get("resolved_frequency")
            if resolved == "daily" and not str(frequency.get("gap_detection_basis") or "").startswith("market_calendar:"):
                bad = True
            if resolved not in {"daily", "weekly", "monthly", "event_driven"}:
                bad = True
            if resolved in {"weekly", "monthly"}:
                a, b = dates[index - 1], dates[index]
                periods = ((b.year * 12 + b.month) - (a.year * 12 + a.month) if resolved == "monthly"
                           else ((b.toordinal() - b.weekday()) - (a.toordinal() - a.weekday())) // 7)
                bad = bad or periods > 1
            bad_prefix.append(bad_prefix[-1] + int(bad))
            returns.append(point["value"] / before["value"] - 1)
            broken = broken or bad
        if peak is None or point["value"] >= peak:
            peak, peak_day = point["value"], point["date"]
        value = None if broken else (point["value"] / peak - 1) * 100
        if value is not None:
            worst = min(worst, value)
        state = "unavailable" if value is None else "ready"
        drawdowns.append({"date": point["date"], "value": value, "status": state})
        maximums.append({"date": point["date"], "value": worst if value is not None else None, "status": state})
    current = None if limitation else risk_payload.get("current_drawdown")
    metric("current_drawdown", "当前回撤", current, trend=drawdowns, reason=limitation,
           definition={"peak_basis": "complete_retained_economic_path", "sign": "negative_percent",
                       "peak_date": peak_day if not limitation else None,
                       "duration_calendar_days": (dates[-1] - date.fromisoformat(peak_day)).days if peak_day and not limitation else None,
                       "recovery_distance_pct": ((1 / (1 + current / 100) - 1) * 100) if current is not None and current > -100 else None})
    metric("max_drawdown", "样本区间最大回撤", worst if points and not limitation else None,
           trend=maximums, reason=limitation, definition={"peak_basis": "complete_retained_economic_path", "sign": "negative_percent",
                                                       "drawdown_summary": risk_payload.get("drawdown_summary")})
    metric("holding_period_max_drawdown", "持有期最大回撤", None, status="not_applicable",
           reason="单资产观察列表没有实际持有路径；实际持有期风险属于组合。", definition={"scope": "actual_holding_path"})
    rolling_vol, downside = [], []
    latest_window = None
    for index, point in enumerate(points):
        target = _shift_months(dates[index], -1)
        start = bisect_right(dates, target, hi=index) - 1
        reason = None
        if not eligible:
            reason = "收益经济口径尚未确认"
        elif start < 0 or index - start < 3:
            reason = "完整一个日历月窗口内不足 3 个收益观察"
        elif bad_prefix[index] != bad_prefix[start]:
            reason = "滚动窗口存在缺失或未确认收益区间"
        elif frequency.get("resolved_frequency") == "event_driven":
            reason = "不规则事件驱动净值未建立可年化的等间隔风险定义"
        sample = returns[start:index] if reason is None else []
        factor = len(sample) / (dates[index] - dates[start]).days * 365.25 if sample else None
        common = {"date": point["date"], "status": "unavailable" if reason else "ready",
                  "unavailable_reason": reason, "observation_count": len(sample),
                  "window_start_date": dates[start].isoformat() if start >= 0 else None,
                  "annualization_factor": factor}
        rolling_vol.append({**common, "value": stdev(sample) * sqrt(factor) * 100 if sample else None})
        downside.append({**common, "value": sqrt(sum(min(value, 0) ** 2 for value in sample) / len(sample) * factor) * 100 if sample else None})
        latest_window = common
    for identifier, label, trend, extra in (
        ("rolling_volatility", "滚动年化波动率 · 1M", rolling_vol, {"standard_deviation_ddof": 1}),
        ("downside_deviation", "下行偏差 · 1M", downside, {"mar_per_period": 0, "denominator": "all_return_observations"}),
    ):
        last = trend[-1] if trend else {}
        metric(identifier, label, last.get("value"), trend=trend, reason=last.get("unavailable_reason") or ("暂无有效观察" if not trend else None),
               observation_count=last.get("observation_count", 0), window_start_date=last.get("window_start_date"),
               definition={**definition, **extra, "annualization_factor": (latest_window or {}).get("annualization_factor")})
    selected = next(option for option in options if option["confidence"] == .95 and option["lookback_days"] == 1095)
    for identifier, label, field in (("var", "历史 VaR · 95% / 3Y", "var"),
                                     ("expected_shortfall", "历史 ES · 95% / 3Y", "expected_shortfall")):
        value = selected.get(field)
        metric(identifier, label, value * 100 if value is not None else None,
               status=selected["status"], reason=selected["reason"],
               observation_count=selected["observation_count"], window_start_date=selected["window_start_date"],
               window_end_date=selected["window_end_date"], coverage={key: selected.get(key) for key in (
                   "history_coverage_status", "expected_observation_count", "missing_internal_count", "uncovered_leading_count",
                   "uncovered_trailing_count", "excluded_interval_count", "missing_intervals", "source_stale")},
               definition={"confidence": .95, "lookback_days": 1095,
               "horizon": "one_session", "method": "inverse_empirical_cdf_fractional_tail_mass", "sign": "positive_loss",
               "tail_effective_observations": selected["tail_effective_observations"]})
    for option in options:
        option.update(metric_snapshot_id=snapshot_id, definition_version=MONITORING_DEFINITION_VERSION,
                      input_snapshot_version=input_version, calculated_at=calculated_at.isoformat())
    period_series = {}
    for key, (_, window, _, _) in _periods(series).items():
        period_series[key] = [{"date": point["date"], "value":
            (point["value"] / points[index - window]["value"] - 1) * 100
            if eligible and index >= window and frequency.get("resolved_frequency") == "daily"
               and bad_prefix[index] == bad_prefix[index - window] else None}
            for index, point in enumerate(points)]
    return {**base, "instrument_id": chart.get("instrument_id"), "scope": "single_unlevered_long_instrument",
            "definition": definition, "calculated_at": calculated_at.isoformat(),
            "source_cutoff_at": source_cutoff_at.isoformat(), "metrics": metrics, "tail_risk_options": options,
            "status": ("unavailable" if not any(row["value"] is not None for row in metrics)
                       else "ready" if all(row["status"] in {"ready", "not_applicable"} for row in metrics) else "partial"),
            "freshness": chart.get("freshness"),
            "period_readings": period_loss_readings({**series, "points": series.get("wealth_points") or series.get("points") or []}, {}),
            "period_series": period_series,
            "history_note": "固定指标历史按留存快照记录；历史回撤与滚动风险使用当前可核验经济收益路径，来源修订不改写旧快照。"}


def monitoring_unavailable(instrument_id, reason="等待后台生成固定监测快照"):
    return {"instrument_id": instrument_id, "metric_snapshot_id": None,
            "definition_version": MONITORING_DEFINITION_VERSION, "status": "unavailable",
            "unavailable_reason": reason, "metrics": [], "tail_risk_options": []}


def monitoring_summary(snapshot):
    """Bounded fixed readings for agents; full series stay at the evidence endpoint."""
    return {key: value for key, value in snapshot.items() if key not in {"metrics", "tail_risk_options", "period_series"}} | {
        "metrics": [{key: value for key, value in metric.items() if key != "series"} for metric in snapshot.get("metrics", [])],
    }


def retained_monitoring_snapshot(snapshot):
    """Keep historical decisions, without copying every old curve each day."""
    return {**monitoring_summary(snapshot), "tail_risk_options": snapshot.get("tail_risk_options", []),
            "path_series_status": "not_retained",
            "path_series_note": "保留当时数值、方法、样本覆盖与输入身份；未重复留存当时整条路径曲线，并非当时没有数据。",
            "metrics": [{key: value for key, value in metric.items() if key != "series"} |
                        {"series": [], "series_status": "not_retained"} for metric in snapshot.get("metrics", [])]}


def retained_monitoring_summary(session, instrument_id, *, as_of):
    """Read the same current snapshot only when it was available by the cutoff."""
    from watchlist_app.db.models import InstrumentRiskReadModel
    record = session.get(InstrumentRiskReadModel, instrument_id, populate_existing=True)
    if record is None:
        return monitoring_unavailable(instrument_id, "研究截止前没有已留存固定监测快照")
    for clock in (record.last_recalculated_at, record.source_cutoff_at):
        if clock is None or clock.replace(tzinfo=clock.tzinfo or UTC) > as_of:
            return monitoring_unavailable(instrument_id, "当前监测快照晚于研究截止时间，未使用后来重算值")
    return record.payload_json.get("monitoring_summary") or monitoring_unavailable(instrument_id)


def preview_rule_changes(snapshot, previous, proposed):
    """Historical line comparison on the retained path, without changing state."""
    drawdown = next((item for item in snapshot.get("metrics", []) if item["metric_id"] == "current_drawdown"), {})
    rows = []
    for key in ("drawdown", "day", "week", "month", "quarter"):
        trend = drawdown.get("series", []) if key == "drawdown" else (snapshot.get("period_series") or {}).get(key, [])
        old = previous.get("drawdown_limit") if key == "drawdown" else previous.get("period_limits", {}).get(key)
        new = proposed.get("drawdown_limit") if key == "drawdown" else proposed.get("period_limits", {}).get(key)
        valid = [point for point in trend if point.get("value") is not None]
        old_dates = {point["date"] for point in valid if old is not None and point["value"] <= -old}
        new_dates = {point["date"] for point in valid if new is not None and point["value"] <= -new}
        rows.append({"key": key, "previous_limit_pct": old, "proposed_limit_pct": new,
                     "evaluable_observations": len(valid), "unavailable_observations": len(trend) - len(valid),
                     "previous_trigger_count": len(old_dates), "proposed_trigger_count": len(new_dates),
                     "new_trigger_dates": sorted(new_dates - old_dates), "removed_trigger_dates": sorted(old_dates - new_dates)})
    return {"metric_snapshot_id": snapshot.get("metric_snapshot_id"), "as_of": snapshot.get("as_of"),
            "rules": rows, "status": "ready" if any(row["evaluable_observations"] for row in rows) else "unavailable",
            "note": "基于本次留存经济收益路径的历史命中比较；不重演历史当时可得信息，不修改规则、事项或通知。"}
