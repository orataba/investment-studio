"""Public company-document scope from actual holdings and explicit research choices."""
from datetime import UTC, datetime

from sqlalchemy import or_, select
from studio_identity import current_principal

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.services.sector_market_data import all_rows, holding_type

FMP_CHANNELS = ("fmp:press_releases", "fmp:stock_news", "fmp:earnings_transcripts", "fmp:sec_filings")
FMP_FUND_CHANNELS = FMP_CHANNELS[:2]
FIXED_INPUT_ORIGIN = "fmp_fixed_input"


def latest_research_focus(session, instrument_id, as_of):
    """Only explicit public entity selections in the latest shared research scope.

    A generated TOP10 directory cannot turn former constituents into permanent
    extra focus. Private/portfolio conversations never establish this scope.
    """
    from watchlist_app.services.research_access import instrument_run_scope, topic_portfolio_ids
    from watchlist_app.services.research_run_context import load_shared_history_fields
    statement = select(ResearchEntry.entry_id, ResearchEntry.topic_id).join(ResearchTopic).where(
        ResearchEntry.kind == "analysis", ResearchEntry.created_at <= as_of,
        ResearchEntry.team_id == current_principal().team_id,
        ResearchTopic.visibility == "team", ResearchTopic.portfolio_id.is_(None),
        ResearchTopic.team_id == current_principal().team_id,
        instrument_run_scope(session, instrument_id),
        or_(ResearchEntry.read_context_json["sector_run"].as_boolean().is_(True),
            ResearchEntry.read_context_json["research_run"].as_boolean().is_(True)),
    ).order_by(ResearchEntry.created_at.desc(), ResearchEntry.entry_id.desc())
    for row in session.execute(statement):
        if topic_portfolio_ids(session, session.get(ResearchTopic, row.topic_id)):
            continue
        context = load_shared_history_fields(session, row.entry_id, instrument_id, {
            "sector_run", "research_run", "instrument_ids", "cutoff", "input_snapshot_cutoff", "market_queries", "incremental_trigger"}).context_json
        if not (context.get("sector_run") or context.get("research_run")):
            continue
        from watchlist_app.services.research_triggers import _queries
        queries = _queries(context, instrument_id)
        if not queries:
            continue
        return sorted({entity.removeprefix("fmp:") for query in queries
            if query.get("origin") != FIXED_INPUT_ORIGIN for entity in query.get("entities", [])
            if entity.startswith("fmp:") and entity.removeprefix("fmp:")})
    return []


def select_companies(symbol, name, kind, holdings, profiles, manual_focus, research_focus):
    """Choose the requested research universe; never normalize selected weights."""
    if kind == "equity":
        selected = {symbol: {"symbol": symbol, "name": name, "reason": "self",
            "weight_percent": None, "holdings_as_of": None}}
        stocks, gaps = {}, []
    else:
        stocks = {row["holding_symbol"]: row for row in holdings if row.get("holding_symbol") and
            holding_type(row.get("holding_name"), row["holding_symbol"], profiles.get(row["holding_symbol"])) == "equity"}
        ranked = sorted((row for row in stocks.values() if row.get("weight_percent") is not None),
            key=lambda row: (-row["weight_percent"], row["holding_symbol"]))
        selected = {row["holding_symbol"]: {"symbol": row["holding_symbol"], "name": row.get("holding_name"),
            "reason": "holding_top10", "weight_percent": row["weight_percent"],
            "holdings_as_of": row.get("report_date"), "holdings_snapshot_date": row.get("snapshot_date"),
            "holdings_provider_updated_at": row.get("provider_updated_at"), "holdings_observed_at": row.get("observed_at")}
            for row in ranked[:10]}
        gaps = []
        if not holdings:
            gaps.append("未取得本ETF持仓；未猜测或以指数成分替代前十大公司。")
        if any(row.get("weight_percent") is None for row in stocks.values()):
            gaps.append("部分股票持仓缺少权重；TOP10仅按已知原始权重排序，不能证明完整前十大覆盖。")
        if any(holding_type(row.get("holding_name"), row.get("holding_symbol"), profiles.get(row.get("holding_symbol"))) == "unclassified"
               for row in holdings):
            gaps.append("部分持仓证券类型未确认，未自动当作公司纳入TOP10。")
    for reason, symbols in (("manual_focus", manual_focus), ("research_focus", research_focus)):
        for focus in symbols:
            if focus in selected or focus == symbol:
                continue
            profile = profiles.get(focus) or {}
            if profile.get("is_etf") is True or profile.get("is_fund") is True:
                gaps.append(f"{focus}的资料表明它是基金，未作为重点公司加入。")
                continue
            holding = stocks.get(focus) or {}
            selected[focus] = {"symbol": focus, "name": holding.get("holding_name") or profile.get("company_name"),
                "reason": reason, "weight_percent": holding.get("weight_percent"),
                "holdings_as_of": holding.get("report_date"), "holdings_snapshot_date": holding.get("snapshot_date"),
                "holdings_provider_updated_at": holding.get("provider_updated_at"), "holdings_observed_at": holding.get("observed_at")}
    return list(selected.values()), gaps


