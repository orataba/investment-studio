"""A saved deterministic solve stays private, source-bound and losslessly pageable."""
from copy import deepcopy
from dataclasses import replace

import pytest

from watchlist_app import research_mcp
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from .test_account_research_access import accounts, as_user
from .test_research_portfolio_reads import wire


def allocation_run(principals, *, flags=None, evidence=None):
    context = {"research_run": True, "portfolio_id": "A", "catalogue": [],
        "page_context": {"as_of_date": "2026-10-02"},
        "research_actor": principals["alice"].to_dict(), "execution": {"attempt": 1},
        "tool_evidence": evidence or [], **(flags or {})}
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="allocation-pages", title="Private allocation", visibility="private",
            created_by_user_id="alice", portfolio_id="A"))
        session.flush()
        session.add(ResearchEntry(entry_id="allocation-run", topic_id="allocation-pages", kind="analysis",
            title="Saved targets", status="running", context_json=context))
        session.commit()
    principals["run-A"] = replace(principals["alice"], credential="run-A",
        resource_scope={"kind": "run", "id": "allocation-run", "attempt": "1"})


def retained_evidence(source_id="allocation:current", portfolio_id="A"):
    return {"source_id": source_id, "tool": "allocation", "retrieved_at": "2026-10-03T00:00:00Z",
        "result": {"portfolio_id": portfolio_id, "as_of_date": "2026-10-02", "inputs": {"capital_mode": "unit_notional"},
            "solution": {"weights": [{"instrument_id": "instrument-a", "weight": 0.75}]}}}


def test_allocation_calculates_once_and_exact_pages_select_only_requested_receipt(accounts, monkeypatch):
    from watchlist_app.api.routes import workbench
    from watchlist_app.services import research_run_context
    client, principals, _ = accounts
    allocation_run(principals)
    as_user(client, "run-A")
    original_text = "原始配置依据：保持资本约束。" * 9000
    weights = [{"instrument_id": f"instrument-{i}", "weight": i / 1000000} for i in range(1200)]
    result = {**retained_evidence()["result"], "solution": {"weights": weights, "explanation": original_text}}
    calls = []
    def solve(service, path):
        calls.append((service, path))
        assert (service, path) == ("portfolio", "/portfolios/A/research/allocation?as_of_date=2026-10-02")
        return deepcopy(result)
    monkeypatch.setattr(workbench, "external_json", solve)
    def request(suffix, payload):
        response = client.post(f"/api/research/runs/allocation-run/{suffix}", json=payload)
        assert response.status_code == 200, response.text
        return response.json()
    monkeypatch.setattr(research_mcp, "request", request)
    overview = wire("calculate_portfolio_allocation")
    source_id = overview["source_id"]
    assert wire("calculate_portfolio_allocation")["source_id"] == source_id
    selected = []
    load = research_run_context.load_run_fields
    def selected_fields(session, run_id, fields, **kwargs):
        selected.append(set(fields))
        return load(session, run_id, fields, **kwargs)
    monkeypatch.setattr(research_run_context, "load_run_fields", selected_fields)
    for path, expected, initial in [(["weights"], weights, []), (["explanation"], original_text, "")]:
        restored, offset = initial, 0
        while offset is not None:
            page = wire("calculate_portfolio_allocation", source_id=source_id, section="solution", path=path,
                offset=offset, limit=100)
            restored += page["data"]
            offset = page["next_offset"]
        assert restored == expected
    assert not any("tool_evidence" in fields for fields in selected)
    assert len(calls) == 1
    with get_session_factory()() as session:
        evidence = session.get(ResearchEntry, "allocation-run").context_json["tool_evidence"]
        assert len(evidence) == 1 and evidence[0]["result"] == result


def test_allocation_pages_recheck_current_authority_and_bound_portfolio(accounts):
    client, principals, grants = accounts
    allocation_run(principals, evidence=[retained_evidence(), retained_evidence("allocation:foreign", "B")])
    path = "/api/research/runs/allocation-run/read"
    payload = {"resource": "allocation", "source_id": "allocation:current", "section": "solution"}
    as_user(client, "run-A")
    assert client.post(path, json=payload).status_code == 200
    for source_id in ("allocation:another-run", "allocation:foreign"):
        assert client.post(path, json={**payload, "source_id": source_id}).status_code == 422
    assert client.post(path.replace("allocation-run", "other-run"), json=payload).status_code == 403
    assert as_user(client, "alice").post(path.replace("/read", "/tools"), json={"tool": "allocation"}).status_code == 403
    principals["forged-run"] = replace(principals["bob"], credential="forged-run", resource_scope=principals["run-A"].resource_scope)
    assert as_user(client, "forged-run").post(path, json=payload).status_code == 403
    principals["old-attempt"] = replace(principals["run-A"], credential="old-attempt",
        resource_scope={"kind": "run", "id": "allocation-run", "attempt": "2"})
    assert as_user(client, "old-attempt").post(path, json=payload).status_code == 409
    as_user(client, "run-A")
    grants["alice"].clear()
    assert client.post(path, json=payload).status_code == 404
    assert client.post(path.replace("/read", "/tools"), json={"tool": "allocation"}).status_code == 404
    principals.pop("run-A")
    assert client.post(path, json=payload).status_code == 401


@pytest.mark.parametrize("flags", [{"portfolio_id": None}, {"risk_run": True}, {"sector_run": True}])
def test_allocation_cannot_escape_into_unlinked_or_automatic_research(accounts, flags):
    client, principals, _ = accounts
    allocation_run(principals, flags=flags, evidence=[retained_evidence()])
    as_user(client, "run-A")
    assert client.post("/api/research/runs/allocation-run/tools", json={"tool": "allocation"}).status_code == 422
    assert client.post("/api/research/runs/allocation-run/read", json={"resource": "allocation",
        "source_id": "allocation:current", "section": "solution"}).status_code == 422


@pytest.mark.parametrize("section,offset,path", [("solution", 0, None), ("overview", 1, None), ("inputs", 0, ["capital_mode"])])
def test_allocation_continuation_requires_its_source(section, offset, path, monkeypatch):
    monkeypatch.setattr(research_mcp, "request", lambda *_args: pytest.fail("Must not calculate again"))
    with pytest.raises(ValueError, match="source_id"):
        research_mcp.calculate_portfolio_allocation(section=section, offset=offset, path=path)
