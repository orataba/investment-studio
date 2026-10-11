"""Wrong source selectors give bounded, correctable feedback, never implicit reads."""
import asyncio
import io
import json
from urllib.error import HTTPError

import pytest

from watchlist_app import research_mcp as mcp


@pytest.mark.parametrize("tool, arguments, endpoint", [
    ("compare_instruments", {"source_id": "prefix-was-removed", "section": "dates"}, "computed-read"),
    ("read_research_numbers", {"source_id": "computed:historical", "section": "data"}, "computed-read"),
    ("read_quant_analysis", {"source_id": "computed:missing", "section": "data"}, "computed-read"),
    ("read_research_dossier", {"instrument_id": "asset", "source_id": "computed:new"}, "read"),
])
def test_source_404_explains_exact_run_and_history_reads_without_guessing(monkeypatch, tool, arguments, endpoint):
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "bound-run")
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN", "test-token")
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_API_BASE_URL", "http://local.invalid/api")
    calls = []
    def missing(request, **kwargs):
        calls.append(request)
        raise HTTPError(request.full_url, 404, "Not found", {},
                        io.BytesIO(b'{"detail":"do-not-echo-private-upstream-body"}'))
    monkeypatch.setattr(mcp, "urlopen", missing)
    with pytest.raises(ValueError) as caught:
        getattr(mcp, tool)(**arguments)
    diagnostic = json.loads(str(caught.value))
    assert diagnostic["status"] == 404 and diagnostic["error"] == "source_not_in_bound_scope"
    assert "read_research_context" in diagnostic["next_action"]
    assert "read_research_dossier" in diagnostic["next_action"]
    assert "sources/versions" in diagnostic["next_action"]
    if endpoint == "computed-read":
        assert "comparison:/computed:" in diagnostic["next_action"]
    assert "do-not-echo-private-upstream-body" not in str(caught.value)
    assert len(calls) == 1 and calls[0].full_url.endswith(f"/bound-run/{endpoint}")
    assert json.loads(calls[0].data)["source_id"] == arguments["source_id"]


def test_tool_schemas_advertise_opaque_source_ids_and_current_run_scope():
    tools = {tool.name: tool for tool in asyncio.run(mcp.mcp.list_tools())}
    for name in ("compare_instruments", "read_research_numbers", "read_quant_analysis", "read_research_dossier"):
        schema = json.dumps(tools[name].input_schema["properties"]["source_id"])
        assert "verbatim" in schema and "comparison:" in schema and "computed:" in schema
    for name in ("compare_instruments", "read_research_numbers"):
        assert "THIS RUN" in tools[name].description
        assert "read_research_dossier" in tools[name].description


def test_non_source_404_still_uses_http_error_without_response_body(monkeypatch):
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_ID", "bound-run")
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN", "test-token")
    error = HTTPError("http://local.invalid", 404, "Not found", {}, io.BytesIO(b'private'))
    monkeypatch.setattr(mcp, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(error))
    with pytest.raises(HTTPError) as caught:
        mcp.request("context")
    assert caught.value is error
