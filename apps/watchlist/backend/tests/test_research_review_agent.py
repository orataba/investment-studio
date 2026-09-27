import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from watchlist_app import review_mcp
from watchlist_app.services import research_review_agent as agent
from watchlist_app.services import sector_fact_review as review


def _bound(monkeypatch, tmp_path, packet):
    packet = {"cutoff": "2026-09-26T00:00:00+00:00", **packet}
    state = {"packet": packet, "response_schema": review._review_schema(packet["draft_reviews"], packet["sources"])}
    for key, filename in (("PACKET", "packet.json"), ("RESULT", "result.json"), ("READS", "reads.jsonl")):
        monkeypatch.setenv("INVESTMENT_STUDIO_REVIEW_" + key, str(tmp_path / filename))
    (tmp_path / "packet.json").write_text(json.dumps(state))
    return state


def _complete_read(tool, **selector):
    def read(path):
        offset = 0
        while offset is not None:
            page = tool(**selector, path=path, offset=offset)
            assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) <= 48000
            for child in page["deferred"]:
                read(child["path"])
            offset = page["next_offset"]
    read([])


def test_review_tool_schema_does_not_share_mutable_registration_or_packet_metadata(monkeypatch, tmp_path):
    state = _bound(monkeypatch, tmp_path, {"draft_reviews": [], "sources": []})
    first = next(tool for tool in asyncio.run(review_mcp.mcp.list_tools())
                 if tool.name == "submit_review_receipts")
    first.input_schema["required"].clear()
    first.input_schema["properties"]["receipts"]["properties"]["reviews"]["maxItems"] = 99
    second = next(tool for tool in asyncio.run(review_mcp.mcp.list_tools())
                  if tool.name == "submit_review_receipts")
    assert second.input_schema["required"] == ["receipts"]
    assert second.input_schema["properties"]["receipts"] == state["response_schema"]
    assert json.loads((tmp_path / "packet.json").read_text()) == state
    assert not (tmp_path / "reads.jsonl").exists()


def test_reviewer_stdio_advertises_bound_schema_and_preserves_submission_checks(monkeypatch, tmp_path):
    import os
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    proposed = {"instrument_id": "asset", "summary": "", "events": [{
        "event_key": "disclosure", "action": "new", "direction": "uncertain", "title": "Disclosure",
        "body": "The original discloses a fact.", "confidence": "confirmed", "information_type": "fact",
        "recording_type": "new", "source_ids": ["original"]}], "coverage": [], "research": None,
        "themes": [{"theme_key": "demand", "title": "Demand", "question": "Does demand persist?",
                    "synthesis": "The disclosure establishes the baseline.", "next_check": "Next disclosure",
                    "priority_reason": "Demand affects the investment outlook.", "source_ids": ["original"]}],
        "reflection": {"status": "reviewed", "summary": "Checked the original.",
                       "reviewed_update_ids": [], "source_ids": ["original"]}}
    state = _bound(monkeypatch, tmp_path, {"draft_reviews": [proposed],
        "sources": [{"source_id": "original", "source_type": "public_source", "instrument_id": "asset",
                     "text": "The complete retained original."}]})
    receipts = {"reviews": [{"instrument_id": "asset", "summary": {"decision": "accept"},
        "change_kind": {"decision": "accept"}, "coverage": {"decision": "accept"},
        "decisions": [{"event_key": "disclosure", "decision": "accept"}], "research": None,
        "themes": [{"theme_key": "demand", "decision": "accept"}], "reflection": {"decision": "accept"}}]}

    async def smoke():
        params = StdioServerParameters(command=sys.executable, args=["-m", "watchlist_app.review_mcp"],
            cwd=str(Path(__file__).resolve().parents[1]), env={key: os.environ[key] for key in (
                "INVESTMENT_STUDIO_REVIEW_PACKET", "INVESTMENT_STUDIO_REVIEW_RESULT", "INVESTMENT_STUDIO_REVIEW_READS")})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                tool = next(tool for tool in listed.tools if tool.name == "submit_review_receipts")
                wire = tool.model_dump(by_alias=True)
                assert wire["inputSchema"]["required"] == ["receipts"]
                schema = wire["inputSchema"]["properties"]["receipts"]
                assert schema == state["response_schema"]
                fields = schema["properties"]["reviews"]["items"]
                assert {"themes", "reflection"} <= set(fields["required"])
                assert fields["properties"]["instrument_id"] == {"const": "asset"}
                for name, key, identity in (("decisions", "event_key", "disclosure"), ("themes", "theme_key", "demand")):
                    assert all(variant["properties"][key] == {"enum": [identity]}
                               for variant in fields["properties"][name]["items"]["oneOf"])
                assert wire["annotations"]["readOnlyHint"] is False
                assert not (tmp_path / "reads.jsonl").exists()

                async def rejected(payload, message):
                    response = await session.call_tool(tool.name, {"receipts": payload})
                    assert response.is_error
                    assert message in response.content[0].text
                    assert not (tmp_path / "result.json").exists()

                await rejected(receipts, "complete draft_reviews")
                await session.call_tool("read_review_context", {"section": "draft_reviews"})
                await rejected(receipts, "substantive original evidence")
                await session.call_tool("read_review_source", {"source_id": "original"})
                for field in ("themes", "reflection"):
                    invalid = deepcopy(receipts)
                    del invalid["reviews"][0][field]
                    await rejected(invalid, "missing fields: " + field)
                invalid = deepcopy(receipts)
                invalid["reviews"][0]["themes"][0]["theme_key"] = "unbound"
                await rejected(invalid, "Invalid receipt")
                response = await session.call_tool(tool.name, {"receipts": receipts})
                assert not response.is_error
                assert response.structured_content == {"accepted": True, "publication": "pending_application_validation"}

    asyncio.run(smoke())
    assert json.loads((tmp_path / "result.json").read_text())["receipts"] == receipts
    assert json.loads((tmp_path / "packet.json").read_text()) == state


