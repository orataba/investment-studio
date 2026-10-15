"""Answer citations address only originals bound to the enclosing conversation run.

A citation is not a tool-delivery receipt. This view adds no recorded read and
never resolves a guessed identifier in the global document/research registries.
"""
import re
from datetime import datetime

from watchlist_app.services.research_run_evidence import (
    INDEX_KEYS, SOURCE_PATHS, _rows, retained_computed_sources,
)

_CITATION = re.compile(r"\[((?:instrument|computed|comparison|text|research):[^\[\]\s]+)\]")
_NOTEBOOK = ("research_dossiers", "*", "notebook")
_JUDGMENT_PATHS = tuple(
    (*_NOTEBOOK, field, *suffix)
    for field in ("investment_view", "decision_brief", "modules", "facts", "questions", "catalysts",
                  "forecasts", "forecast_reviews", "lessons", "changes")
    for suffix in (((), ("versions", "*")) if field in {"investment_view", "decision_brief"}
                   else (("*",), ("*", "versions", "*")))
)
_METADATA = (*INDEX_KEYS, "name", "snapshot_cutoff", "version_id", "updated_at", "url")


def cited_source_ids(body):
    return list(dict.fromkeys(_CITATION.findall(body or "")))


def _bound_citations(session, run_id, source_ids, *, metadata_only):
    """Query selected originals/metadata, never their enclosing input trees."""
    remaining = set(source_ids)
    found = {}

    def keep(values):
        for value in values:
            sid = value.get("source_id")
            if sid in remaining:
                found[sid] = value
                remaining.remove(sid)

    computed = {sid for sid in remaining if sid.startswith(("computed:", "comparison:"))}
    if computed:
        keep(retained_computed_sources(session, run_id, source_ids=computed, metadata_only=metadata_only))
    instruments = {sid for sid in remaining if sid.startswith("instrument:")}
    if instruments:
        keep(_rows(session, run_id, ("instrument_inputs", "*"), source_ids=instruments,
                   metadata_only=metadata_only, metadata_keys=_METADATA))
    # Most citations resolve in the current run. Historical dossier sources are
    # queried only for identifiers still missing; their retained locators remain
    # authoritative when the original is opened.
    for path in SOURCE_PATHS:
        ids = {sid for sid in remaining if not sid.startswith("research:")}
        if not ids:
            break
        keep(_rows(session, run_id, path, source_ids=ids, metadata_only=metadata_only, metadata_keys=_METADATA))
    for path in _JUDGMENT_PATHS:
        ids = {sid.removeprefix("research:") for sid in remaining if sid.startswith("research:")}
        if not ids:
            break
        values = _rows(session, run_id, path, filters=(("version_id", ids),),
                       metadata_only=metadata_only, metadata_keys=_METADATA)
        keep([{**value, "source_id": f"research:{value['version_id']}", "source_type": "research_judgment"}
              for value in values])
    return found


def citation_receipt(source):
    return {"source_id": source["source_id"], "tool": "source", "citation_only": True,
            "title": source.get("title") or source.get("name") or (
                "已绑定的研究判断原版本" if source.get("source_type") == "research_judgment" else "回答引用的已留存资料"),
            "retrieved_at": source.get("retrieved_at"),
            "as_of": source.get("as_of") or source.get("snapshot_cutoff"),
            "recorded_at": source.get("updated_at"),
            **({"url": source["url"]} if source.get("url") else {}),
            "source_type": source.get("source_type") or (
                "instrument_snapshot" if source["source_id"].startswith("instrument:") else None),
            "source_ids": [source["source_id"]]}


def citation_receipts(session, run_id, body, existing):
    recorded = {row.get("source_id") for row in existing}
    ids = [sid for sid in cited_source_ids(body) if sid not in recorded]
    if not ids:
        return existing
    found = _bound_citations(session, run_id, ids, metadata_only=True)
    return [*existing, *(citation_receipt(found[sid]) for sid in ids if sid in found)]


def read_citation(session, run_id, source_id, *, cutoff):
    found = _bound_citations(session, run_id, [source_id], metadata_only=False)
    source = found.get(source_id)
    if source is None:
        return None
    from watchlist_app.services.market_evidence import hydrate_source
    source = hydrate_source(source, cutoff=datetime.fromisoformat(cutoff) if cutoff else None,
                            session=session, bound_source=True)
    return {**citation_receipt(source), "result": {"retained_source": source,
        "limitation": "这是回答引用的本轮绑定资料；引用不代表模型已完整查阅，也不代表独立核验通过。"}}
