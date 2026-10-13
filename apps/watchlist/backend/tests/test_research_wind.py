"""Actual MCP transport with a fake provider; no Wind quota or credentials used."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import httpx2
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from studio_identity import current_principal, principal_context

from watchlist_app.api.routes import sector_research as routes
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_wind as wind
from watchlist_app.services.research_read_projection import source_index, current_research_view
from watchlist_app.services.research_run_evidence import (computed_reference, retained_computed_sources,
    selected_quant_sources, quant_source_catalogue)

CUTOFF = "2026-09-20T00:00:00+00:00"
KEY = "wind-test-credential-never-retained"
SCHEMA = {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"], "additionalProperties": False}
PAYLOAD = {"content": [{"type": "text", "text": json.dumps({"data": {"unit": "%", "value": 0.116132,
    "report_date": "2026-06-30", "published_at": None, "fragment": "供应商片段"}}, ensure_ascii=False)}]}


@pytest.fixture
def provider(monkeypatch):
    calls = []
    response = deepcopy(PAYLOAD)
    def serve(request):
        assert str(request.url) == "https://mcp.wind.com.cn/vserver_fund_data/mcp/"
        assert request.headers["authorization"] == "Bearer " + KEY
        message = json.loads(request.content)
        calls.append(message)
        if "_http_status" in response:
            return httpx2.Response(response["_http_status"], text=KEY + " upstream private diagnostic")
        method = message["method"]
        if method == "notifications/initialized":
            return httpx2.Response(202)
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake-wind", "version": "1"}}
        elif method == "tools/list":
            if message.get("params", {}).get("cursor"):
                result = {"tools": [{"name": "get_fund_quote", "inputSchema": SCHEMA}]}
            else:
                result = {"tools": [{"name": "trade_order", "inputSchema": SCHEMA}], "nextCursor": "page2"}
        else:
            assert method == "tools/call"
            result = response
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})
    original = httpx2.AsyncClient
    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original(**kwargs, transport=httpx2.MockTransport(serve))
    monkeypatch.setattr(wind.httpx2, "AsyncClient", client)
    monkeypatch.setattr(wind, "get_settings", lambda: SimpleNamespace(wind_api_key=SecretStr(KEY)))
    return calls, response


def test_actual_mcp_discovery_and_query_preserve_vendor_json_and_units(provider):
    calls, _ = provider
    tools = wind.list_tools("fund_data")
    assert [row["name"] for row in tools["tools"]] == ["get_fund_quote"]
    assert tools["tools"][0]["inputSchema"] == SCHEMA
    assert wind.query("fund_data", "get_fund_quote", {"code": "TEST.OF"}) == PAYLOAD
    call = next(row for row in calls if row["method"] == "tools/call")
    assert call["params"]["arguments"] == {"code": "TEST.OF"}
    assert KEY not in json.dumps(tools)


def test_actual_schema_validation_and_allowlist_reject_before_paid_tool_call(provider):
    calls, _ = provider
    with pytest.raises(wind.WindError) as wrong:
        wind.query("fund_data", "get_fund_quote", {"guessed": "TEST.OF"})
    assert wrong.value.status_code == 422
    assert not any(row["method"] == "tools/call" for row in calls)
    calls.clear()
    with pytest.raises(wind.WindError, match="只读"):
        wind.query("fund_data", "trade_order", {})
    assert calls == []
    with pytest.raises(wind.WindError, match="analytics"):
        wind.list_tools("analytics_data")


def test_vendor_schema_cannot_add_an_external_network_destination(provider, monkeypatch):
    import urllib.request
    calls, _ = provider
    monkeypatch.setitem(SCHEMA, "properties", {"code": {"$ref": "http://127.0.0.1/private-schema"}})
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args: pytest.fail("external schema retrieval"))
    with pytest.raises(wind.WindError):
        wind.query("fund_data", "get_fund_quote", {"code": "TEST.OF"})
    assert not any(row["method"] == "tools/call" for row in calls)


@pytest.mark.parametrize("body", [
    {"isError": True, "content": [{"type": "text", "text": "authorization denied"}]},
    {"content": [{"type": "text", "text": '{"data":{"code":-1,"message":"quota"}}'}]},
    {"content": [{"type": "text", "text": KEY}]},
])
def test_errors_and_echoed_credentials_are_never_forwarded_or_retained(provider, body):
    _, response = provider
    response.clear()
    response.update(body)
    with pytest.raises(wind.WindError) as failure:
        wind.query("fund_data", "get_fund_quote", {"code": "TEST.OF"})
    assert KEY not in str(failure.value)
    assert "authorization denied" not in str(failure.value)


@pytest.mark.parametrize("status, expected, client_status", [(401, "授权未通过", 502),
    (403, "授权未通过", 502), (429, "限流或额度", 429), (503, "供应商服务暂不可用", 502)])
def test_actual_transport_errors_have_safe_distinct_classification(provider, status, expected, client_status):
    calls, response = provider
    response["_http_status"] = status
    with pytest.raises(wind.WindError) as failure:
        wind.query("fund_data", "get_fund_quote", {"code": "TEST.OF"})
    assert expected in str(failure.value)
    assert str(status) in str(failure.value)
    assert failure.value.status_code == client_status
    assert KEY not in str(failure.value) and "upstream private" not in str(failure.value)
    assert len(calls) == 1  # No automatic retry, even when discovery failed.


def test_business_error_keeps_code_without_vendor_diagnostic(provider):
    _, response = provider
    response.update(content=[{"type": "text", "text": json.dumps({"mcp_tool_error_code": 12345,
        "mcp_tool_error_msg": "private upstream explanation"})}])
    with pytest.raises(wind.WindError, match="code=12345") as failure:
        wind.query("fund_data", "get_fund_quote", {"code": "TEST.OF"})
    assert "private upstream" not in str(failure.value)


def test_supplier_original_is_not_silenced_as_an_internal_numeric_refresh():
    from watchlist_app.services.risk_case_state import should_refer_event, _ordinary_numeric_update
    item = {"direction": "risk", "impact_level": "material", "impact_analysis": "A dated issuer announcement changed the risk."}
    numeric = {"source_type": "computed_metric", "data": {"analysis_kind": "price_risk"}}
    supplier = wind.source("xlk", "financial_docs", "get_company_announcements", {"query": "issuer"}, PAYLOAD)
    for source in (supplier, source_index(supplier)):
        assert should_refer_event(None, item, [source], material_change=True)
        assert should_refer_event({"evidence_json": item}, item, [source], material_change=True)
        assert not _ordinary_numeric_update(item, item, [source])
    assert not should_refer_event(None, item, [numeric], material_change=True)
    assert _ordinary_numeric_update(item, item, [numeric])


@pytest.fixture
def record(client):
    actor = replace(current_principal(), resource_scope={"kind": "run", "id": "wind-run", "attempt": "1"})
    with principal_context(actor):
        with get_session_factory()() as session:
            session.add(ResearchTopic(topic_id="wind-topic", title="Wind test", team_id=actor.team_id,
                visibility="private", created_by_user_id=actor.user_id))
            session.flush()
            session.add(ResearchEntry(entry_id="wind-run", topic_id="wind-topic", team_id=actor.team_id,
                kind="analysis", title="Test", status="running", context_json={"research_run": True,
                    "instrument_ids": ["xlk"], "cutoff": CUTOFF, "input_snapshot_cutoff": CUTOFF,
                    "research_actor": actor.to_dict(), "execution": {"attempt": 1, "stage": "generation"}}))
            session.commit()
        yield


def request(**changes):
    return routes.WindQueryInput(**{"instrument_id": "xlk", "domain": "fund_data", "tool": "get_fund_quote",
        "arguments": {"code": "TEST.OF"}, **changes})


def test_acquisition_reuses_and_quant_reads_exact_original_without_context_lost_update(record, monkeypatch):
    calls = []
    with get_session_factory()() as session:
        def fetch(*args):
            assert not session.in_transaction()  # No row lock across the provider request.
            calls.append(args)
            with get_session_factory()() as concurrent:
                run = concurrent.get(ResearchEntry, "wind-run")
                run.context_json = {**run.context_json, "parallel_task_result": "preserve me"}
                concurrent.commit()
            return deepcopy(PAYLOAD)
        monkeypatch.setattr(wind, "query", fetch)
        receipt = routes.wind_query("wind-run", request(), session)
        again = routes.wind_query("wind-run", request(), session)
        assert receipt["source_id"] == again["source_id"] and again["reused"]
        assert len(calls) == 1 and "payload" not in json.dumps(receipt)
        run = session.get(ResearchEntry, "wind-run")
        assert run.context_json["parallel_task_result"] == "preserve me"
        assert run.context_json["input_snapshot_cutoff"] == CUTOFF
        assert run.context_json["cutoff"] > CUTOFF
        originals, _ = selected_quant_sources(session, run, "xlk", [receipt["source_id"]])
        original = originals[receipt["source_id"]]
        assert original["data"]["payload"] == PAYLOAD
        assert original["methodology"]["no_calculation"] is True
        assert original["methodology"]["operation"] == "provider_read"
        assert "published_at" not in original
        for metadata in (source_index(original), computed_reference(original),
                         retained_computed_sources(session, run.entry_id, metadata_only=True)[0],
                         quant_source_catalogue(session, run, "xlk")[0],
                         current_research_view({"sources": [original]}, "xlk")["sources"][0]):
            assert metadata["analysis_kind"] == "provider_snapshot"
            assert metadata["provider"] == "wind"
            assert metadata["retrieved_at"] == receipt["retrieved_at"]
            assert "payload" not in json.dumps(metadata)


@pytest.mark.parametrize("change,iid", [({"risk_run": True}, "xlk"),
    ({"execution": {"attempt": 1, "stage": "review"}}, "xlk"), ({}, "private-portfolio-id"),
    ({"execution": {"attempt": 2, "stage": "generation"}}, "xlk")])
def test_run_scope_and_stage_guard_before_network(record, monkeypatch, change, iid):
    monkeypatch.setattr(wind, "query", lambda *args: pytest.fail("unauthorized provider query"))
    with get_session_factory()() as session:
        run = session.get(ResearchEntry, "wind-run")
        run.context_json = {**run.context_json, **change}
        session.commit()
        with pytest.raises(HTTPException):
            routes.wind_query("wind-run", request(instrument_id=iid), session)


def test_stale_attempt_after_network_cannot_publish_original(record, monkeypatch):
    def fetch(*args):
        with get_session_factory()() as concurrent:
            run = concurrent.get(ResearchEntry, "wind-run")
            run.context_json = {**run.context_json, "execution": {"attempt": 2, "stage": "generation"}}
            concurrent.commit()
        return PAYLOAD
    monkeypatch.setattr(wind, "query", fetch)
    with get_session_factory()() as session:
        with pytest.raises(HTTPException) as failure:
            routes.wind_query("wind-run", request(), session)
        assert failure.value.status_code == 409
        assert retained_computed_sources(session, "wind-run") == []


def test_discovery_and_query_require_current_run_credential(record, monkeypatch):
    monkeypatch.setattr(wind, "query", lambda *args: pytest.fail("unauthorized query"))
    monkeypatch.setattr(wind, "list_tools", lambda *args: pytest.fail("unauthorized discovery"))
    actor = replace(current_principal(), resource_scope=None)
    with principal_context(actor), get_session_factory()() as session:
        for invoke in (lambda: routes.wind_query("wind-run", request(), session),
                       lambda: routes.wind_tools("wind-run", routes.WindToolsInput(domain="fund_data"), session)):
            with pytest.raises(HTTPException) as failure:
                invoke()
            assert failure.value.status_code == 403


def test_large_vendor_schema_is_reachable_through_existing_pagination(record, monkeypatch):
    schema = {"type": "object", "properties": {"query": {"type": "string", "description": "描述" * 30000}}}
    monkeypatch.setattr(wind, "list_tools", lambda _: {"provider": "wind", "domain": "fund_data",
        "tools": [{"name": "get_fund_quote", "inputSchema": schema}]})
    with get_session_factory()() as session:
        page = routes.wind_tools("wind-run", routes.WindToolsInput(domain="fund_data"), session)
        assert page["deferred"][0]["path"] == [0]
        leaf = routes.wind_tools("wind-run", routes.WindToolsInput(domain="fund_data",
            path=[0, "inputSchema", "properties", "query", "description"], limit=100), session)
        assert leaf["data"] == "描述" * 6000
        assert leaf["next_offset"] == 12000