@pytest.mark.parametrize("target", ["event", "market_view"])
@pytest.mark.parametrize("repair", ["restore_source", "correct_date"])
def test_review_citation_correction_rechecks_dates_and_can_be_repaired_in_same_session(monkeypatch, tmp_path, target, repair):
    event = {"event_key": "ai-dialogue", "action": "new", "direction": "uncertain", "title": "AI dialogue",
        "body": "The original describes the dialogue.", "confidence": "confirmed", "information_type": "fact",
        "recording_type": "new", "source_ids": ["old"], "published_at": "2026-09-23",
        "market_views": [{"publisher": "Original publisher", "view": "The dialogue may affect demand.",
                          "source_ids": ["old"], "published_at": "2026-09-23"}]}
    state = _bound(monkeypatch, tmp_path, {"draft_reviews": [{"instrument_id": "xlk", "events": [event]}],
        "sources": [{"source_id": key, "source_type": "public_source", "instrument_id": "xlk",
                     "text": "The retained original disclosure.", "published_at": date}
                    for key, date in (("old", "2026-09-23"), ("new", "2026-09-25"))]})
    patch = {"source_ids": ["new"]} if target == "event" else {
        "market_views": [{**event["market_views"][0], "source_ids": ["new"]}]}
    receipt = {"event_key": "ai-dialogue", "decision": "correct", "reason": "Use the follow-up original.", "patch": patch}
    receipts = {"reviews": [{"instrument_id": "xlk", "summary": {"decision": "accept"},
        "change_kind": {"decision": "accept"}, "coverage": {"decision": "accept"}, "decisions": [receipt],
        "themes": [], "research": None, "reflection": None}]}
    _complete_read(review_mcp.read_review_context, section="draft_reviews")
    for source in state["packet"]["sources"]:
        review_mcp.read_review_source(source["source_id"])

    async def submit():
        return await review_mcp.mcp.call_tool("submit_review_receipts", {"receipts": receipts})

    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError) as rejected:
        asyncio.run(submit())
    field = "published_at" if target == "event" else "market_views[0].published_at"
    assert f"ai-dialogue {field}:" in str(rejected.value)
    assert event["body"] not in str(rejected.value)
    assert not (tmp_path / "result.json").exists()
    corrected = patch if target == "event" else patch["market_views"][0]
    corrected.update({"source_ids": ["old", "new"]} if repair == "restore_source" else {"published_at": "2026-09-25"})
    assert asyncio.run(submit()).structured_content["accepted"]
    stored = json.loads((tmp_path / "result.json").read_text())["result"]["reviews"][0]["decisions"][0]["event"]
    selected = stored if target == "event" else stored["market_views"][0]
    assert selected["published_at"] == ("2026-09-23" if repair == "restore_source" else "2026-09-25")
    assert selected["source_ids"] == corrected["source_ids"]
    assert json.loads((tmp_path / "packet.json").read_text()) == state


