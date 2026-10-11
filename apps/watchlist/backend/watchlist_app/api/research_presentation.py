"""Browser read projections; retained research and agent inputs stay complete."""

from typing import Any


def review_status_view(review: dict[str, Any] | None) -> dict[str, Any] | None:
    if review is None:
        return None
    notebook = review.get("current_research")
    return {
        **{key: value for key, value in review.items() if key not in {"research", "current_research"}},
        "current_research": None if notebook is None else {"investment_view": notebook.get("investment_view")},
    }


def _source_view(source: dict[str, Any], *, session=None, figure=False) -> dict[str, Any]:
    # SourceList shows provenance/measurements; SavedEvidence retrieves these
    # complete originals through source_id + notebook version_id when opened.
    from watchlist_app.services.research_read_projection import browser_source_view
    view = browser_source_view(source)
    if (figure and session is not None and source.get("retained_reference")
            and source.get("source_type") == "computed_metric"
            and view.get("observation_domain") == "unclassified"):
        # Older stored references predate domain metadata. Resolve only the
        # classification contract for a figure actually used by this notebook;
        # neither chart data nor original inputs belong in the dossier response.
        from watchlist_app.services.research_run_evidence import resolve_reference
        classified = resolve_reference(source, session=session, bound_source=True, projection="classification")
        view["observation_domain"] = classified["observation_domain"]
    return view


def _notebook_view(notebook: dict[str, Any] | None, *, session=None) -> dict[str, Any] | None:
    if notebook is None:
        return None
    figures = {sid for module in notebook.get("modules", []) for sid in module.get("figure_source_ids", [])}
    return {**notebook, "sources": [_source_view(source, session=session, figure=source.get("source_id") in figures)
                                    for source in notebook.get("sources", [])]}


def dossier_view(dossier: dict[str, Any], *, session=None) -> dict[str, Any]:
    return {
        # Themes have their own browser endpoint. The review agenda is agent
        # working context; neither is consumed by the dossier panel.
        **{key: value for key, value in dossier.items() if key not in {"themes", "review_agenda"}},
        "prior_sources": [_source_view(source) for source in dossier.get("prior_sources", [])],
        "notebook": _notebook_view(dossier.get("notebook"), session=session),
        # Reviews and lessons are already shown from the notebook. Their agent
        # catalogue entries do not have the historical event-case schema.
        "historical_cases": [record for record in dossier.get("historical_cases", [])
                             if record.get("source_type") != "research_review"],
        "notebook_history": [
            {**record, "notebook": _notebook_view(record.get("notebook"), session=session)}
            for record in dossier.get("notebook_history", [])
        ],
        "pm_views": [
            {**view, "sources": [_source_view(source) for source in view.get("sources", [])]}
            for view in dossier.get("pm_views", [])
        ],
    }
