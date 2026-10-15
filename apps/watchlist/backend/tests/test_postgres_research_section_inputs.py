"""Parallel-sized task pages must not transfer unrelated frozen input trees."""
from copy import deepcopy
from dataclasses import replace
import json

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from studio_identity import current_principal, principal_context

from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_engine, get_session_factory
from watchlist_app.services.research_read_projection import read_page
from watchlist_app.services.research_section_inputs import load_section_inputs
from watchlist_app.services.research_sections import section_context
from watchlist_app.services.research_tasks import assigned_context
from .test_postgres_instrument_registry_constraints import postgres_watchlist_env

pytestmark = pytest.mark.postgresql_integration


def retained_context(actor, iid, *, selected_text="Original exact statement"):
    large = "UNRELATED_LARGE_ORIGINAL" * 100000 + "\x00"
    cutoff = "2026-10-15T00:00:00+00:00"
    return {"instrument_ids": [iid], "sector_run": True, "cutoff": cutoff, "input_snapshot_cutoff": cutoff,
        "execution": {"attempt": 1}, "research_actor": actor.to_dict(),
        "incremental_trigger": {iid: {"revisions": large}},
        "research_working_sets": {iid: {"affected_event_ids": ["event"], "affected_theme_ids": ["second", "first"], "reasons": large}},
        "research_dossiers": [{"instrument_id": "other", "mandate": {"guidance": large}},
            {"instrument_id": iid, "instrument_type": "etf", "prior_sources": [{"text": large}],
             "mandate": {"guidance": "User guidance", "registration": {"instrument_type": "etf", "name": "ETF"}},
             "themes": [{"theme_id": "closed", "status": "closed", "body": large},
                        {"theme_id": "second", "status": "active", "z_first": selected_text, "a_second": None, "sources": [large]},
                        {"theme_id": "first", "status": "active", "question": "Another question", "versions": [large]}],
             "notebook": {"versions": [large], "investment_view": {"rationale": selected_text},
                          "questions": [{"theme_id": "second", "answer": selected_text}],
                          "modules": [{"key": "market-quantitative", "title": "Prices", "summary": selected_text}]},
             "current_stance": {"body": selected_text}, "pm_views": [{"body": selected_text}]}],
        "prior_events": [{"instrument_id": iid, "follow_up": "watch", "case_id": "event", "title": "Event"}],
        "research_tasks": {role: {"task_id": role, "role": role, "instrument_id": iid, "status": "running",
             "input_snapshot_cutoff": cutoff, "instruction": "Read selected inputs", "depth": 1, "theme_ids": [],
             "result": {"events": [{"importance_score": 4, "title": "Event candidate"}]}}
             for role in ("recent_updates", "themes", "quantitative")},
        "unrelated_original": large, "data_gaps": ["Disclosed gap"]}


def seed(context):
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="section-inputs", title="Inputs", visibility="team"))
        session.flush()
        session.add(ResearchEntry(entry_id="section-inputs", topic_id="section-inputs", kind="analysis", title="Inputs",
            status="running", context_json=context))
        session.commit()


