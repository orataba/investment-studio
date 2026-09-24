"""A compiled baseline is ordinary retained research for the next research round."""
from copy import deepcopy

from watchlist_app import research_mcp as mcp
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research
from watchlist_app.services.research_imports import ResearchImport
from watchlist_app.services.research_read_projection import dossier_sections

from .test_research_imports import package, preview, publish, seed
from .test_research_read_paging import complete, wire


def test_compiled_research_and_exact_evidence_reach_next_automatic_round(client, monkeypatch):
    """Exercise publication, real preparation/binding, HTTP reads and the MCP wire."""
    iid = "fund-us-agg"
    seed(client)
    baseline = package(iid)
    expected_figure = ResearchImport.model_validate(baseline).figures[0].output.model_dump(mode="json")
    first = publish(client, baseline, preview(client, baseline))
    assert first.status_code == 200, first.text
    first_dossier = client.get(f"/api/research/instruments/{iid}/dossier").json()
    original_theme = first_dossier["themes"][0]

    # A later compiled revision establishes real retained history, not a fake
    # earlier prediction or an in-memory substitute for the publication path.
    revision = package(iid)
    revision["review"]["themes"][0]["synthesis"] = "收入改善；应收回收仍是下一次核对重点。"
    revision["review"]["research"]["investment_view"]["direction"] = "增长基线已建立，回报取决于现金兑现。"
    second = publish(client, revision, preview(client, revision))
    assert second.status_code == 200, second.text
    published = client.get(f"/api/research/instruments/{iid}/dossier").json()
    theme = published["themes"][0]
    assert any(version["synthesis"] == original_theme["synthesis"] for version in theme["versions"])

    with get_session_factory()() as session:
        run, created = sector_research.begin_run(session, [iid])
        assert created and run.entry_id not in {first.json()["run_id"], second.json()["run_id"]}
        run_id = run.entry_id
    # The standard client fixture supplies isolated SQLite market/text stores;
    # no model, external data, dossier, publication or binding function is mocked.
    sector_research.prepare_run(run_id)
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        bound = run.context_json["research_dossiers"][0]
        assert bound["notebook"]["version_id"] == published["notebook"]["version_id"]
        assert bound["notebook"]["investment_view"]["direction"] == revision["review"]["research"]["investment_view"]["direction"]
        assert bound["themes"][0]["updates"] == theme["updates"]
        assert len(bound["themes"][0]["updates"]) >= 2
        assert bound["review_agenda"]["focus_themes"][0]["theme_id"] == theme["theme_id"]
        assert bound["pm_views"] == []
        # A repeated instrument read must keep the same bound research snapshot.
        retained = deepcopy(run.context_json["research_dossiers"])
        sector_research.bind_research_instruments(session, run, [iid])
        assert run.context_json["research_dossiers"] == retained

    calls = []

    def request(suffix, payload=None):
        # Replace transport only; the actual scoped run endpoints still resolve
        # every original/version against the prepared, persisted run.
        calls.append(suffix)
        assert payload is None
        response = client.get(f"/api/research/runs/{run_id}/{suffix}")
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(mcp, "request", request)
    overview = wire("read_research_dossier", {"instrument_id": iid})
    assert overview["notebook_version_id"] == published["notebook"]["version_id"]
    assert overview["current_investment_view"]["direction"] == revision["review"]["research"]["investment_view"]["direction"]
    for section in ("themes", "modules", "review_agenda", "research_plan"):
        returned = complete("read_research_dossier", {"instrument_id": iid, "section": section})
        assert returned == dossier_sections(bound)[section]

    figure_id = theme["figure_source_ids"][0]
    figure = complete("read_research_dossier", {"instrument_id": iid, "source_id": figure_id})
    assert figure["source_type"] == "computed_metric"
    assert figure["data"]["tables"] == expected_figure["tables"]
    assert figure["data"]["charts"] == expected_figure["charts"]
    assert figure["methodology"]["executed_at"] is None
    original_id = figure["source_ids"][0]
    original = complete("read_research_dossier", {"instrument_id": iid, "source_id": original_id})
    assert original["text"] == baseline["sources"][0]["text"]
    assert original["provenance"]["raw_capture_kind"] == "author_provided_text"
    assert original["provenance"]["locator"] == baseline["sources"][0]["locator"]
    assert figure["methodology"]["input_sources"][0]["text"] == original["text"]

    prior = complete("read_research_dossier", {"instrument_id": iid,
                     "version_id": original_theme["source_version_id"]})
    assert prior["value"]["synthesis"] == original_theme["synthesis"]
    assert prior["value"]["synthesis"] != theme["synthesis"]
    assert any(source["source_type"] == "computed_metric" for source in prior["sources"])
    assert all(suffix == "context" or suffix.startswith(f"dossier/{iid}?") for suffix in calls)
