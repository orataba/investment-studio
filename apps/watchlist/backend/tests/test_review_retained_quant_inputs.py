"""The isolated reviewer resolves exact retained inputs through the bound API."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from threading import Thread

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from fastapi import HTTPException
import pytest
from studio_identity import current_principal, principal_context

from watchlist_app import review_mcp
from watchlist_app.db.models import InstrumentDetail
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_run_evidence as evidence
from watchlist_app.services import sector_fact_review


@pytest.mark.parametrize("binding", ["run", "dossier", "version", "prior_version"])
def test_isolated_reviewer_reads_paged_nested_inputs_only_through_bound_run_api(client, tmp_path, binding):
    actor = current_principal()
    cutoff, run_id, iid = "2026-10-01T00:00:00+00:00", "review-input-run", "fund-us-agg"
    points = [{"date": f"observation-{index}", "value": index, "unit": "USD"} for index in range(1200)]
    base = {"source_type": "computed_metric", "scope": "instrument", "instrument_id": iid, "as_of": cutoff}
    original = {**base, "source_id": "computed:original", "methodology": "Retained observations; no fills",
                "data": {"points": points}}
    with principal_context(replace(actor, resource_scope={"kind": "run", "id": run_id, "attempt": "1"})), get_session_factory()() as session:
        session.add(InstrumentDetail(instrument_id=iid, instrument_name="Retained ETF", instrument_type="etf",
            detail_view_type="etf", is_active=True, metadata_json={}))
        session.add(ResearchTopic(topic_id="review-input-topic", title="Research", team_id=actor.team_id,
                                  visibility="team", instrument_ids=[iid]))
        session.flush()
        run = ResearchEntry(entry_id=run_id, topic_id="review-input-topic", team_id=actor.team_id,
            kind="analysis", title="Research", status="running", context_json={"research_run": True,
                "research_actor": actor.to_dict(), "execution": {"attempt": 1}, "cutoff": cutoff,
                "instrument_ids": [iid]})
        session.add(run)
        session.flush()
        session.add(ResearchTopic(topic_id="private-input-origin", title="Private origin", team_id=actor.team_id,
            visibility="private", created_by_user_id="other-researcher", instrument_ids=[iid]))
        session.flush()
        session.add(ResearchEntry(entry_id="private-input-run", topic_id="private-input-origin", team_id=actor.team_id,
            kind="analysis", title="Old private computation", status="completed", context_json={
                "research_actor": {**actor.to_dict(), "user_id": "other-researcher"},
                "instrument_ids": [iid], "cutoff": cutoff, "computed_metrics": [original]}))
        session.flush()
        reference = evidence.computed_reference(original, run_id="private-input-run")
        with pytest.raises(HTTPException):
            evidence.resolve_reference(reference, session=session)
        # The exact source was shared into this authorized quant result. Its
        # historical private container does not block reading that shared input.
        intermediate = evidence.retain_computed_source(session, run, {**base, "source_id": "computed:intermediate",
            "data": {"analysis_kind": "python_quant", "metrics": {"mean": 599.5}, "lookalike": reference},
            "methodology": {"code": "result = observed_mean", "input_sources": [reference]}})
        final = {**base, "source_id": "computed:final",
            "data": {"analysis_kind": "python_quant", "metrics": {"mean": 599.5}},
            "methodology": {"code": "result = retained_mean", "input_sources": [evidence.computed_reference(intermediate)]}}
        if binding == "run":
            final = evidence.retain_computed_source(session, run, final)
        else:
            notebook = {"instrument_id": iid, "run_id": "old-quant-run", "version_id": "old-quant-notebook",
                "created_at": cutoff, "source_ids": [final["source_id"]], "sources": [final]}
            session.add(ResearchEntry(entry_id="old-quant-run", topic_id="review-input-topic", team_id=actor.team_id,
                kind="analysis", title="Previously published quant", status="completed",
                created_at=datetime.fromisoformat(cutoff), completed_at=datetime.fromisoformat(cutoff),
                context_json={"research_run": True, "instrument_ids": [iid], "cutoff": cutoff,
                    "research_actor": actor.to_dict(), "reviews": {iid: {"status": "completed", "research": notebook}}}))
            reference = evidence.computed_reference(final, run_id="old-quant-run",
                path=["reviews", iid, "research", "sources", "*"])
            run.context_json = {**run.context_json, "research_dossiers": [{"instrument_id": iid,
                "notebook": {**notebook, "sources": [reference]}}]}
            if binding == "prior_version":
                current_input = evidence.retain_computed_source(session, run, {**base,
                    "source_id": "computed:current-input", "data": {"points": [{"date": "current", "value": 999}]}})
                # Same top source ID in two genuine information sets. The
                # current packet source must never replace the old version.
                final = evidence.retain_computed_source(session, run, {**final,
                    "data": {"analysis_kind": "python_quant", "metrics": {"mean": 999}},
                    "methodology": {"code": "result = current_mean", "input_sources": [evidence.computed_reference(current_input)]}})
        session.commit()
    packet = {"run_id": run_id, "cutoff": cutoff, "draft_reviews": [], "sources": [deepcopy(final)]}
    if binding in {"dossier", "version"}:
        packet["computed_input_reads"] = {final["source_id"]: {"instrument_id": iid,
            **({"version_id": "old-quant-notebook"} if binding == "version" else {})}}
        missing_selector = client.post(f"/api/research/runs/{run_id}/computed-read", json={
            "source_id": final["source_id"], "tool": "quant", "section": "inputs", "path": [0]})
        assert missing_selector.status_code == 404
    if binding in {"version", "prior_version"}:
        version = client.get(f"/api/research/runs/{run_id}/dossier/{iid}", params={"version_id": "old-quant-notebook"})
        assert version.status_code == 200, version.text
        if binding == "prior_version":
            packet["prior_judgment_versions"] = [version.json()]
    # The reviewer must address this run's final computation, never adopt a
    # packet locator as arbitrary API authority or query the original run ID.
    packet["sources"][0]["methodology"]["input_sources"][0]["retained_reference"]["run_id"] = "not-an-api-target"
    paths = {key: tmp_path / filename for key, filename in (
        ("PACKET", "packet.json"), ("RESULT", "result.json"), ("READS", "reads.jsonl"))}
    paths["PACKET"].write_text(json.dumps({"packet": packet,
        "response_schema": sector_fact_review._review_schema([], packet["sources"])}))
    calls = []

    class API(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == f"/api/research/runs/{run_id}/computed-read"
            assert self.headers["Authorization"] == "Bearer offline-review-run-token"
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload)
            response = client.post(self.path, json=payload)
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), API)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {"INVESTMENT_STUDIO_REVIEW_" + key: str(path) for key, path in paths.items()}
    env.update(INVESTMENT_STUDIO_RESEARCH_RUN_ID=run_id,
        INVESTMENT_STUDIO_RESEARCH_API_BASE_URL=f"http://127.0.0.1:{server.server_port}/api",
        INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN="offline-review-run-token")
    assert not any("DATABASE" in key for key in env)

    async def smoke():
        params = StdioServerParameters(command=sys.executable, args=["-m", "watchlist_app.review_mcp"],
                                      cwd=str(Path(__file__).resolve().parents[1]), env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                historical = binding == "prior_version"
                root_path = [0, "sources", 0] if historical else []
                if historical:
                    current = await session.call_tool("read_review_source", {"source_id": final["source_id"],
                        "path": ["methodology", "input_sources", 0, "data", "points"]})
                    assert not current.is_error
                    assert current.structured_content["data"] == [{"date": "current", "value": 999}]

                async def page(path, **kwargs):
                    result = await session.call_tool("read_review_context" if historical else "read_review_source", {
                        **({"section": "prior_judgment_versions"} if historical else {"source_id": final["source_id"]}),
                        "path": path, **kwargs})
                    assert not result.is_error, result.content
                    assert len(result.content[0].text.encode()) <= 48000
                    return result.structured_content

                before_directory = len(calls)
                directory = await page([*root_path, "methodology", "input_sources"])
                assert "retained_reference" in directory["data"][0]
                assert "data" not in directory["data"][0] and len(calls) == before_directory
                prefix = [*root_path, "methodology", "input_sources", 0]
                original_path = [*prefix, "methodology", "input_sources", 0, "data"]
                data = await page(original_path)
                assert data["deferred"] == [{"path": [*original_path, "points"], "type": "array", "count": len(points)}]
                actual, offset = [], 0
                while offset is not None:
                    result = await page(data["deferred"][0]["path"], offset=offset, limit=100)
                    actual.extend(result["data"])
                    offset = result["next_offset"]
                assert actual == points
                # A similarly shaped locator in result data is returned as data,
                # and cannot be traversed as an original source.
                lookalike = await page([*prefix, "data", "lookalike"])
                assert lookalike["data"]["source_id"] == original["source_id"]
                assert lookalike["data"]["retained_reference"]["run_id"] == "private-input-run"
                invalid = await session.call_tool("read_review_context" if historical else "read_review_source", {
                    **({"section": "prior_judgment_versions"} if historical else {"source_id": final["source_id"]}),
                    "path": [*prefix, "data", "lookalike", "data"]})
                assert invalid.is_error
                before = len(calls)
                invalid = await session.call_tool("read_review_source", {"source_id": original["source_id"], "path": []})
                assert invalid.is_error and len(calls) == before
                if historical:
                    await session.call_tool("read_review_context", {"section": "draft_reviews"})
                    invalid = await session.call_tool("submit_review_receipts", {"receipts": {"reviews": []}})
                    assert invalid.is_error
                    assert "Read the exact prior judgment version" in invalid.content[0].text

    try:
        asyncio.run(smoke())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    assert all(call["source_id"] == final["source_id"] and call["tool"] == "quant"
               and call["section"] == "inputs" for call in calls)
    selected_calls = calls[1:] if binding == "prior_version" else calls
    assert all(call.get("instrument_id") == (iid if binding != "run" else None)
               and call.get("version_id") == ("old-quant-notebook" if binding in {"version", "prior_version"} else None)
               for call in selected_calls)
    if binding == "prior_version":
        assert "instrument_id" not in calls[0] and "version_id" not in calls[0]
    reads = [json.loads(line) for line in paths["READS"].read_text().splitlines()]
    local_reads = [row for row in reads if not row.get("retained_input") and row.get("section") != "draft_reviews"]
    assert len(local_reads) == 1
    assert local_reads[0]["path"] == ([0, "sources", 0] if binding == "prior_version" else []) + ["methodology", "input_sources"]
    assert not review_mcp._evidence_read(final, local_reads)  # Locator is not evidence.
    assert not review_mcp._evidence_read(final, reads)  # Inputs do not imply the output was read.
    assert json.loads(paths["PACKET"].read_text())["packet"] == packet


def test_retained_input_locator_is_recognized_only_at_declared_source_positions():
    reference = {"source_id": "computed:input", "source_type": "computed_metric", "retained_reference": {"run_id": "old"}}
    source = {"source_type": "computed_metric", "data": {"analysis_kind": "python_quant", "fake": reference},
              "methodology": {"input_sources": [{"methodology": {"input_sources": [reference]}}]}}
    assert review_mcp._retained_quant_input(source, ["methodology", "input_sources", 0, "methodology", "input_sources", 0, "data"])
    for path in (["data", "fake"], ["methodology", "input_sources", True],
                 ["methodology", "input_sources", 1], ["methodology", "input_sources", 0, "data", "fake"]):
        assert not review_mcp._retained_quant_input(source, path)
