"""Compare retained FMP estimate observations without rolling fiscal periods."""
from datetime import UTC, datetime

from sqlalchemy import select

from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.services.sector_market_data import SECTOR_ETF_TICKERS


_METRICS = {"revenue_avg": ("num_analysts_revenue", "currency"), "eps_avg": ("num_analysts_eps", "currency_per_share")}
_FREQUENCIES = {"annual": "annual_estimates", "quarter": "quarterly_estimates"}
_GAP_LABELS = {
    "estimate_currency_not_supplied": "FMP预期数据未提供币种，不能用股票报价币种代替。",
    "currency_unverified": "至少一侧预期币种未确认，数值差异仅作待核实线索。",
    "currency_changed": "两次预期币种不同，不能直接计算上修或下修。",
    "analyst_coverage_added": "前次无有效分析师覆盖，新增覆盖不等于预测上修。",
    "collection_time_unknown": "缺少明确的源记录采集时间，无法确定观测先后。",
    "source_observation_not_newer": "部分源记录采集时间尚未推进，重复读取不是新的预期观测。",
    "source_dataset_changed": "两次预期数据来源口径不同，变动尚未核实。",
    "current_weight_missing": "部分公司缺少当前ETF权重，权重覆盖不完整。",
    "current_estimates_missing": "当前快照没有可用的公司预期。",
}


