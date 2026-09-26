"""Read shared document versions without copying the corpus into research runs."""
from datetime import datetime, UTC
from functools import lru_cache



@lru_cache(maxsize=1)
def text_store():
    from studio_market.text import TextStore
    return TextStore()


def _initialization_query(context, query):
    initial = context.get("initialization") or {}
    def instant(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC) if value else None
    if (instant(query.get("published_after")) != instant(initial.get("published_after"))
            or instant(query.get("cutoff")) != instant(initial.get("as_of"))
            or query.get("entities") or query.get("observed_after") or query.get("received_after")):
        return None
    return next((item for item in initial.get("identity_queries", [])
        if item["query"] == query.get("query") and query.get("instrument_id") in {item["instrument_id"], None}
        and (query.get("instrument_id") is not None or context.get("instrument_ids") == [item["instrument_id"]])), None)


def search_market_directory(context: dict, request: dict) -> dict:
    """One search/receipt path for initialization and the existing paged API.

    Mutates only the caller's run context. The caller owns authorization, its row
    lock and committing both successful and failed attempts. Failures propagate.
    """
    from watchlist_app.services.research_read_projection import source_index
    query = dict(request)
    cutoff = datetime.fromisoformat(query.get("as_of") or context["cutoff"])
    if cutoff > datetime.fromisoformat(context["cutoff"]):
        raise ValueError("检索时点不能晚于本轮已取得资料的截止时间")
    query["cutoff"] = cutoff.isoformat()
    for key in ("published_after", "observed_after", "received_after", "as_of"):
        if query.get(key):
            query[key] = datetime.fromisoformat(query[key].replace("Z", "+00:00")).isoformat()
    initial = _initialization_query(context, query)
    if initial:
        query["origin"] = "initialization_identity"
    try:
        result = text_store().search(query.get("query", ""), entities=query.get("entities") or None,
            **{key: datetime.fromisoformat(query[key]) if query.get(key) else None
               for key in ("published_after", "observed_after", "received_after")},
            as_of=cutoff, limit=query.get("limit", 30), offset=query.get("offset", 0))
    except Exception as error:
        # Keep the actual attempt, never a synthetic zero-result receipt. Do not
        # turn database/programming errors into a successful limited report.
        context["market_queries"] = [*context.get("market_queries", []),
            {**query, "status": "failed", "error_type": type(error).__name__}]
        raise
    end = query.get("offset", 0) + len(result["rows"])
    receipt = {**query, "status": "succeeded", "total": result["total"],
        "returned_count": len(result["rows"]), "next_offset": end if end < result["total"] else None}
    context["market_queries"] = [*context.get("market_queries", []), receipt]
    context["market_coverage"] = result["coverage"]
    if initial:
        context["initialization_candidates"] = [*context.get("initialization_candidates", []),
            {**receipt, "instrument_id": initial["instrument_id"],
             "rows": [{**source_index(row), "body_available": bool(row.get("content_text"))} for row in result["rows"]]}]
    return {**result, "rows": [{**{key: value for key, value in row.items()
        if key not in {"content_text", "raw_path"}}, "body_available": bool(row.get("content_text"))}
        for row in result["rows"]]}


def initialization_search_summary(context: dict):
    """Describe only executed identity-directory pages, never original reading."""
    initial = context.get("initialization") or {}
    if "identity_queries" not in initial:
        return None  # Legacy/frozen runs did not execute this preparation path.
    rows, candidate_page_count = [], 0
    for item in initial["identity_queries"]:
        receipts = [query for query in context.get("market_queries", [])
                    if _initialization_query(context, query) == item]
        successful = [query for query in receipts if query.get("status") == "succeeded"]
        candidate_page_count += len(successful)
        total = successful[-1]["total"] if successful else None
        # Merge returned intervals; overlapping rereads do not advance coverage.
        end = 0
        for query in sorted(successful, key=lambda value: value.get("offset", 0)):
            if query.get("offset", 0) <= end:
                end = max(end, query.get("offset", 0) + query["returned_count"])
        complete = total is not None and end >= total
        rows.append({**item, "directory_status": "complete" if complete else
            "failed" if receipts and receipts[-1].get("status") == "failed" else "partial" if successful else "pending",
            "total": total, "returned_through": end, "next_offset": None if complete else end,
            "failed_attempts": sum(query.get("status") == "failed" for query in receipts),
            "continuation": {"tool": "search_market_information", "instrument_id": item["instrument_id"],
                "query": item["query"], "published_after": initial["published_after"], "as_of": initial["as_of"],
                "limit": 100, "offset": end}})
    return {"published_after": initial["published_after"], "as_of": initial["as_of"], "queries": rows,
        "candidate_page_count": candidate_page_count,
        "gaps": initial.get("identity_gaps", []),
        "note": "这里只证明登记名称/代码对应目录页实际查询；不证明全部相关主题覆盖、目录已被模型读完或所有原文已读。原文仍须按document_id/version_id读取。"}


def initialization_coverage_notes(context, instrument_id):
    summary = initialization_search_summary(context)
    if summary is None:
        return []
    labels = {"complete": "该筛选目录已取完", "partial": "目录尚有未取页", "pending": "尚未执行", "failed": "查询失败"}
    notes = list(summary["gaps"])
    for row in summary["queries"]:
        if row["instrument_id"] != instrument_id:
            continue
        notes.append(f"初始化登记身份检索 {row['query']!r}：窗口 {summary['published_after']} 至 {summary['as_of']}，"
            f"{labels[row['directory_status']]}（连续取得 {row['returned_through']} 条，匹配总数 {row['total'] if row['total'] is not None else '未知'}）。"
            "这不是全部相关主题或全部原文已读的证明。")
    return notes


def original_source(document: dict) -> dict:
    published = document.get("published_at")
    return {**{key: value for key, value in document.items()
               if key not in {"content_text", "raw_path"}},
            "source_type": "public_source", "text": document.get("content_text", ""),
            "retrieved_at": document.get("observed_at"),
            "discovered_at": document.get("received_at"),
            "time_status": "date_only" if published and len(published) == 10 else "verified" if published else "unknown",
            "coverage": document.get("content_warnings", []),
            "body_available": bool(document.get("content_text"))}


def source_reference(source: dict) -> dict:
    """Keep the immutable version reference; its body remains in the shared store."""
    if not source.get("document_id"):
        return source
    return {key: value for key, value in source.items() if key not in {"text", "content_text", "raw_path"}}


def hydrate_source(source: dict, *, cutoff: datetime | None = None) -> dict:
    if not source.get("document_id") or source.get("text"):
        return source
    document = text_store().read(source["document_id"], version_id=source["version_id"], as_of=cutoff)
    if document is None:
        raise ValueError("引用的原文版本在本次截止时间不可用。")
    return {**source, **original_source(document)}


def capture_source(source: dict) -> dict:
    return original_source(text_store().capture_public_source(source))


def retained_sources(context: dict) -> dict[str, dict]:
    cutoff = datetime.fromisoformat(context["cutoff"])
    result = {}
    for reference in context.get("market_text_sources", []):
        source = hydrate_source(reference, cutoff=cutoff)
        result[source["source_id"]] = source
    return result
