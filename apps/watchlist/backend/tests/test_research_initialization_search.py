"""Executed identity searches, distinct from original reading or full coverage."""
from copy import deepcopy
from datetime import datetime, timedelta, UTC

import pytest
from fastapi import HTTPException

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import market_evidence as evidence, sector_research as service
from watchlist_app.services import sector_fact_review


def prepared(client, *, identifier="CODE123"):
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="internal-not-code", instrument_type="public_fund",
            detail_view_type="public_fund", instrument_name="基金甲", primary_identifier_value=identifier))
        session.commit()
        run, _ = service.begin_run(session, ["internal-not-code"])
        run_id = run.entry_id
    service.prepare_run(run_id)
    return run_id


def context(run_id):
    with get_session_factory()() as session:
        return deepcopy(session.get(ResearchEntry, run_id).context_json)


def capture(label, title, *, age=1, retrieved=None):
    return evidence.text_store().capture_public_source({"source_id": label,
        "url": f"https://issuer.example/{label}", "title": title, "text": "Actual original content.",
        "published_at": (datetime.now(UTC) - timedelta(days=age)).isoformat(),
        "retrieved_at": retrieved or datetime.now(UTC).isoformat()})


def test_initialization_executes_separate_registered_identities_with_real_pit_store(client):
    name_source = capture("name", "基金甲的新公告")
    code_source = capture("code", "CODE123 的公告")
    capture("too-old", "基金甲与 CODE123 旧公告", age=9)
    run_id = prepared(client)
    window = deepcopy(context(run_id)["initialization"])
    service.prepare_initialization_searches(run_id)
    saved = context(run_id)
    assert [q["query"] for q in saved["market_queries"]] == ["基金甲", "CODE123"]
    assert all(q["published_after"] == window["published_after"] and q["cutoff"] == window["as_of"]
               and q["total"] == 1 for q in saved["market_queries"])
    ids = {row["version_id"] for page in saved["initialization_candidates"] for row in page["rows"]}
    assert ids == {name_source["version_id"], code_source["version_id"]}
    assert not saved["market_text_sources"]
    assert all("content_text" not in row and "text" not in row
               for page in saved["initialization_candidates"] for row in page["rows"])
    service.prepare_initialization_searches(run_id)
    assert context(run_id) == saved  # A generation retry does not repeat successful pages.
    read = client.get(f"/api/research/runs/{run_id}/market-source", params={
        "document_id": name_source["document_id"], "version_id": name_source["version_id"]})
    assert read.status_code == 200
    assert len(context(run_id)["market_text_sources"]) == 1


def test_first_page_stays_partial_until_same_window_api_continuation(client, monkeypatch):
    run_id = prepared(client)
    initial = context(run_id)["initialization"]
    calls = []
    def search(query, **kw):
        calls.append((query, kw))
        assert kw["as_of"] == datetime.fromisoformat(initial["as_of"])
        assert kw["published_after"] == datetime.fromisoformat(initial["published_after"])
        total = 102 if query == "基金甲" else 0
        return {"rows": [{"document_id": str(i), "version_id": f"v-{i}", "source_id": f"text:{i}",
                          "title": "Candidate", "content_text": "Not read."}
                         for i in range(kw["offset"], min(total, kw["offset"] + kw["limit"]))],
                "total": total, "coverage": {}, "as_of": kw["as_of"].isoformat()}
    monkeypatch.setattr(evidence.text_store(), "search", search)
    service.prepare_initialization_searches(run_id)
    saved = context(run_id)
    row = evidence.initialization_search_summary(saved)["queries"][0]
    assert row["directory_status"] == "partial" and row["next_offset"] == 100
    service.prepare_initialization_searches(run_id)
    assert len(calls) == 2  # Do not automatically scan an arbitrarily broad directory.
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        run.context_json = {**run.context_json,
            "cutoff": (datetime.fromisoformat(initial["as_of"]) + timedelta(days=1)).isoformat()}
        session.commit()
    payload = {k: v for k, v in row["continuation"].items() if k != "tool"}
    response = client.post(f"/api/research/runs/{run_id}/market-search", json=payload)
    assert response.status_code == 200, response.text
    assert len(response.json()["rows"]) == 2
    saved = context(run_id)
    assert saved["initialization"] == initial
    assert evidence.initialization_search_summary(saved)["queries"][0]["directory_status"] == "complete"
    assert not saved["market_text_sources"]
    packet = sector_fact_review._evidence_packet(saved, [{"instrument_id": "internal-not-code", "events": []}], run_id)
    assert packet["initialization_candidates"] == saved["initialization_candidates"]
    assert "initialization_candidates" not in packet["acquisition"]
    assert packet["acquisition"]["initialization_searches"]["queries"][0]["returned_through"] == 102
    assert not any(source.get("source_id") == "text:0" for source in packet["sources"])
    page = client.post(f"/api/research/runs/{run_id}/read", json={
        "resource": "context", "section": "initialization_candidates", "limit": 1}).json()
    assert page["next_offset"] == 1 and page["total"] == 3