def test_role_and_selected_pages_skip_large_originals_before_json_decode(postgres_watchlist_env):
    actor, iid = current_principal(), postgres_watchlist_env["instrument_id"]
    context = retained_context(actor, iid)
    seed(context)
    transfers = []

    def deserialize(value):
        if isinstance(value, bytes):
            value = value.decode()
        assert "UNRELATED_LARGE_ORIGINAL" not in value
        transfers.append(len(value))
        return json.loads(value)

    engine = create_engine(get_engine().url, json_deserializer=deserialize,
        connect_args={"options": "-c search_path=watchlist,instrument_data,public -c default_transaction_read_only=on"})
    principal = replace(actor, resource_scope={"kind": "run", "id": "section-inputs", "attempt": "1"})
    try:
        with principal_context(principal), Session(engine) as session:
            for role in ("overview", "themes", "quantitative"):
                expected = section_context(deepcopy(context), iid, role)
                loaded = load_section_inputs(session, "section-inputs", instrument_id=iid, section=role)
                actual = section_context(loaded, iid, role)
                assert json.dumps(actual, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)
                for key in expected:
                    selected = load_section_inputs(session, "section-inputs", instrument_id=iid, section=role, path=[key])
                    projected = section_context(selected, iid, role)
                    assert read_page(projected, {}, path=[key], limit=1) == read_page(expected, {}, path=[key], limit=1)
            for task_id in context["research_tasks"]:
                expected = assigned_context(deepcopy(context), task_id, full_results=True)
                if task_id != "recent_updates":
                    loaded = load_section_inputs(session, "section-inputs", task_id=task_id)
                    assert json.dumps(assigned_context(loaded, task_id, full_results=True)) == json.dumps(expected)
                for path in (["assignment"], ["task_results"], ["instrument_id"], ["cutoff"]):
                    selected = load_section_inputs(session, "section-inputs", task_id=task_id, path=path)
                    actual = assigned_context(selected, task_id, full_results=True)
                    assert read_page(actual, {}, path=path, offset=1 if path == ["assignment"] else 0, limit=2) == read_page(
                        expected, {}, path=path, offset=1 if path == ["assignment"] else 0, limit=2)
            # Active-theme order and object-member offsets are part of exact paging.
            selected = load_section_inputs(session, "section-inputs", task_id="themes", path=["active_themes", 0])
            actual = assigned_context(selected, "themes", full_results=True)
            expected = assigned_context(deepcopy(context), "themes", full_results=True)
            assert [row["theme_id"] for row in actual["active_themes"]] == ["second", "first"]
            assert read_page(actual, {}, path=["active_themes", 0], offset=2, limit=1) == read_page(
                expected, {}, path=["active_themes", 0], offset=2, limit=1)
            assert max(transfers) < 10000
            with principal_context(replace(principal, resource_scope={**principal.resource_scope, "attempt": "2"})):
                with pytest.raises(HTTPException) as error:
                    load_section_inputs(session, "section-inputs", task_id="themes", path=["assignment"])
                assert error.value.status_code == 409
            with pytest.raises(ValueError, match="任务不属于"):
                load_section_inputs(session, "section-inputs", task_id="not-bound", path=["assignment"])
    finally:
        engine.dispose()
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, "section-inputs").context_json == context
        with principal_context(principal):
            # This role legitimately consumes the trigger; only its irrelevant
            # dossier originals/working-set reasons may be removed.
            loaded = load_section_inputs(session, "section-inputs", instrument_id=iid, section="recent_updates")
            assert section_context(loaded, iid, "recent_updates") == section_context(deepcopy(context), iid, "recent_updates")


def test_selected_nul_literal_escape_and_replacement_remain_exact(postgres_watchlist_env):
    actor, iid = current_principal(), postgres_watchlist_env["instrument_id"]
    exact = "NUL\x00 preserved; literal \\u0000 and replacement \ufffd " * 300
    context = retained_context(actor, iid, selected_text=exact)
    seed(context)
    principal = replace(actor, resource_scope={"kind": "run", "id": "section-inputs", "attempt": "1"})
    with principal_context(principal), get_session_factory()() as session:
        selected = load_section_inputs(session, "section-inputs", task_id="themes", path=["active_themes", 0, "z_first"])
        result = assigned_context(selected, "themes", full_results=True)
        assert result["active_themes"][0]["z_first"] == exact
        pieces, offset = [], 0
        while True:
            page = read_page(result, {}, path=["active_themes", 0, "z_first"], offset=offset)
            pieces.append(page["data"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        assert "".join(pieces) == exact
        assert session.get(ResearchEntry, "section-inputs").context_json == context


def test_missing_optional_inputs_keep_existing_empty_section_semantics(postgres_watchlist_env):
    actor, iid = current_principal(), postgres_watchlist_env["instrument_id"]
    context = {"instrument_ids": [iid], "sector_run": True, "research_actor": actor.to_dict(),
        "execution": {"attempt": 1}, "input_snapshot_cutoff": "frozen", "cutoff": "frozen"}
    seed(context)
    principal = replace(actor, resource_scope={"kind": "run", "id": "section-inputs", "attempt": "1"})
    with principal_context(principal), get_session_factory()() as session:
        for role in ("overview", "recent_updates", "themes", "quantitative"):
            loaded = load_section_inputs(session, "section-inputs", instrument_id=iid, section=role)
            assert section_context(loaded, iid, role) == section_context(context, iid, role)
