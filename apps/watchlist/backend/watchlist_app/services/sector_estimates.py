"""Compare retained FMP estimate observations without rolling fiscal periods."""
from datetime import UTC, datetime, timedelta

from copy import deepcopy

from studio_market.numeric import NumericStore

from watchlist_app.services.sector_market_data import (
    all_rows, enrich_estimate, numeric_store, read_sector_market_data, reporting_statements_at,
)


_METRICS = {"revenue_avg": ("num_analysts_revenue", "currency"), "eps_avg": ("num_analysts_eps", "currency_per_share")}
_FREQUENCIES = {"annual": "annual_estimates", "quarter": "quarterly_estimates"}
_GAP_LABELS = {
    "estimate_currency_not_supplied": "FMP预期数据未提供币种，不能用股票报价币种代替。",
    "currency_unverified": "至少一侧预期币种未确认，数值差异仅作待核实线索。",
    "currency_changed": "两次预期币种不同，不能直接计算上修或下修。",
    "analyst_coverage_added": "前次无有效分析师覆盖，新增覆盖不等于预测上修。",
    "current_analyst_coverage_missing": "本次分析师样本为零，供应商数值不能视为有效共识或预测下修。",
    "collection_time_unknown": "缺少明确的源记录采集时间，无法确定观测先后。",
    "source_observation_not_newer": "部分源记录采集时间尚未推进，重复读取不是新的预期观测。",
    "source_dataset_changed": "两次预期数据来源口径不同，变动尚未核实。",
    "current_weight_missing": "部分公司缺少当前ETF权重，权重覆盖不完整。",
    "current_estimates_missing": "当前快照没有可用的公司预期。",
    "holding_type_unconfirmed": "部分披露持仓的证券类型未确认，未纳入公司预期范围；已确认公司的观察不能代表全部底层敞口。",
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


def _companies(observation):
    return observation["companies"]


def _descriptor(observation):
    return {key: value for key, value in observation.items() if key != "companies"}


def compare_estimate_snapshots(iid, current, previous=None, *, instrument_type="etf"):
    """Return changes only for identical company/frequency/period/metric/currency.

    Currency-free provider values remain observable leads, not verified monetary
    revisions. ETF weights describe current exposure, never aggregated ETF EPS.
    """
    companies = _companies(current)
    old_companies = _companies(previous) if previous else {}
    latest, old = _rows(companies), _rows(old_companies)
    changes, observations, unmatched, metrics = [], [], [], []
    gaps = set()
    def weight(symbols):
        if instrument_type == "equity":
            return None
        weights = [companies[symbol].get("weight_percent") for symbol in symbols]
        return None if any(value is None for value in weights) else sum(weights)
    for frequency in _FREQUENCIES:
        for metric, (analyst_field, unit) in _METRICS.items():
            available, comparable, changed, unknown_currency = set(), set(), set(), set()
            for (symbol, row_frequency, period), row in latest.items():
                if row_frequency != frequency or row.get(metric) is None:
                    continue
                company = companies[symbol]
                before = old.get((symbol, frequency, period))
                currency = row.get("currency")
                point = {"symbol": symbol, "name": company.get("name"), "weight_percent": company.get("weight_percent"),
                    "frequency": frequency, "target_period_end": period, "metric": metric, "unit": unit,
                    "currency": currency, "previous_currency": before.get("currency") if before else None,
                    "current_currency_status": row.get("currency_status"), "previous_currency_status": before.get("currency_status") if before else None,
                    "current_currency_source": row.get("currency_source") or company.get("reporting_currency_source"),
                    "previous_currency_source": (before or {}).get("currency_source") or old_companies.get(symbol, {}).get("reporting_currency_source"),
                    "current_value": row[metric], "previous_value": before.get(metric) if before else None,
                    "current_collected_at": row.get("collected_at"), "previous_collected_at": before.get("collected_at") if before else None,
                    "current_num_analysts": row.get(analyst_field), "previous_num_analysts": before.get(analyst_field) if before else None,
                    "source_dataset": row.get("source_dataset"), "previous_source_dataset": before.get("source_dataset") if before else None,
                    "raw_sha256": row.get("raw_sha256"), "previous_raw_sha256": before.get("raw_sha256") if before else None,
                    "current_source_id": row.get("source_id"), "previous_source_id": before.get("source_id") if before else None}
                if row.get(analyst_field) == 0:
                    gaps.add("current_analyst_coverage_missing")
                    unmatched.append({**point, "reason": "current_analyst_coverage_missing"})
                    continue
                available.add(symbol)
                if not currency:
                    unknown_currency.add(symbol)
                    gaps.add("estimate_currency_not_supplied")
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
    if instrument_type != "equity" and any(company.get("weight_percent") is None for company in companies.values()):
        gaps.add("current_weight_missing")
    if not latest:
        gaps.add("current_estimates_missing")
    return {"instrument_id": iid, "supported": True, "provider": "FMP",
        "company_symbols": sorted(set(companies) | set(old_companies)),
        "source_id": f"estimates:{current['observation_id']}:{iid}", "source_type": "analyst_estimate_changes",
        "title": f"FMP · {iid.upper()}{'公司' if instrument_type == 'equity' else '成分公司'}预期观测对照",
        "status": "baseline" if previous is None else "comparable" if any(row["comparable_company_count"] for row in metrics) else "limited",
        "current_snapshot": _descriptor(current), "previous_snapshot": _descriptor(previous) if previous else None,
        "coverage": {"current_company_count": len(companies), "equity_weight_pct": weight(companies),
                     "weight_basis": "Not applicable to a single company; no portfolio or fund weight is implied." if instrument_type == "equity" else
                         "Current disclosed fund holdings, percentage points; non-equity positions are not renormalized.", "metrics": metrics},
        "changes": changes, "observations": observations, "unmatched": unmatched,
        "gaps": [_GAP_LABELS[code] for code in sorted(gaps)], "gap_codes": sorted(gaps),
        "semantics": ["采集观察由数据维护任务逐次保存，与是否运行研究无关；财期是预测对象，collected_at是实际采集时间。",
            "只比较同公司、同频率、同财期、同指标及已知相同币种；新增覆盖与滚动财期不是上修。",
            "预期接口本身不披露币种；按公司财报币种推定时，另行保留推定状态及财报来源，不能视为接口直接披露。",
            "币种未确认的数值差异仅为待核实线索，不输出金额或百分比变动；报价币种不代替预期币种。",
            "共识均值变化可能来自分析师样本变化，不代表每位分析师都调整了预测；负数或零基数不计算百分比。",
            "这些是两次观测之间的变化，不是精确发布日期或实际调整时刻；未合成ETF EPS，也未判断投资材料性。"]}


def estimate_scope(session, instrument_id):
    """Resolve a real FMP identity; never turn an index or a fund name into a company."""
    from watchlist_app.services.shared_instrument_registry import get_shared_instrument
    instrument = get_shared_instrument(instrument_id, metadata_only=True) if session is not None else None
    if instrument is not None:
        if instrument.get("instrument_type") not in {"equity", "etf", "public_fund"}:
            return None
        symbol = next((str(item["identifier_value"]).removeprefix("fmp:") for item in instrument.get("identifiers", [])
                       if item.get("identifier_type") == "provider_symbol" and str(item.get("identifier_value", "")).startswith("fmp:")), None)
        if not symbol:
            return None
        return {"symbol": symbol.upper(), "instrument_type": instrument["instrument_type"], "name": instrument.get("instrument_name")}
    return None


def _capture_window(store, symbols, cutoff, since):
    """Retain every complete capture in the window and one baseline per scope.

    observations is bounded to 100 clocks per company/frequency. Walk its actual
    clocks backwards, rather than imposing a day/count limit on retained PIT.
    """
    from collections import defaultdict
    captures, cursor = {}, cutoff
    while True:
        page = store.observations("analyst_estimates", symbols=symbols, as_of=cursor,
                                  limit=100 if since is not None else 2)["observations"]
        groups = defaultdict(list)
        for row in page:
            group = row["symbol"], row["estimate_period"]
            clock = _clock(row["observed_at"])
            if clock is None:
                raise ValueError("Estimate capture has no timezone-aware observation clock")
            groups[group].append(clock)
            key = (*group, clock)
            if key not in captures or str(row.get("batch_id", "")) > str(captures[key].get("batch_id", "")):
                captures[key] = row
        if since is None:
            break
        # Dense/sparse company histories need separate stopping positions. Use
        # the newest still-full group's boundary, so no other group is skipped.
        pending = [min(clocks) for clocks in groups.values()
                   if len(set(clocks)) >= 100 and min(clocks) > since]
        if not pending:
            break
        cursor = max(pending) - timedelta(microseconds=1)
    grouped = defaultdict(list)
    for (symbol, frequency, _), row in captures.items():
        grouped[(symbol, frequency)].append(row)
    for group, history in grouped.items():
        history.sort(key=lambda row: _clock(row["observed_at"]))
        if since is not None:
            baseline = [row for row in history if _clock(row["observed_at"]) <= since]
            grouped[group] = baseline[-1:] + [row for row in history if _clock(row["observed_at"]) > since]
    return grouped


def read_estimate_evidence(session, instrument_id, *, as_of: datetime | None = None,
                           since: datetime | None = None, company_scope=None):
    """Compare each observed revision since the last successfully bound window.

    Without an earlier research boundary, two captures describe the initial
    baseline. A supplied fixed company scope avoids broad ETF constituent reads.
    """
    iid = instrument_id.strip().lower()
    scope = company_scope or estimate_scope(session, instrument_id)
    empty = {"instrument_id": iid, "supported": scope is not None, "provider": "FMP",
             "current_snapshot": None, "previous_snapshot": None, "coverage": {},
             "changes": [], "observations": [], "unmatched": [], "gaps": []}
    if not empty["supported"]:
        return {**empty, "status": "unsupported", "gaps": ["该标的没有适用且已登记的FMP公司预期来源；不能用相似公司或ETF替代。"]}
    if company_scope is not None and not scope["companies"]:
        return {**empty, "supported": bool(scope.get("symbol")),
            "status": "unsupported" if not scope.get("symbol") else "limited" if scope.get("coverage") else "not_applicable",
            "gaps": list(scope.get("coverage") or ["当前固定范围没有适用的直接股票公司，不对非公司敞口套用EPS预期。"])}
    cutoff = as_of if as_of is not None else datetime.now(UTC)
    if cutoff.tzinfo is None or since is not None and since.tzinfo is None:
        raise ValueError("Estimate history cutoff must include a timezone.")
    if since is not None and since > cutoff:
        raise ValueError("Estimate history boundary must not follow cutoff.")
    statement_history = []
    base = numeric_store()
    # Reuse the PostgreSQL pool, but keep the serial DuckDB context private to
    # this request. The cached base store is also used by concurrent readers.
    store = NumericStore(base.settings, engine=base.engine)

    def read_statements(store, symbols, as_of):
        statement_history.extend(all_rows(store, "financial_statements", symbols=symbols,
                                          as_of=as_of, versions=True, page_size=100000))
        return reporting_statements_at(statement_history, symbols, as_of)

    if company_scope is not None:
        companies = {row["symbol"]: {"name": row.get("name"), "weight_percent": row.get("weight_percent"),
            "annual_estimates": [], "quarterly_estimates": []} for row in scope["companies"]}
        market = {"holdings": [{"as_of_date": row.get("holdings_as_of")} for row in scope["companies"]], "gaps": []}
        if companies:
            read_statements(store, list(companies), cutoff)
    else:
        market = read_sector_market_data(session, scope["symbol"], as_of=cutoff,
                                        instrument_type=scope["instrument_type"], scope_only=True, store=store)
        if market is None:
            return {**empty, "status": "no_snapshot", "gaps": ["截至当前时点尚无可用的本公司或披露持仓对应的公司观测。"]}
        companies = {row["holding_symbol"]: {"name": row["holding_name"], "weight_percent": row.get("weight_percent"),
            "annual_estimates": [], "quarterly_estimates": []} for row in market["companies"]}
        if companies:
            read_statements(store, list(companies), cutoff)
    unconfirmed_holdings = any(row["kind"] == "unclassified_holding" for row in market["gaps"])
    if not companies:
        if unconfirmed_holdings:
            return {**empty, "status": "limited", "gap_codes": ["holding_type_unconfirmed"],
                    "gaps": [_GAP_LABELS["holding_type_unconfirmed"]]}
        if scope["instrument_type"] != "equity" and not market["holdings"]:
            return {**empty, "status": "no_snapshot", "gaps": ["尚无已披露持仓，不能确认公司预期是否适用于本基金。"]}
        return {**empty, "supported": False, "status": "not_applicable", "gaps": ["本次披露持仓不包含直接股票公司，不对这些持仓直接套用公司EPS预期；间接敞口仍需穿透核实。"]}
    grouped = _capture_window(store, list(companies), cutoff, since)
    if not grouped:
        return {**empty, "status": "no_snapshot", "gaps": ["截至当前时点尚未采集这些公司的分析师预期；首次采集后建立历史基线。"]
                + ([_GAP_LABELS["holding_type_unconfirmed"]] if unconfirmed_holdings else [])}
    from collections import defaultdict
    scopes_by_clock = defaultdict(set)
    for (symbol, _), history in grouped.items():
        for capture in history:
            scopes_by_clock[capture["observed_at"]].add(symbol)
    captured_rows, source_ids = {}, set()
    with store.query_session():
        for clock, symbols in scopes_by_clock.items():
            statements = reporting_statements_at(statement_history, symbols, clock)
            rows = defaultdict(list)
            for row in all_rows(store, "analyst_estimates", symbols=list(symbols), as_of=cutoff, observed_at=clock):
                rows[(row["symbol"], row["estimate_period"])].append(enrich_estimate(row, statements.get(row["symbol"])))
                source_ids.add(row["source_id"])
            captured_rows[clock] = rows
    current_companies, previous_companies = deepcopy(companies), deepcopy(companies)
    current_clocks, previous_clocks, transitions = [], [], []

    def observation(items, clocks, label):
        return {"observation_id": label + ":" + cutoff.isoformat(), "companies": items,
            "collected_at": max(clocks), "collected_at_min": min(clocks), "collected_at_max": max(clocks),
            "holdings_as_of": max((str(row.get("as_of_date") or "") for row in market["holdings"]), default="") or None,
            "holdings_observed_on": max((str(row.get("snapshot_date") or "") for row in market["holdings"]), default="") or None}

    for (symbol, frequency), history in grouped.items():
        field = _FREQUENCIES[frequency]
        last = history[-1]
        current_companies[symbol][field] = captured_rows[last["observed_at"]][(symbol, frequency)]
        current_clocks.append(last["observed_at"])
        baseline = history[0] if len(history) > 1 or since is not None and _clock(history[0]["observed_at"]) <= since else None
        if baseline:
            previous_companies[symbol][field] = captured_rows[baseline["observed_at"]][(symbol, frequency)]
            previous_clocks.append(baseline["observed_at"])
        for before, after in zip(history, history[1:]):
            def single(capture):
                company = {**companies[symbol], field: captured_rows[capture["observed_at"]][(symbol, frequency)]}
                return observation({symbol: company}, [capture["observed_at"]], capture["observed_at"])
            change = compare_estimate_snapshots(iid, single(after), single(before), instrument_type=scope["instrument_type"])
            for name in ("changes", "observations", "unmatched"):
                for row in change[name]:
                    row.update(previous_observation_at=before["observed_at"], current_observation_at=after["observed_at"])
            transitions.append(change)
    current = observation(current_companies, current_clocks, "current")
    previous = observation(previous_companies, previous_clocks, "baseline") if previous_clocks else None
    result = compare_estimate_snapshots(iid, current, previous, instrument_type=scope["instrument_type"])
    if since is not None:
        # Retain reversals, not just the end-minus-start result. Current coverage
        # still describes unique companies, not the number of revisions.
        endpoint_change_count = len(result["changes"])
        for name in ("changes", "observations", "unmatched"):
            result[name] = [row for transition in transitions for row in transition[name]]
        for metric in result["coverage"]["metrics"]:
            changed = {row["symbol"] for row in result["changes"]
                       if row["frequency"] == metric["frequency"] and row["metric"] == metric["metric"]}
            metric["changed_company_count"] = len(changed)
            weights = [companies[symbol].get("weight_percent") for symbol in changed]
            metric["changed_weight_pct"] = None if scope["instrument_type"] == "equity" or any(w is None for w in weights) else sum(weights)
        result["gap_codes"] = sorted(set(result["gap_codes"]) | {code for transition in transitions for code in transition["gap_codes"]})
        result["gaps"] = [_GAP_LABELS[code] for code in result["gap_codes"]]
        result["comparison_window"] = {"after": since.isoformat(), "through": cutoff.isoformat(),
            "capture_count": sum(len(history) for history in grouped.values()), "transition_count": len(transitions),
            "endpoint_change_count": endpoint_change_count}
        result["semantics"].append("窗口内逐次保留同口径预期变化，包括上修后回落；末值与基线相同不表示期间没有修订，变化行数不是公司数。")
    if unconfirmed_holdings:
        result["status"] = "limited"
        result["gap_codes"].append("holding_type_unconfirmed")
        result["gaps"].append(_GAP_LABELS["holding_type_unconfirmed"])
    return {**result, "as_of": cutoff.isoformat(), "source_ids": sorted(source_ids),
            "comparison_basis": "Every complete source capture in the research window plus its preceding company/frequency baseline; fund weights only when applicable." if since is not None else
                "Latest two complete source captures for each company and forecast frequency; fund weights only when applicable."}


def retained_estimate_sources(context):
    return {evidence["source_id"]: evidence for evidence in context.get("sector_estimate_evidence", [])}


def usable_estimate_change(source, cutoff, instrument_id):
    return (source.get("source_type") == "analyst_estimate_changes" and source.get("instrument_id") == instrument_id
            and bool(source.get("changes"))
            and all(_clock(row.get("current_collected_at")) is not None
                    and _clock(row["current_collected_at"]) <= cutoff for row in source["changes"]))
