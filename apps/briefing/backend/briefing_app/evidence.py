"""Construct one immutable report input from Studio-owned public facts."""
from datetime import datetime, timedelta, UTC
import re
from zoneinfo import ZoneInfo

TEXT_METADATA = ("source_id", "document_id", "version_id", "channel_id", "source_name", "title", "url",
                 "published_at", "published_at_precision", "occurred_at", "occurred_at_precision", "observed_at",
                 "received_at", "information_type", "entities", "event_ids", "content_completeness", "content_warnings")


def coverage_summary(coverage: dict) -> dict:
    result = {key: coverage.get(key) for key in ("bundle_count", "latest_received_at", "latest_source_observed_at", "latest_bundle_window_end")}
    collection = (coverage.get("export_coverage") or {}).get("latest_collection")
    result["latest_collection"] = {key: collection.get(key) for key in (
        "status", "started_at", "completed_at", "routes_attempted", "routes_succeeded", "routes_failed", "routes_skipped",
    )} if collection is not None else None
    result["sources"] = [{"channel_id": row.get("channel_id"), "source_name": row.get("source_name"),
        "admission_status": row.get("admission_status"), "latest_run": {key: (row.get("latest_run") or {}).get(key)
        for key in ("status", "completed_at", "accepted_count", "failed_count")},
        "latest_discovery": {key: row["latest_discovery"].get(key) for key in (
            "route_id", "status", "failure_reason", "last_attempted_discovery_at", "last_successful_discovery_at", "cooldown_until",
        )} if row.get("latest_discovery") is not None else None} for row in coverage.get("sources", [])]
    return result


def current_source_index(snapshot: dict) -> dict:
    """Supply every current headline once; the editor selects originals to verify."""
    channels = {}
    for source in snapshot.get("sources", []):
        if source.get("source_type") != "public_document" or source.get("window_scope", "current") != "current":
            continue
        channel = channels.setdefault(source.get("channel_id"), {
            "channel_id": source.get("channel_id"), "source_name": source.get("source_name"), "rows": []})
        channel["rows"].append([source["source_id"], source.get("title"), source.get("published_at")])
    return {"columns": ["source_id", "title", "published_at"],
            "count": sum(len(channel["rows"]) for channel in channels.values()),
            "channels": list(channels.values()),
            "meaning": "Complete current-period headline index. Publication time stays original; an old publication newly observed is a late discovery. Select relevant bound originals to verify; the index is not full-text coverage."}


def report_window(report_type: str, cutoff: datetime, timezone: str) -> dict:
    local = cutoff.astimezone(ZoneInfo(timezone))
    start = (cutoff.astimezone(UTC) - timedelta(days=1)).astimezone(ZoneInfo(timezone)) if report_type == "daily" else (
        local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=local.weekday()))
    return {"report_type": report_type, "report_date": local.date().isoformat(), "timezone": timezone,
            "period_start": start.isoformat(), "period_end": local.isoformat(), "cutoff": cutoff.astimezone(UTC).isoformat(),
            "period_label": "过去24小时" if report_type == "daily" else "本周至截止时间"}


def collect_text(store, window: dict) -> tuple[list[dict], dict]:
    cutoff = datetime.fromisoformat(window["cutoff"])
    start = datetime.fromisoformat(window["period_start"])
    documents = {}
    coverage = {}
    # New publications and newly observed versions are separate clocks. Late discoveries
    # remain dated by their actual publication; a weekly report rereads the entire week.
    for reason, query in (("published", {"published_after": start, "published_before": cutoff}),
                          ("observed", {"observed_after": start}), ("received", {"received_after": start})):
        offset = 0
        while True:
            page = store.search(as_of=cutoff, limit=100, offset=offset, **query)
            coverage = coverage_summary(page["coverage"])
            rows = page["rows"]
            for row in rows:
                if not row.get("withdrawn"):
                    source = documents.setdefault(row["source_id"], {key: row.get(key) for key in TEXT_METADATA}
                        | {"source_type": "public_document", "window_reasons": []})
                    source["window_reasons"].append(reason)
            offset += len(rows)
            if not rows or offset >= page["total"]:
                break
    for source in documents.values():
        source["window_scope"] = "current" if {"published", "observed"}.intersection(source["window_reasons"]) else "late_received"
    return sorted(documents.values(), key=lambda row: (row.get("published_at") or row.get("observed_at") or "", row["source_id"]), reverse=True), coverage


