from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, RiskCase
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_runner


def setup_reference(client, monkeypatch, *, instrument_type="equity", signal="drawdown_limit"):
    monkeypatch.setattr(research_runner, "harness_available", lambda: True)
    monkeypatch.setattr(research_runner, "run_analysis", lambda *args, **kwargs: None)
    with get_session_factory()() as session:
        for iid in ("risk-reference", "other-risk-asset"):
            session.add(InstrumentDetail(instrument_id=iid, instrument_type=instrument_type,
                detail_view_type=instrument_type, instrument_name=iid, metadata_json={}))
        session.flush()
        session.add(RiskCase(case_id="selected-risk", instrument_id="risk-reference", signal=signal,
            title="当前风险事项", body="当前完整判断", status="investigating", trigger_active=True,
            observed_on=datetime(2026, 9, 10, tzinfo=UTC).date(), updated_at=datetime(2026, 9, 12, 8, tzinfo=UTC),
            evidence_json={"measurement": {"current": -7.25, "threshold": -7.0}},
            history_json=[{"action": "triggered", "at": "2026-09-12T08:00:00Z", "detail": "真实测量触发"}]))
        session.commit()
    topic = client.post("/api/research/topics", json={"title": "讨论风险", "instrument_ids": ["risk-reference"]}).json()
    case = client.get("/api/risk?instrument_id=risk-reference").json()["cases"][0]
    reference = {"instrument_id": "risk-reference", "risk_case_id": case["case_id"], "risk_case_updated_at": case["updated_at"]}
    request = {"question": "这个风险对投资判断有什么影响？", "page_context": {
        "surface": "instrument", "instrument_id": "risk-reference", "research_reference": reference}}
    return topic, case, request


@pytest.mark.parametrize("instrument_type", ["equity", "etf", "index", "public_fund", "private_fund", "crypto"])
def test_assistant_binds_the_exact_current_risk_snapshot_for_every_asset_type(client, monkeypatch, instrument_type):
    topic, case, request = setup_reference(client, monkeypatch, instrument_type=instrument_type)
    response = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json=request)
    assert response.status_code == 202, response.text
    run = response.json()
    snapshot = run["context_json"]["referenced_risk_case"]
    assert snapshot["reference_kind"] == "current_snapshot"
    assert snapshot["case"] == case
    assert snapshot["bound_at"] and "不是不可变历史研究版本" in snapshot["usage_note"]
    assert run["context_json"]["referenced_research_update"] is None
    with get_session_factory()() as session:
        current = session.get(RiskCase, case["case_id"])
        current.body = "之后的新风险判断"
        current.evidence_json = {"measurement": {"current": -8.5}}
        session.commit()
        stored = session.get(ResearchEntry, run["entry_id"]).context_json["referenced_risk_case"]
        assert stored == snapshot
    assert client.get(f"/api/research/runs/{run['entry_id']}/context").json()["referenced_risk_case"] == snapshot


@pytest.mark.parametrize("invalid", ["changed", "wrong-instrument", "missing-id", "missing-clock", "mixed-history"])
def test_invalid_or_stale_risk_reference_rejects_before_queueing(client, monkeypatch, invalid):
    topic, case, request = setup_reference(client, monkeypatch)
    ref = request["page_context"]["research_reference"]
    if invalid == "changed":
        with get_session_factory()() as session:
            session.get(RiskCase, case["case_id"]).updated_at = datetime(2026, 9, 12, 9, tzinfo=UTC)
            session.commit()
    elif invalid == "wrong-instrument":
        with get_session_factory()() as session:
            session.get(RiskCase, case["case_id"]).instrument_id = "other-risk-asset"
            session.commit()
    elif invalid == "missing-id":
        ref.pop("risk_case_id")
    elif invalid == "missing-clock":
        ref.pop("risk_case_updated_at")
    else:
        ref["event_case_id"] = case["case_id"]
        ref["event_version_id"] = "not-a-real-historical-version"
    response = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json=request)
    assert response.status_code == 422, response.text
    assert "刷新" in response.text if invalid != "mixed-history" else "历史研究版本" in response.text
    with get_session_factory()() as session:
        assert not session.scalar(select(ResearchEntry.entry_id).where(ResearchEntry.topic_id == topic["topic_id"]))


def test_legacy_sector_risk_exposes_its_existing_event_version_instead_of_inventing_a_current_risk_version(client, monkeypatch):
    topic, case, request = setup_reference(client, monkeypatch, instrument_type="etf", signal="sector:policy")
    version = case["evidence_json"]["event_version_id"]
    with get_session_factory()() as session:
        assert "event_version_id" not in session.get(RiskCase, case["case_id"]).evidence_json
    invalid = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json=request)
    assert invalid.status_code == 422 and "事件版本" in invalid.text
    correct = deepcopy(request)
    correct["page_context"]["research_reference"] = {"instrument_id": "risk-reference",
        "event_case_id": case["case_id"], "event_version_id": version}
    response = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json=correct)
    assert response.status_code == 202, response.text
    context = response.json()["context_json"]
    assert context["referenced_risk_case"] is None
    assert context["referenced_research_update"]["reference"]["event_version_id"] == version
    assert context["referenced_research_update"]["body"] == "当前完整判断"


def test_assistant_pm_discussion_binds_selected_revision_before_later_changes(client, monkeypatch):
    topic, _, request = setup_reference(client, monkeypatch)
    path = '/api/instruments/risk-reference/research/notes'
    original = {'note_date': '2026-09-10', 'title': '原始投资观点', 'body': '当时判断及反证条件',
                'research_context': {'background': '当时观察到的经营变化'}}
    response = client.post(path, json={'note': original})
    assert response.status_code == 200, response.text
    note = response.json()['notes'][0]
    updated = client.put(path + '/' + note['note_id'], json={'note': {**original, 'body': '后来第二版判断'}})
    assert updated.status_code == 200, updated.text
    request['page_context']['research_reference'] = {'instrument_id': 'risk-reference',
        'pm_note_id': note['note_id'], 'pm_note_revision': 1}
    response = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json=request)
    assert response.status_code == 202, response.text
    run = response.json()
    bound = run['context_json']['referenced_research_versions'][0]
    assert bound['kind'] == 'pm_view' and bound['version_id'] == f"pm:{note['note_id']}:1"
    assert bound['value']['body'] == original['body']
    assert client.put(path + '/' + note['note_id'], json={'note': {**original, 'body': '第三版判断'}}).status_code == 200
    stored = client.get(f"/api/research/runs/{run['entry_id']}/context").json()['referenced_research_versions']
    assert stored == [bound]


@pytest.mark.parametrize('reference', [
    {'pm_note_id': 'unknown'}, {'pm_note_revision': 1},
    {'pm_note_id': 'unknown', 'pm_note_revision': 1},
    {'investment_view_version_id': 'missing-version'},
])
def test_invalid_selected_judgment_never_queues_an_unbound_discussion(client, monkeypatch, reference):
    topic, _, request = setup_reference(client, monkeypatch)
    request['page_context']['research_reference'] = {'instrument_id': 'risk-reference', **reference}
    response = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json=request)
    assert response.status_code == 422, response.text
    with get_session_factory()() as session:
        assert not session.scalar(select(ResearchEntry.entry_id).where(ResearchEntry.topic_id == topic['topic_id']))
