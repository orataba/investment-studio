from copy import deepcopy
from types import SimpleNamespace

import pytest

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import sector_research as service


@pytest.fixture
def registered_instrument(client):
    with get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id="xlk", instrument_type="etf", detail_view_type="etf",
                                     instrument_name="XLK", metadata_json={}))
        session.commit()


def test_explicit_research_request_is_saved_in_the_first_queue_commit(registered_instrument, monkeypatch):
    question = "发布质控复核：核查原观点的证据与因果推断；这不是新市场消息或PM观点。"
    with get_session_factory()() as session:
        commit = session.commit
        committed_questions = []

        def capture_commit():
            committed_questions.extend(record.context_json.get("question") for record in session.new
                                       if isinstance(record, ResearchEntry))
            commit()

        monkeypatch.setattr(session, "commit", capture_commit)
        run, created = service.begin_run(session, ["xlk"], question=question)
        assert created and committed_questions == [question]
        assert run.context_json["sector_run"] is True
        assert run.context_json["scheduled"] is False
        assert run.context_json["incremental_trigger"] == {}
        assert run.context_json["reviews"] == {} and run.body == ""
        run_id = run.entry_id
    with get_session_factory()() as session:
        assert session.get(ResearchEntry, run_id).context_json["question"] == question


@pytest.mark.parametrize("status", ["queued", "running"])
def test_explicit_request_cannot_retarget_an_active_run(registered_instrument, status):
    with get_session_factory()() as session:
        original, _ = service.begin_run(session, ["xlk"], question="原检查任务")
        original.status = status
        session.commit()
        before = deepcopy(original.context_json)
        same, created = service.begin_run(session, ["xlk"], question="后来提出的质控修订")
        assert not created and same.entry_id == original.entry_id
        assert same.context_json == before and same.status == status


def test_new_quality_request_preserves_the_prior_published_record(registered_instrument):
    with get_session_factory()() as session:
        original, _ = service.begin_run(session, ["xlk"])
        original.status = "limited"
        original.body = "原研究记录，保留原时点。"
        original.context_json = {**original.context_json, "reviews": {
            "xlk": {"change_kind": "knowledge", "research": {"investment_view": {
                "direction": "原待复核判断", "version_id": "original-view"}}}}}
        session.commit()
        session.refresh(original)
        before = deepcopy(original.context_json)
        old_id, old_body, old_created_at = original.entry_id, original.body, original.created_at
        new, created = service.begin_run(session, ["xlk"], question="发布质量复核：检查原判断。")
        assert created and new.entry_id != old_id and new.topic_id == original.topic_id
        session.expire(original)
        assert original.context_json == before
        assert original.body == old_body and original.created_at == old_created_at
        assert new.context_json["reviews"] == {}


def test_preparation_and_context_reader_preserve_the_explicit_request(registered_instrument, monkeypatch):
    from watchlist_app import research_mcp
    from watchlist_app.services import market_evidence, research_workbench

    question = "质控复核原版本：以已保留依据核查，不把本请求作为证据。"
    with get_session_factory()() as session:
        run, _ = service.begin_run(session, ["xlk"], question=question)
        run_id = run.entry_id
    monkeypatch.setattr(research_workbench, "catalogue", lambda session: [])
    monkeypatch.setattr(market_evidence, "text_store", lambda: SimpleNamespace(get_coverage=lambda: {}))
    monkeypatch.setattr(service, "bind_research_instruments", lambda session, run, ids: None)
    service.prepare_run(run_id)
    with get_session_factory()() as session:
        context = session.get(ResearchEntry, run_id).context_json
    monkeypatch.setattr(research_mcp, "request", lambda suffix: deepcopy(context))
    assert research_mcp.read_research_context()["question"] == question
    assert context["research_actor"] and context["sector_run"] is True


def test_fact_review_receives_the_request_without_registering_it_as_evidence():
    from watchlist_app.services import sector_fact_review

    question = "发布质控：核查 research:original:investment-view 的计价推断；保留原版本。"
    context = {"cutoff": "2026-09-12T00:00:00+00:00", "question": question,
               "instrument_inputs": [{"instrument_id": "xlk", "name": "XLK"}]}
    candidate = {"instrument_id": "xlk", "events": [], "research": None}
    before = deepcopy(context)
    packet = sector_fact_review._evidence_packet(context, [candidate], "quality-run")
    assert packet["question"] == question
    assert "not factual evidence" in packet["question_note"]
    assert {source["source_id"] for source in packet["sources"]} == {"instrument:quality-run:xlk"}
    assert packet["draft_reviews"] == [candidate]
    assert context == before