def test_review_counts_complete_inline_snapshot_subtree_but_not_unread_or_partial_evidence(monkeypatch, tmp_path):
    sources = [
        {"source_id": "snapshot", "snapshot": {"instrument_id": "xlk", "reference_data": "reference" * 10000,
            "risk": {"drawdown_pct": -4.5, "volatility_pct": 18.2}, "analyst_estimate_history": "history" * 10000}},
        {"source_id": "computed", "data": {"analysis_kind": "python_quant", "status": "available",
            "method_version": "1", "observation_key": "market", "as_of_date": "2026-09-25", "benchmark_id": None,
            "metrics": [{"name": "volatility", "value": 18.2}], "observations": [{"value": n} for n in range(10)]}},
        {"source_id": "deferred", "data": {"metrics": [{"value": n} for n in range(10000)]}},
    ]
    _bound(monkeypatch, tmp_path, {"draft_reviews": [], "sources": sources})

    def reads(source_id):
        return [row for row in map(json.loads, (tmp_path / "reads.jsonl").read_text().splitlines())
                if row.get("source_id") == source_id]

    review_mcp.read_review_context(section="sources")
    assert not any(review_mcp._evidence_read(source, reads(source["source_id"])) for source in sources)
    risk_page = review_mcp.read_review_source("snapshot", path=["snapshot"], offset=2, limit=1)
    assert risk_page["data"] == {"risk": sources[0]["snapshot"]["risk"]} and not risk_page["deferred"]
    assert not review_mcp._complete(sources[0]["snapshot"], ["snapshot"], reads("snapshot"))
    assert review_mcp._evidence_read(sources[0], reads("snapshot"))
    metadata_page = review_mcp.read_review_source("computed", path=["data"], limit=6)
    assert metadata_page["next_offset"] == 6
    assert not review_mcp._evidence_read(sources[1], reads("computed"))
    deferred_page = review_mcp.read_review_source("deferred", path=["data"])
    assert deferred_page["deferred"][0]["path"] == ["data", "metrics"]
    assert not review_mcp._evidence_read(sources[2], reads("deferred"))
    review_mcp.read_review_source("deferred", path=["data", "metrics"], limit=2)
    assert not review_mcp._evidence_read(sources[2], reads("deferred"))
    review_mcp.read_review_source("computed", path=["data"], offset=6, limit=1)
    assert review_mcp._evidence_read(sources[1], reads("computed"))


def test_reviewer_rejects_directory_only_acceptance_and_accepts_selected_complete_original(monkeypatch, tmp_path):
    proposed = {"instrument_id": "asset", "summary": "", "events": [], "coverage": [], "research": None,
        "reflection": {"status": "reviewed", "summary": "原件支持本次复核。", "reviewed_update_ids": [], "source_ids": ["original"]}}
    packet = {"draft_reviews": [proposed], "sources": [{"source_id": "original", "source_type": "public_source",
        "text": "完整原始披露" * 15000, "unrelated_table": list(range(10000))}]}
    _bound(monkeypatch, tmp_path, packet)
    receipts = {"reviews": [{"instrument_id": "asset", "summary": {"decision": "accept"},
        "change_kind": {"decision": "accept"}, "coverage": {"decision": "accept"},
        "decisions": [], "research": None, "themes": [], "reflection": {"decision": "accept"}}]}
    # Build the exact shape that the draft-bound schema permits.
    schema = review._review_schema([proposed], packet["sources"])
    item_schema = schema["properties"]["reviews"]["items"]
    assert item_schema
    with pytest.raises(ValueError):
        review_mcp.submit_review_receipts(receipts)
    contract = review_mcp.read_review_context()["receipt_contract"]
    assert contract["reviews"][0]["instrument_id"] == "asset"
    _complete_read(review_mcp.read_review_context, section="draft_reviews")
    review_mcp.read_review_context(section="sources")
    with pytest.raises(ValueError, match="substantive original"):
        review_mcp.submit_review_receipts(receipts)
    review_mcp.read_review_source("original", path=["text"])
    with pytest.raises(ValueError, match="substantive original"):
        review_mcp.submit_review_receipts(receipts)
    offset = 12000
    while offset is not None:
        offset = review_mcp.read_review_source("original", path=["text"], offset=offset)["next_offset"]
    assert review_mcp.submit_review_receipts(receipts)["accepted"]
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["result"]["reviews"][0]["reflection"] == proposed["reflection"]
    assert "unrelated_table" not in (tmp_path / "reads.jsonl").read_text()
    assert "response_schema" not in (tmp_path / "reads.jsonl").read_text()


