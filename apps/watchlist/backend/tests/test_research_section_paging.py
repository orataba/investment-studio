from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research
from .test_sector_research import seed_sector


def test_section_pages_keep_integer_array_paths_and_bound_instrument(client, monkeypatch):
    seed_sector(client, monkeypatch)
    original = "原始访谈 " * 15000
    with get_session_factory()() as session:
        run, _ = sector_research.begin_run(session, ["xlk"])
        run_id = run.entry_id
        run.context_json = {**run.context_json, "research_dossiers": [{"instrument_id": "xlk",
            "pm_views": [{"body": original}]}], "last_successful_review_cutoff": "2026-10-12T00:00:00Z"}
        session.commit()
    from watchlist_app.services import research_run_context
    load = research_run_context.load_run_fields
    requested = []
    def selected_fields(session, run_id, fields, **kwargs):
        requested.append(set(fields))
        return load(session, run_id, fields, **kwargs)
    monkeypatch.setattr(research_run_context, "load_run_fields", selected_fields)
    path = f"/api/research/runs/{run_id}/section-context"
    selector = {"instrument_id": "xlk", "section": "overview"}
    overview = client.post(path, json=selector)
    assert overview.status_code == 200, overview.text
    assert overview.json()["pm_views"]["inline"] is False
    array = client.post(path, json={**selector, "path": ["pm_views"]})
    assert array.status_code == 200, array.text
    assert array.json()["deferred"][0]["path"] == ["pm_views", 0]
    nested = client.post(path, json={**selector, "path": ["pm_views", 0]})
    assert nested.status_code == 200, nested.text
    assert nested.json()["deferred"][0]["path"] == ["pm_views", 0, "body"]
    parts, offset = [], 0
    while True:
        result = client.post(path, json={**selector, "path": ["pm_views", 0, "body"], "offset": offset})
        assert result.status_code == 200, result.text
        data = result.json()
        parts.append(data["data"])
        if data["next_offset"] is None:
            break
        offset = data["next_offset"]
    assert "".join(parts) == original
    assert client.post(path, json={**selector, "instrument_id": "unbound"}).status_code == 422
    assert not {"instrument_inputs", "sector_inputs", "analyst_estimate_changes", "market_queries"}.intersection(set.union(*requested))
    recent = client.post(path, json={"instrument_id": "xlk", "section": "recent_updates"}).json()
    assert recent["acquisition"]["last_successful_review_cutoff"] == "2026-10-12T00:00:00Z"


def test_section_mcp_preserves_typed_path_in_json(monkeypatch):
    from watchlist_app import research_mcp
    calls = []
    monkeypatch.setattr(research_mcp, "request", lambda suffix, payload: calls.append((suffix,payload)) or {})
    research_mcp.read_research_section("xlk", "overview", path=["pm_views", 0, "body"], offset=12000)
    suffix, payload = calls[0]
    assert suffix == "section-context"
    assert payload["path"] == ["pm_views", 0, "body"] and payload["offset"] == 12000


def test_large_overview_branch_keeps_small_identity_fields_inline():
    from watchlist_app.services.research_read_projection import checked_overview, read_page
    original = {"instrument_id": "xlk", "section": "recent_updates", "skill": "Read relevant evidence",
                "trigger": {"sources": "A complete retained original " * 10000}}
    result = checked_overview(dict(original), pageable_fields=[([key], {"tool": "read", "path": [key]}) for key in original])
    assert result["instrument_id"] == "xlk" and result["skill"] == original["skill"]
    assert result["trigger"]["inline"] is False
    pieces, offset = [], 0
    while True:
        page = read_page(original, {}, path=["trigger", "sources"], offset=offset)
        pieces.append(page["data"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert "".join(pieces) == original["trigger"]["sources"]
