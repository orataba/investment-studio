from copy import deepcopy
from datetime import UTC, datetime

import pytest

from watchlist_app.services import shared_instrument_registry as registry


def record():
    event = {"fund_nav_event_id": "event-1", "fund_nav_action_id": "action-1",
        "revision_number": 1, "revision_kind": "original", "event_type": "cash_distribution",
        "record_date": "2026-09-17", "effective_date": "2026-09-18", "payable_date": "2026-09-18",
        "cash_per_unit": "0.0600", "evidence_kind": "manual_verified",
        "created_at": "2026-09-24T10:00:00Z", "source": "private investor confirmation",
        "external_event_id": "TA-private", "provenance": {"account": "private-account", "fee": "19466.46"},
        "recorded_by": "private-user", "revision_reason": "private audit text"}
    evidence = {"fund_nav_reinvestment_evidence_id": "evidence-1", "fund_nav_event_id": "event-1",
        "revision_number": 1, "revision_kind": "original", "reinvestment_nav": "1.0913",
        "evidence_kind": "manual_verified", "created_at": "2026-09-24T11:00:00Z",
        "source": "private investor confirmation", "provenance": {"image_path": "/private/file"},
        "external_evidence_id": "TA-private", "recorded_by": "private-user", "revision_reason": "private text"}
    return {"instrument_id": "fund", "instrument_type": "private_fund",
        "fund_nav_event_revisions": [event], "fund_nav_reinvestment_evidence_revisions": [evidence]}


def test_fund_action_evidence_excludes_investor_details_and_binds_retention_clock(monkeypatch):
    data = record()
    original = deepcopy(data)
    monkeypatch.setattr(registry.shared_store, "get_instrument_event_details", lambda factory, ids: {"fund": data})
    before = registry.get_shared_fund_actions("fund", as_of=datetime(2026, 9, 23, tzinfo=UTC))
    assert before["events"] == before["reinvestment_evidence"] == []
    between = registry.get_shared_fund_actions("fund", as_of=datetime(2026, 9, 24, 10, 30, tzinfo=UTC))
    assert len(between["events"]) == 1 and between["reinvestment_evidence"] == []
    current = registry.get_shared_fund_actions("fund", as_of=datetime(2026, 9, 25, tzinfo=UTC))
    assert current["events"][0]["cash_per_unit"] == "0.0600"
    assert current["reinvestment_evidence"][0]["reinvestment_nav"] == "1.0913"
    for row in [*current["events"], *current["reinvestment_evidence"]]:
        assert not {"source", "provenance", "external_event_id", "external_evidence_id",
            "recorded_by", "revision_reason"}.intersection(row)
    assert "private" not in str(current)
    assert data == original


def test_correction_and_cancellation_are_effective_only_after_recording(monkeypatch):
    data = record()
    first = data["fund_nav_event_revisions"][0]
    corrected = {**first, "fund_nav_event_id": "event-2", "revision_number": 2,
        "revision_kind": "correction", "cash_per_unit": "0.0500", "created_at": "2026-09-25T10:00:00Z"}
    cancelled = {**corrected, "fund_nav_event_id": "event-3", "revision_number": 3,
        "revision_kind": "cancellation", "created_at": "2026-09-26T10:00:00Z"}
    data["fund_nav_event_revisions"] = [cancelled, first, corrected]
    monkeypatch.setattr(registry.shared_store, "get_instrument_event_details", lambda factory, ids: {"fund": data})
    old = registry.get_shared_fund_actions("fund", as_of=datetime(2026, 9, 25, tzinfo=UTC))
    assert old["events"][0]["fund_nav_event_id"] == "event-1"
    corrected_view = registry.get_shared_fund_actions("fund", as_of=datetime(2026, 9, 26, tzinfo=UTC))
    assert corrected_view["events"][0]["cash_per_unit"] == "0.0500"
    assert corrected_view["reinvestment_evidence"] == []  # The old event's price cannot follow a correction.
    current = registry.get_shared_fund_actions("fund", as_of=datetime(2026, 9, 27, tzinfo=UTC))
    assert current["events"] == current["reinvestment_evidence"] == []


def test_reinvestment_evidence_uses_latest_retained_revision(monkeypatch):
    data = record()
    first = data["fund_nav_reinvestment_evidence_revisions"][0]
    data["fund_nav_reinvestment_evidence_revisions"] += [
        {**first, "fund_nav_reinvestment_evidence_id": "evidence-2", "revision_number": 2,
         "revision_kind": "correction", "reinvestment_nav": "1.1000", "created_at": "2026-09-25T10:00:00Z"},
        {**first, "fund_nav_reinvestment_evidence_id": "evidence-3", "revision_number": 3,
         "revision_kind": "cancellation", "created_at": "2026-09-26T10:00:00Z"}]
    monkeypatch.setattr(registry.shared_store, "get_instrument_event_details", lambda factory, ids: {"fund": data})
    corrected = registry.get_shared_fund_actions("fund", as_of=datetime(2026, 9, 26, tzinfo=UTC))
    assert corrected["reinvestment_evidence"][0]["reinvestment_nav"] == "1.1000"
    cancelled = registry.get_shared_fund_actions("fund", as_of=datetime(2026, 9, 27, tzinfo=UTC))
    assert len(cancelled["events"]) == 1 and cancelled["reinvestment_evidence"] == []


def test_fund_action_cutoff_requires_explicit_timezone(monkeypatch):
    with pytest.raises(ValueError, match="timezone"):
        registry.get_shared_fund_actions("fund", as_of=datetime(2026, 9, 25))


def test_assistant_instrument_source_includes_shared_actions(client, monkeypatch):
    from watchlist_app.services import research_runner
    monkeypatch.setattr(research_runner, "harness_available", lambda: True)
    monkeypatch.setattr(research_runner, "run_analysis", lambda *args: None)
    data = record()
    data["instrument_id"] = "sxv264"
    for row in [*data["fund_nav_event_revisions"], *data["fund_nav_reinvestment_evidence_revisions"]]:
        row["created_at"] = "2020-01-01T00:00:00Z"
    monkeypatch.setattr(registry.shared_store, "get_instrument_event_details", lambda factory, ids: {"sxv264": data})
    watchlist = client.post("/api/watchlists", json={"name": "分红研究"}).json()
    registered = client.post(f"/api/watchlists/{watchlist['watchlist_id']}/items", json={"instrument_ids": ["sxv264"]})
    assert registered.status_code == 200, registered.text
    topic = client.post("/api/research/topics", json={"title": "分红核实", "instrument_ids": ["sxv264"]}).json()
    started = client.post(f"/api/research/topics/{topic['topic_id']}/analysis", json={"question": "核实分红"})
    assert started.status_code == 202, started.text
    run = started.json()
    response = client.post(f"/api/research/runs/{run['entry_id']}/tools",
        json={"tool": "instruments", "instrument_ids": ["sxv264"]})
    assert response.status_code == 200, response.text
    facts = response.json()["result"]["assets"][0]["fund_actions"]
    assert facts["events"][0]["fund_nav_event_id"] == "event-1"
    assert facts["reinvestment_evidence"][0]["reinvestment_nav"] == "1.0913"
    assert "TA-private" not in response.text and "private-account" not in response.text
