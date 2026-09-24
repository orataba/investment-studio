import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from watchlist_app import review_mcp
from watchlist_app.services import research_review_agent as agent
from watchlist_app.services import sector_fact_review as review


def _bound(monkeypatch, tmp_path, packet):
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
    _complete_read(review_mcp.read_review_context, section="draft_reviews")
    _complete_read(review_mcp.read_review_context, section="response_schema")
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