def build_fmp_research_scope(session, as_of, instrument_ids=None):
    """Export active research scope for both collection and frozen research inputs.

    The caller supplies the existing authorized Watchlist principal. No network,
    model invocation, mandate creation or source refresh happens here.
    """
    from watchlist_app.services.research_policy import research_capability
    from watchlist_app.services.research_dossier import _mandate_entry
    from watchlist_app.services.sector_estimates import estimate_scope
    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if isinstance(as_of, str) else as_of
    if cutoff.tzinfo is None:
        raise ValueError("FMP研究范围必须使用带时区的截止时间。")
    requested = set(instrument_ids) if instrument_ids is not None else None
    instruments, symbols, subjects = [], set(), {}
    store = None
    for item in research_capability(session)["instruments"]:
        iid = item["instrument_id"]
        if not item["eligible"] or item["instrument_type"] not in {"equity", "etf"} or (requested is not None and iid not in requested):
            continue
        identity = estimate_scope(session, iid)
        if identity is None:
            instruments.append({"instrument_id": iid, "symbol": None, "instrument_type": item["instrument_type"],
                "companies": [], "coverage": ["未登记明确的FMP代码，未推断或替代。"]})
            continue
        symbol = identity["symbol"]
        if store is None:
            from watchlist_app.services.sector_market_data import numeric_store
            store = numeric_store()
        mandate = _mandate_entry(session, iid)
        manual = ((mandate.context_json.get("mandate") or {}).get("focus_company_symbols", []) if mandate else [])
        focus = latest_research_focus(session, iid, cutoff)
        holdings = all_rows(store, "etf_holdings", symbols=[symbol], as_of=cutoff) if item["instrument_type"] == "etf" else []
        company_symbols = sorted({symbol, *manual, *focus, *(row["holding_symbol"] for row in holdings if row.get("holding_symbol"))})
        profiles = {row["symbol"]: row for row in all_rows(store, "company_profiles", symbols=company_symbols, as_of=cutoff)}
        companies, gaps = select_companies(symbol, identity["name"], item["instrument_type"], holdings, profiles, manual, focus)
        instruments.append({"instrument_id": iid, "symbol": symbol, "instrument_type": item["instrument_type"],
            "companies": companies, "coverage": gaps,
            "holdings_date_note": "holdings_as_of仅限源明确提供的报告日；snapshot_date是供应商更新时间日期或采集日，不冒充持仓报告日；provider_updated_at和observed_at分别保留供应商更新与本项目观察时点。"})
        symbols.update([symbol, *(row["symbol"] for row in companies)])
        subjects[symbol] = "company" if item["instrument_type"] == "equity" else "fund"
        subjects.update({row["symbol"]: "company" for row in companies})
    return {"as_of": cutoff.astimezone(UTC).isoformat(), "instruments": instruments, "symbols": sorted(symbols),
            "subjects": [{"symbol": symbol, "kind": subjects[symbol]} for symbol in sorted(subjects)]}