def test_failed_query_is_not_zero_and_retry_preserves_window_and_completed_identity(client, monkeypatch):
    run_id = prepared(client)
    original = context(run_id)
    calls, fail = [], True
    def search(query, **kw):
        calls.append((query, kw["as_of"]))
        if query == "CODE123" and fail:
            raise RuntimeError("private database diagnostic")
        return {"rows": [], "total": 0, "coverage": {}, "as_of": kw["as_of"].isoformat()}
    monkeypatch.setattr(evidence.text_store(), "search", search)
    with pytest.raises(RuntimeError, match="private database"):
        service.prepare_initialization_searches(run_id)
    saved = context(run_id)
    last = saved["market_queries"][-1]
    assert last["status"] == "failed" and "total" not in last and "private" not in str(last)
    assert evidence.initialization_search_summary(saved)["queries"][1]["directory_status"] == "failed"
    assert any("查询失败" in note for note in evidence.initialization_coverage_notes(saved, "internal-not-code"))
    fail = False
    service.prepare_initialization_searches(run_id)
    assert [q for q, _ in calls] == ["基金甲", "CODE123", "CODE123"]
    assert context(run_id)["initialization"] == original["initialization"]
    assert all(clock.isoformat() == original["initialization"]["as_of"] for _, clock in calls)


def test_missing_identifier_is_not_guessed_and_review_checkpoint_never_collects(client, monkeypatch):
    run_id = prepared(client, identifier=None)
    saved = context(run_id)
    assert [q["query"] for q in saved["initialization"]["identity_queries"]] == ["基金甲"]
    assert saved["initialization"]["identity_gaps"]
    monkeypatch.setattr(evidence.text_store(), "search", lambda *a, **kw: pytest.fail("Frozen review cannot search"))
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, run_id)
        run.context_json = {**run.context_json, "submitted_draft": {"reviews": []}}
        session.commit()
    frozen = context(run_id)
    service.prepare_initialization_searches(run_id)
    assert context(run_id) == frozen


def test_wrong_run_credential_is_rejected_before_initialization_search(client, monkeypatch):
    from studio_identity import Principal, principal_context
    run_id = prepared(client)
    monkeypatch.setattr(evidence.text_store(), "search", lambda *a, **kw: pytest.fail("No cross-run read"))
    with principal_context(Principal("other", "Other", "default", resource_scope={"kind": "run", "id": "different"})):
        with pytest.raises(HTTPException) as caught:
            service.prepare_initialization_searches(run_id)
    assert caught.value.status_code == 403


def test_receipts_do_not_upgrade_narrower_dates_terms_or_repeated_pages():
    initial = {"published_after": "2026-09-19T12:00:00+00:00", "as_of": "2026-09-26T12:00:00+00:00",
        "identity_queries": [{"instrument_id": "gold", "query": "黄金", "identity_kind": "name"}]}
    receipt = {"instrument_id": "gold", "query": "黄金", "published_after": initial["published_after"],
        "cutoff": initial["as_of"], "status": "succeeded", "offset": 0, "returned_count": 100, "total": 120}
    ctx = {"instrument_ids": ["gold"], "initialization": initial, "market_queries": [receipt, deepcopy(receipt),
        {**receipt, "published_after": "2026-09-25T12:00:00+00:00", "offset": 100, "returned_count": 20},
        {**receipt, "query": "黄金 人民币", "offset": 100, "returned_count": 20}]}
    row = evidence.initialization_search_summary(ctx)["queries"][0]
    assert row["directory_status"] == "partial" and row["returned_through"] == 100
    ctx["market_queries"].append({**receipt, "published_after": "2026-09-19T20:00:00+08:00",
        "cutoff": "2026-09-26T20:00:00+08:00", "offset": 100, "returned_count": 20})
    assert evidence.initialization_search_summary(ctx)["queries"][0]["directory_status"] == "complete"
