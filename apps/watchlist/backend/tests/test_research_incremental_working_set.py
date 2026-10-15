from .research_sections_fixture import completed_sections
"""Incremental work is durable; historical breadth does not become model scope."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from watchlist_app.db.models.research_monitor import ResearchMonitorState
from watchlist_app.db.models.workbench import ResearchEntry, RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_monitor as monitor, research_working_set as working, sector_research as sector
from watchlist_app.services.research_identity import research_identity
from .test_sector_research import seed_sector
from .test_research_triggers import store

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def test_closed_history_never_loads_into_working_set_and_current_nul_is_exact(client, monkeypatch):
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        for i in range(120):
            session.add(RiskCase(case_id=f"closed-{i}", instrument_id="xlk", signal=f"sector:closed-{i}",
                title="closed", body="closed", status="recorded", trigger_active=False,
                evidence_json={"follow_up": "none", "sources": [{"text": "closed" * 10000}]}, history_json=[{"text": "old" * 10000}]))
        session.add_all([
            RiskCase(case_id="watch", instrument_id="xlk", signal="sector:watch", title="watch", body="watch",
                status="recorded", trigger_active=False, evidence_json={"follow_up": "watch", "event_version_id": "watch:1", "next_watch": "公告\x00原值"}, history_json=[]),
            RiskCase(case_id="active", instrument_id="xlk", signal="sector:active", title="active risk", body="active",
                status="investigating", trigger_active=True, evidence_json={"follow_up": "none", "event_version_id": "active:1"}, history_json=[]),
            RiskCase(case_id="withdrawn", instrument_id="xlk", signal="sector:withdrawn", title="withdrawn", body="withdrawn",
                status="recorded", trigger_active=False, evidence_json={"follow_up": "watch", "withdrawn": True}, history_json=[]),
        ])
        session.commit()
        session.expunge_all()
        loaded = []
        def on_load(session, instance):
            if isinstance(instance, RiskCase):
                loaded.append(instance.case_id)
        event.listen(session, "loaded_as_persistent", on_load)
        rows = working.current_events(session, "xlk")
        assert set(loaded) == {"watch", "active"}
        assert {row["case_id"] for row in rows} == {"watch", "active"}
        assert next(row for row in rows if row["case_id"] == "watch")["next_watch"] == "公告\x00原值"
        assert len(json.dumps(rows)) < 15000


def test_selected_current_work_and_affected_dependencies_are_distinct():
    original = {"version_id": "nb-v1", "modules": [{"key": "economics", "summary": "current", "analysis": "long" * 10000,
        "version_id": "module-v1", "source_ids": ["source-current"], "versions": [{"analysis": "old" * 10000}]}],
        "questions": [{"key": "kept", "theme_id": "selected", "status": "supported", "tracking_status": "active"},
                      {"key": "closed", "theme_id": "selected", "status": "open", "tracking_status": "closed"},
                      {"key": "other", "theme_id": "unselected", "tracking_status": "active"}],
        "sources": [{"source_id": "source-current", "text": "original", "versions": "literal source field"}]}
    compact = working.working_notebook(original, {"selected"})
    assert [row["key"] for row in compact["questions"]] == ["kept"]
    assert "analysis" not in compact["modules"][0] and "versions" not in compact["modules"][0]
    assert compact["sources"] == original["sources"] and "analysis" in original["modules"][0]
    dossier = {"instrument_id": "xlk", "notebook": compact, "themes": [
        {"theme_id": "selected", "status": "active", "priority": "important", "source_version_id": "t1", "source_ids": ["source-current"]},
        {"theme_id": "other", "status": "active", "priority": "core", "source_version_id": "t2"},
        {"theme_id": "closed", "status": "closed", "priority": "core", "source_version_id": "t3"}]}
    context = {"cutoff": NOW.isoformat(), "scheduled": True, "last_successful_review_cutoff": NOW.isoformat(),
        "incremental_trigger": {"xlk": {"reasons": [{"kind": "new_research_sources", "sources": [{"source_id": "withdrawal", "previous_source_id": "source-current", "change": "withdrawn_original"}]}]}}}
    frozen = working.build_working_set(dossier, [], context)
    assert frozen["selected_theme_ids"] == ["other", "selected"]
    assert frozen["affected_theme_ids"] == ["selected"] and frozen["reviewed_theme_ids"] == []
    assert frozen["source_dependencies"]["source-current"] == [{"kind": "theme", "id": "selected"}, {"kind": "notebook", "id": "nb-v1"}]


def test_scan_failure_rolls_back_and_successful_application_consumes_only_frozen_delta(client, monkeypatch):
    seed_sector(client, monkeypatch)
    from watchlist_app.services import research_triggers
    def trigger(source):
        return {"reasons": [{"kind": "new_research_sources", "sources": [{"source_id": source}]}], "coverage_cursor": {"attempted_at": NOW.isoformat()}}
    latest = {"value": trigger("old")}
    monkeypatch.setattr(research_triggers, "research_trigger", lambda *a, **k: deepcopy(latest["value"]))
    with get_session_factory()() as session:
        run, _ = sector.begin_run(session, ["xlk"])
        actor = research_identity()
        state, frozen = monitor.scan(session, "xlk", {"cutoff": NOW.isoformat()}, team_id=actor["team_id"], now=NOW)
        monitor.mark_attempt(state, run.entry_id)
        run.context_json = {**run.context_json, "incremental_trigger": {"xlk": frozen}}
        session.commit()
        latest["value"] = trigger("new")
        monitor.scan(session, "xlk", {"cutoff": (NOW + timedelta(days=1)).isoformat()}, team_id=actor["team_id"], now=NOW + timedelta(hours=1))
        monitor.mark_applied(session, run)
        session.commit()
        assert {row["value"]["source_id"] for row in state.pending_changes_json.values()} == {"new"}
        assert state.applied_at.replace(tzinfo=UTC) == NOW
        before = deepcopy(state.scan_cursor_json)
        def fail(*a, **k):
            raise OSError("source unavailable")
        monkeypatch.setattr(research_triggers, "research_trigger", fail)
        with pytest.raises(OSError):
            monitor.scan(session, "xlk", {}, team_id=actor["team_id"], now=NOW + timedelta(hours=2))
        session.rollback()
        assert state.scan_cursor_json == before


def test_due_suppression_keeps_only_current_versions_and_never_enters_model_cursor(client, monkeypatch):
    seed_sector(client, monkeypatch)
    from watchlist_app.services import research_triggers
    due = {"reasons": [{"kind": "research_check_due", "items": [{"case_id": "event", "event_version_id": "event:2", "due_at": "2026-10-01"}]}], "coverage_cursor": {}}
    key = next(iter(monitor.change_items(due)))
    monkeypatch.setattr(research_triggers, "research_trigger", lambda *a, **k: deepcopy(due))
    with get_session_factory()() as session:
        state = ResearchMonitorState(team_id="default", instrument_id="xlk", scanned_at=NOW,
            scan_cursor_json={"applied_due_changes": ["obsolete", key]}, pending_changes_json={}, revision=1, attempted_revision=1)
        session.add(state); session.commit()
        state, trigger = monitor.scan(session, "xlk", {}, team_id="default", now=NOW + timedelta(days=1))
        assert trigger is None and state.scan_cursor_json["applied_due_changes"] == [key]
        assert "applied_due_changes" not in monitor.pending_trigger(state, "xlk", now=NOW)["coverage_cursor"]
        due["reasons"] = []
        state, trigger = monitor.scan(session, "xlk", {}, team_id="default", now=NOW + timedelta(days=2))
        assert trigger is None and state.scan_cursor_json["applied_due_changes"] == []


def test_cross_day_quiet_scan_and_ordinary_price_change_create_no_model_run(client, monkeypatch, store):
    seed_sector(client, monkeypatch)
    from watchlist_app.db.models import InstrumentChartReadModel
    class Clock:
        current = NOW
        @classmethod
        def now(cls, tz): return cls.current.astimezone(tz)
        fromisoformat = datetime.fromisoformat
    monkeypatch.setattr(sector, "datetime", Clock)
    with get_session_factory()() as session:
        first, _ = sector.begin_run(session, ["xlk"])
        first.status = "completed"
        session.commit()
        Clock.current += timedelta(days=1)
        second, created = sector.begin_run(session, ["xlk"], scheduled=True)
        assert not created and second.entry_id == first.entry_id
        session.add(InstrumentChartReadModel(instrument_id="xlk", data_freshness_status="fresh", last_recalculated_at=NOW, payload_json={"research_returns": {"points": [{"date": "2026-10-13", "value": 1.03}]}}))
        session.commit()
        Clock.current += timedelta(days=1)
        assert not sector.begin_run(session, ["xlk"], scheduled=True)[1]
        assert len(session.scalars(select(ResearchEntry).where(ResearchEntry.kind == "analysis")).all()) == 1
        state = session.get(ResearchMonitorState, (research_identity()["team_id"], "xlk"))
        assert state.scanned_at is not None and state.applied_at is None


def test_private_fund_does_not_recover_generic_news_queries(client, monkeypatch, store):
    seed_sector(client, monkeypatch)
    from investment_studio_instrument_core.db_models import Instrument
    from watchlist_app.services import research_triggers
    with get_session_factory()() as session:
        registry = session.get(Instrument, "xlk")
        registry.instrument_type = "private_fund"
        registry.exchange_code = None
        session.commit()
        monkeypatch.setattr(research_triggers, "_monitoring_context", lambda *args: pytest.fail("private fund must not scan generic-news history"))
        result = research_triggers.research_trigger(session, "xlk", {"cutoff": NOW.isoformat(), "instrument_ids": ["xlk"]}, now=NOW + timedelta(days=1))
        assert result["coverage_cursor"]["market_queries"] == [] and not result["reasons"]


def test_exact_history_http_read_binds_original_without_reopening_and_checks_attempt(client, monkeypatch):
    from dataclasses import replace
    from studio_identity import Principal
    from watchlist_app.services.research_notebook import research_sources
    import watchlist_app.main as main
    seed_sector(client, monkeypatch)
    actor = Principal("reader", "Reader", "default", resource_scope={"kind": "run", "id": "history-run", "attempt": "1"})
    monkeypatch.setattr(main, "resolve_request", lambda *a, **k: actor)
    source = {"source_id": "closed-original", "source_type": "public_source", "url": "https://issuer.example/old", "title": "Original",
              "text": "Original\x00untouched", "published_at": "2026-09-01", "retrieved_at": "2026-09-02T00:00:00Z"}
    snapshot = {"event_key": "closed", "event_version_id": "closed:1", "recorded_at": "2026-09-02T00:00:00Z",
                "checked_at": "2026-09-02T00:00:00Z", "sources": [source], "follow_up": "none"}
    from watchlist_app.db.models.workbench import ResearchTopic
    with get_session_factory()() as session:
        session.add(RiskCase(case_id="closed", instrument_id="xlk", signal="sector:closed", title="old", body="old",
            status="recorded", trigger_active=False, evidence_json=snapshot,
            history_json=[{"at": snapshot["recorded_at"], "snapshot": snapshot}]))
        session.add(ResearchTopic(topic_id="history-topic", title="History", visibility="private", created_by_user_id="reader"))
        session.flush()
        session.add(ResearchEntry(entry_id="history-run", topic_id="history-topic", kind="analysis", title="History", status="running",
            context_json={"sector_run": True, "run_id": "history-run", "instrument_ids": ["xlk"], "cutoff": NOW.isoformat(),
                "research_actor": {"kind": "user", "user_id": "reader"}, "execution": {"attempt": 1},
                "research_dossiers": [{"instrument_id": "xlk", "notebook": None, "materials": [], "themes": []}], "prior_events": []}))
        session.commit()
    payload = {"resource": "dossier", "instrument_id": "xlk", "version_id": "closed:1"}
    response = client.post("/api/research/runs/history-run/read", json=payload)
    assert response.status_code == 200, response.text
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "history-run")
        binding = run.context_json["research_historical_reads"]["xlk:closed:1"]
        assert binding["value"]["event_key"] == "closed" and binding["version_id"] == "closed:1"
        assert "Original\u0000untouched" == research_sources(run.context_json, run.entry_id)["closed-original"]["text"]
        assert run.context_json["prior_events"] == []
        case = session.get(RiskCase, "closed")
        assert not case.trigger_active and case.evidence_json["follow_up"] == "none"
        before = deepcopy(run.context_json)
        case.history_json = [*case.history_json, {"at": (NOW + timedelta(days=1)).isoformat(),
            "snapshot": {**snapshot, "event_version_id": "closed:2", "recorded_at": (NOW + timedelta(days=1)).isoformat()}}]
        session.commit()
    response = client.post("/api/research/runs/history-run/read", json={**payload, "version_id": "closed:2"})
    assert response.status_code == 404 and "截止" in response.text
    actor = replace(actor, resource_scope={"kind": "run", "id": "history-run", "attempt": "0"})
    assert client.post("/api/research/runs/history-run/read", json=payload).status_code == 409
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "history-run").context_json == before


def test_sparse_notebook_merge_preserves_unknown_fields_and_unreviewed_dates():
    from watchlist_app.services.research_notebook import ResearchNotebook, retain_notebook
    previous = {"version_id": "old", "legacy_unknown": {"recorded_at": None, "body": "retain"},
        "questions": [{"key": "q", "question": "old", "status": "open", "tracking_status": "active", "legacy_unknown": "untouched"}],
        "modules": [{"key": "economics", "summary": "old", "analysis": "complete old body", "updated_at": "2026-01-01", "version_id": "old-module", "unknown": "retain"}]}
    next_value = retain_notebook(ResearchNotebook(questions=[{"key": "q", "question": "new", "assessment": "new", "next_check": "new"}]), previous, {}, "new", NOW.isoformat())
    assert next_value["legacy_unknown"] == previous["legacy_unknown"]
    assert next_value["questions"][0]["legacy_unknown"] == "untouched"
    assert next_value["modules"] == previous["modules"]


def test_only_affected_theme_is_required_and_unselected_theme_keeps_judgment_clock(client, monkeypatch):
    from .test_sector_research import bind_policy_theme, result
    from watchlist_app.services.research_themes import ThemeInput, save_theme, theme_index
    from watchlist_app.services.research_dossier import read_dossier
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = sector.begin_run(session, ["xlk"])
        first = bind_policy_theme(session, run)
        second = save_theme(session, "xlk", ThemeInput(title="成本", question="成本如何变化？"))
        bound = read_dossier(session, "xlk")
        before = deepcopy(next(row for row in theme_index(session, "xlk") if row["theme_id"] == second["theme_id"]))
        run.context_json = {**run.context_json, "research_dossiers": [bound],
            "research_working_sets": {"xlk": {"affected_theme_ids": [first["theme_id"]], "affected_event_ids": [],
                "selected_theme_ids": [first["theme_id"], second["theme_id"]]}}}
        with pytest.raises(ValueError, match="受影响主题"):
            sector.validate_result(session, run, sector.ReviewResult.model_validate_json(result()))
        completed_sections(run)
        sector.apply_result(session, run, result(theme=first))
        session.commit()
        after = next(row for row in theme_index(session, "xlk") if row["theme_id"] == second["theme_id"])
        assert after == before
        assert run.context_json["research_working_sets"]["xlk"]["reviewed_theme_ids"] == [first["theme_id"]]


def test_due_event_cannot_be_silently_consumed_by_empty_success(client, monkeypatch):
    from .test_sector_research import result
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = sector.begin_run(session, ["xlk"])
        run.context_json = {**run.context_json, "research_working_sets": {
            "xlk": {"affected_theme_ids": [], "affected_event_ids": ["due-case"]}}}
        with pytest.raises(ValueError, match="跟踪事项尚未复核"):
            sector.validate_result(session, run, sector.ReviewResult.model_validate_json(result()))
        assert run.status == "queued" and run.completed_at is None


def test_closed_due_is_pruned_but_failed_original_survives_until_exact_revision_supersedes(client, monkeypatch):
    seed_sector(client, monkeypatch)
    from watchlist_app.services import research_triggers
    old = {"reasons": [
        {"kind": "research_check_due", "items": [{"case_id": "closed", "event_version_id": "closed:1", "due_at": "2026-10-01"}]},
        {"kind": "theme_baseline", "themes": [{"theme_id": "now-inactive", "baseline_requested_at": "2026-10-01"}]},
        {"kind": "new_research_sources", "sources": [
            {"document_id": "doc", "source_id": "v2", "version_id": "v2", "previous_source_id": "v1"},
            {"document_id": "other", "source_id": "unapplied", "version_id": "other-v1"}]}]}
    latest = {"value": {"reasons": [], "coverage_cursor": {}}}
    monkeypatch.setattr(research_triggers, "research_trigger", lambda *a, **k: deepcopy(latest["value"]))
    with get_session_factory()() as session:
        pending = monitor.change_items(old)
        state = ResearchMonitorState(team_id="default", instrument_id="xlk", scanned_at=NOW,
            scan_cursor_json={}, pending_changes_json=pending, revision=1, attempted_revision=1)
        session.add(state); session.commit()
        state, trigger = monitor.scan(session, "xlk", {}, team_id="default", now=NOW + timedelta(days=1))
        assert trigger is None and state.revision == state.attempted_revision
        assert {row["kind"] for row in state.pending_changes_json.values()} == {"new_research_sources"}
        assert {row["value"]["source_id"] for row in state.pending_changes_json.values()} == {"v2", "unapplied"}
        latest["value"] = {"reasons": [{"kind": "new_research_sources", "sources": [
            {"document_id": "doc", "source_id": "v3", "version_id": "v3", "change": "withdrawn_original", "previous_source_id": "v2"}]}], "coverage_cursor": {}}
        state, trigger = monitor.scan(session, "xlk", {}, team_id="default", now=NOW + timedelta(days=2))
        remaining = {row["value"]["source_id"]: row["value"] for row in state.pending_changes_json.values()}
        assert set(remaining) == {"v3", "unapplied"}
        assert remaining["v3"]["previous_source_ids"] == ["v1", "v2"]
        assert trigger is not None


def test_registered_terms_change_is_material_but_refresh_clock_is_not(client, monkeypatch, store):
    from watchlist_app.db.models import InstrumentManualProfile
    from watchlist_app.services import research_triggers
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        profile = InstrumentManualProfile(instrument_id="xlk", price_payload_json={"redemption": "monthly"}, updated_at=NOW)
        session.add(profile); session.commit()
        context = {"cutoff": NOW.isoformat(), "instrument_ids": ["xlk"]}
        first = research_triggers.research_trigger(session, "xlk", context, now=NOW + timedelta(days=1))
        assert first["reasons"] == []
        context["incremental_trigger"] = {"xlk": first}
        profile.updated_at = NOW + timedelta(days=1); session.commit()
        quiet = research_triggers.research_trigger(session, "xlk", context, now=NOW + timedelta(days=2))
        assert quiet["reasons"] == []
        context["incremental_trigger"] = {"xlk": quiet}
        profile.price_payload_json = {"redemption": "quarterly"}; session.commit()
        changed = research_triggers.research_trigger(session, "xlk", context, now=NOW + timedelta(days=3))
        material = changed["reasons"][0]["materials"][0]
        assert material["key"] == "product_material" and material["changed_fields"] == ["terms_and_fees"]
        assert material["previous"]["terms_and_fees"] == {"redemption": "monthly"}
        assert material["current"]["terms_and_fees"] == {"redemption": "quarterly"}


def test_registered_material_originals_are_available_initially_and_only_reread_when_changed(client, monkeypatch):
    from watchlist_app.services.instrument_documents import instrument_document_dir
    from watchlist_app.db.models import InstrumentManualProfile
    from watchlist_app.services import research_dossier
    seed_sector(client, monkeypatch)
    folder = instrument_document_dir("xlk")
    folder.mkdir(parents=True)
    (folder / "terms.txt").write_text("管理人确认：赎回每季度开放。", encoding="utf-8")
    with get_session_factory()() as session:
        session.add(InstrumentManualProfile(instrument_id="xlk", updated_at=NOW,
            documents_payload_json={"current_documents": [{"title": "赎回条款", "stored_file_name": "terms.txt",
                "uploaded_at": NOW.isoformat(), "as_of_date": "2026-10-01"}]}))
        session.commit()
        context = {"cutoff": NOW.isoformat(), "instrument_ids": ["xlk"], "scheduled": True}
        dossier, _ = working.read_working_dossier(session, "xlk", context, actor=research_identity())
        material, = dossier["materials"]
        assert material["body"] == "管理人确认：赎回每季度开放。"
        assert material["source_id"].startswith("material:document:xlk:")
        context["last_successful_review_cutoff"] = NOW.isoformat()
        original_reader = research_dossier._document_materials
        monkeypatch.setattr(research_dossier, "_document_materials", lambda *a: pytest.fail("unchanged originals must not be re-extracted"))
        quiet, _ = working.read_working_dossier(session, "xlk", context, actor=research_identity())
        assert quiet["materials"] == [] and quiet["material_index"]
        monkeypatch.setattr(research_dossier, "_document_materials", original_reader)
        context["incremental_trigger"] = {"xlk": {"reasons": [{"kind": "material_arrival",
            "materials": [{"key": "registered_material_directory"}]}]}}
        changed, _ = working.read_working_dossier(session, "xlk", context, actor=research_identity())
        assert changed["materials"] == dossier["materials"]


def test_theme_enqueue_pause_reports_saved_theme_as_paused(client, monkeypatch):
    from studio_runtime.model_work import ModelWorkPaused
    from watchlist_app.api.routes.research_dossier import _request_theme_research
    from watchlist_app.services import research_policy, research_runner, research_execution
    seed_sector(client, monkeypatch)
    # Isolate the race after admission; the real policy rejection has separate
    # API coverage. A pause at enqueue must remain a pause in this response.
    monkeypatch.setattr(research_policy, "require_instrument_research", lambda *a, **k: None)
    monkeypatch.setattr(research_runner, "harness_available", lambda: True)
    def paused(*args, **kwargs):
        raise ModelWorkPaused("全局研究已暂停")
    monkeypatch.setattr(research_execution, "enqueue_run", paused)
    with get_session_factory()() as session:
        response = _request_theme_research(session, "xlk", {"theme_id": "saved-theme"})
        assert response["research_status"] == "paused" and response["research_run_id"]
        assert "全局研究已暂停" in response["research_message"]


@pytest.mark.parametrize("summary_only", [False, True])
def test_shared_working_set_projection_does_not_grant_other_run_or_private_history_access(client, monkeypatch, summary_only):
    from fastapi import HTTPException
    from studio_identity import Principal, principal_context
    from watchlist_app.db.models.workbench import ResearchTopic
    from watchlist_app.services.research_dossier import latest_working_set
    from watchlist_app.services.research_run_context import load_run_fields
    seed_sector(client, monkeypatch)
    with get_session_factory()() as session:
        run, _ = sector.begin_run(session, ["xlk"])
        run.status = "completed"
        value = {"selected_theme_ids": ["主题\x00原值"], "reviewed_theme_ids": [], "cutoff": NOW.isoformat()}
        run.context_json = {**run.context_json, "research_working_sets": {"xlk": value}}
        session.commit()
        token = Principal("reader", "Reader", "default", resource_scope={"kind": "run", "id": "another-run", "attempt": "1"})
        with principal_context(token):
            assert latest_working_set(session, "xlk", actor=research_identity(), summary_only=summary_only) == value
            with pytest.raises(HTTPException) as denied:
                load_run_fields(session, run.entry_id, {"research_working_sets"}, summary_only=True)
            assert denied.value.status_code == 403
            topic = session.get(ResearchTopic, run.topic_id)
            topic.visibility = "private"
            session.flush()
            assert latest_working_set(session, "xlk", actor=research_identity(), summary_only=summary_only) is None
            topic.visibility = "team"
            topic.portfolio_id = "restricted-portfolio"
            session.flush()
            assert latest_working_set(session, "xlk", actor=research_identity(), summary_only=summary_only) is None


def test_monitor_retires_only_proven_dangling_pending_dependencies_without_applying_them(client, monkeypatch):
    seed_sector(client, monkeypatch)
    from watchlist_app.services import research_triggers
    monkeypatch.setattr(research_triggers, "research_trigger", lambda *a, **k: {"reasons": [], "coverage_cursor": {}})
    def revision(source, **extra):
        return {"source_id": "text:" + source + "-new", "previous_source_id": "text:" + source, "version_id": source + "-new",
                "dependency": "previously_read_original", **extra}
    pending = monitor.change_items({"reasons": [{"kind": "new_research_sources", "sources": [
        revision("dangling"), revision("current"),
        revision("multiple", previous_source_id="text:other-old", previous_source_ids=["text:other-old", "text:current"]),
        {"source_id": "query-discovery", "version_id": "query-v1", "matched_query": {"query": "bound topic"}},
        {"source_id": "unknown-legacy", "version_id": "legacy"}]}]})
    context = {"research_dossiers": [{"instrument_id": "xlk", "prior_sources": [], "notebook": {
        "investment_view": {"source_ids": ["text:current"]},
        # Missing optional metadata does not remove the actual current reference.
        "sources": [{"source_id": "text:dangling"}]}}]}
    with get_session_factory()() as session:
        state = ResearchMonitorState(team_id="default", instrument_id="xlk", scanned_at=NOW,
            scan_cursor_json={}, pending_changes_json=pending, revision=1, attempted_revision=1)
        session.add(state); session.commit()
        state, trigger = monitor.scan(session, "xlk", context, team_id="default", now=NOW + timedelta(days=1))
        assert trigger is None and state.applied_at is None and state.revision == state.attempted_revision == 1
        assert {row["value"]["source_id"] for row in state.pending_changes_json.values()} == {"text:current-new", "text:multiple-new", "query-discovery", "unknown-legacy"}
        session.commit(); session.expire_all()
        retained = session.get(ResearchMonitorState, ("default", "xlk"))
        retirement = retained.scan_cursor_json["last_dependency_pruning"]
        assert retirement["reason"] == "no_current_research_reference"
        assert [row["source_id"] for row in retirement["changes"]] == ["text:dangling-new"]
        assert "last_dependency_pruning" not in monitor.pending_trigger(retained, "xlk", now=NOW)["coverage_cursor"]


@pytest.mark.parametrize("known_current_scope", [True, False])
def test_unattempted_dangling_pending_cannot_start_research_but_unknown_scope_is_not_guessed(client, monkeypatch, known_current_scope):
    seed_sector(client, monkeypatch)
    from watchlist_app.services import research_triggers
    monkeypatch.setattr(research_triggers, "research_trigger", lambda *a, **k: {"reasons": [], "coverage_cursor": {}})
    pending = monitor.change_items({"reasons": [{"kind": "new_research_sources", "sources": [
        {"source_id": "text:new", "previous_source_id": "text:old", "version_id": "new", "dependency": "previously_read_original"}]}]})
    context = {"research_dossiers": [{"instrument_id": "xlk", "notebook": {"sources": [{"source_id": "text:old"}]}, "prior_sources": []}]} if known_current_scope else {}
    with get_session_factory()() as session:
        state = ResearchMonitorState(team_id="default", instrument_id="xlk", scanned_at=NOW,
            scan_cursor_json={}, pending_changes_json=pending, revision=1, attempted_revision=0)
        session.add(state); session.commit()
        state, trigger = monitor.scan(session, "xlk", context, team_id="default", now=NOW + timedelta(days=1))
        assert (trigger is None) is known_current_scope
        assert bool(state.pending_changes_json) is not known_current_scope
        assert state.applied_at is None and state.attempted_revision == 0