def fixed_document_queries(scope, *, cutoff):
    """Applicable document types only, regenerated from the current holdings scope."""
    queries = []
    for item in scope["instruments"]:
        groups = [("company", sorted({row["symbol"] for row in item["companies"]}), FMP_CHANNELS)]
        if item.get("symbol") and item["instrument_type"] == "etf":
            groups.insert(0, ("fund", [item["symbol"]], FMP_FUND_CHANNELS))
        for kind, symbols, channels in groups:
            if not symbols:
                continue
            for channel in channels:
                queries.append({"instrument_id": item["instrument_id"], "subject_kind": kind,
                    "query": "", "entities": [f"fmp:{symbol}" for symbol in symbols],
                    "channel_ids": [channel], "origin": FIXED_INPUT_ORIGIN, "cutoff": cutoff})
    return queries


def bind_fmp_document_inputs(session, context, instrument_ids, cutoff):
    """Freeze compact source directories; original reading stays a separate tool action."""
    from watchlist_app.services.market_evidence import text_store
    from watchlist_app.services.research_read_projection import source_index
    scope = build_fmp_research_scope(session, as_of=cutoff, instrument_ids=instrument_ids)
    if not scope["instruments"]:
        return
    inputs = dict(context.get("fmp_inputs") or {})
    queries = fixed_document_queries(scope, cutoff=scope["as_of"])
    store = text_store() if queries else None
    coverage = store.get_fmp_coverage(scope["symbols"], as_of=cutoff)["sources"] if store else []
    for item in scope["instruments"]:
        directories = []
        for query in (row for row in queries if row["instrument_id"] == item["instrument_id"]):
            parameters = {key: query[key] for key in ("instrument_id", "query", "entities", "channel_ids")}
            page = store.search("", entities=query["entities"], channel_ids=query["channel_ids"], as_of=cutoff, limit=20)
            rows = [{**source_index(row), "content_completeness": row.get("content_completeness"),
                "content_scope": (row.get("provenance") or {}).get("content_scope"),
                "body_available": bool(row.get("content_text")) and row.get("content_completeness") in {"full_text", "source_excerpt"},
                "read": {"tool": "read_market_source", "document_id": row["document_id"], "version_id": row["version_id"]}}
                for row in page["rows"]]
            next_offset = len(rows) if len(rows) < page["total"] else None
            directories.append({**parameters, "subject_kind": query["subject_kind"], "as_of": scope["as_of"],
                "total": page["total"], "rows": rows, "next_offset": next_offset,
                "coverage": [row for row in coverage if f"fmp:{row['symbol']}" in query["entities"]
                    and row["channel_id"] in query["channel_ids"]],
                "read": {"tool": "search_market_information", **parameters, "as_of": scope["as_of"], "limit": 20, "offset": 0},
                "continuation": {"tool": "search_market_information", **parameters, "as_of": scope["as_of"],
                    "limit": 20, "offset": next_offset} if next_offset is not None else None})
        inputs[item["instrument_id"]] = {**item, "as_of": scope["as_of"], "directories": directories,
            "note": ("固定资料入口包含适用公司公告、新闻、业绩电话会文字稿和监管披露目录。目录不等于原文已读；source_excerpt与披露索引不是全文。按需要读取准确原件和后续页，明确实际覆盖。ETF范围为真实前十大股票持仓及明确重点公司，不代表全部成分覆盖。"
                if item.get("symbol") else "本标的没有明确登记的FMP代码，本轮未绑定FMP资料目录；请使用本标的已登记的其他来源。")}
    context["fmp_inputs"] = inputs
