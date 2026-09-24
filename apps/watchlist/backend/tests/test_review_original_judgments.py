"""A restricted reviewer must see the exact prior judgment, not its latest edit."""
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from watchlist_app.services import sector_fact_review as review


@pytest.mark.parametrize("explicit", [True, False], ids=["explicit", "inherited"])
def test_review_packet_loads_only_bound_forecast_and_pm_versions_with_their_own_sources(monkeypatch, explicit):
    forecast_version, pm_version = "old-run:forecasts:demand", "pm:manager-note:1"
    links = {"forecast_reviews": {"forecast_key": "demand", "forecast_version_id": forecast_version},
             "lessons": {"forecast_key": "demand", "forecast_version_id": forecast_version},
             "questions": {"pm_note_id": "manager-note", "pm_note_revision": 1}}
    previous = {field: [{"key": "review", **reference}] for field, reference in links.items()}
    previous["forecasts"] = [{"key": "demand", "version_id": "new-run:forecasts:demand", "claim": "后来改为谨慎", "versions": [
        {"version_id": forecast_version, "claim": "最初预计需求增加"}]}]
    context = {"sector_run": True, "instrument_ids": ["asset"], "cutoff": "2026-09-24T00:00:00+00:00",
               "research_dossiers": [{"instrument_id": "asset", "notebook": previous,
                    "pm_views": [{"note_id": "manager-note", "revision_number": 2, "body": "后来修改的观点",
                                  "versions": [{"version_id": pm_version, "revision_number": 1}]}]}],
               "web_evidence": [{"operation": "fetch", "sources": [{"source_id": "legacy-id", "text": "当前另一个版本正文"}]}]}
    proposal = {field: [{"key": "review", **(reference if explicit else {})}] for field, reference in links.items()}
    draft = [{"instrument_id": "asset", "events": [], "research": proposal}]
    originals = {forecast_version: {"instrument_id": "asset", "version_id": forecast_version, "kind": "forecasts",
        "value": {"key": "demand", "claim": "最初预计需求增加", "version_id": forecast_version},
        "sources": [{"source_id": "legacy-id", "text": "当时留存的版本正文"}], "information_cutoff": "2026-08-01T00:00:00+00:00"},
        pm_version: {"instrument_id": "asset", "version_id": pm_version, "kind": "pm_view",
                    "value": {"body": "经理原始观点", "revision_number": 1}, "sources": []}}
    requests = []
    def api(run_id, suffix, payload=None):
        assert run_id == "bound-run"
        query = parse_qs(urlsplit(suffix).query)
        assert urlsplit(suffix).path == "dossier/asset" and set(query) == {"version_id"}
        version_id = query["version_id"][0]
        requests.append(version_id)
        return deepcopy(originals[version_id])
    monkeypatch.setattr(review, "_api_request", api)
    packet = review._evidence_packet(context, draft, "bound-run")
    assert sorted(requests) == sorted(originals)  # Shared forecast reference is fetched once.
    assert packet["prior_judgment_versions"] == [originals[key] for key in sorted(originals)]
    assert next(source for source in packet["sources"] if source["source_id"] == "legacy-id")["text"] == "当前另一个版本正文"
    assert packet["prior_judgment_versions"][0]["sources"][0]["text"] == "当时留存的版本正文"
    assert packet["draft_reviews"] == draft


def test_unrelated_existing_references_do_not_expand_a_quiet_review_packet(monkeypatch):
    context = {"cutoff": "2026-09-24T00:00:00+00:00", "research_dossiers": [{"instrument_id": "asset", "notebook": {
        "forecast_reviews": [{"key": "prior", "forecast_key": "demand", "forecast_version_id": "unrelated-old"}]}}]}
    monkeypatch.setattr(review, "_api_request", lambda *args: pytest.fail("Unreferenced history should not be loaded"))
    packet = review._evidence_packet(context, [{"instrument_id": "asset", "events": [], "research": None}], "bound-run")
    assert packet["prior_judgment_versions"] == []


def test_version_response_cannot_substitute_a_different_judgment(monkeypatch):
    monkeypatch.setattr(review, "_api_request", lambda *args: {"instrument_id": "asset", "version_id": "different", "value": {}})
    with pytest.raises(ValueError, match="bound reference"):
        review._evidence_packet({"cutoff": "2026-09-24T00:00:00+00:00"}, [{"instrument_id": "asset", "events": [],
            "research": {"questions": [{"key": "q", "pm_note_id": "n", "pm_note_revision": 1}]}}], "bound-run")
