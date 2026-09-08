"""Source-bound numerical observations shared by both research entrances."""
from __future__ import annotations

from datetime import UTC, date
from math import isfinite, log, sqrt
from statistics import median
from uuid import uuid4

from studio_market.numeric.datasets import DATASETS
from studio_market.numeric.store import cutoff_instant

from watchlist_app.services.calculation_frequency import _market_calendar_sessions
from watchlist_app.services.sector_market_data import numeric_store


SERIES_DATASETS = (
    "macro_series", "market_series_daily", "regime_market_daily",
    "us_eod_daily", "raw_eod_daily", "cn_equity_daily",
)
NUMERIC_FIELDS = {"value", "close", "adjusted_close", "open", "high", "low", "volume"}
EWMA_HALF_LIFE = 21
EWMA_MIN_RETURNS = 63
EWMA_WINDOW = 252


def _evidence(title, cutoff, data, methodology, sources=(), **extra):
    return {
        "source_id": f"computed:{uuid4().hex}", "source_type": "computed_metric",
        "scope": "public_market", "title": title, "as_of": cutoff.isoformat(),
        "methodology": methodology, "data": data,
        "source_ids": [row["source_id"] for row in sources], "sources": list(sources),
        **extra,
    }


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if isfinite(number) else None


def _point(row, field):
    # Source identity, observed time and release availability travel with values.
    return {key: row.get(key) for key in (
        "date", "source_id", "batch_id", "observed_at", "available_at", "availability_precision",
    )} | {"value": _number(row.get(field))}


def research_numeric_data(*, action="catalogue", as_of, dataset=None, series_ids=None,
                          field=None, start=None, end=None, store=None):
    """Catalogue actual available series, read them, or compare their changes.

    Dates bound observation periods; as_of independently bounds the information set.
    The returned evidence must be retained in the run's computed_metrics collection.
    """
    cutoff = cutoff_instant(as_of)
    if action not in {"catalogue", "series", "compare"}:
        raise ValueError("Unknown numeric research action")
    if dataset is not None and dataset not in SERIES_DATASETS:
        raise ValueError("Choose a supported series dataset from the catalogue")
    if action == "catalogue" and dataset is None:
        return _evidence("共享数值目录", cutoff, {
            "datasets": [{"dataset": key, "description": DATASETS[key].description,
                          "historical_use": DATASETS[key].historical_use} for key in SERIES_DATASETS],
            "limitations": ["这是已支持的数据集目录；选择数据集后查询实际已采集序列，目录不保证数据覆盖。"],
        }, "Application dataset definitions; query a dataset for observations available at this cutoff.")
    if dataset is None:
        raise ValueError("dataset is required")
    store = store or numeric_store()
    if action == "catalogue":
        page = store.latest(dataset, as_of=cutoff, limit=100)
        return _evidence(f"{dataset} 可用序列", cutoff, {
            "dataset": dataset, "total": page["total"], "truncated": page["total"] > len(page["rows"]),
            "series": [{"series_id": row["symbol"], "latest_date": row["date"],
                        "unit": row.get("unit"), "currency": row.get("currency"),
                        "frequency": row.get("frequency") or ("daily" if dataset.endswith("daily") else "not_supplied"),
                        "available_fields": [key for key in sorted(NUMERIC_FIELDS) if _number(row.get(key)) is not None],
                        "source_id": row["source_id"], "observed_at": row["observed_at"],
                        "available_at": row["available_at"]} for row in page["rows"]],
            "limitations": ["仅列本截止时间已取得的序列；最多显示100项，可直接按已知序列标识读取。"],
        }, "Latest actual observation per series, filtered by observed_at and available_at.", page["rows"])
    if not series_ids or len(series_ids) > 6 or len(set(series_ids)) != len(series_ids):
        raise ValueError("Choose one to six distinct series identifiers")
    field = field or ("value" if dataset == "macro_series" else "close")
    if field not in NUMERIC_FIELDS:
        raise ValueError("Choose an available numeric field from the catalogue")
    if start is None or end is None:
        raise ValueError("An explicit observation start and end date is required")
    start = date.fromisoformat(str(start))
    end = min(date.fromisoformat(str(end)), cutoff.date())
    if start > end:
        raise ValueError("Invalid observation date range")
    # No silent truncation: calculations must cover the requested observed sample.
    page = store.query(dataset, symbols=series_ids, start=start.isoformat(), end=end.isoformat(),
                       as_of=cutoff, limit=10000)
    if page["total"] > len(page["rows"]):
        raise ValueError("Requested sample exceeds 10000 rows; narrow the date range or series")
    rows = page["rows"]
    output, by_series = [], {}
    for symbol in series_ids:
        observations = sorted((row for row in rows if row["symbol"] == symbol), key=lambda row: row["date"])
        valid = [row for row in observations if _number(row.get(field)) is not None]
        points = [_point(row, field) for row in valid]
        by_series[symbol] = {point["date"]: point for point in points}
        units = {row.get("unit") for row in valid}
        currencies = {row.get("currency") for row in valid}
        unit = next(iter(units)) if len(units) == 1 else None
        comparable = len(units) <= 1 and len(currencies) <= 1
        first, last = (points[0], points[-1]) if points else (None, None)
        change = last["value"] - first["value"] if len(points) > 1 and comparable else None
        output.append({
            "series_id": symbol, "dataset": dataset, "field": field,
            "unit": unit, "currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "frequency": sorted({row.get("frequency") or ("daily" if dataset.endswith("daily") else "not_supplied") for row in valid}),
            "status": "available" if points else "unavailable", "observations": len(points),
            "missing_values": len(observations) - len(valid), "comparable_units": comparable,
            "first": first, "latest": last, "previous": points[-2] if len(points) > 1 else None,
            "change": change, "change_unit": "percentage_points" if unit == "percent" else unit,
            "relative_change_pct": (last["value"] / first["value"] - 1) * 100
                if change is not None and first["value"] > 0 and field in {"close", "adjusted_close"} else None,
            "minimum": min((p["value"] for p in points), default=None) if comparable else None,
            "maximum": max((p["value"] for p in points), default=None) if comparable else None,
            "points": points if action == "series" else [],
        })
    common = sorted(set.intersection(*(set(points) for points in by_series.values())))
    aligned = []
    if action == "compare" and len(common) >= 2:
        for item in output:
            first, last = by_series[item["series_id"]][common[0]], by_series[item["series_id"]][common[-1]]
            aligned.append({"series_id": item["series_id"], "start": first, "end": last,
                            "change": last["value"] - first["value"] if item["comparable_units"] else None,
                            "change_unit": item["change_unit"]})
    return _evidence(f"{dataset} · {field} · 数值研究", cutoff, {
        "dataset": dataset, "requested_start": start.isoformat(), "effective_end": end.isoformat(),
        "series": output, "common_observation_count": len(common), "aligned_changes": aligned,
        "limitations": [
            "计算仅描述所选实际观察区间，不前向填充，不将跨缺失日变化称为单日收益；不推断因果或涨跌方向。",
            "日期是数值覆盖期，不是发布日期；历史修订仅在已采集版本与可用时间内读取，非事前预测证据。",
            "单位或币种未提供时保留未知；频率未知的序列不能用于日频归因。不同单位的序列分别描述，不相减或排名。",
        ],
    }, {"operation": action, "field": field, "change": "last minus first observed value",
        "comparison": "same observed endpoints on intersection of dates; no forward fill",
        "version_policy": page["provenance"]}, rows)


