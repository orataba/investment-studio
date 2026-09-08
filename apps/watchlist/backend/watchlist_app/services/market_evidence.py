"""Read shared document versions without copying the corpus into research runs."""
from datetime import datetime
from functools import lru_cache



@lru_cache(maxsize=1)
def text_store():
    from studio_market.text import TextStore
    return TextStore()


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