def _entity_key(identity: str) -> tuple[str, str] | None:
    """Recognize identifiers supplied by the feed, never infer a name from prose."""
    for prefix, kind in (("issuer:cik:", "cik"), ("issuer:hkex:stock_code:", "hk"),
                         ("issuer:security_code:", "cn")):
        if identity.startswith(prefix) and identity[len(prefix):].isdigit():
            return kind, identity[len(prefix):].lstrip("0") or "0"
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]*", identity):
        return "symbol", identity.upper()
    return None


def _numeric_rows(store, dataset: str, **query):
    offset = 0
    while True:
        page = store.query(dataset, limit=100000, offset=offset, **query)
        yield from page["rows"]
        offset += len(page["rows"])
        if not page["rows"] or offset >= page["total"]:
            break


def related_equity_snapshot(store, documents: list[dict], *, report_type: str,
                            period_start, period_end, cutoff: datetime, existing_symbols=()) -> dict:
    """Bind current-document issuer identities to saved, comparable price versions."""
    references = {}
    for document in documents:
        if document.get("window_scope") != "current":
            continue
        for entity in document.get("entities") or []:
            key = _entity_key(entity["entity_id"])
            if key is not None:
                reference = references.setdefault(key, {"entity_ids": set(), "document_source_ids": set()})
                reference["entity_ids"].add(entity["entity_id"])
                reference["document_source_ids"].add(document["source_id"])
    result = {"market_rows": [], "sources": [], "coverage": []}
    if not references:
        return result

    candidates = {}
    matched = set()
    # CIK is an issuer identifier: multiple listed share classes keep their own
    # symbols. The saved profile supplies both the mapping and its PIT clock.
    for profile in _numeric_rows(store, "company_profiles", as_of=cutoff, _latest=True):
        if profile.get("is_etf") or profile.get("is_fund"):
            continue
        symbol = profile["symbol"]
        keys = {("symbol", symbol.upper())}
        cik = str(profile.get("cik") or "")
        if cik.isdigit():
            keys.add(("cik", cik.lstrip("0") or "0"))
        code, _, suffix = symbol.rpartition(".")
        if code.isdigit() and suffix in {"HK", "SH", "SZ", "BJ"}:
            keys.add(("hk" if suffix == "HK" else "cn", code.lstrip("0") or "0"))
        linked = keys.intersection(references)
        matched.update(linked)
        if linked and symbol not in existing_symbols:
            candidates[symbol] = {"profile": profile,
                "entity_ids": sorted(set().union(*(references[key]["entity_ids"] for key in linked))),
                "related_document_source_ids": sorted(set().union(*(references[key]["document_source_ids"] for key in linked)))}
    for key in sorted(references.keys() - matched):
        result["coverage"].append({"symbol": sorted(references[key]["entity_ids"])[0],
            "status": "unresolved_security", "asset_type": "equity",
            "related_document_source_ids": sorted(references[key]["document_source_ids"])})

    by_dataset = {}
    for symbol in candidates:
        dataset = "raw_eod_daily" if symbol.endswith((".HK", ".SH", ".SZ", ".BJ")) else "us_eod_daily"
        by_dataset.setdefault(dataset, []).append(symbol)
    for dataset, symbols in by_dataset.items():
        prices = {symbol: [] for symbol in symbols}
        for row in _numeric_rows(store, dataset, symbols=symbols, as_of=cutoff,
                start=(period_start - timedelta(days=35)).isoformat(), end=min(period_end, cutoff.date()).isoformat()):
            prices[row["symbol"]].append(row)
        for symbol in sorted(symbols):
            candidate = candidates[symbol]
            profile = candidate["profile"]
            rows = prices[symbol]  # NumericStore orders actual dates newest first.
            if report_type == "daily":
                final = rows[0] if rows else None
                base = rows[1] if len(rows) > 1 else None
            else:
                final = next((r for r in rows if r["date"] >= period_start.isoformat()), None)
                base = next((r for r in rows if r["date"] < period_start.isoformat()), None)
            metadata = {"symbol": symbol, "label": profile.get("company_name") or symbol,
                "asset_type": "equity", "entity_ids": candidate["entity_ids"],
                "related_document_source_ids": candidate["related_document_source_ids"],
                "identity_source_ids": [profile["source_id"]]}
            result["sources"].append(profile)
            if final is None or base is None:
                result["coverage"].append({**metadata, "status": "insufficient_history",
                    "latest_date": rows[0]["date"] if rows else None})
                continue
            first, last = base.get("close"), final.get("close")
            if first is None or last is None or first <= 0:
                result["coverage"].append({**metadata, "status": "missing_comparable_prices"})
                continue
            result["market_rows"].append({**metadata, "start_date": base["date"], "end_date": final["date"],
                "start_close": first, "end_close": last, "return_pct": (last / first - 1) * 100,
                "price_field": "close", "return_basis": "split_adjusted_price" if dataset == "us_eod_daily" else "unadjusted_price",
                "source_ids": [base["source_id"], final["source_id"]]})
            result["sources"].extend([base, final])
            result["coverage"].append({**metadata, "status": "available", "effective_date": final["date"],
                "observed_at": final["observed_at"]})
    return result