def ewma_price_evidence(series, *, instrument_type, calendar, as_of):
    """Calculate on the latest contiguous run of actual exchange-session prices."""
    cutoff = cutoff_instant(as_of)
    crypto = instrument_type == "crypto"
    half_life, minimum_returns, window, annualization = (30, 90, 365, 365.25) if crypto else (EWMA_HALF_LIFE, EWMA_MIN_RETURNS, EWMA_WINDOW, 252)
    metadata = series.get("metadata") or {}
    result = {
        "status": "unavailable", "current": None, "previous": None, "five_sessions_ago": None,
        "change_pp": None, "change_pct": None, "five_session_change_pp": None,
        "history": [], "historical_reference": None, "limitations": [], "input_points": [],
        "methodology": {
            "metric": "ewma_volatility", "half_life_sessions": half_life,
            "minimum_returns": minimum_returns, "maximum_returns": window,
            "annualization": annualization, "return_basis": "log(P_t/P_previous_session)",
            "variance": "normalized exponential weights; weighted mean removed; population variance",
            "definition": "30 UTC-calendar-day half-life; completed daily spot prices" if crypto else "1m means a 21-trading-observation half-life, not a one-month rolling sample",
            "quote_basis": metadata.get("quote_basis"), "return_kind": metadata.get("return_kind"),
            "calendar": calendar,
        },
    }
    if instrument_type not in {"equity", "etf", "index", "crypto"} or metadata.get("quote_basis") not in {"close", "adjusted_close", "last"}:
        result["limitations"].append("本指标仅适用于交易所日频价格；普通基金或净值口径不套用此年化波动定义。")
        return result
    if crypto and calendar != "24/7":
        result["limitations"].append("加密资产缺少24/7 UTC日线合同，未套用交易所交易日年化。")
        return result
    if metadata.get("return_series_status") not in {"ready", "complete", "partial"} or metadata.get("return_segment_breaks"):
        result["limitations"].append("价格收益口径未就绪或存在待确认断点。")
        return result
    cutoff_day = cutoff.astimezone(UTC).date().isoformat()
    points = sorted((p for p in series.get("points", [])
                     if (p["date"] < cutoff_day if crypto else p["date"] <= cutoff_day)), key=lambda p: p["date"])
    # Older prices are outside every estimate below. In particular they need not
    # fall inside the calendar package's retained historical session coverage.
    points = points[-2 * window - 1:]
    if not points:
        result["limitations"].append("截止时间内没有可用价格。")
        return result
    days = [date.fromisoformat(p["date"]) for p in points]
    if len(set(days)) != len(days) or any(_number(p.get("value")) is None or float(p["value"]) <= 0 for p in points):
        result["limitations"].append("价格日期重复或存在无效价格，未计算波动。")
        return result
    sessions = _market_calendar_sessions(calendar, days[0], days[-1]) if calendar else None
    if not sessions or days[0] not in sessions or days[-1] not in sessions:
        result["limitations"].append("缺少覆盖实际样本的交易日历，无法确认日频收益间隔。")
        return result
    positions = {day: i for i, day in enumerate(sessions)}
    if any(day not in positions for day in days):
        result["limitations"].append("价格含非交易日观察，未将净值填充或假日值作为交易收益。")
        return result
    last_break = 0
    for index in range(1, len(days)):
        if positions[days[index]] != positions[days[index - 1]] + 1:
            last_break = index
    if last_break:
        result["limitations"].append("历史存在缺失交易日；只用最后缺口之后的连续观察重新计算，不跨缺口合成日收益。")
    # Retain one year of volatility history, with a year of prior prices for its first estimate.
    points = points[last_break:]
    result["input_points"] = points
    if len(points) < minimum_returns + 1:
        result["limitations"].append(f"连续实际日收益不足{minimum_returns}个；未生成短样本年化波动。")
        return result
    returns = [log(float(b["value"]) / float(a["value"])) for a, b in zip(points, points[1:])]
    decay = 0.5 ** (1 / half_life)
    history = []
    for stop in range(max(minimum_returns, len(returns) - window + 1), len(returns) + 1):
        sample = returns[max(0, stop - window):stop]
        weights = [decay ** index for index in range(len(sample) - 1, -1, -1)]
        total = sum(weights)
        mean = sum(w * value for w, value in zip(weights, sample)) / total
        variance = sum(w * (value - mean) ** 2 for w, value in zip(weights, sample)) / total
        history.append({"date": points[stop]["date"], "volatility_pct": sqrt(variance * annualization) * 100,
                        "returns": len(sample), "sample_start": points[stop - len(sample)]["date"]})
    current = history[-1]
    previous = history[-2] if len(history) >= 2 else None
    five = history[-6] if len(history) >= 6 else None
    # Earlier estimates only: current value is not its own historical reference.
    earlier = [item["volatility_pct"] for item in history[:-1]]
    result.update(
        status="available", current=current, previous=previous, five_sessions_ago=five,
        change_pp=current["volatility_pct"] - previous["volatility_pct"] if previous else None,
        change_pct=(current["volatility_pct"] / previous["volatility_pct"] - 1) * 100
            if previous and previous["volatility_pct"] > 0 else None,
        five_session_change_pp=current["volatility_pct"] - five["volatility_pct"] if five else None,
        history=history,
        historical_reference={"start_date": history[0]["date"], "end_date": history[-2]["date"],
                              "observations": len(earlier), "median_pct": median(earlier),
                              "minimum_pct": min(earlier), "maximum_pct": max(earlier)} if earlier else None,
    )
    result["limitations"].append("波动是历史风险观察；没有预设报警阈值，不等同于未来下跌或减仓建议。")
    return result