def test_narrow_correction_uses_one_schema_path_without_skipping_acquisition_or_validation(monkeypatch, tmp_path):
    from copy import deepcopy
    proposed = {"instrument_id": "asset", "summary": "", "events": [], "coverage": [],
        "research": {"investment_view": {"direction": "Original outlook", "risk": "Retained uncertainty"},
                     "source_ids": ["original"]}}
    packet = {"draft_reviews": [proposed], "acquisition": {"coverage": "Specific gap. " * 6000},
        "sources": [{"source_id": "original", "source_type": "public_source", "text": "The relevant original."}]}
    _bound(monkeypatch, tmp_path, packet)
    contract = review_mcp.read_review_context()["receipt_contract"]["reviews"][0]
    view_path = contract["research_fields"]["investment_view"]["schema_path"]
    selected = review_mcp.read_review_context(section="response_schema", path=view_path)
    assert not selected["deferred"] and selected["next_offset"] is None
    _complete_read(review_mcp.read_review_context, section="draft_reviews")
    review_mcp.read_review_source("original")
    receipts = {"reviews": [{"instrument_id": "asset", "summary": {"decision": "accept"},
        "change_kind": {"decision": "accept"}, "coverage": {"decision": "accept"}, "decisions": [],
        "themes": [], "reflection": None, "research": {"decision": "correct", "reason": "Qualify the outlook",
            "patch": {"investment_view": {"decision": "correct", "reason": "Original supports a conditional view",
                "patch": {"direction": "Conditional outlook"}, "omit_fields": []}}, "omit_fields": []}}]}
    with pytest.raises(ValueError, match="complete acquisition"):
        review_mcp.submit_review_receipts(receipts)
    review_mcp.read_review_context(section="acquisition")
    with pytest.raises(ValueError, match="complete acquisition"):
        review_mcp.submit_review_receipts(receipts)
    _complete_read(review_mcp.read_review_context, section="acquisition")
    invalid = deepcopy(receipts)
    invalid["reviews"][0]["research"]["patch"]["investment_view"]["patch"]["attractiveness"] = "Unproposed field"
    with pytest.raises(ValueError, match="Invalid receipt"):
        review_mcp.submit_review_receipts(invalid)
    assert review_mcp.submit_review_receipts(receipts)["accepted"]
    result = json.loads((tmp_path / "result.json").read_text())["result"]["reviews"][0]
    assert result["research"]["investment_view"] == {"direction": "Conditional outlook", "risk": "Retained uncertainty"}
    schema_reads = [row for row in map(json.loads, (tmp_path / "reads.jsonl").read_text().splitlines())
                    if row.get("section") == "response_schema"]
    assert len(schema_reads) == 1 and schema_reads[0]["path"] == view_path


def test_reviewer_cannot_replace_bound_draft_ids_or_accept_unread_deferred_draft(monkeypatch, tmp_path):
    proposed = {"instrument_id": "asset", "summary": "很长的完整草稿" * 12000, "events": [], "coverage": []}
    packet = {"draft_reviews": [proposed], "sources": []}
    _bound(monkeypatch, tmp_path, packet)
    page = review_mcp.read_review_context(section="draft_reviews")
    assert page["deferred"]
    assert not review_mcp._complete(packet["draft_reviews"], [], [json.loads(line) for line in (tmp_path / "reads.jsonl").read_text().splitlines()])
    _complete_read(review_mcp.read_review_context, section="draft_reviews")
    reads = [json.loads(line) for line in (tmp_path / "reads.jsonl").read_text().splitlines()]
    assert review_mcp._complete(packet["draft_reviews"], [], reads)
    with pytest.raises(ValueError, match="Invalid receipt"):
        review_mcp.submit_review_receipts({"reviews": [{"instrument_id": "other"}]})


