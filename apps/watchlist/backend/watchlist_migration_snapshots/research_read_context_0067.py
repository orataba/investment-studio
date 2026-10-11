"""Frozen 0067 read projection; source evidence is never rewritten."""

SERIES_DATASETS = (
    "macro_series", "market_series_daily", "regime_market_daily",
    "us_eod_daily", "raw_eod_daily", "cn_equity_daily",
)


def observation_domain(source):
    """Classify the calculation contract, never infer a domain from prose/title."""
    explicit = source.get("observation_domain")
    if explicit in {"market", "fundamental"}:
        return explicit
    data = source.get("data") or {}
    method = source.get("methodology") or {}
    method = method if isinstance(method, dict) else {}
    if (method.get("metric") == "ewma_volatility"
            or data.get("analysis_kind") in {"watchlist_observations", "event_market_reaction"}
            or "input_series" in source):
        return "market"
    if data.get("dataset") in SERIES_DATASETS and method.get("field") in {"close", "adjusted_close", "volume"}:
        return "market"
    return "unclassified"


SOURCE_INDEX_FIELDS = frozenset({"source_id", "source_type", "title", "url", "instrument_id", "instrument_ids", "document_id",
        "version_id", "source_run_id", "run_cutoff", "information_cutoff", "published_at", "occurred_at",
        "observed_at", "received_at", "retrieved_at", "recorded_at", "collected_at", "as_of", "as_of_date",
        "body_sha256", "content_hash", "body_available", "status", "time_status", "provider", "currency", "unit"})
BROWSER_SOURCE_FIELDS = frozenset({"source", "scope", "published_at_raw", "discovered_at", "time_status",
    "measurement", "methodology", "pm_binding_note", "current_snapshot", "previous_snapshot", "changes"})


def source_index(source):
    return {key: item for key, item in source.items() if key in SOURCE_INDEX_FIELDS}


def browser_source_view(source):
    """Source-list metadata only; complete calculations/originals load by ID.

    Computed sources can embed other sources, series and input snapshots. They
    belong to the saved-evidence endpoint, including when nested below a source.
    """
    result = {**source_index(source), **{key: source[key] for key in BROWSER_SOURCE_FIELDS if key in source}}
    if isinstance(source.get("metadata"), dict) and "published_at" in source["metadata"]:
        result["metadata"] = {"published_at": source["metadata"]["published_at"]}
    if source.get("source_type") == "computed_metric":
        result["observation_domain"] = observation_domain(source)
    return result


EXACT_FIELDS = frozenset({
    "role", "instrument_id", "instrument_ids", "sector_run", "research_run", "risk_run", "recordkeeping_only",
    "cutoff", "input_snapshot_cutoff", "portfolio_id", "watchlist_id", "risk_scope", "research_actor",
    "execution", "runtime_error", "citation_correction", "organization_revision",
    "background", "author", "revision_number", "origin", "managed_by", "theme_key", "theme_status",
    "close_reason", "source_ids", "updated_by", "updated_by_user_id", "updated_by_role", "recorded_via",
    "source_run_id", "publication", "kind", "priority", "priority_reason", "pinned", "synthesis",
    "latest_development", "next_check", "figure_source_ids", "reference", "last_reviewed_at",
    "baseline_status", "baseline_requested_at", "migration_origin", "lifecycle_owner",
    "incremental_trigger", "numeric_monitor_inputs", "market_queries", "attempted_theme_baselines",
})


import hashlib
import json


def shape(value):
    kind = "object" if isinstance(value, dict) else "array" if isinstance(value, list) else "text" if isinstance(value, str) else "value"
    return {"type": kind, "count": len(value) if isinstance(value, (dict, list, str)) else 0 if value is None else 1}


COMPUTATION_INPUTS = frozenset({
    "input_snapshot", "benchmark_snapshot", "input_series", "input_points", "input_sources",
    "sources", "source_ids", "points", "history",
})


