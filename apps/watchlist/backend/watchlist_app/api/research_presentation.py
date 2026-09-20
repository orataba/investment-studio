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


def _source_view(source: dict[str, Any]) -> dict[str, Any]:
    # SourceList shows provenance/measurements; SavedEvidence retrieves these
    # complete originals through source_id + notebook version_id when opened.
    return {key: value for key, value in source.items() if key not in {"text", "body", "snapshot", "company", "data"}}


def _notebook_view(notebook: dict[str, Any] | None) -> dict[str, Any] | None:
    if notebook is None:
        return None
    return {**notebook, "sources": [_source_view(source) for source in notebook.get("sources", [])]}


def dossier_view(dossier: dict[str, Any]) -> dict[str, Any]:
    return {
        **dossier,
        "prior_sources": [_source_view(source) for source in dossier.get("prior_sources", [])],
        "notebook": _notebook_view(dossier.get("notebook")),
        "notebook_history": [
            {**record, "notebook": _notebook_view(record.get("notebook"))}
            for record in dossier.get("notebook_history", [])
        ],
        "pm_views": [
            {**view, "sources": [_source_view(source) for source in view.get("sources", [])]}
            for view in dossier.get("pm_views", [])
        ],
    }