def test_methodology_is_not_numerical_evidence():
    source = {"source_id": "metric", "methodology": {"analysis_kind": "return"}, "data": {"return": 0.2, "status": "ready"}}
    reads = [{"path": ["methodology"], "offset": 0, "next_offset": None, "total": 1, "deferred": []}]
    assert not review_mcp._evidence_read(source, reads)
    assert not review_mcp._evidence_read(source, [{"path": ["data", "status"], "offset": 0, "next_offset": None, "total": 5, "deferred": []}])
    reads.append({"path": ["data"], "offset": 0, "next_offset": None, "total": 2, "deferred": []})
    assert review_mcp._evidence_read(source, reads)


def test_review_projection_keeps_acquisition_required_and_details_readable_from_same_packet(monkeypatch, tmp_path):
    proposed = {"instrument_id": "asset", "summary": "", "events": [], "coverage": [], "research": None,
        "reflection": {"status": "reviewed", "summary": "Original supports this check.",
                       "reviewed_update_ids": [], "source_ids": ["original"]}}
    original = {"source_id": "original", "source_type": "public_source", "text": "Complete original\x00final line."}
    theme = {"theme_id": "theme-1", "theme_key": "demand", "notes": [{"author": "PM", "body": "Wait"}],
        "versions": [{"version_id": "theme:1", "body": "Original judgment", "source_ids": ["original"]}],
        "updates": [], "research_progress": [{"body": "Checked", "source_ids": ["original"]}]}
    coverage = {"latest_received_at": "2026-09-25T00:00:00+00:00",
                "sources": [{"channel": "unrelated", "status": "partial", "error": "Missing originals"}]}
    packet = review._evidence_packet({"cutoff": "2026-09-26T00:00:00+00:00", "market_coverage": coverage,
        "market_text_sources": [original], "research_dossiers": [{"instrument_id": "asset", "themes": [theme],
            "review_agenda": {"focus_themes": [theme]}}]}, [proposed], "bound")
    _bound(monkeypatch, tmp_path, packet)
    overview = review_mcp.read_review_context()
    assert {"market_coverage", "theme_history"} <= set(overview["sections"])
    _complete_read(review_mcp.read_review_context, section="draft_reviews")
    assert review_mcp.read_review_source("original")["data"] == original
    receipts = {"reviews": [{"instrument_id": "asset", "summary": {"decision": "accept"},
        "change_kind": {"decision": "accept"}, "coverage": {"decision": "accept"},
        "decisions": [], "research": None, "themes": [], "reflection": {"decision": "accept"}}]}
    review_mcp.read_review_context(section="acquisition", path=["market_coverage"])
    with pytest.raises(ValueError, match="complete acquisition"):
        review_mcp.submit_review_receipts(receipts)
    _complete_read(review_mcp.read_review_context, section="acquisition")
    # Unrelated historical/channel details are available, not automatically mandatory.
    assert review_mcp.submit_review_receipts(receipts)["accepted"]
    focus = packet["research_dossiers"][0]["review_agenda"]["focus_themes"][0]["current_read"]
    current = review_mcp.read_review_context(section=focus["section"], path=focus["path"])["data"]
    reads = [json.loads(line) for line in (tmp_path / "reads.jsonl").read_text().splitlines()]
    assert not any(row.get("section") in {"market_coverage", "theme_history"} for row in reads)
    history_read = current["history_read"]
    history = review_mcp.read_review_context(section=history_read["section"], path=history_read["path"])["data"]
    assert {**{key: value for key, value in current.items() if key != "history_read"}, **history} == theme
    assert review_mcp.read_review_context(section="market_coverage")["data"] == coverage
    assert json.loads((tmp_path / "packet.json").read_text())["packet"]["sources"] == [original]


def test_missing_review_receipt_names_required_fields_without_echoing_values(monkeypatch, tmp_path):
    _bound(monkeypatch, tmp_path, {'draft_reviews': [{'instrument_id': 'asset', 'summary': '', 'events': [], 'coverage': []}], 'sources': []})
    with pytest.raises(ValueError, match='missing fields:') as rejected:
        review_mcp.submit_review_receipts({'reviews': [{'instrument_id': 'asset', 'summary': {'decision': 'accept'}}]})
    assert 'change_kind' in str(rejected.value)
    assert 'accept' not in str(rejected.value)