def risk_snapshot_projection(snapshot, side="current"):
    if snapshot is None or snapshot.get("evidence_projection") == "risk_decision_v1":
        return snapshot

    def reference(value, path):
        # Selectors differ across current/previous snapshots. Content identity
        # lets readers distinguish a changed original from a different address,
        # without reloading the original merely to compare decision pages.
        digest = hashlib.sha256()
        for part in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":")).iterencode(value):
            digest.update(part.encode())
        return {"inline": False, **shape(value), "content_sha256": digest.hexdigest(),
            "read": {"tool": "read_risk_evidence", "snapshot": side, "path": path},
            "note": "本轮冻结的原始计算输入/来源；按论证需要读取，不要求逐点复算已提供的指标。未读取不能声称已核实原文。"}

    def visit(value, path, computation=False):
        if isinstance(value, list):
            return [visit(item, [*path, index], computation) for index, item in enumerate(value)]
        if not isinstance(value, dict):
            return value
        # Existing projections are idempotent and keep their original selector.
        if value.get("inline") is False and (value.get("read") or {}).get("tool") == "read_risk_evidence":
            return value
        computation = (computation or value.get("source_type") == "computed_metric"
            or (isinstance(value.get("methodology"), dict) and "input_sources" in value["methodology"]))
        result = {}
        for key, item in value.items():
            child = [*path, key]
            if (key == "history_json" or (computation and key in COMPUTATION_INPUTS)
                    or (key in {"text", "content_text"} and value.get("source_id"))):
                result[key] = reference(item, child) if item else item
            else:
                result[key] = visit(item, child, computation)
        return result
    return {**visit(snapshot, []), "evidence_projection": "risk_decision_v1"}


def derive_read_context(context):

    def sources(rows):
        return [browser_source_view(source) for source in rows or [] if isinstance(source, dict)]

    def notebook(value):
        if isinstance(value, list):
            return [notebook(item) for item in value]
        if isinstance(value, dict):
            return {key: sources(item) if key == "sources" else notebook(item) for key, item in value.items()}
        return value

    if not isinstance(context, dict):
        # Malformed retained scopes must keep failing closed in their readers.
        return context
    result = {key: value for key, value in context.items() if key in EXACT_FIELDS}
    # A risk decision consumes the complete case/metric/limitation surface, not
    # recursively embedded raw calculations. Exact originals remain addressable.
    if context.get("risk_run"):
        for key, side in (("risk_inputs", "current"), ("prior_inputs", "previous")):
            if key in context:
                result[key] = risk_snapshot_projection(context[key], side)
        for key in ("risk_input_version", "prepared_at", "catalogue"):
            if key in context:
                result[key] = context[key]
    if "computed_metrics" in context:
        result["computed_metrics"] = [{**{key: row.get(key) for key in
            ("source_id", "source_type", "instrument_id", "title", "as_of")},
            "data": {"analysis_kind": (row.get("data") or {}).get("analysis_kind")},
            **({"input_series": []} if "input_series" in row else {})}
            for row in context["computed_metrics"] or []]
    if "tool_evidence" in context:
        result["tool_evidence"] = [{key: row.get(key) for key in
            ("source_id", "tool", "request", "retrieved_at")} for row in context["tool_evidence"] or []]
    if "sources" in context:
        result["sources"] = sources(context["sources"])
    if "reviews" in context:
        reviews = context["reviews"]
        result["reviews"] = {iid: {key: notebook(value) if key == "research" else value
            for key, value in review.items() if key in {"status", "summary", "view_updated_at", "change_kind", "coverage", "reflection", "research", "events", "themes"}}
            for iid, review in reviews.items()} if isinstance(reviews, dict) else reviews
    # Fetched originals are small compared with recursive numerical snapshots.
    # Preserve text/metadata exactly for eligibility and batch attribution; the
    # source reader still applies its existing hydration and provenance checks.
    for key in ("market_text_sources", "web_evidence"):
        if key in context:
            result[key] = context[key]
    if "submitted_draft" in context:
        result["submitted_draft"] = notebook(context["submitted_draft"])
    if "research_dossiers" in context:
        result["research_dossiers"] = [{"instrument_id": dossier.get("instrument_id"),
            "prior_sources": sources(dossier.get("prior_sources")),
            "notebook": {"sources": sources((dossier.get("notebook") or {}).get("sources"))}}
            for dossier in context["research_dossiers"] or []]
    if "attempted_theme_baselines" not in result and "research_dossiers" in context:
        result["attempted_theme_baselines"] = {theme["theme_id"]: theme.get("baseline_requested_at") or theme.get("created_at")
            for dossier in context.get("research_dossiers") or [] for theme in dossier.get("themes", []) if theme.get("theme_id")}
    return result