def build_input(report_type: str, cutoff: datetime, settings) -> dict:
    from studio_market.config import MarketSettings
    from studio_market.numeric.reporting import build_report_snapshot
    from studio_market.numeric import NumericStore
    from studio_market.text import TextStore

    window = report_window(report_type, cutoff, settings.timezone)
    market = MarketSettings.from_environment(data_root=settings.data_root)
    text_store = TextStore(market)
    try:
        documents, text_coverage = collect_text(text_store, window)
    finally:
        text_store.close()
    # The daily price return uses the last two valid closes, whereas the text window
    # uses 24 hours. Weekly returns start at the last close before local Monday.
    local_end = datetime.fromisoformat(window["period_end"]).date()
    price_start = local_end if report_type == "daily" else datetime.fromisoformat(window["period_start"]).date()
    numeric = build_report_snapshot(market, report_kind=report_type, period_start=price_start,
                                    period_end=local_end, cutoff=cutoff)
    numeric_store = NumericStore(market)
    try:
        related = related_equity_snapshot(numeric_store, documents, report_type=report_type,
            period_start=price_start, period_end=local_end, cutoff=cutoff,
            existing_symbols={row["symbol"] for row in numeric["market_rows"]})
    finally:
        numeric_store.close()
    numeric["market_rows"].extend(related["market_rows"])
    numeric["coverage"].extend(related["coverage"])
    numeric["sources"] = list({row["source_id"]: row for row in numeric["sources"] + related["sources"]}.values())
    if numeric["market_rows"]:
        numeric["status"] = "ready"
    sources = list(documents)
    for source in numeric["sources"]:
        sources.append({**source, "source_type": "numeric"})
    # Derived return rows are program-computed evidence too: the model may cite a
    # return without reproducing the arithmetic or replacing the original price rows.
    for index, row in enumerate(numeric["market_rows"]):
        sources.append({"source_id": f"market-row:{index}", "source_type": "market_row", **row})
    for index, row in enumerate(numeric["macro_rows"]):
        sources.append({"source_id": f"macro-row:{index}", "source_type": "macro_row", **row})
    document_counts = {"current": sum(row["window_scope"] == "current" for row in documents),
                       "late_received": sum(row["window_scope"] == "late_received" for row in documents),
                       "by_completeness": {kind: sum(row["content_completeness"] == kind for row in documents)
                                           for kind in sorted({row["content_completeness"] for row in documents})}}
    return {**window, "edition_role": settings.edition_role, "sources": sources, "market_rows": numeric["market_rows"], "macro_rows": numeric["macro_rows"],
            "numeric_coverage": numeric["coverage"], "text_coverage": text_coverage,
            "source_count": len(documents), "document_counts": document_counts, "numeric_status": numeric["status"],
            "prepared_at": datetime.now(UTC).isoformat()}


def read_bound_source(snapshot: dict, source_id: str, settings=None) -> dict:
    """Resolve a preserved text version; report storage owns only its reference."""
    source = next((row for row in snapshot.get("sources", []) if row["source_id"] == source_id), None)
    if source is None:
        raise LookupError("这份证据不属于当前报告版本")
    if source["source_type"] != "public_document":
        return source
    from briefing_app.settings import get_settings
    from studio_market.config import MarketSettings
    from studio_market.text import TextStore

    settings = settings or get_settings()
    store = TextStore(MarketSettings.from_environment(data_root=settings.data_root))
    try:
        original = store.read(source_id, version_id=source["version_id"],
                              as_of=datetime.fromisoformat(snapshot["cutoff"]))
        if original is None:
            raise LookupError("报告绑定的文本版本当前不可读取")
        return {key: value for key, value in original.items() if key != "raw_path"} | {"source_type": "public_document"}
    finally:
        store.close()