def instrument_price_risk(session, instrument_id, *, as_of):
    from investment_studio_instrument_core.db_models import Instrument
    from watchlist_app.db.models import InstrumentChartReadModel

    cutoff = cutoff_instant(as_of)
    instrument = session.get(Instrument, instrument_id)
    chart = session.get(InstrumentChartReadModel, instrument_id)
    if instrument is None:
        raise ValueError("Unknown registered instrument")
    clock = chart.last_recalculated_at if chart else None
    known_at = clock.replace(tzinfo=clock.tzinfo or UTC) if clock else None
    source_clock = chart.source_cutoff_at if chart else None
    source_clock = source_clock.replace(tzinfo=source_clock.tzinfo or UTC) if source_clock else None
    if known_at is None or known_at > cutoff or (source_clock is not None and source_clock > cutoff):
        return _evidence("价格波动研究", cutoff, {
            "status": "unavailable", "limitations": ["没有本研究截止时间之前生成的价格快照，未使用后来重算数据。"],
        }, "Current retained chart snapshot must predate the research cutoff.", instrument_id=instrument_id)
    series = chart.payload_json.get("research_returns") or {}
    calendar = (instrument.source_settings_json or {}).get("market_calendar") or instrument.exchange_code
    data = ewma_price_evidence(series, instrument_type=instrument.instrument_type, calendar=calendar, as_of=cutoff)
    return _evidence("价格波动研究 · 30日半衰期EWMA" if instrument.instrument_type == "crypto" else "价格波动研究 · 21交易日半衰期EWMA", cutoff, data, data["methodology"],
                     instrument_id=instrument_id, input_snapshot={
                         "source_type": "instrument_chart_snapshot", "instrument_id": instrument_id,
                         "known_at": known_at.isoformat(),
                         "source_cutoff_at": source_clock.isoformat() if source_clock else None,
                         "materialization_version": chart.materialization_version,
                         "currency": series.get("currency"), "metadata": series.get("metadata"),
                         "frequency": series.get("frequency"), "freshness": chart.data_freshness_status,
                     })
