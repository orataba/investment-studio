"""Small, transactional read projection of retained research; never source evidence."""

EXACT_FIELDS = frozenset({
    "role", "instrument_id", "instrument_ids", "sector_run", "research_run", "risk_run", "recordkeeping_only",
    "cutoff", "input_snapshot_cutoff", "portfolio_id", "watchlist_id", "risk_scope", "research_actor",
    "execution", "runtime_error", "citation_correction", "organization_revision",
    "background", "author", "revision_number", "origin", "managed_by", "theme_key", "theme_status",
    "close_reason", "source_ids", "updated_by", "updated_by_user_id", "updated_by_role", "recorded_via",
    "source_run_id", "publication", "kind", "priority", "priority_reason", "pinned", "synthesis",
    "latest_development", "next_check", "figure_source_ids", "reference", "last_reviewed_at",
    "baseline_status", "baseline_requested_at", "migration_origin", "lifecycle_owner",
    "incremental_trigger", "market_queries", "attempted_theme_baselines",
    "research_working_sets",
})


def derive_read_context(context):
    from watchlist_app.services.research_read_projection import browser_source_view

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
        from watchlist_app.services.risk_evidence_projection import risk_snapshot_projection
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
