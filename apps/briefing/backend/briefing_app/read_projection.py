"""Frozen serving projections; source versions and model inputs remain authoritative."""
from collections import Counter

SOURCE_FIELDS = ("source_id", "source_type", "title", "source_name", "url", "published_at",
                 "occurred_at", "observed_at", "received_at", "information_type",
                 "content_completeness", "window_reasons", "symbol", "date")
COMPLETENESS = ("full_text", "source_excerpt", "title_only", "unavailable")


def completeness_kind(value):
    return value if value in COMPLETENESS else "unknown" if value else "not_retained"


def cited_source_ids(result):
    found = set()
    def visit(node):
        if isinstance(node, dict):
            found.update(node.get("source_ids", []))
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(result)
    return found


def reading_projection(snapshot, result):
    from briefing_app.evidence import with_quote_metadata
    sources = snapshot.get("sources", [])
    cited = cited_source_ids(result)
    counts = Counter(completeness_kind(s.get("content_completeness"))
                     for s in sources if s.get("source_type") == "public_document")
    return {
        **{key: snapshot.get(key) for key in ("cutoff", "period_start", "period_end", "timezone", "period_label",
           "price_window_method_version", "macro_rows", "numeric_coverage", "text_coverage", "document_counts")},
        "market_rows": [with_quote_metadata(snapshot, row) for row in snapshot.get("market_rows", [])],
        "source_completeness": {key: counts[key] for key in (*COMPLETENESS, "not_retained", "unknown")},
        "sources": [{key: source.get(key) for key in SOURCE_FIELDS}
                    for source in sources if source.get("source_id") in cited],
    }


def source_projection_rows(report_id, snapshot):
    from briefing_app.evidence import with_quote_metadata
    for ordinal, source in enumerate(snapshot.get("sources", [])):
        yield {"report_id": report_id, "ordinal": ordinal, "source_id": source["source_id"],
               "source_type": source.get("source_type") or "", "window_scope": source.get("window_scope", "current"),
               "completeness": completeness_kind(source.get("content_completeness")),
               "search_text": " ".join(str(source.get(k) or "") for k in ("title", "source_name", "url", "entities", "symbol")).casefold(),
               "metadata_json": {key: source.get(key) for key in SOURCE_FIELDS if source.get(key) is not None},
               "payload_json": source if source.get("source_type") == "public_document" else with_quote_metadata(snapshot, source)}
