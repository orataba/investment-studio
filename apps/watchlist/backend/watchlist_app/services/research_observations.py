"""Small deterministic research observations over retained, attributable inputs.

No network fetch, inferred holdings, model-generated chart values or trade rules.
The numeric route retains these outputs in the existing computed_metrics namespace.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import exchange_calendars
from exchange_calendars.errors import CalendarError
from sqlalchemy.exc import SQLAlchemyError
from studio_market.numeric.store import cutoff_instant

from watchlist_app.services.calculation_frequency import _market_calendar_sessions, source_calendar_date
from watchlist_app.services.research_metrics import _evidence, _number, ewma_price_evidence
from watchlist_app.services.return_windows import (
    named_return_window_spec, period_return_percent, resolve_return_window, return_window_metadata,
)
from watchlist_app.services.sector_market_data import all_rows, holding_type, numeric_store


OBSERVATION_METHOD_VERSION = "watchlist-observations/v1"
REACTION_METHOD_VERSION = "event-daily-reaction/v1"


def _completed(day, calendar, cutoff):
    if calendar == "24/7":
        return day < cutoff.astimezone(UTC).date()
    try:
        exchange = exchange_calendars.get_calendar("XSHG" if calendar == "XSHE" else calendar)
        # Old canonical history may precede the calendar package's default
        # rolling coverage; it is still completed. Path/session validation below
        # requests the complete historical calendar when it is actually needed.
        if day < cutoff.astimezone(exchange.tz).date():
            return True
        return exchange.is_session(day.isoformat()) and exchange.session_close(day.isoformat()).to_pydatetime() <= cutoff
    except (CalendarError, ValueError, TypeError):
        return False


def _points(series):
    metadata = series.get("metadata") or {}
    if metadata.get("return_series_status") not in {"ready", "complete", "partial"} or metadata.get("return_segment_breaks"):
        return []
    points = series.get("points") or []
    if len({p.get("date") for p in points}) != len(points):
        return []
    if any(_number(p.get("value")) is None or float(p["value"]) <= 0 for p in points):
        return []
    return sorted(({**p, "value": float(p["value"]), "as_of_date": date.fromisoformat(p["date"])} for p in points), key=lambda p: p["date"])


def _comparable(left, right):
    if not left.get("currency") or left.get("currency") != right.get("currency"):
        return "币种不同或未知，未进行汇率换算。"
    kind = (left.get("metadata") or {}).get("return_kind")
    if kind not in {"total_return", "price_return"} or kind != (right.get("metadata") or {}).get("return_kind"):
        return "收益口径未知或不一致，未混用复权与非复权收益。"
    return None


def relative_performance(series, benchmark=None, *, as_of_date, windows=("1W", "1M")):
    """Exact shared endpoints; calendar spans remain anchored to the request date."""
    target = _points(series)
    other = {p["date"]: p for p in _points(benchmark or {})}
    reason = _comparable(series, benchmark) if benchmark is not None else "未配置可比较基准。"
    common = [p for p in target if p["date"] in other] if reason is None else target
    result = []
    for name in windows:
        spec = named_return_window_spec(name, as_of_date)
        window = resolve_return_window(common, requested_start_date=spec.requested_start_date,
            requested_end_date=spec.requested_end_date, anchor_mode=spec.anchor_mode)
        row = {"window": name, "status": "unavailable", "target_return_pct": None,
            "benchmark_return_pct": None, "difference_pp": None,
            "requested_start_date": spec.requested_start_date.isoformat(),
            "requested_end_date": as_of_date.isoformat(), "anchor_date": None, "end_date": None,
            "currency": series.get("currency"), "return_kind": (series.get("metadata") or {}).get("return_kind"),
            "limitation": reason}
        if window:
            row.update(return_window_metadata(window), target_return_pct=period_return_percent(window),
                status="partial" if reason else "available")
            if reason is None:
                comparison = (other[window.end_date.isoformat()]["value"] / other[window.anchor_date.isoformat()]["value"] - 1) * 100
                row.update(benchmark_return_pct=comparison, difference_pp=row["target_return_pct"] - comparison)
        else:
            row["limitation"] = reason or "共同实际观察日不足，不能形成请求区间。"
        result.append(row)
    return result


def volume_observation(rows, *, calendar, as_of):
    """Latest volume against previous volume, preserving units and session gaps."""
    cutoff = cutoff_instant(as_of)
    rows = sorted((r for r in rows if _completed(date.fromisoformat(r["date"]), calendar, cutoff)), key=lambda r: r["date"])
    result = {"status": "unavailable", "current": None, "previous": None, "change_pct": None,
        "unit": "shares", "method": "最近两个相邻完整交易日成交股数的百分比变化；不设异常阈值。"}
    if len(rows) < 2:
        return result
    before, latest = rows[-2:]
    values = [_number(r.get("volume")) for r in (before, latest)]
    sessions = _market_calendar_sessions(calendar, date.fromisoformat(before["date"]), date.fromisoformat(latest["date"]))
    if sessions is None or len(sessions) != 2 or any(v is None or v < 0 for v in values):
        return result
    result.update(status="available" if values[0] > 0 else "partial",
        current={"date": latest["date"], "value": values[1], "source_id": latest["source_id"]},
        previous={"date": before["date"], "value": values[0], "source_id": before["source_id"]},
        change_pct=(values[1] / values[0] - 1) * 100 if values[0] > 0 else None)
    return result


def _long_only_weights(holdings):
    weights = [_number(row.get("weight_percent")) for row in holdings]
    return (bool(holdings) and all(w is not None and w >= 0 for w in weights)
        and sum(Decimal(str(w)) for w in weights) <= Decimal("100")
        and all(_number(row.get("shares")) is None or _number(row.get("shares")) >= 0 for row in holdings))


def basket_observations(holdings, member_series, *, as_of_date, total_members=None, ordinary_equity=False, calendar=None):
    """Current disclosed basket only; no historic constituents or contribution claims.

    `ordinary_equity` is certified by the caller from actual dated holdings and
    product structure, never from the name or an LLM argument.
    """
    limits = ["当前持仓篮子的历史表现，不代表当时真实持仓、ETF收益贡献或策略回测。"]
    dates = {r.get("snapshot_date") for r in holdings}
    valid_date = len(dates) == 1 and None not in dates and next(iter(dates)) <= as_of_date.isoformat()
    keys = [r.get("holding_key") for r in holdings]
    weights = [_number(r.get("weight_percent")) for r in holdings]
    weights_valid = _long_only_weights(holdings)
    identities_valid = None not in keys and len(set(keys)) == len(keys)
    result = {"status": "unavailable", "holdings_date": next(iter(dates)) if len(dates) == 1 else None,
        "total_members": total_members, "disclosed_members": len(holdings), "top10": [],
        "ranking_status": "available" if all(w is not None for w in weights) else "partial",
        "concentration_pct": None, "concentration_status": "unavailable",
        "breadth": {"status": "unavailable", "current_pct": None, "previous_pct": None, "change_pp": None,
            "valid_members": 0, "above_members": 0, "weight_coverage_pct": None, "common_members": [],
            "as_of_date": as_of_date.isoformat(), "previous_date": None, "scope": "unavailable"},
        "limitations": limits}
    if not holdings or not valid_date or not identities_valid:
        limits.append("持仓日期、身份或实际持仓资料不足；未按名称猜测成分。")
        return result
    ranked = sorted(holdings, key=lambda r: (-(_number(r.get("weight_percent")) if _number(r.get("weight_percent")) is not None else -1), r["holding_key"]))
    for row in ranked[:10]:
        series = member_series.get(row.get("holding_symbol")) or {}
        returns = relative_performance(series, as_of_date=as_of_date)
        result["top10"].append({"holding_key": row["holding_key"], "symbol": row.get("holding_symbol"),
            "name": row.get("holding_name"), "weight_percent": _number(row.get("weight_percent")),
            "source_id": row.get("source_id"), "returns": returns,
            "status": "available" if all(r["target_return_pct"] is not None for r in returns) else "partial" if any(r["target_return_pct"] is not None for r in returns) else "unavailable"})
    result["status"] = "partial"
    if ordinary_equity and weights_valid and all(w is not None for w in weights):
        result.update(concentration_status="available", concentration_pct=sum(_number(r.get("weight_percent")) for r in ranked[:10]))
    else:
        limits.append("未确认普通非杠杆多头股票篮子与统一权重分母，不发布普通Top 10集中度。")
    if not weights_valid:
        limits.append("持仓存在缺失、负权重或总权重超过100%；不合并为普通权重覆盖。")
    if result["ranking_status"] == "partial":
        limits.append("部分成分权重缺失，已知权重排序不能保证是整个篮子的真实Top 10。")
    if not ordinary_equity or not weights_valid:
        limits.append("底层股票及非杠杆结构未完整确认；仅保留披露的Top 10信息，不套用股票内部广度。")
        return result
    sessions = _market_calendar_sessions(calendar, as_of_date - timedelta(days=200), as_of_date) if calendar else None
    if not sessions or sessions[-1] != as_of_date or len(sessions) < 51:
        limits.append("缺少覆盖50个交易观察及前次比较的日历。")
        return result
    current_dates, previous_dates = [d.isoformat() for d in sessions[-50:]], [d.isoformat() for d in sessions[-51:-1]]
    valid, both = [], []
    for row in holdings:
        series = member_series.get(row.get("holding_symbol")) or {}
        points = {p["date"]: p["value"] for p in _points(series)}
        # Each member uses a confirmed, consistent currency/basis internally.
        if not series.get("currency") or (series.get("metadata") or {}).get("return_kind") not in {"total_return", "price_return"}:
            continue
        observed_dates = sorted(day for day in points if day <= as_of_date.isoformat())
        if observed_dates[-50:] != current_dates:
            continue
        current = points[current_dates[-1]] > sum(points[d] for d in current_dates) / 50
        observed_previous = [day for day in observed_dates if day <= previous_dates[-1]]
        previous = points[previous_dates[-1]] > sum(points[d] for d in previous_dates) / 50 if observed_previous[-50:] == previous_dates else None
        item = {"holding_key": row["holding_key"], "symbol": row.get("holding_symbol"), "above": current,
            "previous_above": previous, "weight_percent": _number(row.get("weight_percent"))}
        valid.append(item)
        if previous is not None:
            both.append(item)
    top_symbols = {r.get("holding_symbol") for r in ranked[:10]}
    if valid and {r["symbol"] for r in valid}.issubset(top_symbols) and (total_members is None or total_members > len(valid)):
        limits.append("目前行情仅覆盖披露Top 10范围，不能输出整个ETF的内部广度。")
        result["breadth"]["scope"] = "top10_only"
        return result
    breadth = result["breadth"]
    if valid:
        complete = total_members == len(valid) == len(holdings)
        breadth.update(status="available" if complete else "partial", scope="full_disclosed_basket" if complete else "covered_members",
            current_pct=sum(r["above"] for r in valid) / len(valid) * 100, valid_members=len(valid),
            above_members=sum(r["above"] for r in valid), members=valid,
            weight_coverage_pct=sum(r["weight_percent"] for r in valid) if weights_valid else None,
            common_members=[r["holding_key"] for r in both], previous_date=previous_dates[-1])
        if both:
            previous = sum(r["previous_above"] for r in both) / len(both) * 100
            current_common = sum(r["above"] for r in both) / len(both) * 100
            breadth.update(previous_pct=previous, current_common_pct=current_common,
                change_pp=current_common - previous, comparison_members=len(both))
        if not complete:
            limits.append("仅为已覆盖成分广度；前后变化只用同时有效的共同成员，样本变化不解释为行情变化。")
    return result


def _snapshot(session, instrument_id, cutoff):
    from investment_studio_instrument_core.db_models import Instrument
    from watchlist_app.db.models import InstrumentChartReadModel
    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        raise ValueError("Unknown registered instrument")
    chart = session.get(InstrumentChartReadModel, instrument_id)
    calendar = (instrument.source_settings_json or {}).get("market_calendar") or instrument.exchange_code
    known = chart.last_recalculated_at if chart else None
    source = chart.source_cutoff_at if chart else None
    known = known.replace(tzinfo=known.tzinfo or UTC) if known else None
    source = source.replace(tzinfo=source.tzinfo or UTC) if source else None
    if known is None or known > cutoff or (source is not None and source > cutoff):
        return instrument, {}, calendar, {"status": "unavailable", "instrument_id": instrument_id,
            "limitation": "没有本研究截止时间之前生成的价格快照，未使用后来重算数据。"}
    series = deepcopy(chart.payload_json.get("research_returns") or {})
    # Keep only completed sessions for listed daily observations. Fund NAV keeps
    # its existing clock and is not coerced to a trading-session template.
    if instrument.instrument_type in {"equity", "etf", "index", "crypto"}:
        series["points"] = [p for p in series.get("points", []) if _completed(date.fromisoformat(p["date"]), calendar, cutoff)]
    return instrument, series, calendar, {"source_type": "instrument_chart_snapshot", "instrument_id": instrument_id,
        "known_at": known.isoformat(), "source_cutoff_at": source.isoformat() if source else None,
        "materialization_version": chart.materialization_version, "freshness": chart.data_freshness_status,
        "currency": series.get("currency"), "metadata": series.get("metadata"), "series": series}


def _benchmark(session, instrument_id, benchmark_id, cutoff):
    from watchlist_app.db.models import InstrumentManualProfile
    if not benchmark_id:
        profile = session.get(InstrumentManualProfile, instrument_id)
        benchmark_id = ((profile.nav_settings_json or {}).get("default_benchmark_instrument_id") if profile else None)
    if not benchmark_id or benchmark_id == instrument_id:
        return None, None, None
    _, series, _, snapshot = _snapshot(session, benchmark_id, cutoff)
    return benchmark_id, series, snapshot


def _table(key, title, columns, rows):
    return {"key": key, "title": title, "columns": [{"key": k, "label": label, "unit": unit} for k, label, unit in columns],
        "rows": [{k: row.get(k) for k, _, _ in columns} for row in rows]}


def _raw_market(instrument, cutoff, store, as_of_date):
    """Read existing FMP observations in batches; provider identity is explicit."""
    symbol = next((i.identifier_value.removeprefix("fmp:") for i in instrument.identifiers
        if i.identifier_type == "provider_symbol" and i.identifier_value.startswith("fmp:")), None)
    if not symbol:
        return [], [], {}, None, []
    store = store or numeric_store()
    holdings = all_rows(store, "etf_holdings", symbols=[symbol], as_of=cutoff) if instrument.instrument_type == "etf" else []
    symbols = sorted({symbol, *(r["holding_symbol"] for r in holdings if r.get("holding_symbol"))})
    profiles = store.latest("company_profiles", symbols=symbols, as_of=cutoff, limit=100000)["rows"]
    profiles_by_symbol = {r["symbol"]: r for r in profiles}
    info = store.latest("etf_info", symbols=[symbol], as_of=cutoff)["rows"] if holdings else []
    # This read horizon covers 50 observations and 1M endpoints. All rows are
    # paged, not silently truncated when the fund holds many securities.
    prices = all_rows(store, "us_eod_daily", symbols=symbols, as_of=cutoff,
        start=(as_of_date - timedelta(days=200)).isoformat(), end=as_of_date.isoformat())
    calendar = (instrument.source_settings_json or {}).get("market_calendar") or instrument.exchange_code
    prices = [r for r in prices if _completed(date.fromisoformat(r["date"]), calendar, cutoff)]
    grouped = {}
    for row in prices:
        grouped.setdefault(row["symbol"], []).append(row)
    # Only these rows can enter either 50-observation window or a 1W/1M return.
    # Retain every used source, without copying unnecessary daily history into
    # each research version. Missing sessions still fail closed in the consumer.
    grouped = {symbol: sorted(rows, key=lambda r: r["date"])[-51:] for symbol, rows in grouped.items()}
    prices = [row for rows in grouped.values() for row in rows]
    series = {}
    for member, rows in grouped.items():
        profile = profiles_by_symbol.get(member) or {}
        currencies = {r.get("currency") or profile.get("currency") for r in rows}
        series[member] = {"currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "metadata": {"quote_basis": "adjusted_close", "return_kind": "total_return", "return_series_status": "ready"},
            "points": [{"date": r["date"], "value": r.get("adjusted_close"), "source_id": r["source_id"]} for r in rows]}
    normalized = []
    for row in holdings:
        profile = profiles_by_symbol.get(row.get("holding_symbol")) or {}
        kind = holding_type(row.get("holding_name"), row.get("holding_symbol"), profile)
        if kind == "equity" and (profile.get("is_etf") is not False or profile.get("is_fund") is not False):
            kind = "unclassified"
        normalized.append({**row, "holding_type": kind})
    return grouped.get(symbol, []), normalized, series, (info[0] if info else None), [*holdings, *profiles, *info, *prices]


def instrument_observations(session, instrument_id, *, as_of, benchmark_id=None, store=None):
    from watchlist_app.services.canonical_recalc import _current_drawdown
    cutoff = cutoff_instant(as_of)
    instrument, series, calendar, snapshot = _snapshot(session, instrument_id, cutoff)
    benchmark_id, benchmark, benchmark_snapshot = _benchmark(session, instrument_id, benchmark_id, cutoff)
    points = _points(series)
    as_of_date = date.fromisoformat(points[-1]["date"]) if points else source_calendar_date(cutoff, calendar)
    limits = ["固定数值只提供观察线索，不预设买卖阈值，也不自动确认机会或风险。"]
    if snapshot.get("limitation"):
        limits.append(snapshot["limitation"])
    relative = relative_performance(series, benchmark, as_of_date=as_of_date)
    ewma = ewma_price_evidence(series, instrument_type=instrument.instrument_type, calendar=calendar, as_of=cutoff)
    # Canonical path definition; validate the actual path before calling its helper.
    days = [p["as_of_date"] for p in points]
    sessions = _market_calendar_sessions(calendar, days[0], days[-1]) if calendar and days else None
    complete_path = sessions is not None and tuple(days) == tuple(sessions)
    drawdown = {"status": "available" if complete_path and len(points) >= 2 else "unavailable",
        "current_pct": _current_drawdown(points) if complete_path else None,
        "start_date": points[0]["date"] if points else None, "end_date": points[-1]["date"] if points else None}
    volume, holdings, members, info, originals = {"status": "unavailable", "change_pct": None}, [], {}, None, []
    if instrument.instrument_type in {"equity", "etf"}:
        try:
            raw, holdings, members, info, originals = _raw_market(instrument, cutoff, store, as_of_date)
            volume = volume_observation(raw, calendar=calendar, as_of=cutoff)
        except (OSError, SQLAlchemyError) as error:
            limits.append("共享数值来源读取失败；成交量与持仓观察不可用，未用缺失值替代。")
            volume = {"status": "unavailable", "change_pct": None, "error_kind": type(error).__name__}
    observations = {"relative_performance": relative, "volume": volume, "ewma": ewma, "drawdown": drawdown}
    metrics = {"1W相对基准（百分点）": relative[0]["difference_pp"], "1M相对基准（百分点）": relative[1]["difference_pp"]}
    if instrument.instrument_type in {"equity", "index", "crypto"}:
        metrics["EWMA年化波动率（%）"] = (ewma.get("current") or {}).get("volatility_pct")
        metrics["当前回撤（%）"] = drawdown["current_pct"]
    if instrument.instrument_type == "equity":
        metrics["成交量较前一交易日（%）"] = volume.get("change_pct")
        if volume["status"] == "unavailable":
            limits.append("成交量缺少最近两个相邻完整交易日的有效记录。")
        elif volume["status"] == "partial":
            limits.append("前一交易日成交量为零，保留实际值但不计算百分比变化。")
    tables = [_table("relative", "共同区间表现", [("window", "区间", ""), ("anchor_date", "实际起点", ""),
        ("end_date", "实际终点", ""), ("currency", "币种", ""), ("return_kind", "收益口径", ""),
        ("target_return_pct", "标的收益", "%"), ("benchmark_return_pct", "基准收益", "%"),
        ("difference_pp", "收益差", "百分点"), ("limitation", "限制", "")], relative)]
    if instrument.instrument_type == "etf":
        count = (info or {}).get("holdings_count")
        # Actual complete all-equity positions and net-asset weights establish a
        # long-only unlevered basket. Partial disclosures cannot certify it.
        ordinary = (bool(holdings) and count == len(holdings)
            and all(r["holding_type"] in {"equity", "cash"} for r in holdings) and _long_only_weights(holdings))
        cash_count = sum(row["holding_type"] == "cash" for row in holdings) if ordinary else 0
        equities = [row for row in holdings if row["holding_type"] == "equity"] if ordinary else holdings
        member_count = count - cash_count if ordinary else count
        basket = basket_observations(equities, members, as_of_date=as_of_date, total_members=member_count,
            ordinary_equity=ordinary, calendar=calendar)
        if cash_count:
            basket["limitations"].append(f"已披露的 {cash_count} 个现金头寸不作为股票成分；股票权重仍以产品净资产为分母，未归一为100%。")
        basket["total_positions"] = count
        observations["basket"] = basket
        limits.extend(basket["limitations"])
        breadth = basket["breadth"]
        metrics["Top 10集中度（%）"] = basket["concentration_pct"]
        metrics["已覆盖成分高于50观察均价（%）"] = breadth["current_pct"]
        limits.append(f"广度有效成员 {breadth['valid_members']} / 总股票成员 {member_count if member_count is not None else '未知'}；"
            f"可确认权重覆盖 {breadth['weight_coverage_pct'] if breadth['weight_coverage_pct'] is not None else '未知'}%；"
            f"持仓日期 {basket['holdings_date'] or '未知'}。")
        if breadth["change_pp"] is not None:
            limits.append(f"共同成员 {breadth['comparison_members']} 个，较 {breadth['previous_date']} 的广度变化 {breadth['change_pp']:.4g} 个百分点。")
        tables.append(_table("top10", ("已披露Top 10" if basket["ranking_status"] == "available" else "已知权重排序（权重覆盖不完整）") + " · 当前持仓篮子的历史表现", [
            ("symbol", "证券", ""), ("name", "名称", ""), ("weight_percent", "披露权重", "%"),
            ("currency", "报价币种", ""), ("return_kind", "收益口径", ""), ("return_1w", "1W收益", "%"), ("return_1m", "1M收益", "%"),
            ("anchor_1w", "1W实际起点", ""), ("anchor_1m", "1M实际起点", ""), ("end", "实际终点", ""),
            ("status", "覆盖", "")], [{**r, "currency": r["returns"][0]["currency"], "return_kind": r["returns"][0]["return_kind"],
                "return_1w": r["returns"][0]["target_return_pct"], "return_1m": r["returns"][1]["target_return_pct"],
                "anchor_1w": r["returns"][0]["anchor_date"], "anchor_1m": r["returns"][1]["anchor_date"],
                "end": r["returns"][0]["end_date"]} for r in basket["top10"]]))
    limits.extend(ewma.get("limitations", []))
    limits.extend(r["limitation"] for r in relative if r["limitation"])
    basket = observations.get("basket") or {}
    available = (any(isinstance(v, (int, float)) for v in metrics.values())
        or any(r["target_return_pct"] is not None for r in relative)
        or any(r["status"] != "unavailable" for r in basket.get("top10", [])))
    complete = all(value is not None for value in metrics.values()) and all(r["status"] == "available" for r in relative)
    if basket:
        complete = complete and basket["breadth"]["status"] == "available" and all(r["status"] == "available" for r in basket["top10"])
    status = "available" if available and complete else "partial" if available else "unavailable"
    data = {"analysis_kind": "watchlist_observations", "status": status, "method_version": OBSERVATION_METHOD_VERSION,
        "observation_key": f"watchlist-observations:{instrument_id}", "as_of_date": as_of_date.isoformat() if points else None,
        "benchmark_id": benchmark_id, "summary": (f"截至 {as_of_date.isoformat()} 的固定量化观察；各项保留实际区间与覆盖样本。" if points else "固定量化观察；仅披露已取得部分的实际日期与覆盖样本。") if available else "量化数据不足，尚不能形成有效观察。",
        "metrics": metrics, "tables": tables, "charts": [], "limitations": list(dict.fromkeys(limits)), "observations": observations}
    return _evidence("量化观察", cutoff, data, {"version": OBSERVATION_METHOD_VERSION,
        "relative_return": "same-currency same-return-kind observed endpoints; arithmetic difference in percentage points",
        "breadth": "price above mean of 50 completed observations; changes use common members",
        "holdings": "dated disclosed weights; current basket history, not historical holdings or contribution"}, originals,
        instrument_id=instrument_id, input_snapshot=snapshot, benchmark_snapshot=benchmark_snapshot)


def event_reaction(series, *, event_date, calendar, as_of, benchmark=None, timing="date_only", sessions=1):
    """Daily observation window with conservative date-only / after-close timing."""
    if timing not in {"date_only", "before_open", "after_close"} or sessions not in {1, 3, 5}:
        raise ValueError("Choose a supported event timing and 1, 3 or 5 completed sessions")
    cutoff = cutoff_instant(as_of)
    event_day = date.fromisoformat(str(event_date))
    result = {"analysis_kind": "event_market_reaction", "status": "unavailable", "event_date": event_day.isoformat(),
        "event_timing": timing, "sessions": sessions, "anchor_date": None, "end_date": None,
        "target_return_pct": None, "benchmark_return_pct": None, "difference_pp": None,
        "currency": series.get("currency"), "return_kind": (series.get("metadata") or {}).get("return_kind"),
        "limitations": ["价格变化是窗口内观察，不证明事件因果、市场一致看法或已经充分计价。"]}
    trading = _market_calendar_sessions(calendar, event_day - timedelta(days=20), event_day + timedelta(days=30)) if calendar else None
    if not trading:
        result["limitations"].append("缺少事件所在市场的交易日历。")
        return result
    anchor = max((d for d in trading if d < event_day or (timing != "before_open" and d == event_day)), default=None)
    if anchor is None or trading.index(anchor) + sessions >= len(trading):
        return result
    end = trading[trading.index(anchor) + sessions]
    result.update(anchor_date=anchor.isoformat(), end_date=end.isoformat())
    if timing == "date_only":
        result["limitations"].append("仅知事件日期：保守从当日收盘起观察后续完整交易日；未把事件日涨跌当作事后反应，可能遗漏日内反应。")
    if not _completed(end, calendar, cutoff):
        result["status"] = "pending"
        result["limitations"].append("观察窗口尚未结束，尚待观察。")
        return result
    points = {p["date"]: p["value"] for p in _points(series)}
    if anchor.isoformat() not in points or end.isoformat() not in points:
        result["limitations"].append("事件窗口已结束，但缺少精确起止日价格；未跨缺失日替代。")
        return result
    result.update(status="partial", target_return_pct=(points[end.isoformat()] / points[anchor.isoformat()] - 1) * 100)
    reason = _comparable(series, benchmark) if benchmark is not None else "未配置可比较基准。"
    other = {p["date"]: p["value"] for p in _points(benchmark or {})}
    if reason is None and anchor.isoformat() in other and end.isoformat() in other:
        value = (other[end.isoformat()] / other[anchor.isoformat()] - 1) * 100
        result.update(status="available", benchmark_return_pct=value, difference_pp=result["target_return_pct"] - value)
    else:
        result["limitations"].append(reason or "基准缺少相同实际起止日价格，未计算相对反应。")
    return result


def instrument_event_reaction(session, instrument_id, *, as_of, event_date, timing="date_only", benchmark_id=None, sessions=1):
    cutoff = cutoff_instant(as_of)
    instrument, series, calendar, snapshot = _snapshot(session, instrument_id, cutoff)
    if instrument.instrument_type not in {"equity", "etf", "index", "crypto"}:
        raise ValueError("事件日频价格反应仅适用于已有日频市场价格的标的")
    benchmark_id, benchmark, benchmark_snapshot = _benchmark(session, instrument_id, benchmark_id, cutoff)
    data = event_reaction(series, event_date=event_date, calendar=calendar, as_of=cutoff,
        benchmark=benchmark, timing=timing, sessions=sessions)
    data.update(method_version=REACTION_METHOD_VERSION, benchmark_id=benchmark_id,
        summary="观察窗口尚未结束，尚待观察。" if data["status"] == "pending" else "日频事件窗口表现；数据不构成事件因果证明。",
        metrics={"标的收益（%）": data["target_return_pct"], "基准收益（%）": data["benchmark_return_pct"],
            "收益差（百分点）": data["difference_pp"], "实际起点": data["anchor_date"], "实际终点": data["end_date"]},
        tables=[], charts=[])
    if snapshot.get("limitation"):
        data["limitations"].append(snapshot["limitation"])
    return _evidence("事件市场反应", cutoff, data, {"version": REACTION_METHOD_VERSION,
        "timing": timing, "sessions": sessions, "operation": "exact completed-session endpoints; no causal attribution"},
        instrument_id=instrument_id, input_snapshot=snapshot, benchmark_snapshot=benchmark_snapshot)


def observation_business_values(evidence):
    """Stable value/sample/method comparison; source IDs or read clocks are not change."""
    ignored = {"source_id", "source_ids", "observed_at", "available_at", "batch_id", "known_at", "source_cutoff_at"}
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key not in ignored}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    data = evidence.get("data") or {}
    return clean({key: data.get(key) for key in ("method_version", "observation_key", "benchmark_id", "observations")})