def test_harness_receipt_is_revalidated_and_originals_never_enter_initial_prompt(monkeypatch):
    packet = {"run_id": "bound", "cutoff": "2026-09-24T00:00:00Z", "draft_reviews": [],
        "sources": [{"source_id": "large", "text": "original-sentinel" * 100000}]}
    captures = []
    def run(command, **kwargs):
        assert "original-sentinel" not in str(command)
        assert "review_harness.patch.yml" in " ".join(command)
        assert kwargs["env"]["INVESTMENT_STUDIO_WATCHLIST_HARNESS_MODE"] == "review"
        outcome_patch = Path(command[-2])
        assert outcome_patch.name == "outcome.patch.json"
        assert json.loads(outcome_patch.read_text())[0]["insert"][0]["name"].endswith("research_harness_outcome.mjs")
        path = Path(kwargs["env"]["INVESTMENT_STUDIO_REVIEW_PACKET"])
        assert path.stat().st_mode & 0o777 == 0o600
        assert json.loads(path.read_text())["packet"] == packet
        Path(kwargs["env"]["INVESTMENT_STUDIO_REVIEW_RESULT"]).write_text(json.dumps({"receipts": {"reviews": []}, "result": {"reviews": []}}))
        return SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr(agent.subprocess, "run", run)
    assert agent.run_review_agent(packet, review._review_schema([], []), "review instructions", captures.append) == {"reviews": []}
    assert captures[0]["agent_metadata"]["engine"] == "deepseek_harness"
    assert captures[0]["compact_result"] == {"reviews": []}


@pytest.mark.parametrize("stderr,retryable", [("dsh: NETWORK: fetch failed", True), ("arbitrary private-token text", False)])
def test_agent_error_classification_does_not_leak_stderr(monkeypatch, stderr, retryable):
    monkeypatch.setattr(agent.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=stderr))
    captures = []
    with pytest.raises(agent.ReviewAgentError) as failure:
        agent.run_review_agent({"sources": [], "draft_reviews": []}, {}, "rules", captures.append)
    assert failure.value.retryable is retryable
    assert stderr not in json.dumps(captures)
    assert review._safe_failure(failure.value)["retryable"] is retryable


@pytest.mark.parametrize('reason,error_type', [('max-tokens', 'OutputLimitExceeded'),
                                              ('content-filter', 'ProviderContentFilter')])
def test_reviewer_retains_native_terminal_failure_without_retry(monkeypatch, reason, error_type):
    stderr = 'private provider material\nRESEARCH_HARNESS_END ' + json.dumps({'reason': reason}, separators=(',', ':')) + '\n'
    monkeypatch.setattr(agent.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=stderr))
    captures = []
    with pytest.raises(agent.ReviewAgentError) as failure:
        agent.run_review_agent({'sources': [], 'draft_reviews': []}, {}, 'rules', captures.append)
    assert not failure.value.retryable
    assert captures[0]['agent_metadata']['error_type'] == error_type
    assert 'private provider' not in str(failure.value) + json.dumps(captures)


def test_unknown_review_startup_exit_keeps_only_allowlisted_diagnostic_signals(monkeypatch):
    stderr = 'dsh: MCP: private-key=SECRET HTTP 503 ECONNREFUSED\n{"status_code":401,"message":"private source"}'
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stderr=stderr))
    captures = []
    with pytest.raises(agent.ReviewAgentError) as failure:
        agent.run_review_agent({"sources": [], "draft_reviews": []}, {}, "rules", captures.append)
    safe = review._safe_failure(failure.value)
    diagnostic = json.loads(safe["diagnostic"])
    assert diagnostic["harness_codes"] == ["MCP"]
    assert diagnostic["http_statuses"] == [401, 503]
    assert diagnostic["system_codes"] == ["ECONNREFUSED"]
    assert diagnostic["stderr_present"] and diagnostic["read_pages"] == 0
    assert not failure.value.retryable
    assert "SECRET" not in json.dumps([safe, captures])
    assert "private source" not in json.dumps([safe, captures])