def _clock(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _rows(companies):
    return {(symbol, frequency, row["target_period_end"]): row
            for symbol, company in companies.items() for frequency, field in _FREQUENCIES.items()
            for row in company.get(field, [])}


def _descriptor(run, iid):
    context = run.context_json or {}
    sector = next((row for row in context.get("sector_inputs", []) if row["instrument_id"] == iid), {})
    times = sorted({row["collected_at"] for row in _rows(context["sector_company_data"][iid]).values() if row.get("collected_at")})
    return {"run_id": run.entry_id, "analysis_status": run.status, "cutoff": context.get("cutoff"),
            "read_at": sector.get("source", {}).get("read_at"), "holdings_as_of": sector.get("holdings_as_of"),
            "collected_at_min": times[0] if times else None, "collected_at_max": times[-1] if times else None}


def compare_estimate_snapshots(iid, current, previous=None):
    """Return changes only for identical company/frequency/period/metric/currency.

    Currency-free provider values remain observable leads, not verified monetary
    revisions. ETF weights describe current exposure, never aggregated ETF EPS.
    """
    companies = current.context_json["sector_company_data"][iid]
    old_companies = previous.context_json["sector_company_data"][iid] if previous else {}
    latest, old = _rows(companies), _rows(old_companies)
    changes, observations, unmatched, metrics = [], [], [], []
    gaps = set()
    def weight(symbols):
        return sum(companies[symbol].get("weight_percent") or 0 for symbol in symbols)
    for frequency in _FREQUENCIES:
        for metric, (analyst_field, unit) in _METRICS.items():
            available, comparable, changed, unknown_currency = set(), set(), set(), set()
            for (symbol, row_frequency, period), row in latest.items():
                if row_frequency != frequency or row.get(metric) is None:
                    continue
                available.add(symbol)
                company = companies[symbol]
                before = old.get((symbol, frequency, period))
                currency = row.get("currency")
                if not currency:
                    unknown_currency.add(symbol)
                    gaps.add("estimate_currency_not_supplied")
                point = {"symbol": symbol, "name": company.get("name"), "weight_percent": company.get("weight_percent"),
                    "frequency": frequency, "target_period_end": period, "metric": metric, "unit": unit,
                    "currency": currency, "previous_currency": before.get("currency") if before else None,
                    "current_value": row[metric], "previous_value": before.get(metric) if before else None,
                    "current_collected_at": row.get("collected_at"), "previous_collected_at": before.get("collected_at") if before else None,
                    "current_num_analysts": row.get(analyst_field), "previous_num_analysts": before.get(analyst_field) if before else None,
                    "source_dataset": row.get("source_dataset"), "previous_source_dataset": before.get("source_dataset") if before else None,
                    "raw_sha256": row.get("raw_sha256"), "previous_raw_sha256": before.get("raw_sha256") if before else None}
                if previous is None:
                    continue
                if before is None or before.get(metric) is None:
                    unmatched.append({**point, "reason": "new_company_coverage" if symbol not in old_companies else
                                      "new_period_coverage" if before is None else "new_metric_coverage"})
                    continue
                reason = None
                old_clock, new_clock = _clock(before.get("collected_at")), _clock(row.get("collected_at"))
                if old_clock is not None and new_clock is not None and new_clock <= old_clock:
                    gaps.add("source_observation_not_newer")
                if not currency or not before.get("currency"):
                    reason = "currency_unverified"
                elif currency != before["currency"]:
                    reason = "currency_changed"
                elif before.get(analyst_field) == 0:
                    reason = "analyst_coverage_added"
                elif old_clock is None or new_clock is None:
                    reason = "collection_time_unknown"
                elif new_clock <= old_clock:
                    reason = "source_observation_not_newer"
                elif (row.get("source_dataset") and before.get("source_dataset")
                      and row["source_dataset"] != before["source_dataset"]):
                    reason = "source_dataset_changed"
                if reason:
                    gaps.add(reason)
                    if row[metric] != before[metric]:
                        observations.append({**point, "reason": reason})
                    continue
                comparable.add(symbol)
                if row[metric] == before[metric]:
                    continue
                changed.add(symbol)
                delta = row[metric] - before[metric]
                changes.append({**point, "delta": delta,
                    "delta_pct": delta / before[metric] * 100 if before[metric] > 0 else None,
                    "percent_change_status": "available" if before[metric] > 0 else "nonpositive_base",
                    "analyst_count_changed": row.get(analyst_field) != before.get(analyst_field)
                        if row.get(analyst_field) is not None and before.get(analyst_field) is not None else None})
            metrics.append({"frequency": frequency, "metric": metric, "current_company_count": len(available),
                "current_weight_pct": weight(available), "comparable_company_count": len(comparable),
                "comparable_weight_pct": weight(comparable), "changed_company_count": len(changed),
                "changed_weight_pct": weight(changed), "unknown_currency_company_count": len(unknown_currency),
                "unknown_currency_weight_pct": weight(unknown_currency)})
    if previous:
        for (symbol, frequency, period), row in old.items():
            for metric in _METRICS:
                if row.get(metric) is not None and latest.get((symbol, frequency, period), {}).get(metric) is None:
                    unmatched.append({"symbol": symbol, "frequency": frequency, "target_period_end": period,
                        "metric": metric, "previous_value": row[metric], "current_value": None,
                        "reason": "removed_company_coverage" if symbol not in companies else "missing_current_coverage"})
    if any(company.get("weight_percent") is None for company in companies.values()):
        gaps.add("current_weight_missing")
    if not latest:
        gaps.add("current_estimates_missing")
    return {"instrument_id": iid, "supported": True, "provider": "FMP",
        "source_id": f"estimates:{current.entry_id}:{iid}", "source_type": "analyst_estimate_changes",
        "title": f"FMP · {iid.upper()}成分公司预期观测对照",
        "status": "baseline" if previous is None else "comparable" if any(row["comparable_company_count"] for row in metrics) else "limited",
        "current_snapshot": _descriptor(current, iid), "previous_snapshot": _descriptor(previous, iid) if previous else None,
        "coverage": {"current_company_count": len(companies), "equity_weight_pct": weight(companies),
                     "weight_basis": "Current ETF holdings, percentage points; non-equity positions are not renormalized.", "metrics": metrics},
        "changes": changes, "observations": observations, "unmatched": unmatched,
        "gaps": [_GAP_LABELS[code] for code in sorted(gaps)], "gap_codes": sorted(gaps),
        "semantics": ["财期是预测对象，collected_at是源记录实际采集时间，read_at是本系统读取快照时间。",
            "只比较同公司、同频率、同财期、同指标及已知相同币种；新增覆盖与滚动财期不是上修。",
            "币种未确认的数值差异仅为待核实线索，不输出金额或百分比变动；报价币种不代替预期币种。",
            "共识均值变化可能来自分析师样本变化，不代表每位分析师都调整了预测；负数或零基数不计算百分比。",
            "这些是两次观测之间的变化，不是精确发布日期或实际调整时刻；未合成ETF EPS，也未判断投资材料性。"]}


def read_estimate_evidence(session, instrument_id, *, current_run=None):
    iid = instrument_id.strip().lower()
    empty = {"instrument_id": iid, "supported": iid.upper() in SECTOR_ETF_TICKERS, "provider": "FMP",
             "current_snapshot": None, "previous_snapshot": None, "coverage": {},
             "changes": [], "observations": [], "unmatched": [], "gaps": []}
    if not empty["supported"]:
        return {**empty, "status": "unsupported", "gaps": ["该标的尚未接入这11只美股行业ETF的FMP预期快照；A股及其他ETF不能套用。"]}
    runs = [run for run in session.scalars(select(ResearchEntry).where(ResearchEntry.topic_id.in_(["us-sector-daily-review", f"instrument-events:{iid}"]))
                                         .order_by(ResearchEntry.created_at.desc()))
            if iid in (run.context_json or {}).get("sector_company_data", {})]
    current = current_run or (runs[0] if runs else None)
    if current is None or iid not in (current.context_json or {}).get("sector_company_data", {}):
        return {**empty, "status": "no_snapshot", "gaps": ["尚未保留该ETF的公司预期快照。"]}
    previous = next((run for run in runs if run.entry_id != current.entry_id and run.created_at < current.created_at), None)
    return compare_estimate_snapshots(iid, current, previous)


def retained_estimate_sources(context):
    return {evidence["source_id"]: evidence for evidence in context.get("sector_estimate_evidence", [])}


def usable_estimate_change(source, cutoff, instrument_id):
    return (source.get("source_type") == "analyst_estimate_changes" and source.get("instrument_id") == instrument_id
            and bool(source.get("changes"))
            and all(_clock(row.get("current_collected_at")) is not None
                    and _clock(row["current_collected_at"]) <= cutoff for row in source["changes"]))
