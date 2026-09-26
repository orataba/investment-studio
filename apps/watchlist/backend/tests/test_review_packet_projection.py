"""Review-only navigation preserves current judgments and every retained history."""
from copy import deepcopy

import pytest

from watchlist_app.services import sector_fact_review as review


def _theme(theme_id="theme-a"):
    return {"theme_id": theme_id, "theme_key": "gold-demand", "title": "Demand",
        "status": "active", "owner": "pm", "revision": 7, "version_id": "theme:7",
        "pinned": True, "pin_reason": "PM monitoring", "priority": 2,
        "notes": [{"author": "PM", "body": "Do not confuse flows with price."}],
        "pm_views": [{"note_id": "pm-1", "revision_number": 3}],
        "current_questions": [{"key": "flows", "question": "Has demand changed?", "source_ids": ["original"]}],
        "source_ids": ["original"], "figure_source_ids": ["metric:1"],
        "current_judgment": "Conditional view", "latest_development": "Await disclosure",
        "created_at": "2026-01-01", "updated_at": "2026-09-20", "last_checked_at": "2026-09-25",
        "next_review_on": "2026-10-01", "versions": [{"version_id": "theme:6", "synthesis": "Earlier view", "source_ids": ["original"]}],
        "updates": [{"event_key": "disclosure", "body": "The original update", "occurred_at": "2026-09-10"}],
        "research_progress": [{"source_run_id": "prior", "body": "An unresolved check", "source_ids": ["original"]}]}


def test_theme_history_reconstructs_all_fields_and_agenda_references_exact_current_theme():
    first, second = _theme(), {**_theme("theme-b"), "theme_key": "supply", "versions": []}
    different = {**deepcopy(first), "current_judgment": "Distinct agenda judgment"}
    dossier = {"instrument_id": "gold", "themes": [first, second],
        "review_agenda": {"focus_themes": [second, first, different],
            "focus_policy": {"instruction": "Check all focus themes"},
            "pending_events": [{"event_key": "pending", "status": "pending"}],
            "pm_views": [{"note_id": "pm-1", "revision_number": 3}],
            "tracked_questions": [{"key": "flows"}], "instruction": "Preserve the full agenda"}}
    original = deepcopy(dossier)
    current, history = review._review_dossier_outline(dossier, 2)
    assert dossier == original
    for before, after in zip(original["themes"], current["themes"], strict=True):
        read = after["history_read"]
        assert read == {"tool": "read_review_context", "section": "theme_history",
                        "path": ["gold", before["theme_id"]]}
        assert set(history[before["theme_id"]]) == {"versions", "updates", "research_progress"}
        assert {**{key: value for key, value in after.items() if key != "history_read"},
                **history[before["theme_id"]]} == before
    assert {key: value for key, value in current["review_agenda"].items() if key != "focus_themes"} == {
        key: value for key, value in original["review_agenda"].items() if key != "focus_themes"}
    for reference, index in zip(current["review_agenda"]["focus_themes"][:2], [1, 0], strict=True):
        assert reference == {"theme_id": original["themes"][index]["theme_id"],
            "theme_key": original["themes"][index]["theme_key"],
            "current_read": {"tool": "read_review_context", "section": "research_dossiers", "path": [2, "themes", index]}}
    assert current["review_agenda"]["focus_themes"][2] == different


@pytest.mark.parametrize("historical", [{}, {"updates": []}, {"versions": [], "research_progress": []}])
def test_theme_history_preserves_absent_and_explicitly_empty_arrays(historical):
    theme = {"theme_id": "theme-a", "theme_key": "demand", "notes": [], **historical}
    current, history = review._review_dossier_outline({"instrument_id": "gold", "themes": [theme]}, 0)
    assert history == ({"theme-a": historical} if historical else {})
    assert {**{key: value for key, value in current["themes"][0].items() if key != "history_read"},
            **history.get("theme-a", {})} == theme


def test_packet_separates_full_coverage_and_history_without_changing_originals_or_draft():
    source = {"source_id": "original", "source_type": "public_source",
        "text": "Exact original\x00with literal \\u0000 and a long ending." * 1000,
        "published_at": "2026-09-24T00:00:00+00:00", "retrieved_at": "2026-09-25T00:00:00+00:00"}
    coverage = {"latest_bundle_window_end": "2026-09-24T00:00:00+00:00",
        "latest_received_at": "2026-09-25T00:00:00+00:00", "sources": [{"channel": "used", "status": "partial"}],
        "export_coverage": {"latest_collection": "partial", "failed": 13, "skipped": 28,
                            "sources": [{"channel": "unrelated", "error": "not collected"}]}}
    theme = _theme()
    dossier = {"instrument_id": "gold", "themes": [theme], "review_agenda": {"focus_themes": [theme]}}
    context = {"cutoff": "2026-09-26T00:00:00+00:00", "research_dossiers": [
        {"instrument_id": "unrequested", "themes": [_theme("out-of-scope")]}, dossier],
        "market_coverage": coverage, "market_text_sources": [source],
        "market_queries": [{"instrument_id": "gold", "query": "gold", "cutoff": "2026-09-25T12:00:00+00:00"}]}
    proposed = [{"instrument_id": "gold", "summary": "", "events": [], "coverage": [],
        "reflection": {"status": "reviewed", "summary": "Check", "reviewed_update_ids": [], "source_ids": ["original"]}}]
    original_context, original_draft = deepcopy(context), deepcopy(proposed)
    packet = review._evidence_packet(context, proposed, "bound")
    assert context == original_context and proposed == original_draft
    assert packet["sources"] == [source] and packet["draft_reviews"] == original_draft
    assert packet["market_coverage"] == coverage
    summary = packet["acquisition"]["market_coverage"]
    assert summary["source_count"] == 1 and "sources" not in summary
    assert summary["export_coverage"] == {"latest_collection": "partial", "failed": 13, "skipped": 28, "source_count": 1}
    assert summary["latest_received_at"] == coverage["latest_received_at"]
    assert packet["acquisition"]["market_queries"] == context["market_queries"]
    assert packet["acquisition"]["market_channel_gaps"]["gold"]
    assert set(packet["theme_history"]) == {"gold"}
    assert packet["theme_history"]["gold"]["theme-a"] == {
        key: theme[key] for key in ("versions", "updates", "research_progress")}
    assert packet["research_dossiers"][0]["review_agenda"]["focus_themes"][0]["current_read"]["path"] == [0, "themes", 0]
